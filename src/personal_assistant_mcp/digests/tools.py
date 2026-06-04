"""MCP tool decorators for digest operations."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Any

from obsidian_livesync_mcp.client import ObsidianVaultClient

from ..tasks.paths import today_in_vault_tz
from ..tool_errors import surface_tool_errors
from . import digest


def _parse_date_or_today(value: str | None, field_name: str) -> date:
    if value is None:
        return today_in_vault_tz()
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field_name!r} must be an ISO date (YYYY-MM-DD): {value!r}") from exc


def register(mcp: Any, get_vault: Callable[[], ObsidianVaultClient]) -> None:
    """Attach digest tools to the FastMCP server."""

    @mcp.tool()
    @surface_tool_errors("digest_read")
    async def digest_read(kind: str, target_date: str | None = None) -> dict[str, Any] | None:
        """Read a digest note for a given date.

        Args:
            kind: ``rss`` or ``releases``.
            target_date: ISO date; defaults to today in vault timezone.
        """
        parsed = _parse_date_or_today(target_date, "target_date")
        return await digest.read_digest(get_vault(), kind, parsed)

    @mcp.tool()
    @surface_tool_errors("digest_write")
    async def digest_write(
        kind: str,
        content: str,
        target_date: str | None = None,
    ) -> dict[str, Any]:
        """Write or overwrite a digest note.

        Args:
            kind: ``rss`` or ``releases``.
            content: full Markdown content for the digest.
            target_date: ISO date; defaults to today.
        """
        parsed = _parse_date_or_today(target_date, "target_date")
        return await digest.write_digest(get_vault(), kind, parsed, content)


__all__ = ["register"]
