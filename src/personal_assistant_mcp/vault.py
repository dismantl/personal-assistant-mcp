"""Async wrapper around obsidian-livesync-mcp's ``ObsidianVaultClient``.

The upstream ``Config`` is a frozen dataclass whose field defaults are evaluated
from environment variables at *module import* time. To avoid depending on that
implicit lookup, we always construct ``Config`` with explicit keyword arguments
derived from our own ``Settings`` — making the data flow visible and testable.
"""

from __future__ import annotations

from obsidian_livesync_mcp.client import ObsidianVaultClient
from obsidian_livesync_mcp.config import Config as VaultConfig

from .config import Settings


def build_vault_config(settings: Settings) -> VaultConfig:
    """Translate our ``Settings`` into the upstream ``VaultConfig``."""
    return VaultConfig(
        couch_url=settings.couchdb_url,
        couch_user=settings.couchdb_user,
        couch_pass=settings.couchdb_password,
        db_name=settings.couchdb_db,
        obfuscate_passphrase=settings.obfuscate_passphrase,
    )


def build_vault_client(settings: Settings) -> ObsidianVaultClient:
    """Return a configured ``ObsidianVaultClient``.

    Pass the returned client into MCP tool handlers. Call
    ``await client.close()`` on shutdown to release the underlying
    ``httpx.AsyncClient`` connection pool.
    """
    return ObsidianVaultClient(build_vault_config(settings))


async def iter_all_notes(
    vault: ObsidianVaultClient,
    folder: str | None = None,
    *,
    page: int = 100,
) -> list:
    """Page through ``vault.list_notes(folder=...)`` and return every metadata record.

    Centralized so the four subsystems that enumerate notes (tasks/list, daily,
    weekly, planner) share one paginator. Return type is whatever the upstream
    client returns (``NoteMetadata``); callers use ``meta.path`` etc.
    """
    out: list = []
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
