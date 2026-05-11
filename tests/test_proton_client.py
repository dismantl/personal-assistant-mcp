"""Integration-style tests for the Proton IMAP/SMTP client (with mocked sockets)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from personal_assistant_mcp.proton.client import (
    ProtonConfig,
    _assert_bridge_local,
    _quote_imap_astring,
    archive_message_ai,
    check_unsubscribe,
    delete_message_ai,
    list_folders,
    list_recent,
    list_unread,
    read_message,
    send_message_ai,
    unsubscribe_url,
)

_CONFIG = ProtonConfig(
    imap_host="127.0.0.1",
    imap_port=1143,
    smtp_host="127.0.0.1",
    smtp_port=1025,
    primary_user="primary@example",
    primary_password="primary-pw",
    ai_user="ai@example",
    ai_password="ai-pw",
)
_FIXED_NOW = datetime(2026, 5, 11, 12, 0, tzinfo=timezone.utc)


def _raw_message(message_id: str = "<m1@example>", subject: str = "hi") -> bytes:
    return (
        f"Message-ID: {message_id}\r\n"
        "From: sender@example\r\n"
        "To: recipient@example\r\n"
        f"Subject: {subject}\r\n"
        "Date: Mon, 11 May 2026 10:00:00 +0000\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n"
        "\r\n"
        "body content\r\n"
    ).encode("utf-8")


def _mock_imap(search_uids: bytes = b"", fetch_payload: bytes | None = None) -> MagicMock:
    client = MagicMock()
    client.search.return_value = ("OK", [search_uids])
    if fetch_payload is not None:
        client.fetch.return_value = ("OK", [(b"1 (RFC822 {N}", fetch_payload)])
    else:
        client.fetch.return_value = ("OK", [None])
    client.copy.return_value = ("OK", [b"copy ok"])
    client.list.return_value = (
        "OK",
        [
            b'(\\HasNoChildren) "/" "INBOX"',
            b'(\\HasNoChildren) "/" "Archive"',
        ],
    )
    return client


# -----------------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------------


def test_config_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROTON_IMAP_HOST", "127.0.0.1")
    monkeypatch.setenv("PROTON_IMAP_PORT", "1143")
    monkeypatch.setenv("PROTON_SMTP_HOST", "localhost")
    monkeypatch.setenv("PROTON_SMTP_PORT", "1025")
    monkeypatch.setenv("PROTON_PRIMARY_USER", "p")
    monkeypatch.setenv("PROTON_PRIMARY_PASSWORD", "pp")
    monkeypatch.setenv("PROTON_AI_USER", "a")
    monkeypatch.setenv("PROTON_AI_PASSWORD", "ap")
    config = ProtonConfig.from_env()
    assert config.imap_host == "127.0.0.1"
    assert config.imap_port == 1143
    assert config.smtp_host == "localhost"
    assert config.smtp_port == 1025
    assert config.primary_user == "p"


def test_credentials_dispatches_by_account() -> None:
    assert _CONFIG.credentials("primary") == ("primary@example", "primary-pw")
    assert _CONFIG.credentials("ai") == ("ai@example", "ai-pw")


def test_credentials_rejects_unknown_account() -> None:
    with pytest.raises(ValueError, match="Unknown account"):
        _CONFIG.credentials("other")


# -----------------------------------------------------------------------------
# Loopback assertion for Bridge endpoints
# -----------------------------------------------------------------------------


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost", "proton-bridge"])
def test_assert_bridge_local_accepts_loopback_and_known_hostnames(host: str) -> None:
    _assert_bridge_local(host, "PROTON_IMAP_HOST")  # no raise


@pytest.mark.parametrize(
    "host",
    ["10.0.0.5", "192.168.1.10", "8.8.8.8", "imap.example.com", "evil.example"],
)
def test_assert_bridge_local_rejects_non_local(host: str) -> None:
    with pytest.raises(ValueError, match="loopback"):
        _assert_bridge_local(host, "PROTON_IMAP_HOST")


def test_proton_config_from_env_rejects_non_local_bridge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PROTON_IMAP_HOST", "imap.evil.example")
    monkeypatch.setenv("PROTON_IMAP_PORT", "1143")
    monkeypatch.setenv("PROTON_SMTP_HOST", "127.0.0.1")
    monkeypatch.setenv("PROTON_SMTP_PORT", "1025")
    monkeypatch.setenv("PROTON_PRIMARY_USER", "p")
    monkeypatch.setenv("PROTON_PRIMARY_PASSWORD", "pp")
    monkeypatch.setenv("PROTON_AI_USER", "a")
    monkeypatch.setenv("PROTON_AI_PASSWORD", "ap")
    with pytest.raises(ValueError, match="MITM-exposed"):
        ProtonConfig.from_env()


# -----------------------------------------------------------------------------
# IMAP astring quoting (injection guard)
# -----------------------------------------------------------------------------


def test_quote_imap_astring_quotes_plain_value() -> None:
    assert _quote_imap_astring("<m1@example>") == '"<m1@example>"'


def test_quote_imap_astring_escapes_backslash_and_quote() -> None:
    assert _quote_imap_astring('a"b\\c') == '"a\\"b\\\\c"'


@pytest.mark.parametrize(
    "payload",
    [
        '<a"\r\nUID DELETE 1:*\r\n">',
        "embedded\nnewline",
        "embedded\rcarriage",
        "embedded\x00nul",
    ],
)
def test_quote_imap_astring_rejects_control_chars(payload: str) -> None:
    with pytest.raises(ValueError, match="control characters"):
        _quote_imap_astring(payload)


async def test_read_message_rejects_imap_injection_attempt() -> None:
    """A crafted Message-ID containing CRLF must be rejected before IMAP send."""
    mock_client = _mock_imap()
    with patch("personal_assistant_mcp.proton.client._imap_connect", return_value=mock_client):
        with pytest.raises(ValueError, match="control characters"):
            await read_message(_CONFIG, "primary", 'evil"\r\nUID DELETE 1:*\r\n"')
    mock_client.search.assert_not_called()


# -----------------------------------------------------------------------------
# list_unread / list_recent / list_folders / read_message
# -----------------------------------------------------------------------------


async def test_list_unread_uses_unseen_since_criteria() -> None:
    mock_client = _mock_imap(search_uids=b"1", fetch_payload=_raw_message())
    with patch("personal_assistant_mcp.proton.client._imap_connect", return_value=mock_client):
        results = await list_unread(_CONFIG, "primary", now=_FIXED_NOW)
    assert len(results) == 1
    assert results[0]["subject"] == "hi"
    assert results[0]["account"] == "primary"
    args, _ = mock_client.search.call_args
    # 7d window from _FIXED_NOW (2026-05-11) -> SINCE 04-May-2026
    assert "UNSEEN SINCE 04-May-2026" in args


async def test_list_recent_uses_since_criteria() -> None:
    mock_client = _mock_imap(search_uids=b"1", fetch_payload=_raw_message())
    with patch("personal_assistant_mcp.proton.client._imap_connect", return_value=mock_client):
        await list_recent(_CONFIG, "ai", now=_FIXED_NOW)
    args, _ = mock_client.search.call_args
    # 3d window from _FIXED_NOW -> SINCE 08-May-2026
    assert "SINCE 08-May-2026" in args


async def test_list_unread_returns_empty_when_no_matches() -> None:
    mock_client = _mock_imap(search_uids=b"")
    with patch("personal_assistant_mcp.proton.client._imap_connect", return_value=mock_client):
        results = await list_unread(_CONFIG, "primary", now=_FIXED_NOW)
    assert results == []


async def test_list_folders_strips_quotes_and_separators() -> None:
    mock_client = _mock_imap()
    with patch("personal_assistant_mcp.proton.client._imap_connect", return_value=mock_client):
        result = await list_folders(_CONFIG, "primary")
    assert result["account"] == "primary"
    assert "INBOX" in result["folders"]
    assert "Archive" in result["folders"]


async def test_read_message_returns_body() -> None:
    mock_client = _mock_imap(search_uids=b"42", fetch_payload=_raw_message())
    with patch("personal_assistant_mcp.proton.client._imap_connect", return_value=mock_client):
        result = await read_message(_CONFIG, "primary", "<m1@example>")
    assert result["subject"] == "hi"
    assert result["body"].rstrip() == "body content"
    assert "snippet" not in result


async def test_read_message_returns_error_when_missing() -> None:
    mock_client = _mock_imap(search_uids=b"")
    with patch("personal_assistant_mcp.proton.client._imap_connect", return_value=mock_client):
        result = await read_message(_CONFIG, "primary", "<missing@example>")
    assert "error" in result
    assert result["account"] == "primary"


# -----------------------------------------------------------------------------
# archive_message_ai / delete_message_ai
# -----------------------------------------------------------------------------


async def test_archive_message_ai_moves_to_archive_folder() -> None:
    mock_client = _mock_imap(search_uids=b"5")
    with patch("personal_assistant_mcp.proton.client._imap_connect", return_value=mock_client):
        result = await archive_message_ai(_CONFIG, "<m1@example>")
    assert result["success"] is True
    assert result["action"] == "archive"
    assert result["account"] == "ai"
    mock_client.copy.assert_called_once()
    args, _ = mock_client.copy.call_args
    assert args[1] == "Archive"
    mock_client.store.assert_called_once_with(b"5", "+FLAGS", "\\Deleted")
    mock_client.expunge.assert_called_once()


async def test_delete_message_ai_moves_to_trash_folder() -> None:
    mock_client = _mock_imap(search_uids=b"7")
    with patch("personal_assistant_mcp.proton.client._imap_connect", return_value=mock_client):
        result = await delete_message_ai(_CONFIG, "<m1@example>")
    assert result["action"] == "delete"
    args, _ = mock_client.copy.call_args
    assert args[1] == "Trash"


async def test_archive_returns_error_when_message_missing() -> None:
    mock_client = _mock_imap(search_uids=b"")
    with patch("personal_assistant_mcp.proton.client._imap_connect", return_value=mock_client):
        result = await archive_message_ai(_CONFIG, "<m@example>")
    assert "error" in result
    mock_client.copy.assert_not_called()


# -----------------------------------------------------------------------------
# send_message_ai
# -----------------------------------------------------------------------------


async def test_send_message_ai_uses_smtp_correctly() -> None:
    mock_smtp_class = MagicMock()
    mock_smtp_instance = mock_smtp_class.return_value.__enter__.return_value
    with patch("personal_assistant_mcp.proton.client.smtplib.SMTP", mock_smtp_class):
        result = await send_message_ai(_CONFIG, "to@example", "Test subject", "Hello world")
    assert result["success"] is True
    assert result["to"] == "to@example"
    assert result["from"] == "ai@example"
    mock_smtp_class.assert_called_once_with("127.0.0.1", 1025)
    mock_smtp_instance.starttls.assert_called_once()
    mock_smtp_instance.login.assert_called_once_with("ai@example", "ai-pw")
    mock_smtp_instance.send_message.assert_called_once()


async def test_send_message_rejects_empty_fields() -> None:
    with pytest.raises(ValueError, match="to_addr"):
        await send_message_ai(_CONFIG, "", "subj", "body")
    with pytest.raises(ValueError, match="subject"):
        await send_message_ai(_CONFIG, "to", "", "body")
    with pytest.raises(ValueError, match="body"):
        await send_message_ai(_CONFIG, "to", "subj", "")


# -----------------------------------------------------------------------------
# check_unsubscribe / unsubscribe_url
# -----------------------------------------------------------------------------


def _raw_with_unsubscribe() -> bytes:
    return (
        "Message-ID: <u1@example>\r\n"
        "From: news@example\r\n"
        "Subject: Newsletter\r\n"
        "List-Unsubscribe: <mailto:u@news.example>, <https://news.example/u>\r\n"
        "List-Unsubscribe-Post: List-Unsubscribe=One-Click\r\n"
        "Content-Type: text/plain\r\n"
        "\r\n"
        "body\r\n"
    ).encode("utf-8")


async def test_check_unsubscribe_returns_parsed_headers() -> None:
    mock_client = _mock_imap(search_uids=b"1", fetch_payload=_raw_with_unsubscribe())
    with patch("personal_assistant_mcp.proton.client._imap_connect", return_value=mock_client):
        info = await check_unsubscribe(_CONFIG, "<u1@example>")
    assert info["has_unsubscribe"] is True
    assert info["mailto"] == "mailto:u@news.example"
    assert info["url"] == "https://news.example/u"
    assert info["from"] == "news@example"
    assert info["subject"] == "Newsletter"


async def test_check_unsubscribe_returns_error_when_missing() -> None:
    mock_client = _mock_imap(search_uids=b"")
    with patch("personal_assistant_mcp.proton.client._imap_connect", return_value=mock_client):
        info = await check_unsubscribe(_CONFIG, "<missing@example>")
    assert "error" in info


async def test_unsubscribe_url_returns_one_click_payload() -> None:
    mock_client = _mock_imap(search_uids=b"1", fetch_payload=_raw_with_unsubscribe())
    with patch("personal_assistant_mcp.proton.client._imap_connect", return_value=mock_client):
        info = await unsubscribe_url(_CONFIG, "<u1@example>")
    assert info["url"] == "https://news.example/u"
    assert info["one_click"] is True
    assert info["post_body"] == "List-Unsubscribe=One-Click"


async def test_unsubscribe_url_returns_error_when_no_url() -> None:
    raw = (
        "Message-ID: <u@example>\r\n"
        "From: x@example\r\n"
        "Subject: s\r\n"
        "List-Unsubscribe: <mailto:u@example>\r\n"
        "\r\n"
        "body"
    ).encode("utf-8")
    mock_client = _mock_imap(search_uids=b"1", fetch_payload=raw)
    with patch("personal_assistant_mcp.proton.client._imap_connect", return_value=mock_client):
        info = await unsubscribe_url(_CONFIG, "<u@example>")
    assert "error" in info
    assert "options" in info
