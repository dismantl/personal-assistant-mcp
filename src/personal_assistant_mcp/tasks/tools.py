"""MCP tool decorators for task CRUD.

Translates string-typed agent inputs into strongly-typed ``crud`` calls and
serializes ``Task`` / ``TaskRef`` / ``MutationResult`` back into
JSON-friendly dicts for the MCP wire format.

The module-level ``register(mcp, get_vault)`` function attaches the tools to
the given FastMCP server, with ``get_vault`` called on each invocation to
obtain the active, possibly lazily-constructed ``ObsidianVaultClient``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date
from typing import Any

from obsidian_livesync_mcp.client import ObsidianVaultClient

from ..tool_errors import surface_tool_errors
from . import cache, crud, planner
from .crud import MoveResult, MutationResult, TaskRef
from .paths import normalize_vault_path, resolve_move_destination

logger = logging.getLogger(__name__)

OPEN_TASK_STATUSES = (" ", "/")


def _parse_date(value: str | None, field_name: str) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field_name!r} must be an ISO date (YYYY-MM-DD): {value!r}") from exc


def _serialize_task_ref(ref: TaskRef) -> dict[str, Any]:
    """Serialize a TaskRef for the MCP wire.

    ``line_number`` is intentionally omitted — it's an artifact of parsing,
    not a stable identifier, and inconsistent across tools (set by mutations
    that look up by content, ``None`` on freshly-created tasks). The stable
    identifier is ``id`` (the content hash).
    """
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
        "is_complete": t.is_complete,
        "is_cancelled": t.is_cancelled,
    }


def _serialize_mutation(result: MutationResult) -> dict[str, Any]:
    out = _serialize_task_ref(result.ref)
    out["multiple_matches_in_file"] = result.multiple_matches_in_file
    return out


def _serialize_move(result: MoveResult) -> dict[str, Any]:
    out = _serialize_task_ref(result.ref)
    out["source_path"] = result.source_path
    out["dest_path"] = result.dest_path
    out["appended_to_dest"] = result.appended_to_dest
    out["removed_from_source"] = result.removed_from_source
    out["multiple_matches_in_source"] = result.multiple_matches_in_source
    return out


async def _freshness(
    vault: ObsidianVaultClient,
    meta: dict[str, Any] | None,
    *,
    spec_path: str,
) -> dict[str, Any]:
    if meta is None:
        return {"computed_at": None, "spec_hash": None, "source": "live", "stale": None}

    stale: bool | None = None
    try:
        spec = await planner.load_planner_spec(vault, spec_path)
        stale = cache.spec_hash(spec) != meta["spec_hash"]
    except Exception:
        stale = None

    return {
        "computed_at": meta["computed_at"],
        "spec_hash": meta["spec_hash"],
        "source": "cache",
        "stale": stale,
    }


def _status_filter(statuses: str | None, *, include_closed: bool) -> tuple[str, ...] | None:
    if statuses is not None:
        return tuple(statuses)
    if include_closed:
        return None
    return OPEN_TASK_STATUSES


async def _patch_cache_after_mutation(
    vault: ObsidianVaultClient,
    *paths: str,
) -> None:
    for path in paths:
        try:
            await cache.patch_cache_for_path(vault, path)
        except Exception:
            logger.warning("Failed to patch task cache for %s", path, exc_info=True)


def register(mcp: Any, get_vault: Callable[[], ObsidianVaultClient]) -> None:
    """Attach task CRUD tools to the FastMCP server.

    ``get_vault`` is invoked on each tool call so a single client instance
    can be shared across tools and closed cleanly on server shutdown.
    """

    @mcp.tool()
    @surface_tool_errors("tasks_list")
    async def tasks_list(
        folder: str | None = None,
        priority_bucket: str | None = None,
        statuses: str | None = None,
        due_before: str | None = None,
        include_closed: bool = False,
    ) -> dict[str, Any]:
        """List tasks from TODO.md-selected task sources, with optional filters.

        Args:
            folder: vault-relative folder prefix to restrict listing.
            priority_bucket: ``high``, ``medium``, or ``low``.
            statuses: explicit status characters to include. Overrides
                ``include_closed`` when provided.
            due_before: ISO date; include only tasks due strictly before this date.
            include_closed: include all cached/scanned statuses when no explicit
                ``statuses`` filter is provided.
        """
        vault = get_vault()
        refs, meta = await cache.cached_refs(vault, spec_path=planner.DEFAULT_SPEC_PATH)
        filtered = cache.filter_list(
            refs,
            folder=normalize_vault_path(folder) if folder else None,
            priority_bucket=priority_bucket,
            statuses=_status_filter(statuses, include_closed=include_closed),
            due_before=_parse_date(due_before, "due_before"),
        )
        return {
            "tasks": [_serialize_task_ref(r) for r in filtered],
            **await _freshness(vault, meta, spec_path=planner.DEFAULT_SPEC_PATH),
        }

    @mcp.tool()
    @surface_tool_errors("tasks_search")
    async def tasks_search(
        query: str,
        folder: str | None = None,
        statuses: str | None = None,
        include_closed: bool = False,
    ) -> dict[str, Any]:
        """Substring-search TODO.md-selected tasks, open-only by default."""
        vault = get_vault()
        refs, meta = await cache.cached_refs(vault, spec_path=planner.DEFAULT_SPEC_PATH)
        filtered = cache.filter_search(
            refs,
            query,
            folder=normalize_vault_path(folder) if folder else None,
            statuses=_status_filter(statuses, include_closed=include_closed),
        )
        return {
            "tasks": [_serialize_task_ref(r) for r in filtered],
            **await _freshness(vault, meta, spec_path=planner.DEFAULT_SPEC_PATH),
        }

    @mcp.tool()
    @surface_tool_errors("tasks_compute")
    async def tasks_compute(spec_path: str = planner.DEFAULT_SPEC_PATH) -> dict[str, Any]:
        """Recompute the task cache from the TODO planner source-selection spec."""
        path = normalize_vault_path(spec_path)
        payload = await cache.compute_cache(get_vault(), spec_path=path)
        return {
            "computed_at": payload["computed_at"],
            "spec_path": payload["spec_path"],
            "spec_hash": payload["spec_hash"],
            "task_count": len(payload["tasks"]),
        }

    @mcp.tool()
    @surface_tool_errors("tasks_add")
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
            file_path: vault path. ``today`` resolves to today's daily note,
                where the task lands under ``## Inbox``.
            priority: ``high``, ``medium``, ``low``, or a priority emoji.
            due, scheduled, start: ISO dates.
            recurrence: free-form rule text (e.g. ``every Monday``).
        """
        path = normalize_vault_path(file_path)
        vault = get_vault()
        ref = await crud.add_task(
            vault,
            path,
            text,
            priority=priority,
            due=_parse_date(due, "due"),
            scheduled=_parse_date(scheduled, "scheduled"),
            start=_parse_date(start, "start"),
            recurrence=recurrence,
        )
        await _patch_cache_after_mutation(vault, path)
        return _serialize_task_ref(ref)

    @mcp.tool()
    @surface_tool_errors("tasks_complete")
    async def tasks_complete(
        file_path: str,
        task_id: str | None = None,
        body: str | None = None,
    ) -> dict[str, Any]:
        """Mark a task done. Identify by ``task_id`` (from tasks_list) or by exact ``body``."""
        path = normalize_vault_path(file_path)
        vault = get_vault()
        result = await crud.complete_task(vault, path, task_id=task_id, body=body)
        await _patch_cache_after_mutation(vault, path)
        return _serialize_mutation(result)

    @mcp.tool()
    @surface_tool_errors("tasks_uncomplete")
    async def tasks_uncomplete(
        file_path: str,
        task_id: str | None = None,
        body: str | None = None,
    ) -> dict[str, Any]:
        """Reopen a completed task; clears its done date."""
        path = normalize_vault_path(file_path)
        vault = get_vault()
        result = await crud.uncomplete_task(vault, path, task_id=task_id, body=body)
        await _patch_cache_after_mutation(vault, path)
        return _serialize_mutation(result)

    @mcp.tool()
    @surface_tool_errors("tasks_update")
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
        vault = get_vault()
        result = await crud.update_task(
            vault,
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
        await _patch_cache_after_mutation(vault, path)
        return _serialize_mutation(result)

    @mcp.tool()
    @surface_tool_errors("tasks_delete")
    async def tasks_delete(
        file_path: str,
        task_id: str | None = None,
        body: str | None = None,
    ) -> dict[str, Any]:
        """Remove a task line from a file."""
        path = normalize_vault_path(file_path)
        vault = get_vault()
        result = await crud.delete_task(vault, path, task_id=task_id, body=body)
        await _patch_cache_after_mutation(vault, path)
        return _serialize_mutation(result)

    @mcp.tool()
    @surface_tool_errors("tasks_render_planner")
    async def tasks_render_planner(
        spec_path: str = planner.DEFAULT_SPEC_PATH,
        compute: bool = False,
        write_to: str = "TODO-rendered.md",
    ) -> dict[str, Any]:
        """Render the TODO planner view from its frontmatter spec.

        Args:
            spec_path: vault path of the note holding the planner spec
                (default ``TODO.md`` at the vault root).
            compute: when true, recompute the task cache before rendering.
            write_to: vault-relative path where the rendered markdown is written.

        Returns a dict with ``markdown`` (the rendered view) and ``sections``
        (each section's title + ordered task refs).
        """
        path = normalize_vault_path(spec_path)
        output_path = normalize_vault_path(write_to)
        vault = get_vault()
        payload = (
            await cache.compute_cache(vault, spec_path=path)
            if compute
            else await cache.read_cache(vault)
        )
        meta: dict[str, Any] | None = None
        if payload is not None and payload["spec_path"] == path:
            spec = await planner.load_planner_spec(vault, path)
            tasks_by_path: dict[str, list] = {}
            for task_data in payload["tasks"]:
                ref = cache.dict_to_ref(task_data)
                tasks_by_path.setdefault(ref.file_path, []).append(ref.task)
            output = planner.assemble_sections(spec, tasks_by_path)
            meta = {
                "computed_at": payload["computed_at"],
                "spec_path": payload["spec_path"],
                "spec_hash": payload["spec_hash"],
            }
        else:
            output = await planner.render_planner(vault, spec_path=path)
        markdown = output.to_markdown()
        await vault.write_note(output_path, markdown)
        return {
            "markdown": markdown,
            "write_to": output_path,
            "sections": [
                {
                    "title": section.title,
                    "tasks": [_serialize_task_ref(ref) for ref in section.refs],
                }
                for section in output.sections
            ],
            **await _freshness(vault, meta, spec_path=path),
        }

    @mcp.tool()
    @surface_tool_errors("tasks_move")
    async def tasks_move(
        source_path: str,
        dest_path: str,
        task_id: str | None = None,
        body: str | None = None,
    ) -> dict[str, Any]:
        """Move a task between files. Best-effort idempotent by content match.

        Args:
            source_path: vault path of the file the task currently lives in.
            dest_path: vault path or Project/Area folder. If a folder under
                ``1 Projects/`` or ``2 Areas/``, resolves to ``<folder>/todo.md``
                (creating the file if missing). Daily-note destinations land
                under ``## Inbox``.
            task_id: optional content-hash from ``tasks_list``.
            body: alternative identity — exact task body text.

        Raises ``TaskMoveConflict`` if source changes during the move.
        """
        source = normalize_vault_path(source_path)
        dest = resolve_move_destination(dest_path)
        vault = get_vault()
        result = await crud.move_task(vault, source, dest, task_id=task_id, body=body)
        await _patch_cache_after_mutation(vault, source, dest)
        return _serialize_move(result)


__all__ = ["register"]
