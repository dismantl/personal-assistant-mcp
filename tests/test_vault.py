"""Unit tests for the vault-client wiring."""

from __future__ import annotations

from obsidian_livesync_mcp.client import ObsidianVaultClient

from personal_assistant_mcp.config import Settings
from personal_assistant_mcp.vault import build_vault_client, build_vault_config


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "couchdb_url": "http://couchdb:5984",
        "couchdb_user": "obsidian",
        "couchdb_password": "secret",
        "couchdb_db": "my-vault",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def test_build_vault_config_maps_settings() -> None:
    config = build_vault_config(_settings(obfuscate_passphrase="phrase"))
    assert config.couch_url == "http://couchdb:5984"
    assert config.couch_user == "obsidian"
    assert config.couch_pass == "secret"
    assert config.db_name == "my-vault"
    assert config.obfuscate_passphrase == "phrase"


def test_build_vault_config_passes_none_obfuscation_passphrase() -> None:
    config = build_vault_config(_settings())
    assert config.obfuscate_passphrase is None


def test_build_vault_client_returns_configured_client() -> None:
    client = build_vault_client(_settings())
    assert isinstance(client, ObsidianVaultClient)
    assert client.config.couch_url == "http://couchdb:5984"
    assert client.config.db_name == "my-vault"


def test_build_vault_client_db_url_property() -> None:
    """The upstream ``Config.db_url`` property concatenates couch_url and db_name."""
    client = build_vault_client(_settings(couchdb_db="custom-db"))
    assert client.config.db_url == "http://couchdb:5984/custom-db"
