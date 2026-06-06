"""Shared test fixtures."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from obsidian_livesync_mcp.models import NoteContent, NoteMetadata


@dataclass
class FakeVaultClient:
    """In-memory stand-in for ``ObsidianVaultClient`` used in unit tests.

    Implements the surface area we depend on:

    - ``read_note(path) -> NoteContent | None``
    - ``write_note(path, content) -> bool``
    - ``delete_note(path, hard=False) -> bool``
    - ``read_frontmatter(path) -> dict | None``
    - ``list_notes(folder?, limit, skip) -> list[NoteMetadata]``

    Exposes ``notes`` (mutable mapping of path -> markdown), ``frontmatters``
    (path -> dict for ``read_frontmatter``), and ``writes`` (append-only log
    of every write call) so tests can introspect both end-state and call
    history.

    **Divergence from the real client (intentional, documented):**

    - ``read_frontmatter`` reads from a separate ``frontmatters`` dict rather
      than extracting YAML from ``notes`` content. Adequate for the planner
      tests (which treat frontmatter as opaque config) but won't catch a
      regression where the planner starts reading note body alongside
      frontmatter.
    - ``read_note`` returns ``None`` for missing files. Tests can populate
      ``read_errors`` when they need to model upstream read failures such as
      missing LiveSync chunks.
    """

    notes: dict[str, str] = field(default_factory=dict)
    frontmatters: dict[str, dict[str, Any]] = field(default_factory=dict)
    read_errors: dict[str, Exception] = field(default_factory=dict)
    writes: list[tuple[str, str]] = field(default_factory=list)

    async def read_note(self, path: str) -> NoteContent | None:
        if path in self.read_errors:
            raise self.read_errors[path]
        if path not in self.notes:
            return None
        content = self.notes[path]
        return NoteContent(
            path=path,
            content=content,
            size=len(content.encode("utf-8")),
            is_binary=False,
        )

    async def write_note(self, path: str, content: str) -> bool:
        self.notes[path] = content
        self.writes.append((path, content))
        return True

    async def delete_note(self, path: str, hard: bool = False) -> bool:
        return self.notes.pop(path, None) is not None

    async def close(self) -> None:
        return None

    async def read_frontmatter(self, path: str) -> dict[str, Any] | None:
        return self.frontmatters.get(path)

    async def list_notes(
        self, folder: str | None = None, limit: int = 50, skip: int = 0
    ) -> list[NoteMetadata]:
        paths = sorted(self.notes.keys())
        if folder is not None:
            prefix = folder.rstrip("/") + "/"
            paths = [p for p in paths if p.startswith(prefix) or p == folder]
        sliced = paths[skip : skip + limit]
        return [
            NoteMetadata(
                path=p,
                size=len(self.notes[p].encode("utf-8")),
                ctime=0,
                mtime=0,
                doc_type="plain",
                chunk_count=1,
            )
            for p in sliced
        ]


@pytest.fixture
def fake_vault() -> FakeVaultClient:
    """Empty in-memory vault. Mutate ``fake_vault.notes`` to seed content."""
    return FakeVaultClient()
