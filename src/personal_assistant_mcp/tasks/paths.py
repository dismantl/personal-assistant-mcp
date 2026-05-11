"""Vault-path normalization.

Agents pass paths in many shapes — leading slashes, backslashes, percent
encoding, ``vault://`` prefixes, the literal string ``today``. The mutation
tools and content-hash identity both depend on a single canonical form, so
every path argument flows through ``normalize_vault_path`` before any I/O
or hashing happens.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from urllib.parse import unquote
from zoneinfo import ZoneInfo

VAULT_TIMEZONE = ZoneInfo("America/New_York")
DAILY_NOTES_DIR = "0 Logs"


def today_in_vault_tz() -> date:
    """Current date in the vault's canonical timezone (America/New_York)."""
    return datetime.now(VAULT_TIMEZONE).date()


def normalize_vault_path(raw: str, *, today: date | None = None) -> str:
    """Canonicalize an agent-supplied path to a vault-relative form.

    Rules applied in order:

    - Trim whitespace; reject empty input.
    - URL-decode percent-encoded characters (``%20`` -> space, etc.).
    - Convert backslashes to forward slashes.
    - Strip leading ``vault://``, ``./``, and ``/`` prefixes.
    - Collapse repeated slashes, strip trailing slashes.
    - Reject any ``..`` path component (traversal).
    - Resolve ``today``, ``today.md``, ``<DAILY_NOTES_DIR>/today``,
      ``<DAILY_NOTES_DIR>/today.md`` to today's daily note
      (``<DAILY_NOTES_DIR>/YYYY-MM-DD.md``) using ``today`` arg or
      ``today_in_vault_tz()``.

    Raises ``ValueError`` on empty input, empty result, or path traversal.
    """
    if raw is None or not raw.strip():
        raise ValueError("Empty path")

    p = unquote(raw.strip()).replace("\\", "/")

    if p.startswith("vault://"):
        p = p[len("vault://") :]

    while p.startswith("./") or p.startswith("/"):
        p = p[2:] if p.startswith("./") else p[1:]

    p = re.sub(r"/+", "/", p).rstrip("/")

    if any(part == ".." for part in p.split("/")):
        raise ValueError(f"Path traversal not allowed: {raw!r}")

    if not p:
        raise ValueError(f"Empty path after normalization: {raw!r}")

    today_date = today or today_in_vault_tz()
    today_iso = today_date.isoformat()
    daily_today_forms = {
        "today",
        "today.md",
        f"{DAILY_NOTES_DIR}/today",
        f"{DAILY_NOTES_DIR}/today.md",
    }
    if p in daily_today_forms:
        return f"{DAILY_NOTES_DIR}/{today_iso}.md"

    return p
