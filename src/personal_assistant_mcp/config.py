"""Environment-driven settings for personal-assistant-mcp.

Construct ``Settings`` directly with explicit values in tests; call
``Settings.from_env()`` for production wiring. Required CouchDB credentials
are validated by ``from_env``; ``Settings`` itself permits any values so
callers can keep partial configurations in flight.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    """All runtime configuration for the server."""

    couchdb_url: str
    couchdb_user: str
    couchdb_password: str
    couchdb_db: str = "obsidian"

    obfuscate_passphrase: str | None = None

    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> Settings:
        """Build settings from process environment.

        Raises ``ValueError`` if any required CouchDB env var is unset or empty.
        """
        return cls(
            couchdb_url=_required_env("COUCHDB_URL"),
            couchdb_user=_required_env("COUCHDB_USER"),
            couchdb_password=_required_env("COUCHDB_PASSWORD"),
            couchdb_db=os.environ.get("COUCHDB_DB", "obsidian") or "obsidian",
            obfuscate_passphrase=os.environ.get("OBSIDIAN_OBFUSCATE_PASSPHRASE") or None,
            log_level=os.environ.get("LOG_LEVEL", "INFO") or "INFO",
        )


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"Required environment variable {name!r} is not set or empty")
    return value
