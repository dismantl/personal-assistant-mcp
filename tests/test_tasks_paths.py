"""Unit tests for vault-path normalization."""

from __future__ import annotations

from datetime import date

import pytest

from personal_assistant_mcp.tasks.paths import (
    DAILY_NOTES_DIR,
    normalize_vault_path,
    today_in_vault_tz,
)

_FIXED_TODAY = date(2026, 5, 11)
_TODAY_PATH = f"{DAILY_NOTES_DIR}/2026-05-11.md"


@pytest.mark.parametrize(
    "raw,expected",
    [
        # Already-canonical paths pass through
        ("1 Projects/x/todo.md", "1 Projects/x/todo.md"),
        ("0 Logs/2026-05-11.md", "0 Logs/2026-05-11.md"),
        # 'today' resolution
        ("today", _TODAY_PATH),
        ("today.md", _TODAY_PATH),
        ("0 Logs/today", _TODAY_PATH),
        ("0 Logs/today.md", _TODAY_PATH),
        # Leading prefixes stripped
        ("/0 Logs/today.md", _TODAY_PATH),
        ("./0 Logs/today.md", _TODAY_PATH),
        ("vault://0 Logs/today.md", _TODAY_PATH),
        # Backslashes converted
        ("0 Logs\\today.md", _TODAY_PATH),
        ("0 Logs\\sub\\file.md", "0 Logs/sub/file.md"),
        # Repeated slashes collapsed
        ("0 Logs//today.md", _TODAY_PATH),
        ("1 Projects///x///todo.md", "1 Projects/x/todo.md"),
        # Trailing slash stripped
        ("1 Projects/x/", "1 Projects/x"),
        # Percent-encoded space decoded
        ("0%20Logs/today.md", _TODAY_PATH),
        # Whitespace trimmed
        ("  1 Projects/x/todo.md  ", "1 Projects/x/todo.md"),
    ],
)
def test_normalize_path_canonical_forms(raw: str, expected: str) -> None:
    assert normalize_vault_path(raw, today=_FIXED_TODAY) == expected


@pytest.mark.parametrize(
    "bad",
    [
        "../etc/passwd",
        "0 Logs/../../etc",
        "..",
        "./..",
        "0 Logs/sub/../../escape",
        "1 Projects/x/../../../oops",
    ],
)
def test_normalize_rejects_traversal(bad: str) -> None:
    with pytest.raises(ValueError, match="traversal|Empty"):
        normalize_vault_path(bad, today=_FIXED_TODAY)


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "   ",
        "/",
        "//",
        "./",
        "vault://",
        "vault:///",
    ],
)
def test_normalize_rejects_empty(bad: str) -> None:
    with pytest.raises(ValueError, match="Empty"):
        normalize_vault_path(bad, today=_FIXED_TODAY)


def test_normalize_default_today_uses_vault_tz() -> None:
    """Without an explicit `today`, resolution uses America/New_York current date."""
    result = normalize_vault_path("today")
    actual_today = today_in_vault_tz().isoformat()
    assert result == f"{DAILY_NOTES_DIR}/{actual_today}.md"


def test_today_in_vault_tz_returns_date() -> None:
    d = today_in_vault_tz()
    assert isinstance(d, date)


def test_normalize_preserves_unicode_path_segments() -> None:
    """Vault paths may contain Unicode (Holden's folders use spaces and emoji-adjacent chars)."""
    assert (
        normalize_vault_path("3 Resources/digests/releases/2026-05-11.md", today=_FIXED_TODAY)
        == "3 Resources/digests/releases/2026-05-11.md"
    )


def test_normalize_does_not_add_md_extension() -> None:
    """The function does not auto-add .md — callers are responsible for the extension."""
    assert normalize_vault_path("1 Projects/x/todo", today=_FIXED_TODAY) == "1 Projects/x/todo"
