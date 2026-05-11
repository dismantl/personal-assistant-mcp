"""MCP tool decorators for release-tracker state."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from obsidian_livesync_mcp.client import ObsidianVaultClient

from . import state as release_state


def register(mcp: Any, get_vault: Callable[[], ObsidianVaultClient]) -> None:
    """Attach release-state tools to the FastMCP server."""

    @mcp.tool()
    async def release_state_read() -> dict[str, Any]:
        """Return the current release-tracker state (empty dict if uninitialized)."""
        return {"state": await release_state.read_state(get_vault())}

    @mcp.tool()
    async def release_state_update(entries: dict[str, Any]) -> dict[str, Any]:
        """Merge ``entries`` into the state by ``canonical_project_key`` and persist.

        Args:
            entries: ``{ "<key>": { "canonical_project_key": "...", ... } }``.
                Existing fields not in the new entry are preserved.
        """
        new_state = await release_state.update_state(get_vault(), entries)
        return {"state": new_state}


__all__ = ["register"]
