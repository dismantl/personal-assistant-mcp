"""Weekly review notes.

Weekly reviews live at ``0 Logs/Weekly Reviews/YYYY-MM-DD.md``. The "current"
review is the one stamped with today's date in the vault canonical timezone.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from obsidian_livesync_mcp.client import ObsidianVaultClient

from ..tasks.paths import DAILY_NOTES_DIR, today_in_vault_tz

WEEKLY_DIR = f"{DAILY_NOTES_DIR}/Weekly Reviews"

_WEEKLY_PATH_RE = re.compile(rf"^{re.escape(WEEKLY_DIR)}/(?P<date>\d{{4}}-\d{{2}}-\d{{2}})\.md$")


def weekly_path(target_date: date) -> str:
    """Vault path for a weekly review note dated ``target_date``."""
    return f"{WEEKLY_DIR}/{target_date.isoformat()}.md"


async def read_weekly(vault: ObsidianVaultClient, target_date: date) -> dict[str, Any] | None:
    """Read a specific weekly review. Returns ``None`` if it doesn't exist."""
    path = weekly_path(target_date)
    note = await vault.read_note(path)
    if note is None:
        return None
    return {"date": target_date.isoformat(), "path": path, "content": note.content}


async def read_latest_weekly(
    vault: ObsidianVaultClient,
    *,
    today: date | None = None,
    include_today: bool = True,
) -> dict[str, Any] | None:
    """Return the most recent weekly review whose date is ``<= today``.

    Pass ``include_today=False`` to find the latest review strictly before today.
    """
    today = today or today_in_vault_tz()
    today_iso = today.isoformat()

    metas = await _all_weekly_metas(vault)
    candidates: list[tuple[str, str]] = []
    for meta in metas:
        m = _WEEKLY_PATH_RE.match(meta.path)
        if m is None:
            continue
        date_str = m.group("date")
        if date_str > today_iso:
            continue
        if date_str == today_iso and not include_today:
            continue
        candidates.append((date_str, meta.path))
    if not candidates:
        return None
    candidates.sort(reverse=True)

    date_str, path = candidates[0]
    note = await vault.read_note(path)
    if note is None:
        return None
    return {"date": date_str, "path": path, "content": note.content}


async def write_current_weekly(
    vault: ObsidianVaultClient,
    content: str,
    *,
    today: date | None = None,
) -> dict[str, Any]:
    """Write (or overwrite) this week's review at ``WEEKLY_DIR/<today>.md``."""
    today = today or today_in_vault_tz()
    path = weekly_path(today)

    if not content.endswith("\n"):
        content = content + "\n"

    existed = await vault.read_note(path) is not None
    await vault.write_note(path, content)
    return {
        "date": today.isoformat(),
        "path": path,
        "created": not existed,
        "size": len(content),
    }


async def _all_weekly_metas(vault: ObsidianVaultClient) -> list[Any]:
    out: list[Any] = []
    skip = 0
    page = 100
    while True:
        batch = await vault.list_notes(folder=WEEKLY_DIR, limit=page, skip=skip)
        if not batch:
            break
        out.extend(batch)
        if len(batch) < page:
            break
        skip += page
    return out


__all__ = [
    "WEEKLY_DIR",
    "read_latest_weekly",
    "read_weekly",
    "weekly_path",
    "write_current_weekly",
]
