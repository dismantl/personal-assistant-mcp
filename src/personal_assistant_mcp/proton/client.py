"""Proton Mail IMAP / SMTP client.

Consolidates the six legacy ``email_*.py`` wrappers into a single async
module. All synchronous IMAP / SMTP work is wrapped with ``asyncio.to_thread``
so the FastMCP event loop is never blocked.

Env vars (loaded by ``ProtonConfig.from_env``):

- ``PROTON_IMAP_HOST`` / ``PROTON_IMAP_PORT``
- ``PROTON_SMTP_HOST`` / ``PROTON_SMTP_PORT``
- ``PROTON_PRIMARY_USER`` / ``PROTON_PRIMARY_PASSWORD``
- ``PROTON_AI_USER`` / ``PROTON_AI_PASSWORD``

Proton Bridge serves IMAP / SMTP locally over TLS with a self-signed
certificate; the SSL context disables hostname / chain verification to match
the upstream wrapper behaviour.
"""

from __future__ import annotations

import asyncio
import email
import imaplib
import ipaddress
import os
import smtplib
import ssl
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.message import Message
from email.mime.text import MIMEText
from typing import Any

from .parse import (
    decode_header_value,
    parse_message_bytes,
    parse_unsubscribe_headers,
    sort_key_for_date,
)

_BRIDGE_LOCAL_HOSTNAMES = frozenset({"localhost", "proton-bridge"})

VALID_ACCOUNTS = ("primary", "ai")
_DEFAULT_FETCH_LIMIT = 50
_UNREAD_WINDOW_DAYS = 7
_RECENT_WINDOW_DAYS = 3


@dataclass(frozen=True)
class ProtonConfig:
    imap_host: str
    imap_port: int
    smtp_host: str
    smtp_port: int
    primary_user: str
    primary_password: str
    ai_user: str
    ai_password: str

    def credentials(self, account: str) -> tuple[str, str]:
        if account == "primary":
            return (self.primary_user, self.primary_password)
        if account == "ai":
            return (self.ai_user, self.ai_password)
        raise ValueError(f"Unknown account {account!r}: expected one of {VALID_ACCOUNTS}")

    @classmethod
    def from_env(cls) -> ProtonConfig:
        config = cls(
            imap_host=_required("PROTON_IMAP_HOST"),
            imap_port=int(_required("PROTON_IMAP_PORT")),
            smtp_host=_required("PROTON_SMTP_HOST"),
            smtp_port=int(_required("PROTON_SMTP_PORT")),
            primary_user=_required("PROTON_PRIMARY_USER"),
            primary_password=_required("PROTON_PRIMARY_PASSWORD"),
            ai_user=_required("PROTON_AI_USER"),
            ai_password=_required("PROTON_AI_PASSWORD"),
        )
        _assert_bridge_local(config.imap_host, "PROTON_IMAP_HOST")
        _assert_bridge_local(config.smtp_host, "PROTON_SMTP_HOST")
        return config


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"Required environment variable {name!r} is not set or empty")
    return value


def _assert_bridge_local(host: str, var_name: str) -> None:
    """Refuse non-local Proton Bridge endpoints.

    Bridge serves IMAP/SMTP locally with a self-signed cert, and ``_bridge_ssl_context``
    disables verification accordingly. If the configured host ever drifts to a
    non-loopback address, the disabled verification silently becomes a real MITM
    exposure on the LAN. Accepts loopback IPs (``127.0.0.0/8``, ``::1``) and the
    conventional hostnames ``localhost`` and ``proton-bridge`` (Docker-network DNS).
    """
    if host in _BRIDGE_LOCAL_HOSTNAMES:
        return
    try:
        ip = ipaddress.ip_address(host)
    except ValueError as exc:
        raise ValueError(
            f"{var_name}={host!r} must be a loopback IP, 'localhost', or 'proton-bridge' "
            "(Proton Bridge uses CERT_NONE TLS; non-local hosts are MITM-exposed)"
        ) from exc
    if not ip.is_loopback:
        raise ValueError(
            f"{var_name}={host!r} must be a loopback IP (e.g. 127.0.0.1 or ::1); "
            "Proton Bridge uses CERT_NONE TLS, so non-loopback hosts are MITM-exposed"
        )


def _quote_imap_astring(value: str) -> str:
    """Return ``value`` properly quoted as an IMAP astring.

    Rejects strings containing CR/LF/NUL — those would allow IMAP protocol
    injection by breaking out of the quoted argument and starting a new
    tagged command on the next line. Backslash and double-quote are escaped
    per RFC 3501.
    """
    if any(c in value for c in ("\r", "\n", "\x00")):
        raise ValueError(
            "IMAP astring must not contain control characters (CR/LF/NUL); "
            "rejecting potential protocol injection attempt"
        )
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _bridge_ssl_context() -> ssl.SSLContext:
    """SSL context matching Proton Bridge's self-signed local cert."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _imap_connect(config: ProtonConfig, account: str) -> imaplib.IMAP4:
    user, password = config.credentials(account)
    client = imaplib.IMAP4(config.imap_host, config.imap_port)
    client.starttls(ssl_context=_bridge_ssl_context())
    client.login(user, password)
    return client


def _find_message_id(client: imaplib.IMAP4, message_id: str) -> bytes | None:
    """Locate a message by ``Message-ID`` header.

    ``message_id`` is quoted via ``_quote_imap_astring`` and passed as a
    separate IMAP search argument, which prevents protocol injection through
    attacker-controlled Message-ID values.
    """
    client.select("INBOX")
    _, data = client.search(None, "HEADER", "Message-ID", _quote_imap_astring(message_id))
    ids = data[0].split()
    return ids[0] if ids else None


def _fetch_message_bytes(client: imaplib.IMAP4, uid: bytes) -> bytes | None:
    _, message_data = client.fetch(uid, "(RFC822)")
    if message_data and message_data[0] is not None:
        return message_data[0][1]
    return None


# -----------------------------------------------------------------------------
# Sync implementations — run via asyncio.to_thread
# -----------------------------------------------------------------------------


def _list_by_criteria_sync(
    config: ProtonConfig,
    account: str,
    criteria: str,
    *,
    limit: int = _DEFAULT_FETCH_LIMIT,
) -> list[dict[str, Any]]:
    client = _imap_connect(config, account)
    try:
        client.select("INBOX")
        _, data = client.search(None, criteria)
        ids = data[0].split()
        if not ids:
            return []
        ids = ids[-limit:][::-1]
        results: list[dict[str, Any]] = []
        for uid in ids:
            raw = _fetch_message_bytes(client, uid)
            if raw is not None:
                results.append(parse_message_bytes(raw, account=account))
        return results
    finally:
        client.logout()


def _list_folders_sync(config: ProtonConfig, account: str) -> dict[str, Any]:
    client = _imap_connect(config, account)
    try:
        _, folders = client.list()
    finally:
        client.logout()
    return {
        "account": account,
        "folders": [
            folder.decode().split('"/"')[-1].strip().strip('"') for folder in folders if folder
        ],
    }


def _read_message_sync(config: ProtonConfig, account: str, message_id: str) -> dict[str, Any]:
    client = _imap_connect(config, account)
    try:
        uid = _find_message_id(client, message_id)
        if uid is None:
            return {"error": f"Message not found: {message_id}", "account": account}
        raw = _fetch_message_bytes(client, uid)
        if raw is None:
            return {"error": f"Message not retrievable: {message_id}", "account": account}
        return parse_message_bytes(raw, account=account, include_body=True)
    finally:
        client.logout()


def _move_message_sync(
    config: ProtonConfig,
    account: str,
    message_id: str,
    *,
    dest_folder: str,
    action: str,
) -> dict[str, Any]:
    client = _imap_connect(config, account)
    try:
        uid = _find_message_id(client, message_id)
        if uid is None:
            return {
                "error": f"Message not found in {account} inbox: {message_id}",
                "account": account,
            }
        result = client.copy(uid, dest_folder)
        if result[0] != "OK":
            return {
                "error": f"Failed to copy to {dest_folder}: {result[1]}",
                "account": account,
            }
        client.store(uid, "+FLAGS", "\\Deleted")
        client.expunge()
        return {
            "success": True,
            "action": action,
            "msg_id": message_id,
            "account": account,
        }
    finally:
        client.logout()


def _send_message_sync(
    config: ProtonConfig, to_addr: str, subject: str, body: str
) -> dict[str, Any]:
    message = MIMEText(body)
    message["From"] = config.ai_user
    message["To"] = to_addr
    message["Subject"] = subject
    with smtplib.SMTP(config.smtp_host, config.smtp_port) as smtp:
        smtp.starttls(context=_bridge_ssl_context())
        smtp.login(config.ai_user, config.ai_password)
        smtp.send_message(message)
    return {
        "success": True,
        "to": to_addr,
        "subject": subject,
        "from": config.ai_user,
        "account": "ai",
    }


def _check_unsubscribe_sync(config: ProtonConfig, message_id: str) -> dict[str, Any]:
    client = _imap_connect(config, "primary")
    try:
        uid = _find_message_id(client, message_id)
        if uid is None:
            return {"error": f"Message not found: {message_id}"}
        raw = _fetch_message_bytes(client, uid)
    finally:
        client.logout()

    if raw is None:
        return {"error": f"Message not retrievable: {message_id}"}
    message: Message = email.message_from_bytes(raw)
    info = parse_unsubscribe_headers(message)
    info["from"] = decode_header_value(message.get("From", ""))
    info["subject"] = decode_header_value(message.get("Subject", ""))
    return info


def _unsubscribe_url_sync(config: ProtonConfig, message_id: str) -> dict[str, Any]:
    info = _check_unsubscribe_sync(config, message_id)
    if "error" in info:
        return info
    if not info.get("url"):
        return {"error": "No URL unsubscribe option", "options": info}
    return {
        "url": info["url"],
        "one_click": info.get("one_click", False),
        "post_body": info.get("post_body"),
    }


# -----------------------------------------------------------------------------
# Async wrappers
# -----------------------------------------------------------------------------


async def list_unread(
    config: ProtonConfig, account: str, *, now: datetime | None = None
) -> list[dict[str, Any]]:
    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(days=_UNREAD_WINDOW_DAYS)).strftime("%d-%b-%Y")
    results = await asyncio.to_thread(
        _list_by_criteria_sync, config, account, f"UNSEEN SINCE {since}"
    )
    results.sort(key=lambda item: sort_key_for_date(item.get("date", "")), reverse=True)
    return results


async def list_recent(
    config: ProtonConfig, account: str, *, now: datetime | None = None
) -> list[dict[str, Any]]:
    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(days=_RECENT_WINDOW_DAYS)).strftime("%d-%b-%Y")
    results = await asyncio.to_thread(_list_by_criteria_sync, config, account, f"SINCE {since}")
    results.sort(key=lambda item: sort_key_for_date(item.get("date", "")), reverse=True)
    return results


async def list_folders(config: ProtonConfig, account: str) -> dict[str, Any]:
    return await asyncio.to_thread(_list_folders_sync, config, account)


async def read_message(config: ProtonConfig, account: str, message_id: str) -> dict[str, Any]:
    return await asyncio.to_thread(_read_message_sync, config, account, message_id)


async def send_message_ai(
    config: ProtonConfig, to_addr: str, subject: str, body: str
) -> dict[str, Any]:
    if not to_addr.strip():
        raise ValueError("to_addr must not be empty")
    if not subject.strip():
        raise ValueError("subject must not be empty")
    if not body.strip():
        raise ValueError("body must not be empty")
    return await asyncio.to_thread(_send_message_sync, config, to_addr, subject, body)


async def archive_message_ai(config: ProtonConfig, message_id: str) -> dict[str, Any]:
    return await asyncio.to_thread(
        _move_message_sync,
        config,
        "ai",
        message_id,
        dest_folder="Archive",
        action="archive",
    )


async def delete_message_ai(config: ProtonConfig, message_id: str) -> dict[str, Any]:
    return await asyncio.to_thread(
        _move_message_sync,
        config,
        "ai",
        message_id,
        dest_folder="Trash",
        action="delete",
    )


async def check_unsubscribe(config: ProtonConfig, message_id: str) -> dict[str, Any]:
    return await asyncio.to_thread(_check_unsubscribe_sync, config, message_id)


async def unsubscribe_url(config: ProtonConfig, message_id: str) -> dict[str, Any]:
    return await asyncio.to_thread(_unsubscribe_url_sync, config, message_id)


__all__ = [
    "VALID_ACCOUNTS",
    "ProtonConfig",
    "archive_message_ai",
    "check_unsubscribe",
    "delete_message_ai",
    "list_folders",
    "list_recent",
    "list_unread",
    "read_message",
    "send_message_ai",
    "unsubscribe_url",
]
