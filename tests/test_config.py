"""Unit tests for ``Settings`` environment loading and direct construction."""

from __future__ import annotations

import dataclasses

import pytest

from personal_assistant_mcp.config import Settings


def _set_required_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COUCHDB_URL", "http://couchdb:5984")
    monkeypatch.setenv("COUCHDB_USER", "obsidian")
    monkeypatch.setenv("COUCHDB_PASSWORD", "secret")


def test_from_env_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required_env(monkeypatch)
    monkeypatch.setenv("COUCHDB_DB", "my-vault")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("OBSIDIAN_OBFUSCATE_PASSPHRASE", "phrase")

    settings = Settings.from_env()
    assert settings.couchdb_url == "http://couchdb:5984"
    assert settings.couchdb_user == "obsidian"
    assert settings.couchdb_password == "secret"
    assert settings.couchdb_db == "my-vault"
    assert settings.log_level == "DEBUG"
    assert settings.obfuscate_passphrase == "phrase"


def test_from_env_defaults_when_optionals_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required_env(monkeypatch)
    monkeypatch.delenv("COUCHDB_DB", raising=False)
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    monkeypatch.delenv("OBSIDIAN_OBFUSCATE_PASSPHRASE", raising=False)

    settings = Settings.from_env()
    assert settings.couchdb_db == "obsidian"
    assert settings.log_level == "INFO"
    assert settings.obfuscate_passphrase is None


@pytest.mark.parametrize("missing", ["COUCHDB_URL", "COUCHDB_USER", "COUCHDB_PASSWORD"])
def test_from_env_raises_when_required_missing(
    monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    _set_required_env(monkeypatch)
    monkeypatch.delenv(missing, raising=False)

    with pytest.raises(ValueError, match=missing):
        Settings.from_env()


def test_from_env_treats_empty_string_as_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required_env(monkeypatch)
    monkeypatch.setenv("COUCHDB_URL", "   ")

    with pytest.raises(ValueError, match="COUCHDB_URL"):
        Settings.from_env()


def test_construct_directly_uses_defaults() -> None:
    settings = Settings(
        couchdb_url="http://localhost:5984",
        couchdb_user="user",
        couchdb_password="pass",
    )
    assert settings.couchdb_db == "obsidian"
    assert settings.log_level == "INFO"
    assert settings.obfuscate_passphrase is None


def test_settings_is_immutable() -> None:
    settings = Settings(
        couchdb_url="http://localhost:5984",
        couchdb_user="user",
        couchdb_password="pass",
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        settings.couchdb_url = "http://elsewhere"  # type: ignore[misc]
