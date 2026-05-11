"""MCP tool decorators for task CRUD.

Translates string-typed agent inputs into strongly-typed ``crud`` calls and
serializes ``Task`` / ``TaskRef`` / ``MutationResult`` back into
JSON-friendly dicts for the MCP wire format.

The module-level ``register(mcp, get_vault)`` function attaches the tools to
the given FastMCP server, with ``get_vault`` called on each invocation to
obtain the (possibly lazily-constructed) ``ObsidianVaultClient`` singleton.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Any

from obsidian_livesync_mcp.client import ObsidianVaultClient

from . import crud
from .crud import MutationResult, TaskRef
from .paths import normalize_vault_path


def _parse_date(value: str | None, field_name: str) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field_name!r} must be an ISO date (YYYY-MM-DD): {value!r}") from exc


def _serialize_task_ref(ref: TaskRef) -> dict[str, Any]:
    t = ref.task
    return {
        "id": ref.id,
        "file_path": ref.file_path,
        "body": t.body,
        "status": t.status,
        "priority": t.priority,
        "priority_bucket": t.priority_bucket,
        "due": t.due.isoformat() if t.due else None,
        "scheduled": t.scheduled.isoformat() if t.scheduled else None,
        "start": t.start.isoformat() if t.start else None,
        "created": t.created.isoformat() if t.created else None,
        "done": t.done.isoformat() if t.done else None,
        "cancelled_date": (t.cancelled_date.isoformat() if t.cancelled_date else None),
        "recurrence": t.recurrence,
        "tags": list(t.tags),
        "line_number": t.line_number,
        "is_complete": t.is_complete,
        "is_cancelled": t.is_cancelled,
    }


def _serialize_mutation(result: MutationResult) -> dict[str, Any]:
    out = _serialize_task_ref(result.ref)
    out["multiple_matches_in_file"] = result.multiple_matches_in_file
    return out


def register(mcp: Any, get_vault: Callable[[], ObsidianVaultClient]) -> None:
    """Attach task CRUD tools to the FastMCP server.

    ``get_vault`` is invoked on each tool call so a single client instance
    can be shared across tools and closed cleanly on server shutdown.
    """

    @mcp.tool()
    async def tasks_list(
        folder: str | None = None,
        priority_bucket: str | None = None,
        statuses: str = " /",
        due_before: str | None = None,
    ) -> dict[str, Any]:
        """List open tasks across the vault, with optional filters.

        Args:
            folder: vault-relative folder prefix to restrict listing.
            priority_bucket: ``high``, ``medium``, or ``low``.
            statuses: string of status characters to include (default ``" /"``).
            due_before: ISO date; include only tasks due strictly before this date.
        """
        refs = await crud.list_tasks(
            get_vault(),
            folder=normalize_vault_path(folder) if folder else None,
            priority_bucket=priority_bucket,
            statuses=tuple(statuses),
            due_before=_parse_date(due_before, "due_before"),
        )
        return {"tasks": [_serialize_task_ref(r) for r in refs]}

    @mcp.tool()
    async def tasks_search(query: str, folder: str | None = None) -> dict[str, Any]:
        """Substring-search open tasks (case-insensitive)."""
        refs = await crud.search_tasks(
            get_vault(),
            query,
            folder=normalize_vault_path(folder) if folder else None,
        )
        return {"tasks": [_serialize_task_ref(r) for r in refs]}

    @mcp.tool()
    async def tasks_add(
        text: str,
        file_path: str,
        priority: str | None = None,
        due: str | None = None,
        scheduled: str | None = None,
        start: str | None = None,
        recurrence: str | None = None,
    ) -> dict[str, Any]:
        """Add a new task to a vault file. Creates the file if it doesn't exist.

        Args:
            text: task body (without leading checkbox).
            file_path: vault path. ``today`` resolves to today's daily note.
            priority: ``high``, ``medium``, ``low``, or a priority emoji.
            due, scheduled, start: ISO dates.
            recurrence: free-form rule text (e.g. ``every Monday``).
        """
        path = normalize_vault_path(file_path)
        ref = await crud.add_task(
            get_vault(),
            path,
            text,
            priority=priority,
            due=_parse_date(due, "due"),
            scheduled=_parse_date(scheduled, "scheduled"),
            start=_parse_date(start, "start"),
            recurrence=recurrence,
        )
        return _serialize_task_ref(ref)

    @mcp.tool()
    async def tasks_complete(
        file_path: str,
        task_id: str | None = None,
        body: str | None = None,
    ) -> dict[str, Any]:
        """Mark a task done. Identify by ``task_id`` (from tasks_list) or by exact ``body``."""
        path = normalize_vault_path(file_path)
        result = await crud.complete_task(get_vault(), path, task_id=task_id, body=body)
        return _serialize_mutation(result)

    @mcp.tool()
    async def tasks_uncomplete(
        file_path: str,
        task_id: str | None = None,
        body: str | None = None,
    ) -> dict[str, Any]:
        """Reopen a completed task; clears its done date."""
        path = normalize_vault_path(file_path)
        result = await crud.uncomplete_task(get_vault(), path, task_id=task_id, body=body)
        return _serialize_mutation(result)

    @mcp.tool()
    async def tasks_update(
        file_path: str,
        task_id: str | None = None,
        body: str | None = None,
        new_body: str | None = None,
        new_priority: str | None = None,
        new_due: str | None = None,
        new_scheduled: str | None = None,
        new_start: str | None = None,
        new_recurrence: str | None = None,
    ) -> dict[str, Any]:
        """Update fields on an existing task. ``None`` means leave unchanged."""
        path = normalize_vault_path(file_path)
        result = await crud.update_task(
            get_vault(),
            path,
            task_id=task_id,
            body=body,
            new_body=new_body,
            new_priority=new_priority,
            new_due=_parse_date(new_due, "new_due"),
            new_scheduled=_parse_date(new_scheduled, "new_scheduled"),
            new_start=_parse_date(new_start, "new_start"),
            new_recurrence=new_recurrence,
        )
        return _serialize_mutation(result)

    @mcp.tool()
    async def tasks_delete(
        file_path: str,
        task_id: str | None = None,
        body: str | None = None,
    ) -> dict[str, Any]:
        """Remove a task line from a file."""
        path = normalize_vault_path(file_path)
        result = await crud.delete_task(get_vault(), path, task_id=task_id, body=body)
        return _serialize_mutation(result)


__all__ = ["register"]
