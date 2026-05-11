"""Unit tests for digest read/write operations."""

from __future__ import annotations

from datetime import date

import pytest

from personal_assistant_mcp.digests.digest import (
    DIGEST_KINDS,
    digest_path,
    read_digest,
    write_digest,
)
from tests.conftest import FakeVaultClient

_TODAY = date(2026, 5, 11)


def test_digest_kinds_are_rss_and_releases() -> None:
    assert DIGEST_KINDS == frozenset({"rss", "releases"})


@pytest.mark.parametrize(
    "kind,expected",
    [
        ("rss", "3 Resources/digests/rss/2026-05-11.md"),
        ("releases", "3 Resources/digests/releases/2026-05-11.md"),
    ],
)
def test_digest_path(kind: str, expected: str) -> None:
    assert digest_path(kind, _TODAY) == expected


def test_digest_path_rejects_unknown_kind() -> None:
    with pytest.raises(ValueError, match="Unknown digest kind"):
        digest_path("blog", _TODAY)


async def test_read_digest_returns_none_for_missing(
    fake_vault: FakeVaultClient,
) -> None:
    assert await read_digest(fake_vault, "rss", _TODAY) is None


async def test_read_digest_returns_content(fake_vault: FakeVaultClient) -> None:
    path = digest_path("rss", _TODAY)
    fake_vault.notes[path] = "## Today's feed\n- thing\n"
    result = await read_digest(fake_vault, "rss", _TODAY)
    assert result == {
        "kind": "rss",
        "date": "2026-05-11",
        "path": path,
        "content": "## Today's feed\n- thing\n",
    }


async def test_write_digest_creates_new_note(fake_vault: FakeVaultClient) -> None:
    result = await write_digest(fake_vault, "releases", _TODAY, "## Releases\n- foo 1.2.3")
    assert result["created"] is True
    assert result["kind"] == "releases"
    assert result["path"] == "3 Resources/digests/releases/2026-05-11.md"
    assert fake_vault.notes[result["path"]] == "## Releases\n- foo 1.2.3\n"


async def test_write_digest_overwrites_existing(fake_vault: FakeVaultClient) -> None:
    path = digest_path("rss", _TODAY)
    fake_vault.notes[path] = "old\n"
    result = await write_digest(fake_vault, "rss", _TODAY, "new")
    assert result["created"] is False
    assert fake_vault.notes[path] == "new\n"


async def test_write_digest_rejects_unknown_kind(
    fake_vault: FakeVaultClient,
) -> None:
    with pytest.raises(ValueError, match="Unknown digest kind"):
        await write_digest(fake_vault, "podcasts", _TODAY, "content")


async def test_read_digest_rejects_unknown_kind(
    fake_vault: FakeVaultClient,
) -> None:
    with pytest.raises(ValueError, match="Unknown digest kind"):
        await read_digest(fake_vault, "podcasts", _TODAY)


async def test_rss_and_releases_paths_are_distinct(
    fake_vault: FakeVaultClient,
) -> None:
    await write_digest(fake_vault, "rss", _TODAY, "rss body")
    await write_digest(fake_vault, "releases", _TODAY, "releases body")
    rss = await read_digest(fake_vault, "rss", _TODAY)
    releases = await read_digest(fake_vault, "releases", _TODAY)
    assert rss is not None and releases is not None
    assert rss["content"].rstrip() == "rss body"
    assert releases["content"].rstrip() == "releases body"
    assert rss["path"] != releases["path"]
