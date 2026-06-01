"""Unit tests for daily-note operations."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from personal_assistant_mcp.daily.note import (
    ARCHIVE_ROOT,
    DAILY_TEMPLATE_PATH,
    append_inbox_task,
    append_log,
    append_to_section,
    archive_old_dailies,
    archive_path,
    daily_path,
    ensure_today_note,
    get_template,
    read_daily,
    read_recent_dailies,
    write_daily,
)
from personal_assistant_mcp.tasks.paths import VAULT_TIMEZONE
from tests.conftest import FakeVaultClient

_TEMPLATE_BODY = "## Priorities\n\n\n## Schedule\n\n\n## Inbox\n\n\n## Reflection\n\n\n## Log\n"
_TODAY = date(2026, 5, 11)
_NOW = datetime(2026, 5, 11, 10, 30, tzinfo=VAULT_TIMEZONE)


def _seed_template(fake_vault: FakeVaultClient) -> None:
    fake_vault.notes[DAILY_TEMPLATE_PATH] = _TEMPLATE_BODY


# -----------------------------------------------------------------------------
# Pure helpers
# -----------------------------------------------------------------------------


def test_daily_path() -> None:
    assert daily_path(_TODAY) == "0 Logs/2026-05-11.md"


def test_archive_path() -> None:
    assert archive_path(date(2026, 3, 7)) == f"{ARCHIVE_ROOT}/2026/2026-03/2026-03-07.md"


# append_to_section --------------------------------------------------------


def test_append_to_section_inserts_into_empty_section() -> None:
    content = "## Inbox\n\n\n## Reflection\n"
    result = append_to_section(content, "## Inbox", "- [ ] new")
    assert result == "## Inbox\n- [ ] new\n\n\n## Reflection\n"


def test_append_to_section_after_existing_content() -> None:
    content = "## Inbox\n\n- [ ] alpha\n\n## Reflection\n"
    result = append_to_section(content, "## Inbox", "- [ ] beta")
    assert result == "## Inbox\n\n- [ ] alpha\n- [ ] beta\n\n## Reflection\n"


def test_append_to_section_at_eof() -> None:
    content = "## Inbox\n## Log\n"
    result = append_to_section(content, "## Log", "- 10:00 — area: did thing")
    assert result == "## Inbox\n## Log\n- 10:00 — area: did thing\n"


def test_append_to_section_forces_trailing_newline() -> None:
    """Output always ends with ``\\n`` (Obsidian canonical shape), regardless of input."""
    assert append_to_section("## Log\n", "## Log", "- entry").endswith("\n")
    assert append_to_section("## Log", "## Log", "- entry").endswith("\n")


def test_append_to_section_raises_when_missing() -> None:
    content = "## Inbox\n"
    with pytest.raises(ValueError, match="not found"):
        append_to_section(content, "## Log", "x")


def test_append_to_section_ignores_h3_subheadings() -> None:
    """H3 (``### ...``) headings should not terminate an H2 section."""
    content = "## Inbox\n\n### sub-heading\n- [ ] alpha\n\n## Reflection\n"
    result = append_to_section(content, "## Inbox", "- [ ] beta")
    assert "- [ ] alpha\n- [ ] beta" in result
    assert "## Reflection" in result


def test_append_to_section_ignores_h2_inside_fenced_code_block() -> None:
    """A ``## foo`` line inside a fenced code block must not terminate the section."""
    content = "## Inbox\n- [ ] alpha\n```\n## not a real heading\n```\n- [ ] beta\n## Reflection\n"
    result = append_to_section(content, "## Inbox", "- [ ] gamma")
    # New line should land before "## Reflection", after "- [ ] beta"
    assert "- [ ] beta\n- [ ] gamma\n## Reflection" in result
    # The fenced sample heading stays intact
    assert "```\n## not a real heading\n```" in result


def test_append_to_section_ignores_target_inside_fenced_code_block() -> None:
    """The section *opening* match also skips fenced content."""
    content = "## Notes\n```\n## Inbox\n```\n## Inbox\n- [ ] real\n"
    result = append_to_section(content, "## Inbox", "- [ ] new")
    # The append must land under the real heading at the end, not the fenced sample
    assert result.endswith("- [ ] real\n- [ ] new\n") or result.endswith("- [ ] real\n- [ ] new")


# -----------------------------------------------------------------------------
# get_template / ensure_today_note
# -----------------------------------------------------------------------------


async def test_get_template_returns_body(fake_vault: FakeVaultClient) -> None:
    _seed_template(fake_vault)
    assert await get_template(fake_vault) == _TEMPLATE_BODY


async def test_get_template_raises_when_missing(fake_vault: FakeVaultClient) -> None:
    with pytest.raises(FileNotFoundError, match=DAILY_TEMPLATE_PATH):
        await get_template(fake_vault)


async def test_ensure_today_note_creates_when_missing(
    fake_vault: FakeVaultClient,
) -> None:
    _seed_template(fake_vault)
    result = await ensure_today_note(fake_vault, today=_TODAY)
    assert result == {"path": "0 Logs/2026-05-11.md", "created": True}
    assert fake_vault.notes["0 Logs/2026-05-11.md"] == _TEMPLATE_BODY


async def test_ensure_today_note_idempotent(fake_vault: FakeVaultClient) -> None:
    _seed_template(fake_vault)
    fake_vault.notes["0 Logs/2026-05-11.md"] = "existing content\n"
    result = await ensure_today_note(fake_vault, today=_TODAY)
    assert result == {"path": "0 Logs/2026-05-11.md", "created": False}
    assert fake_vault.notes["0 Logs/2026-05-11.md"] == "existing content\n"


# -----------------------------------------------------------------------------
# read_daily / read_recent_dailies
# -----------------------------------------------------------------------------


async def test_read_daily_returns_none_for_missing(
    fake_vault: FakeVaultClient,
) -> None:
    assert await read_daily(fake_vault, _TODAY) is None


async def test_read_daily_returns_content(fake_vault: FakeVaultClient) -> None:
    fake_vault.notes["0 Logs/2026-05-11.md"] = "hello\n"
    result = await read_daily(fake_vault, _TODAY)
    assert result == {
        "date": "2026-05-11",
        "path": "0 Logs/2026-05-11.md",
        "content": "hello\n",
    }


async def test_read_recent_dailies_returns_newest_first(
    fake_vault: FakeVaultClient,
) -> None:
    fake_vault.notes["0 Logs/2026-05-09.md"] = "a"
    fake_vault.notes["0 Logs/2026-05-10.md"] = "b"
    fake_vault.notes["0 Logs/2026-05-11.md"] = "c"
    result = await read_recent_dailies(fake_vault, n=2, today=_TODAY)
    assert [r["date"] for r in result] == ["2026-05-11", "2026-05-10"]


async def test_read_recent_dailies_excludes_future(
    fake_vault: FakeVaultClient,
) -> None:
    fake_vault.notes["0 Logs/2026-05-10.md"] = "a"
    fake_vault.notes["0 Logs/2026-12-31.md"] = "future"
    result = await read_recent_dailies(fake_vault, n=5, today=_TODAY)
    assert [r["date"] for r in result] == ["2026-05-10"]


async def test_read_recent_dailies_excludes_today_when_requested(
    fake_vault: FakeVaultClient,
) -> None:
    fake_vault.notes["0 Logs/2026-05-10.md"] = "a"
    fake_vault.notes["0 Logs/2026-05-11.md"] = "b"
    result = await read_recent_dailies(fake_vault, n=5, today=_TODAY, include_today=False)
    assert [r["date"] for r in result] == ["2026-05-10"]


async def test_read_recent_dailies_ignores_non_date_notes(
    fake_vault: FakeVaultClient,
) -> None:
    fake_vault.notes["0 Logs/Weekly Reviews/2026-05-04.md"] = "weekly"
    fake_vault.notes["0 Logs/attachments/image.md"] = "attachment"
    fake_vault.notes["0 Logs/2026-05-10.md"] = "daily"
    result = await read_recent_dailies(fake_vault, n=10, today=_TODAY)
    assert [r["date"] for r in result] == ["2026-05-10"]


# -----------------------------------------------------------------------------
# write_daily
# -----------------------------------------------------------------------------


async def test_write_daily_creates_when_missing(fake_vault: FakeVaultClient) -> None:
    result = await write_daily(fake_vault, "## Priorities\nfoo\n", today=_TODAY)
    assert result["created"] is True
    assert result["date"] == "2026-05-11"
    assert result["path"] == "0 Logs/2026-05-11.md"
    assert fake_vault.notes["0 Logs/2026-05-11.md"] == "## Priorities\nfoo\n"


async def test_write_daily_overwrites_existing(fake_vault: FakeVaultClient) -> None:
    fake_vault.notes["0 Logs/2026-05-11.md"] = "old content\n"
    result = await write_daily(fake_vault, "new content", today=_TODAY)
    assert result["created"] is False
    assert fake_vault.notes["0 Logs/2026-05-11.md"] == "new content\n"


async def test_write_daily_forces_trailing_newline(fake_vault: FakeVaultClient) -> None:
    await write_daily(fake_vault, "no trailing newline", today=_TODAY)
    assert fake_vault.notes["0 Logs/2026-05-11.md"].endswith("\n")


async def test_write_daily_preserves_concurrent_inbox_and_log_appends(
    fake_vault: FakeVaultClient,
) -> None:
    stale_content = append_to_section(_TEMPLATE_BODY, "## Inbox", "- [ ] planned task")
    current_content = append_to_section(stale_content, "## Inbox", "- [ ] captured task")
    current_content = append_to_section(current_content, "## Log", "- 10:31 — WARD: captured")
    fake_vault.notes["0 Logs/2026-05-11.md"] = current_content

    await write_daily(fake_vault, stale_content, today=_TODAY)

    body = fake_vault.notes["0 Logs/2026-05-11.md"]
    assert "- [ ] planned task" in body
    assert "- [ ] captured task" in body
    assert "- 10:31 — WARD: captured" in body


async def test_write_daily_does_not_restore_inbox_task_moved_elsewhere(
    fake_vault: FakeVaultClient,
) -> None:
    current_content = append_to_section(_TEMPLATE_BODY, "## Inbox", "- [ ] moved task")
    submitted_content = append_to_section(_TEMPLATE_BODY, "## Priorities", "- [ ] moved task")
    fake_vault.notes["0 Logs/2026-05-11.md"] = current_content

    await write_daily(fake_vault, submitted_content, today=_TODAY)

    body = fake_vault.notes["0 Logs/2026-05-11.md"]
    assert "## Priorities\n- [ ] moved task" in body
    assert "## Inbox\n- [ ] moved task" not in body


async def test_write_daily_can_disable_append_preservation_for_destructive_rewrite(
    fake_vault: FakeVaultClient,
) -> None:
    current_content = append_to_section(_TEMPLATE_BODY, "## Inbox", "- [ ] remove task")
    fake_vault.notes["0 Logs/2026-05-11.md"] = current_content

    await write_daily(fake_vault, _TEMPLATE_BODY, today=_TODAY, preserve_append_only=False)

    assert "- [ ] remove task" not in fake_vault.notes["0 Logs/2026-05-11.md"]


# -----------------------------------------------------------------------------
# append_log
# -----------------------------------------------------------------------------


async def test_append_log_creates_today_note_if_missing(
    fake_vault: FakeVaultClient,
) -> None:
    _seed_template(fake_vault)
    result = await append_log(fake_vault, "WARD", "fixed calendar bugs", today=_TODAY, now=_NOW)
    assert result["path"] == "0 Logs/2026-05-11.md"
    assert result["entry"] == "- 10:30 — WARD: fixed calendar bugs"
    body = fake_vault.notes["0 Logs/2026-05-11.md"]
    assert "## Log\n- 10:30 — WARD: fixed calendar bugs" in body


async def test_append_log_stacks_entries(fake_vault: FakeVaultClient) -> None:
    _seed_template(fake_vault)
    await append_log(fake_vault, "A", "first", today=_TODAY, now=_NOW)
    later = datetime(2026, 5, 11, 14, 0, tzinfo=VAULT_TIMEZONE)
    await append_log(fake_vault, "B", "second", today=_TODAY, now=later)
    body = fake_vault.notes["0 Logs/2026-05-11.md"]
    assert "- 10:30 — A: first" in body
    assert "- 14:00 — B: second" in body
    # ordering: first entry before second
    assert body.index("- 10:30 — A: first") < body.index("- 14:00 — B: second")


async def test_append_log_uses_vault_timezone(fake_vault: FakeVaultClient) -> None:
    """A UTC datetime input is converted via strftime in the vault tz."""
    _seed_template(fake_vault)
    utc_now = datetime(2026, 5, 11, 14, 30, tzinfo=timezone.utc)
    # strftime uses the tzinfo on the datetime — we expect the input to already
    # be in vault tz; document by passing a non-tz-converted datetime here
    result = await append_log(
        fake_vault, "X", "y", today=_TODAY, now=utc_now.astimezone(VAULT_TIMEZONE)
    )
    # 14:30 UTC = 10:30 EDT
    assert "10:30" in result["entry"]


async def test_append_log_rejects_empty_inputs(
    fake_vault: FakeVaultClient,
) -> None:
    _seed_template(fake_vault)
    with pytest.raises(ValueError, match="project"):
        await append_log(fake_vault, "", "x", today=_TODAY)
    with pytest.raises(ValueError, match="description"):
        await append_log(fake_vault, "x", "", today=_TODAY)


async def test_append_log_rejects_newlines(fake_vault: FakeVaultClient) -> None:
    _seed_template(fake_vault)
    with pytest.raises(ValueError, match="newline"):
        await append_log(fake_vault, "p", "line1\nline2", today=_TODAY)


# -----------------------------------------------------------------------------
# append_inbox_task
# -----------------------------------------------------------------------------


async def test_append_inbox_task_creates_today_and_inserts(
    fake_vault: FakeVaultClient,
) -> None:
    _seed_template(fake_vault)
    result = await append_inbox_task(fake_vault, "Buy milk", priority="medium", today=_TODAY)
    assert result["path"] == "0 Logs/2026-05-11.md"
    assert result["body"] == "Buy milk"
    assert result["priority_bucket"] == "medium"
    body = fake_vault.notes["0 Logs/2026-05-11.md"]
    assert "## Inbox\n- [ ] Buy milk \U0001f53c" in body  # 🔼


async def test_append_inbox_task_rejects_empty_text(
    fake_vault: FakeVaultClient,
) -> None:
    _seed_template(fake_vault)
    with pytest.raises(ValueError, match="empty"):
        await append_inbox_task(fake_vault, "", today=_TODAY)


async def test_append_inbox_task_preserves_metadata(
    fake_vault: FakeVaultClient,
) -> None:
    _seed_template(fake_vault)
    result = await append_inbox_task(
        fake_vault,
        "task",
        priority="high",
        due=date(2026, 5, 20),
        recurrence="every Monday",
        today=_TODAY,
    )
    assert result["due"] == "2026-05-20"
    assert result["recurrence"] == "every Monday"
    body = fake_vault.notes["0 Logs/2026-05-11.md"]
    assert "⏫" in body  # high priority
    assert "📅 2026-05-20" in body
    assert "🔁 every Monday" in body


# -----------------------------------------------------------------------------
# archive_old_dailies
# -----------------------------------------------------------------------------


async def test_archive_old_dailies_moves_eligible_notes(
    fake_vault: FakeVaultClient,
) -> None:
    # Today = 2026-05-11. Cutoff = 30 days back = 2026-04-11.
    # Current month is May 2026.
    fake_vault.notes["0 Logs/2026-03-15.md"] = "very old"  # >30d, not current month -> archived
    fake_vault.notes["0 Logs/2026-04-01.md"] = "old"  # >30d, April -> archived
    fake_vault.notes["0 Logs/2026-04-15.md"] = "within 30d"  # <30d -> kept
    fake_vault.notes["0 Logs/2026-05-01.md"] = "current month"  # current month -> kept
    fake_vault.notes["0 Logs/2026-05-11.md"] = "today"  # current -> kept

    result = await archive_old_dailies(fake_vault, days=30, today=_TODAY)
    moved_from = {m["from"] for m in result["moved"]}
    assert moved_from == {"0 Logs/2026-03-15.md", "0 Logs/2026-04-01.md"}

    # Originals removed, archives present
    assert "0 Logs/2026-03-15.md" not in fake_vault.notes
    assert "0 Logs/2026-04-01.md" not in fake_vault.notes
    assert fake_vault.notes[f"{ARCHIVE_ROOT}/2026/2026-03/2026-03-15.md"] == "very old"
    assert fake_vault.notes[f"{ARCHIVE_ROOT}/2026/2026-04/2026-04-01.md"] == "old"

    # Kept files untouched
    assert fake_vault.notes["0 Logs/2026-04-15.md"] == "within 30d"
    assert fake_vault.notes["0 Logs/2026-05-01.md"] == "current month"
    assert fake_vault.notes["0 Logs/2026-05-11.md"] == "today"


async def test_archive_old_dailies_skips_when_archive_exists(
    fake_vault: FakeVaultClient,
) -> None:
    fake_vault.notes["0 Logs/2026-03-15.md"] = "duplicate-original"
    fake_vault.notes[f"{ARCHIVE_ROOT}/2026/2026-03/2026-03-15.md"] = "already-archived"
    result = await archive_old_dailies(fake_vault, days=30, today=_TODAY)
    assert result["moved"] == []
    assert result["skipped"] == ["0 Logs/2026-03-15.md"]
    # Original still in place — no overwrite of archive
    assert fake_vault.notes["0 Logs/2026-03-15.md"] == "duplicate-original"
    assert fake_vault.notes[f"{ARCHIVE_ROOT}/2026/2026-03/2026-03-15.md"] == "already-archived"


async def test_archive_old_dailies_ignores_non_daily_paths(
    fake_vault: FakeVaultClient,
) -> None:
    fake_vault.notes["0 Logs/Weekly Reviews/2026-03-15.md"] = "weekly review"
    fake_vault.notes["0 Logs/attachments/file.md"] = "attachment"
    result = await archive_old_dailies(fake_vault, days=30, today=_TODAY)
    assert result["moved"] == []
    assert fake_vault.notes["0 Logs/Weekly Reviews/2026-03-15.md"] == "weekly review"
