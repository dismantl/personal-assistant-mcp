"""Pure helpers for parsing email messages.

Extracted from the legacy ``email_*.py`` wrappers. No I/O, no IMAP — these
are easily testable building blocks used by ``proton.client``.
"""

from __future__ import annotations

import email
import re
from email.header import decode_header
from email.message import Message
from email.utils import parsedate_to_datetime
from html import unescape
from typing import Any

_HTML_BLOCK_TAGS = re.compile(r"<(style|script|head)[^>]*>.*?</\1>", flags=re.S | re.I)
_HTML_BR = re.compile(r"<br\s*/?>", flags=re.I)
_HTML_BLOCK_END = re.compile(r"</(p|div|tr|li|h[1-6])>", flags=re.I)
_HTML_ANY_TAG = re.compile(r"<[^>]+>")
_WHITESPACE_RUN = re.compile(r"[ \t]+")
_BLANK_LINES = re.compile(r"\n{3,}")


def decode_header_value(raw: str | None) -> str:
    """Decode an RFC 2047 encoded header value into a plain string."""
    if raw is None:
        return ""
    parts: list[str] = []
    for data, charset in decode_header(raw):
        if isinstance(data, bytes):
            parts.append(data.decode(charset or "utf-8", errors="replace"))
        else:
            parts.append(data)
    return "".join(parts)


def strip_html_to_text(html: str) -> str:
    """Reduce HTML body content to readable plain text."""
    text = _HTML_BLOCK_TAGS.sub("", html)
    text = _HTML_BR.sub("\n", text)
    text = _HTML_BLOCK_END.sub("\n", text)
    text = _HTML_ANY_TAG.sub(" ", text)
    text = unescape(text)
    text = _WHITESPACE_RUN.sub(" ", text)
    text = _BLANK_LINES.sub("\n\n", text)
    return text.strip()


def extract_body(message: Message, max_len: int = 1000) -> str:
    """Return the message body (plain text preferred, HTML fallback) trimmed to ``max_len``."""
    parts = [message] if not message.is_multipart() else list(message.walk())
    for part in parts:
        if part.get_content_type() == "text/plain":
            payload = part.get_payload(decode=True)
            if payload:
                text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
                return text[:max_len] + ("..." if len(text) > max_len else "")
    for part in parts:
        if part.get_content_type() == "text/html":
            payload = part.get_payload(decode=True)
            if payload:
                text = strip_html_to_text(
                    payload.decode(part.get_content_charset() or "utf-8", errors="replace")
                )
                return text[:max_len] + ("..." if len(text) > max_len else "")
    return ""


def parse_message_bytes(
    data: bytes,
    *,
    account: str,
    include_body: bool = False,
) -> dict[str, Any]:
    """Parse raw RFC822 bytes into a dict for the MCP wire."""
    message = email.message_from_bytes(data)
    result: dict[str, Any] = {
        "id": message.get("Message-ID", ""),
        "from": decode_header_value(message.get("From", "")),
        "to": decode_header_value(message.get("To", "")),
        "subject": decode_header_value(message.get("Subject", "")),
        "date": message.get("Date", ""),
        "account": account,
    }
    if include_body:
        result["body"] = extract_body(message)
    else:
        result["snippet"] = extract_body(message, max_len=200)
    return result


def parse_unsubscribe_headers(message: Message) -> dict[str, Any]:
    """Parse ``List-Unsubscribe`` / ``List-Unsubscribe-Post`` from a message."""
    raw = message.get("List-Unsubscribe", "")
    if not raw:
        return {"has_unsubscribe": False}
    result: dict[str, Any] = {
        "has_unsubscribe": True,
        "raw": raw,
        "mailto": None,
        "url": None,
    }
    for part in raw.split(","):
        part = part.strip().strip("<>")
        if part.startswith("mailto:"):
            result["mailto"] = part
        elif part.startswith("http"):
            result["url"] = part
    post_header = message.get("List-Unsubscribe-Post", "")
    if post_header:
        result["one_click"] = True
        result["post_body"] = post_header.strip()
    return result


def sort_key_for_date(date_str: str) -> float:
    """Return a unix-timestamp sort key, or 0.0 for unparseable dates."""
    try:
        return parsedate_to_datetime(date_str).timestamp()
    except (TypeError, ValueError):
        return 0.0


__all__ = [
    "decode_header_value",
    "extract_body",
    "parse_message_bytes",
    "parse_unsubscribe_headers",
    "sort_key_for_date",
    "strip_html_to_text",
]
