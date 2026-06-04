"""MCP tool decorators for weekly-review operations."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Any

from obsidian_livesync_mcp.client import ObsidianVaultClient

from ..tool_errors import surface_tool_errors
from . import review


def _parse_date(value: str | None, field_name: str) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field_name!r} must be an ISO date (YYYY-MM-DD): {value!r}") from exc


def register(mcp: Any, get_vault: Callable[[], ObsidianVaultClient]) -> None:
    """Attach weekly-review tools to the FastMCP server."""

    @mcp.tool()
    @surface_tool_errors("weekly_latest")
    async def weekly_latest(include_today: bool = True) -> dict[str, Any] | None:
        """Return the most recent weekly review note, or ``None`` if none exist."""
        return await review.read_latest_weekly(get_vault(), include_today=include_today)

    @mcp.tool()
    @surface_tool_errors("weekly_read")
    async def weekly_read(target_date: str) -> dict[str, Any] | None:
        """Read a specific weekly review by ISO date."""
        parsed = _parse_date(target_date, "target_date")
        if parsed is None:
            raise ValueError("target_date is required")
        return await review.read_weekly(get_vault(), parsed)

    @mcp.tool()
    @surface_tool_errors("weekly_write_current")
    async def weekly_write_current(content: str) -> dict[str, Any]:
        """Write or overwrite this week's review at ``0 Logs/Weekly Reviews/<today>.md``."""
        return await review.write_current_weekly(get_vault(), content)


__all__ = ["register"]
