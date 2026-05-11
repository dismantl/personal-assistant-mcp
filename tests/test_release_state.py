"""Unit tests for release-tracker state persistence."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from personal_assistant_mcp.release.state import (
    STATE_PATH,
    migrate_from_local_file,
    read_state,
    update_state,
    validate_entries,
)
from tests.conftest import FakeVaultClient

# -----------------------------------------------------------------------------
# validate_entries
# -----------------------------------------------------------------------------


def test_validate_entries_accepts_well_formed() -> None:
    validate_entries(
        {
            "k1": {"canonical_project_key": "k1", "version": "1.0"},
            "k2": {"canonical_project_key": "k2"},
        }
    )


def test_validate_entries_rejects_non_dict() -> None:
    with pytest.raises(ValueError, match="must be a JSON object"):
        validate_entries(["list", "not", "dict"])


def test_validate_entries_rejects_non_dict_value() -> None:
    with pytest.raises(ValueError, match="must be a dict"):
        validate_entries({"k": "not a dict"})


def test_validate_entries_rejects_missing_canonical_key() -> None:
    with pytest.raises(ValueError, match="canonical_project_key"):
        validate_entries({"k": {"version": "1.0"}})


# -----------------------------------------------------------------------------
# read_state
# -----------------------------------------------------------------------------


async def test_read_state_returns_empty_when_missing(
    fake_vault: FakeVaultClient,
) -> None:
    assert await read_state(fake_vault) == {}


async def test_read_state_parses_existing_json(fake_vault: FakeVaultClient) -> None:
    fake_vault.notes[STATE_PATH] = json.dumps(
        {"foo": {"canonical_project_key": "foo", "version": "1.0"}}
    )
    state = await read_state(fake_vault)
    assert state == {"foo": {"canonical_project_key": "foo", "version": "1.0"}}


async def test_read_state_raises_on_corrupt_json(fake_vault: FakeVaultClient) -> None:
    fake_vault.notes[STATE_PATH] = "{ not valid json"
    with pytest.raises(ValueError, match="Corrupt release state"):
        await read_state(fake_vault)


# -----------------------------------------------------------------------------
# update_state
# -----------------------------------------------------------------------------


async def test_update_state_creates_file_when_missing(
    fake_vault: FakeVaultClient,
) -> None:
    entries = {"foo": {"canonical_project_key": "foo", "version": "1.0", "url": "https://x"}}
    new_state = await update_state(fake_vault, entries)
    assert new_state == entries
    persisted = json.loads(fake_vault.notes[STATE_PATH])
    assert persisted == entries


async def test_update_state_merges_into_existing(
    fake_vault: FakeVaultClient,
) -> None:
    fake_vault.notes[STATE_PATH] = json.dumps(
        {
            "foo": {
                "canonical_project_key": "foo",
                "version": "1.0",
                "url": "https://old",
            }
        }
    )
    entries = {"foo": {"canonical_project_key": "foo", "version": "2.0"}}
    new_state = await update_state(fake_vault, entries)
    # version updated, url preserved
    assert new_state["foo"]["version"] == "2.0"
    assert new_state["foo"]["url"] == "https://old"


async def test_update_state_adds_new_keys(fake_vault: FakeVaultClient) -> None:
    fake_vault.notes[STATE_PATH] = json.dumps(
        {"foo": {"canonical_project_key": "foo", "version": "1.0"}}
    )
    entries = {"bar": {"canonical_project_key": "bar", "version": "0.1"}}
    new_state = await update_state(fake_vault, entries)
    assert set(new_state) == {"foo", "bar"}


async def test_update_state_validates_input(fake_vault: FakeVaultClient) -> None:
    with pytest.raises(ValueError):
        await update_state(fake_vault, {"k": {"version": "1.0"}})  # missing canonical key


async def test_update_state_writes_pretty_json_with_trailing_newline(
    fake_vault: FakeVaultClient,
) -> None:
    await update_state(
        fake_vault,
        {"foo": {"canonical_project_key": "foo", "version": "1.0"}},
    )
    raw = fake_vault.notes[STATE_PATH]
    assert raw.endswith("\n")
    # indent=2 produces newlines between fields
    assert "\n" in raw.rstrip("\n")


# -----------------------------------------------------------------------------
# migrate_from_local_file
# -----------------------------------------------------------------------------


async def test_migrate_from_local_file_copies_data(
    fake_vault: FakeVaultClient, tmp_path: Path
) -> None:
    source = tmp_path / "release-state.json"
    source.write_text(json.dumps({"foo": {"canonical_project_key": "foo", "version": "1.0"}}))
    migrated = await migrate_from_local_file(fake_vault, source)
    assert migrated["foo"]["version"] == "1.0"
    assert STATE_PATH in fake_vault.notes
    persisted = json.loads(fake_vault.notes[STATE_PATH])
    assert persisted == migrated


async def test_migrate_refuses_when_vault_state_exists(
    fake_vault: FakeVaultClient, tmp_path: Path
) -> None:
    fake_vault.notes[STATE_PATH] = json.dumps({"foo": {"canonical_project_key": "foo"}})
    source = tmp_path / "release-state.json"
    source.write_text(json.dumps({"bar": {"canonical_project_key": "bar"}}))
    with pytest.raises(RuntimeError, match="refusing to overwrite"):
        await migrate_from_local_file(fake_vault, source)


async def test_migrate_refuses_when_empty_state_file_exists(
    fake_vault: FakeVaultClient, tmp_path: Path
) -> None:
    """An empty-dict ({}) state file still represents a prior migration."""
    fake_vault.notes[STATE_PATH] = "{}"
    source = tmp_path / "release-state.json"
    source.write_text(json.dumps({"foo": {"canonical_project_key": "foo"}}))
    with pytest.raises(RuntimeError, match="refusing to overwrite"):
        await migrate_from_local_file(fake_vault, source)
    # The empty-state file must not have been overwritten
    assert fake_vault.notes[STATE_PATH] == "{}"


async def test_migrate_rejects_missing_source_file(
    fake_vault: FakeVaultClient, tmp_path: Path
) -> None:
    with pytest.raises(FileNotFoundError):
        await migrate_from_local_file(fake_vault, tmp_path / "ghost.json")


async def test_migrate_rejects_invalid_json(fake_vault: FakeVaultClient, tmp_path: Path) -> None:
    source = tmp_path / "release-state.json"
    source.write_text("{ not valid")
    with pytest.raises(ValueError, match="Invalid JSON"):
        await migrate_from_local_file(fake_vault, source)


async def test_migrate_validates_entries(fake_vault: FakeVaultClient, tmp_path: Path) -> None:
    source = tmp_path / "release-state.json"
    source.write_text(json.dumps({"k": {"missing_canonical_key": "yes"}}))
    with pytest.raises(ValueError, match="canonical_project_key"):
        await migrate_from_local_file(fake_vault, source)
