"""Daily-note operations: template instantiation, sectional appends, archive, read.

Sectional appends are the non-obvious part. Holden's template is::

    ## Priorities


    ## Schedule


    ## Inbox


    ## Reflection


    ## Log

``append_to_section`` finds the named heading, walks back past any trailing
blank lines that belong to the section, and inserts the new line right after
the last content line — preserving the empty lines that separate sections.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from obsidian_livesync_mcp.client import ObsidianVaultClient

from ..tasks import Task, render_task
from ..tasks.crud import resolve_priority
from ..tasks.paths import DAILY_NOTES_DIR, VAULT_TIMEZONE, today_in_vault_tz
from ..vault import iter_all_notes

DAILY_TEMPLATE_PATH = "Templates/Daily Note.md"
ARCHIVE_ROOT = f"{DAILY_NOTES_DIR}/Archive"

_DAILY_PATH_RE = re.compile(
    rf"^{re.escape(DAILY_NOTES_DIR)}/(?P<date>\d{{4}}-\d{{2}}-\d{{2}})\.md$"
)
_H2_HEADING_RE = re.compile(r"^##\s+\S")


# -----------------------------------------------------------------------------
# Pure helpers
# -----------------------------------------------------------------------------


def daily_path(target_date: date) -> str:
    """Vault path of the daily note for ``target_date``."""
    return f"{DAILY_NOTES_DIR}/{target_date.isoformat()}.md"


def archive_path(target_date: date) -> str:
    """Vault path of the archived daily note for ``target_date``."""
    yyyy = f"{target_date.year:04d}"
    yyyy_mm = f"{yyyy}-{target_date.month:02d}"
    return f"{ARCHIVE_ROOT}/{yyyy}/{yyyy_mm}/{target_date.isoformat()}.md"


def append_to_section(content: str, heading: str, new_line: str) -> str:
    """Insert ``new_line`` at the end of the named section.

    The section runs from its heading line up to the next H2 heading (or EOF).
    Trailing blank lines within the section are preserved — the new line is
    inserted before them. Raises ``ValueError`` if the heading is not found.

    Heading detection ignores lines inside fenced code blocks (``\\`\\`\\``
    delimited) so a code sample containing ``## foo`` doesn't terminate the
    enclosing section.
    """
    target = heading.strip()
    lines = content.splitlines()

    section_start: int | None = None
    section_end = len(lines)
    in_fence = False
    for i, line in enumerate(lines):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if section_start is None:
            if line.strip() == target:
                section_start = i
        else:
            if _H2_HEADING_RE.match(line):
                section_end = i
                break

    if section_start is None:
        raise ValueError(f"Section {heading!r} not found in note")

    insert_at = section_end
    while insert_at > section_start + 1 and lines[insert_at - 1].strip() == "":
        insert_at -= 1

    new_lines = lines[:insert_at] + [new_line] + lines[insert_at:]
    # Always force trailing newline (Obsidian canonical shape; matches crud._rebuild).
    return "\n".join(new_lines) + "\n"


# -----------------------------------------------------------------------------
# Async operations
# -----------------------------------------------------------------------------


async def get_template(vault: ObsidianVaultClient) -> str:
    note = await vault.read_note(DAILY_TEMPLATE_PATH)
    if note is None:
        raise FileNotFoundError(f"Daily-note template not found at {DAILY_TEMPLATE_PATH!r}")
    return note.content


async def ensure_today_note(
    vault: ObsidianVaultClient, *, today: date | None = None
) -> dict[str, Any]:
    """Create today's daily note from the template if it doesn't exist. Idempotent."""
    today = today or today_in_vault_tz()
    path = daily_path(today)
    if await vault.read_note(path) is not None:
        return {"path": path, "created": False}
    template = await get_template(vault)
    await vault.write_note(path, template)
    return {"path": path, "created": True}


async def read_daily(vault: ObsidianVaultClient, target_date: date) -> dict[str, Any] | None:
    """Return the daily note for ``target_date`` or ``None`` if missing."""
    path = daily_path(target_date)
    note = await vault.read_note(path)
    if note is None:
        return None
    return {"date": target_date.isoformat(), "path": path, "content": note.content}


async def read_recent_dailies(
    vault: ObsidianVaultClient,
    n: int = 7,
    *,
    today: date | None = None,
    include_today: bool = True,
) -> list[dict[str, Any]]:
    """Up to ``n`` most-recent daily notes (newest first). Never includes future dates."""
    today = today or today_in_vault_tz()
    today_iso = today.isoformat()

    metas = await _all_daily_metas(vault)
    candidates: list[tuple[str, str]] = []
    for meta in metas:
        m = _DAILY_PATH_RE.match(meta.path)
        if m is None:
            continue
        date_str = m.group("date")
        if date_str > today_iso:
            continue
        if date_str == today_iso and not include_today:
            continue
        candidates.append((date_str, meta.path))
    candidates.sort(reverse=True)

    out: list[dict[str, Any]] = []
    for date_str, path in candidates[:n]:
        note = await vault.read_note(path)
        if note is not None:
            out.append({"date": date_str, "path": path, "content": note.content})
    return out


async def append_log(
    vault: ObsidianVaultClient,
    project: str,
    description: str,
    *,
    today: date | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Append a ``- HH:MM — Project: description`` line to today's ``## Log`` section.

    Auto-creates today's daily note from template if it doesn't already exist.
    Timestamp is generated in ``America/New_York`` (vault canonical tz).
    """
    project = project.strip()
    description = description.strip()
    if not project:
        raise ValueError("project must not be empty")
    if not description:
        raise ValueError("description must not be empty")
    if "\n" in project or "\n" in description:
        raise ValueError("project and description must not contain newlines")

    today = today or today_in_vault_tz()
    now = now or datetime.now(VAULT_TIMEZONE)
    entry = f"- {now.strftime('%H:%M')} — {project}: {description}"

    await ensure_today_note(vault, today=today)
    path = daily_path(today)
    note = await vault.read_note(path)
    assert note is not None  # just created above

    new_content = append_to_section(note.content, "## Log", entry)
    await vault.write_note(path, new_content)
    return {"path": path, "entry": entry}


async def append_inbox_task(
    vault: ObsidianVaultClient,
    text: str,
    *,
    priority: str | None = None,
    due: date | None = None,
    scheduled: date | None = None,
    start: date | None = None,
    recurrence: str | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    """Append a task to today's ``## Inbox`` section. Auto-creates today's note if missing."""
    cleaned = text.strip()
    if not cleaned:
        raise ValueError("Task text must not be empty")
    if "\n" in cleaned:
        raise ValueError("Task text must not contain newlines")

    today = today or today_in_vault_tz()
    await ensure_today_note(vault, today=today)
    path = daily_path(today)
    note = await vault.read_note(path)
    assert note is not None

    task = Task(
        body=cleaned,
        priority=resolve_priority(priority),
        due=due,
        scheduled=scheduled,
        start=start,
        recurrence=recurrence,
    )
    rendered = render_task(task)
    new_content = append_to_section(note.content, "## Inbox", rendered)
    await vault.write_note(path, new_content)

    return {
        "path": path,
        "body": task.body,
        "priority": task.priority,
        "priority_bucket": task.priority_bucket,
        "due": task.due.isoformat() if task.due else None,
        "scheduled": task.scheduled.isoformat() if task.scheduled else None,
        "start": task.start.isoformat() if task.start else None,
        "recurrence": task.recurrence,
    }


async def archive_old_dailies(
    vault: ObsidianVaultClient,
    *,
    days: int = 30,
    today: date | None = None,
) -> dict[str, Any]:
    """Move daily notes older than ``days`` AND outside the current month into the archive.

    Returns ``{"moved": [{from, to}, ...], "skipped": [path, ...]}``.
    """
    today = today or today_in_vault_tz()
    cutoff_ordinal = today.toordinal() - days

    metas = await _all_daily_metas(vault)
    moved: list[dict[str, str]] = []
    skipped: list[str] = []

    for meta in metas:
        m = _DAILY_PATH_RE.match(meta.path)
        if m is None:
            continue
        try:
            target_date = date.fromisoformat(m.group("date"))
        except ValueError:
            skipped.append(meta.path)
            continue

        in_current_month = target_date.year == today.year and target_date.month == today.month
        if target_date.toordinal() >= cutoff_ordinal or in_current_month:
            continue

        from_path = daily_path(target_date)
        to_path = archive_path(target_date)

        if await vault.read_note(to_path) is not None:
            skipped.append(from_path)
            continue

        source = await vault.read_note(from_path)
        if source is None:
            skipped.append(from_path)
            continue

        await vault.write_note(to_path, source.content)
        await vault.delete_note(from_path)
        moved.append({"from": from_path, "to": to_path})

    return {"moved": moved, "skipped": skipped}


async def _all_daily_metas(vault: ObsidianVaultClient) -> list[Any]:
    return await iter_all_notes(vault, DAILY_NOTES_DIR)


__all__ = [
    "ARCHIVE_ROOT",
    "DAILY_TEMPLATE_PATH",
    "append_inbox_task",
    "append_log",
    "append_to_section",
    "archive_old_dailies",
    "archive_path",
    "daily_path",
    "ensure_today_note",
    "get_template",
    "read_daily",
    "read_recent_dailies",
]
