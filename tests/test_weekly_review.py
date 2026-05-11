"""Unit tests for weekly-review operations."""

from __future__ import annotations

from datetime import date

from personal_assistant_mcp.weekly.review import (
    WEEKLY_DIR,
    read_latest_weekly,
    read_weekly,
    weekly_path,
    write_current_weekly,
)
from tests.conftest import FakeVaultClient

_TODAY = date(2026, 5, 11)


def test_weekly_path() -> None:
    assert weekly_path(date(2026, 4, 26)) == f"{WEEKLY_DIR}/2026-04-26.md"


async def test_read_weekly_returns_none_for_missing(
    fake_vault: FakeVaultClient,
) -> None:
    assert await read_weekly(fake_vault, _TODAY) is None


async def test_read_weekly_returns_content(fake_vault: FakeVaultClient) -> None:
    path = weekly_path(date(2026, 4, 26))
    fake_vault.notes[path] = "## ✅ Wins\nstuff\n"
    result = await read_weekly(fake_vault, date(2026, 4, 26))
    assert result == {
        "date": "2026-04-26",
        "path": path,
        "content": "## ✅ Wins\nstuff\n",
    }


async def test_read_latest_weekly_returns_most_recent(
    fake_vault: FakeVaultClient,
) -> None:
    fake_vault.notes[weekly_path(date(2026, 4, 12))] = "older"
    fake_vault.notes[weekly_path(date(2026, 4, 26))] = "newer"
    fake_vault.notes[weekly_path(date(2026, 5, 3))] = "newest before today"
    result = await read_latest_weekly(fake_vault, today=_TODAY)
    assert result is not None
    assert result["date"] == "2026-05-03"
    assert result["content"] == "newest before today"


async def test_read_latest_weekly_excludes_future(
    fake_vault: FakeVaultClient,
) -> None:
    fake_vault.notes[weekly_path(date(2026, 4, 26))] = "past"
    fake_vault.notes[weekly_path(date(2026, 6, 1))] = "future"
    result = await read_latest_weekly(fake_vault, today=_TODAY)
    assert result is not None
    assert result["date"] == "2026-04-26"


async def test_read_latest_weekly_excludes_today_when_requested(
    fake_vault: FakeVaultClient,
) -> None:
    fake_vault.notes[weekly_path(date(2026, 5, 3))] = "last week"
    fake_vault.notes[weekly_path(_TODAY)] = "today"
    result = await read_latest_weekly(fake_vault, today=_TODAY, include_today=False)
    assert result is not None
    assert result["date"] == "2026-05-03"


async def test_read_latest_weekly_returns_none_when_empty(
    fake_vault: FakeVaultClient,
) -> None:
    result = await read_latest_weekly(fake_vault, today=_TODAY)
    assert result is None


async def test_read_latest_weekly_ignores_non_date_files(
    fake_vault: FakeVaultClient,
) -> None:
    fake_vault.notes[f"{WEEKLY_DIR}/README.md"] = "not a review"
    fake_vault.notes[weekly_path(date(2026, 4, 26))] = "real"
    result = await read_latest_weekly(fake_vault, today=_TODAY)
    assert result is not None
    assert result["date"] == "2026-04-26"


async def test_write_current_weekly_creates(fake_vault: FakeVaultClient) -> None:
    result = await write_current_weekly(fake_vault, "## Wins\nshipped Phase 7\n", today=_TODAY)
    assert result["created"] is True
    assert result["date"] == "2026-05-11"
    assert result["path"] == f"{WEEKLY_DIR}/2026-05-11.md"
    assert fake_vault.notes[result["path"]] == "## Wins\nshipped Phase 7\n"


async def test_write_current_weekly_overwrites(
    fake_vault: FakeVaultClient,
) -> None:
    path = weekly_path(_TODAY)
    fake_vault.notes[path] = "old content\n"
    result = await write_current_weekly(fake_vault, "new content", today=_TODAY)
    assert result["created"] is False
    assert fake_vault.notes[path] == "new content\n"


async def test_write_current_weekly_adds_trailing_newline(
    fake_vault: FakeVaultClient,
) -> None:
    await write_current_weekly(fake_vault, "no newline", today=_TODAY)
    path = weekly_path(_TODAY)
    assert fake_vault.notes[path].endswith("\n")
