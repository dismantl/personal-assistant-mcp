"""MCP tool decorators for daily-note operations."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Any

from obsidian_livesync_mcp.client import ObsidianVaultClient

from . import note as daily_note


def _parse_date(value: str | None, field_name: str) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field_name!r} must be an ISO date (YYYY-MM-DD): {value!r}") from exc


def register(mcp: Any, get_vault: Callable[[], ObsidianVaultClient]) -> None:
    """Attach daily-note tools to the FastMCP server."""

    @mcp.tool()
    async def daily_create_today() -> dict[str, Any]:
        """Create today's daily note from the template if absent. Idempotent."""
        return await daily_note.ensure_today_note(get_vault())

    @mcp.tool()
    async def daily_template() -> dict[str, Any]:
        """Return the daily-note template body."""
        return {"content": await daily_note.get_template(get_vault())}

    @mcp.tool()
    async def daily_read_today() -> dict[str, Any] | None:
        """Read today's daily note. Returns ``None`` if it does not exist."""
        from ..tasks.paths import today_in_vault_tz

        return await daily_note.read_daily(get_vault(), today_in_vault_tz())

    @mcp.tool()
    async def daily_read(target_date: str) -> dict[str, Any] | None:
        """Read a specific daily note by ISO date."""
        parsed = _parse_date(target_date, "target_date")
        if parsed is None:
            raise ValueError("target_date is required")
        return await daily_note.read_daily(get_vault(), parsed)

    @mcp.tool()
    async def daily_read_recent(n: int = 7) -> dict[str, Any]:
        """Return up to ``n`` most recent daily notes (newest first)."""
        notes = await daily_note.read_recent_dailies(get_vault(), n=n)
        return {"notes": notes}

    @mcp.tool()
    async def daily_append_log(project: str, description: str) -> dict[str, Any]:
        """Append a ``- HH:MM — Project: description`` entry to today's Log section.

        Timestamp is generated server-side in America/New_York.
        """
        return await daily_note.append_log(get_vault(), project, description)

    @mcp.tool()
    async def daily_append_inbox(
        text: str,
        priority: str | None = None,
        due: str | None = None,
        scheduled: str | None = None,
        start: str | None = None,
        recurrence: str | None = None,
    ) -> dict[str, Any]:
        """Add a task to today's daily-note ``## Inbox`` section."""
        return await daily_note.append_inbox_task(
            get_vault(),
            text,
            priority=priority,
            due=_parse_date(due, "due"),
            scheduled=_parse_date(scheduled, "scheduled"),
            start=_parse_date(start, "start"),
            recurrence=recurrence,
        )

    @mcp.tool()
    async def daily_archive_old(days: int = 30) -> dict[str, Any]:
        """Move daily notes older than ``days`` and outside the current month into the archive.

        Archive layout: ``0 Logs/Archive/YYYY/YYYY-MM/YYYY-MM-DD.md``.
        """
        return await daily_note.archive_old_dailies(get_vault(), days=days)


__all__ = ["register"]
