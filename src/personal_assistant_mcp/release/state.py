"""Release-tracker state persistence, backed by the vault.

Schema (verbatim from the legacy wrapper)::

    {
      "<canonical_project_key>": {
        "canonical_project_key": "...",
        "project_name": "...",
        "version": "...",
        "published_at": "...",
        "url": "...",
        "checked_at": "..."
      },
      ...
    }

``update_state`` merges incoming entries into the existing state keyed by
``canonical_project_key`` — new fields overwrite old, missing fields are
preserved. This matches the upstream wrapper exactly.

State lives at ``3 Resources/digests/releases/state.json`` in the vault, next
to the dated digest files it supports.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from obsidian_livesync_mcp.client import ObsidianVaultClient

STATE_PATH = "3 Resources/digests/releases/state.json"


async def read_state(vault: ObsidianVaultClient) -> dict[str, Any]:
    """Read the release-tracker state. Returns ``{}`` if the file does not exist."""
    note = await vault.read_note(STATE_PATH)
    if note is None:
        return {}
    try:
        return json.loads(note.content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Corrupt release state at {STATE_PATH!r}: {exc}") from exc


async def update_state(vault: ObsidianVaultClient, entries: dict[str, Any]) -> dict[str, Any]:
    """Merge ``entries`` into the existing state keyed by ``canonical_project_key``.

    Returns the new full state.
    """
    validate_entries(entries)
    state = await read_state(vault)
    for value in entries.values():
        key = value["canonical_project_key"]
        merged = state.get(key, {})
        merged.update(value)
        state[key] = merged
    serialized = json.dumps(state, indent=2, ensure_ascii=False) + "\n"
    await vault.write_note(STATE_PATH, serialized)
    return state


def validate_entries(data: Any) -> None:
    """Validate that ``data`` is a dict-of-dicts, each with ``canonical_project_key``.

    Raises ``ValueError`` on shape mismatches.
    """
    if not isinstance(data, dict):
        raise ValueError("Input must be a JSON object (dict)")
    for key, value in data.items():
        if not isinstance(value, dict):
            raise ValueError(f"Entry {key!r} must be a dict")
        if "canonical_project_key" not in value:
            raise ValueError(f"Entry {key!r} missing canonical_project_key")


async def migrate_from_local_file(
    vault: ObsidianVaultClient, source_path: str | Path
) -> dict[str, Any]:
    """One-shot helper to move release state from a local JSON file into the vault.

    Refuses to overwrite an existing vault-resident state file. Intended for a
    one-time migration from a prior local state file, not exposed as an MCP tool.
    """
    path = Path(source_path) if isinstance(source_path, str) else source_path
    if not path.is_file():
        raise FileNotFoundError(path)

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {path}: {exc}") from exc

    validate_entries(data)

    # File-existence check (not content-truthy) — an empty ``{}`` state file
    # still represents a prior migration and must not be silently clobbered.
    if await vault.read_note(STATE_PATH) is not None:
        raise RuntimeError(
            f"Vault state already exists at {STATE_PATH!r} (refusing to overwrite). "
            "Manually inspect and clear if intentional."
        )

    serialized = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    await vault.write_note(STATE_PATH, serialized)
    return data


__all__ = [
    "STATE_PATH",
    "migrate_from_local_file",
    "read_state",
    "update_state",
    "validate_entries",
]
