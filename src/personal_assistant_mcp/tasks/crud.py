"""CRUD operations on tasks across the vault.

All path arguments are expected to be pre-normalized by ``normalize_vault_path``.
Identity is content-hash by default; ``body`` is accepted as a fallback for
agent ergonomics. When multiple tasks in a single file share identity, the
first match is used and the result includes ``multiple_matches_in_file=True``
so callers can surface a warning.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import date

from obsidian_livesync_mcp.client import ObsidianVaultClient

from .model import Task
from .parse import PRIORITY_EMOJI, parse_task
from .paths import today_in_vault_tz
from .render import render_task

_BUCKET_TO_EMOJI: dict[str, str] = {
    "high": "⏫",  # ⏫
    "medium": "\U0001f53c",  # 🔼
    "low": "\U0001f53d",  # 🔽
}

# Folders that never host task content and should be skipped during list operations.
_SKIP_PATH_FRAGMENTS: tuple[str, ...] = ("4 Archives", "/attachments/")


@dataclass(frozen=True)
class TaskRef:
    """A task plus the file it lives in. ``id`` is the stable content-hash."""

    file_path: str
    task: Task

    @property
    def id(self) -> str:
        return self.task.content_hash(self.file_path)


@dataclass(frozen=True)
class MutationResult:
    """Result of a single-task mutation."""

    ref: TaskRef
    multiple_matches_in_file: bool = False


# -----------------------------------------------------------------------------
# Read / list / search
# -----------------------------------------------------------------------------


async def read_tasks(vault: ObsidianVaultClient, file_path: str) -> list[Task]:
    """Return all tasks in a single file with ``line_number`` populated.

    Returns an empty list if the file does not exist.
    """
    note = await vault.read_note(file_path)
    if note is None:
        return []
    return _parse_lines(note.content)


async def list_tasks(
    vault: ObsidianVaultClient,
    *,
    folder: str | None = None,
    priority_bucket: str | None = None,
    statuses: tuple[str, ...] = (" ", "/"),
    due_before: date | None = None,
) -> list[TaskRef]:
    """List tasks across the vault, with optional filters.

    Args:
        folder: vault-relative folder prefix to restrict listing.
        priority_bucket: ``"high"``, ``"medium"``, or ``"low"``. ``None`` = any bucket.
        statuses: accepted status characters. Defaults to open (``" "``) + in-progress (``"/"``).
        due_before: include only tasks with a due date strictly before this date.
    """
    out: list[TaskRef] = []
    for meta in await _enumerate_notes(vault, folder):
        if _should_skip_path(meta.path):
            continue
        for task in await read_tasks(vault, meta.path):
            if task.status not in statuses:
                continue
            if priority_bucket is not None and task.priority_bucket != priority_bucket:
                continue
            if due_before is not None and (task.due is None or task.due >= due_before):
                continue
            out.append(TaskRef(meta.path, task))
    return out


async def search_tasks(
    vault: ObsidianVaultClient,
    query: str,
    *,
    folder: str | None = None,
    statuses: tuple[str, ...] = (" ", "/"),
) -> list[TaskRef]:
    """Case-insensitive substring search across task bodies."""
    q = query.strip().lower()
    if not q:
        return []

    out: list[TaskRef] = []
    for meta in await _enumerate_notes(vault, folder):
        if _should_skip_path(meta.path):
            continue
        for task in await read_tasks(vault, meta.path):
            if task.status not in statuses:
                continue
            if q in task.body.lower():
                out.append(TaskRef(meta.path, task))
    return out


# -----------------------------------------------------------------------------
# Mutations
# -----------------------------------------------------------------------------


async def add_task(
    vault: ObsidianVaultClient,
    file_path: str,
    text: str,
    *,
    priority: str | None = None,
    due: date | None = None,
    scheduled: date | None = None,
    start: date | None = None,
    recurrence: str | None = None,
) -> TaskRef:
    """Append a new task to ``file_path``. Creates the file if it doesn't exist."""
    cleaned = text.strip()
    if not cleaned:
        raise ValueError("Task text must not be empty")
    if "\n" in cleaned:
        raise ValueError("Task text must not contain newlines")

    new_task = Task(
        body=cleaned,
        priority=_resolve_priority(priority),
        due=due,
        scheduled=scheduled,
        start=start,
        recurrence=recurrence,
    )
    rendered = render_task(new_task)

    note = await vault.read_note(file_path)
    if note is None:
        await vault.write_note(file_path, rendered + "\n")
    else:
        lines = note.content.splitlines()
        lines.append(rendered)
        await vault.write_note(file_path, _rebuild(note.content, lines))

    return TaskRef(file_path, new_task)


async def complete_task(
    vault: ObsidianVaultClient,
    file_path: str,
    *,
    task_id: str | None = None,
    body: str | None = None,
    today: date | None = None,
) -> MutationResult:
    """Mark a task done. Adds ``✅ YYYY-MM-DD`` if no done date is already present."""
    today = today or today_in_vault_tz()

    def transform(t: Task) -> Task:
        return replace(t, status="x", done=t.done or today)

    return await _apply_to_first_match(vault, file_path, transform, task_id=task_id, body=body)


async def uncomplete_task(
    vault: ObsidianVaultClient,
    file_path: str,
    *,
    task_id: str | None = None,
    body: str | None = None,
) -> MutationResult:
    """Re-open a completed task. Clears the done date."""

    def transform(t: Task) -> Task:
        return replace(t, status=" ", done=None)

    return await _apply_to_first_match(vault, file_path, transform, task_id=task_id, body=body)


async def update_task(
    vault: ObsidianVaultClient,
    file_path: str,
    *,
    task_id: str | None = None,
    body: str | None = None,
    new_body: str | None = None,
    new_priority: str | None = None,
    new_due: date | None = None,
    new_scheduled: date | None = None,
    new_start: date | None = None,
    new_recurrence: str | None = None,
) -> MutationResult:
    """Update one or more fields on an existing task.

    ``None`` means *leave unchanged*. Clearing a field (e.g. removing a due
    date) is not supported in this iteration; re-add the task instead.
    """
    new_body_clean = new_body.strip() if new_body is not None else None
    if new_body_clean == "":
        raise ValueError("new_body must not be empty if provided")
    if new_body_clean is not None and "\n" in new_body_clean:
        raise ValueError("new_body must not contain newlines")

    resolved_priority = _resolve_priority(new_priority) if new_priority is not None else None

    def transform(t: Task) -> Task:
        return replace(
            t,
            body=new_body_clean if new_body_clean is not None else t.body,
            priority=resolved_priority if new_priority is not None else t.priority,
            due=new_due if new_due is not None else t.due,
            scheduled=new_scheduled if new_scheduled is not None else t.scheduled,
            start=new_start if new_start is not None else t.start,
            recurrence=new_recurrence if new_recurrence is not None else t.recurrence,
        )

    return await _apply_to_first_match(vault, file_path, transform, task_id=task_id, body=body)


async def delete_task(
    vault: ObsidianVaultClient,
    file_path: str,
    *,
    task_id: str | None = None,
    body: str | None = None,
) -> MutationResult:
    """Remove the matched task line from the file."""
    note = await vault.read_note(file_path)
    if note is None:
        raise FileNotFoundError(file_path)
    lines = note.content.splitlines()
    matches = _find_matching_tasks(lines, file_path, task_id=task_id, body=body)
    if not matches:
        raise LookupError(_no_match_error(task_id, body, file_path))

    line_idx, old_task = matches[0]
    new_lines = lines[:line_idx] + lines[line_idx + 1 :]
    await vault.write_note(file_path, _rebuild(note.content, new_lines))
    return MutationResult(
        ref=TaskRef(file_path, old_task),
        multiple_matches_in_file=len(matches) > 1,
    )


# -----------------------------------------------------------------------------
# Internal helpers
# -----------------------------------------------------------------------------


def _parse_lines(content: str) -> list[Task]:
    out: list[Task] = []
    for i, line in enumerate(content.splitlines()):
        task = parse_task(line, line_number=i)
        if task is not None:
            out.append(task)
    return out


def _find_matching_tasks(
    lines: list[str],
    file_path: str,
    *,
    task_id: str | None,
    body: str | None,
) -> list[tuple[int, Task]]:
    if task_id is None and body is None:
        raise ValueError("Must provide task_id or body")

    body_clean = body.strip() if body is not None else None
    matches: list[tuple[int, Task]] = []
    for i, line in enumerate(lines):
        t = parse_task(line, line_number=i)
        if t is None:
            continue
        if task_id is not None and t.content_hash(file_path) == task_id:
            matches.append((i, t))
        elif task_id is None and body_clean is not None and t.body == body_clean:
            matches.append((i, t))
    return matches


def _no_match_error(task_id: str | None, body: str | None, file_path: str) -> str:
    parts = []
    if task_id is not None:
        parts.append(f"task_id={task_id!r}")
    if body is not None:
        parts.append(f"body={body!r}")
    return f"No task matching {' '.join(parts)} in {file_path}"


async def _apply_to_first_match(
    vault: ObsidianVaultClient,
    file_path: str,
    transform: Callable[[Task], Task],
    *,
    task_id: str | None,
    body: str | None,
) -> MutationResult:
    note = await vault.read_note(file_path)
    if note is None:
        raise FileNotFoundError(file_path)
    lines = note.content.splitlines()
    matches = _find_matching_tasks(lines, file_path, task_id=task_id, body=body)
    if not matches:
        raise LookupError(_no_match_error(task_id, body, file_path))

    line_idx, old_task = matches[0]
    new_task = transform(old_task)
    lines[line_idx] = render_task(new_task)
    await vault.write_note(file_path, _rebuild(note.content, lines))
    return MutationResult(
        ref=TaskRef(file_path, new_task),
        multiple_matches_in_file=len(matches) > 1,
    )


def _rebuild(original: str, new_lines: list[str]) -> str:
    if not new_lines:
        return ""
    text = "\n".join(new_lines)
    if original.endswith("\n"):
        text += "\n"
    return text


def _resolve_priority(value: str | None) -> str | None:
    if value is None:
        return None
    if value in PRIORITY_EMOJI:
        return value
    bucket = value.lower()
    if bucket in _BUCKET_TO_EMOJI:
        return _BUCKET_TO_EMOJI[bucket]
    raise ValueError(
        f"Unknown priority {value!r}: expected one of {sorted(_BUCKET_TO_EMOJI)} "
        f"or a priority emoji from {sorted(PRIORITY_EMOJI)}"
    )


async def _enumerate_notes(vault: ObsidianVaultClient, folder: str | None) -> list[object]:
    """Page through ``list_notes`` and return all metadata records."""
    page = 100
    out: list[object] = []
    skip = 0
    while True:
        batch = await vault.list_notes(folder=folder, limit=page, skip=skip)
        if not batch:
            break
        out.extend(batch)
        if len(batch) < page:
            break
        skip += page
    return out


def _should_skip_path(path: str) -> bool:
    return any(fragment in path for fragment in _SKIP_PATH_FRAGMENTS)


__all__ = [
    "MutationResult",
    "TaskRef",
    "add_task",
    "complete_task",
    "delete_task",
    "list_tasks",
    "read_tasks",
    "search_tasks",
    "uncomplete_task",
    "update_task",
]


# Make the Awaitable import explicit for future type-checking with `Callable` returns.
_ = Awaitable  # noqa: B018 - referenced for type-readers
