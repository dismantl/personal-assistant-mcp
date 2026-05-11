"""Unit tests for the pure email-parsing helpers."""

from __future__ import annotations

import email
import email.utils

from personal_assistant_mcp.proton.parse import (
    decode_header_value,
    extract_body,
    parse_message_bytes,
    parse_unsubscribe_headers,
    sort_key_for_date,
    strip_html_to_text,
)

# -----------------------------------------------------------------------------
# decode_header_value
# -----------------------------------------------------------------------------


def test_decode_header_value_returns_empty_for_none() -> None:
    assert decode_header_value(None) == ""


def test_decode_header_value_passes_through_plain() -> None:
    assert decode_header_value("Hello") == "Hello"


def test_decode_header_value_handles_rfc2047_utf8() -> None:
    raw = "=?utf-8?b?SOKEog==?="  # base64-encoded "H™"
    assert decode_header_value(raw) == "H™"


def test_decode_header_value_handles_quoted_printable() -> None:
    raw = "=?utf-8?q?Hello_world?="
    assert decode_header_value(raw) == "Hello world"


# -----------------------------------------------------------------------------
# strip_html_to_text
# -----------------------------------------------------------------------------


def test_strip_html_removes_style_and_script_blocks() -> None:
    html = "<style>body { color: red; }</style>real text<script>alert(1)</script>"
    assert strip_html_to_text(html) == "real text"


def test_strip_html_converts_br_to_newline() -> None:
    assert strip_html_to_text("line1<br>line2<br/>line3") == "line1\nline2\nline3"


def test_strip_html_converts_block_ends_to_newline() -> None:
    out = strip_html_to_text("<p>one</p><p>two</p>")
    assert "one" in out and "two" in out
    assert "\n" in out


def test_strip_html_collapses_repeated_blank_lines() -> None:
    assert strip_html_to_text("a<br><br><br><br>b") == "a\n\nb"


def test_strip_html_unescapes_entities() -> None:
    assert strip_html_to_text("Tom &amp; Jerry &lt;3") == "Tom & Jerry <3"


def test_strip_html_collapses_whitespace() -> None:
    assert strip_html_to_text("a    \t  b") == "a b"


# -----------------------------------------------------------------------------
# extract_body
# -----------------------------------------------------------------------------


def _build_plain_message(body: str) -> email.message.Message:
    msg = email.message.EmailMessage()
    msg["Subject"] = "test"
    msg.set_content(body)
    return msg


def test_extract_body_returns_plain_text() -> None:
    msg = _build_plain_message("Hello world")
    assert extract_body(msg).rstrip() == "Hello world"


def test_extract_body_truncates_at_max_len() -> None:
    msg = _build_plain_message("a" * 500)
    body = extract_body(msg, max_len=100)
    assert len(body) <= 103  # 100 chars + ellipsis
    assert body.endswith("...")


def test_extract_body_prefers_plain_over_html() -> None:
    raw = (
        "From: a@example\r\n"
        "To: b@example\r\n"
        "Subject: mixed\r\n"
        'Content-Type: multipart/alternative; boundary="bnd"\r\n'
        "\r\n"
        "--bnd\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n"
        "\r\n"
        "PLAIN BODY\r\n"
        "--bnd\r\n"
        "Content-Type: text/html; charset=utf-8\r\n"
        "\r\n"
        "<p>HTML BODY</p>\r\n"
        "--bnd--\r\n"
    )
    msg = email.message_from_string(raw)
    body = extract_body(msg)
    assert "PLAIN BODY" in body
    assert "HTML BODY" not in body


def test_extract_body_falls_back_to_html() -> None:
    raw = (
        "From: a@example\r\n"
        "Subject: html-only\r\n"
        "Content-Type: text/html; charset=utf-8\r\n"
        "\r\n"
        "<p>only html</p>\r\n"
    )
    msg = email.message_from_string(raw)
    body = extract_body(msg)
    assert "only html" in body


# -----------------------------------------------------------------------------
# parse_message_bytes
# -----------------------------------------------------------------------------


def _raw_message(subject: str = "hi", from_addr: str = "a@example", body: str = "hello") -> bytes:
    return (
        f"Message-ID: <abc@example>\r\n"
        f"From: {from_addr}\r\n"
        "To: b@example\r\n"
        f"Subject: {subject}\r\n"
        "Date: Mon, 11 May 2026 10:00:00 +0000\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n"
        "\r\n"
        f"{body}\r\n"
    ).encode("utf-8")


def test_parse_message_bytes_returns_snippet_by_default() -> None:
    parsed = parse_message_bytes(_raw_message(body="abc"), account="primary")
    assert parsed["from"] == "a@example"
    assert parsed["subject"] == "hi"
    assert parsed["account"] == "primary"
    assert parsed["id"] == "<abc@example>"
    assert "body" not in parsed
    assert parsed["snippet"].rstrip() == "abc"


def test_parse_message_bytes_includes_body_when_requested() -> None:
    parsed = parse_message_bytes(_raw_message(body="long body"), account="ai", include_body=True)
    assert "snippet" not in parsed
    assert parsed["body"].rstrip() == "long body"


# -----------------------------------------------------------------------------
# parse_unsubscribe_headers
# -----------------------------------------------------------------------------


def test_parse_unsubscribe_returns_false_when_missing() -> None:
    msg = email.message.EmailMessage()
    assert parse_unsubscribe_headers(msg) == {"has_unsubscribe": False}


def test_parse_unsubscribe_extracts_mailto_and_url() -> None:
    msg = email.message.EmailMessage()
    msg["List-Unsubscribe"] = "<mailto:u@list.example>, <https://list.example/u>"
    info = parse_unsubscribe_headers(msg)
    assert info["has_unsubscribe"] is True
    assert info["mailto"] == "mailto:u@list.example"
    assert info["url"] == "https://list.example/u"


def test_parse_unsubscribe_marks_one_click_with_post() -> None:
    msg = email.message.EmailMessage()
    msg["List-Unsubscribe"] = "<https://list.example/u>"
    msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    info = parse_unsubscribe_headers(msg)
    assert info["one_click"] is True
    assert info["post_body"] == "List-Unsubscribe=One-Click"


# -----------------------------------------------------------------------------
# sort_key_for_date
# -----------------------------------------------------------------------------


def test_sort_key_for_known_date_returns_timestamp() -> None:
    key = sort_key_for_date("Mon, 11 May 2026 10:00:00 +0000")
    assert isinstance(key, float)
    assert key > 0


def test_sort_key_for_unparseable_returns_zero() -> None:
    assert sort_key_for_date("garbage") == 0.0
    assert sort_key_for_date("") == 0.0
