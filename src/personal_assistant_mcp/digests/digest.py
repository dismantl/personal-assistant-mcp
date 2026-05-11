"""Read and write RSS and release digest notes.

Digests live at ``3 Resources/digests/<kind>/YYYY-MM-DD.md`` where ``kind`` is
either ``rss`` or ``releases``. Both follow the same file convention so they
share one module.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from obsidian_livesync_mcp.client import ObsidianVaultClient

DIGEST_KINDS: frozenset[str] = frozenset({"rss", "releases"})
DIGEST_ROOTS: dict[str, str] = {
    "rss": "3 Resources/digests/rss",
    "releases": "3 Resources/digests/releases",
}


def digest_path(kind: str, target_date: date) -> str:
    """Vault path for a digest of ``kind`` dated ``target_date``."""
    if kind not in DIGEST_KINDS:
        raise ValueError(f"Unknown digest kind {kind!r}: expected one of {sorted(DIGEST_KINDS)}")
    return f"{DIGEST_ROOTS[kind]}/{target_date.isoformat()}.md"


async def read_digest(
    vault: ObsidianVaultClient,
    kind: str,
    target_date: date,
) -> dict[str, Any] | None:
    """Read a digest note. Returns ``None`` if missing."""
    path = digest_path(kind, target_date)
    note = await vault.read_note(path)
    if note is None:
        return None
    return {
        "kind": kind,
        "date": target_date.isoformat(),
        "path": path,
        "content": note.content,
    }


async def write_digest(
    vault: ObsidianVaultClient,
    kind: str,
    target_date: date,
    content: str,
) -> dict[str, Any]:
    """Write or overwrite a digest note."""
    path = digest_path(kind, target_date)
    if not content.endswith("\n"):
        content = content + "\n"

    existed = await vault.read_note(path) is not None
    await vault.write_note(path, content)
    return {
        "kind": kind,
        "date": target_date.isoformat(),
        "path": path,
        "created": not existed,
        "size": len(content),
    }


__all__ = [
    "DIGEST_KINDS",
    "DIGEST_ROOTS",
    "digest_path",
    "read_digest",
    "write_digest",
]
