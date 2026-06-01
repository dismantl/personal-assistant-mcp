"""Smoke tests for the FastMCP server bootstrap, health tool, and tool registry."""

import os
import subprocess
import sys

import pytest
from starlette.testclient import TestClient

from personal_assistant_mcp import server as server_module
from personal_assistant_mcp.daily import note as daily_note
from personal_assistant_mcp.daily.note import DAILY_TEMPLATE_PATH
from personal_assistant_mcp.server import health, mcp
from tests.conftest import FakeVaultClient

_TODAY_PATH = "0 Logs/2026-05-11.md"
_TEMPLATE_BODY = "## Priorities\n\n\n## Schedule\n\n\n## Inbox\n\n\n## Reflection\n\n\n## Log\n"


def _inbox_client(monkeypatch: pytest.MonkeyPatch, fake_vault: FakeVaultClient) -> TestClient:
    monkeypatch.setattr(server_module, "_api_key", "test-key", raising=False)
    monkeypatch.setattr(server_module, "_vault", fake_vault)
    monkeypatch.setattr(daily_note, "today_in_vault_tz", lambda: daily_note.date(2026, 5, 11))
    return TestClient(server_module.mcp.streamable_http_app())


def test_mcp_server_name() -> None:
    assert mcp.name == "personal-assistant"


@pytest.mark.asyncio
async def test_health_returns_ok() -> None:
    result = await health()
    assert result["status"] == "ok"
    assert result["service"] == "personal-assistant-mcp"
    assert "transport" in result
    assert result["legacy_email_tools_enabled"] is False


@pytest.mark.asyncio
async def test_default_tools_registered_without_legacy_email() -> None:
    """Core tools register by default, while legacy email tools stay hidden."""
    tools = await mcp.list_tools()
    names = {t.name for t in tools}
    expected = {
        "health",
        "tasks_list",
        "tasks_search",
        "tasks_add",
        "tasks_complete",
        "tasks_uncomplete",
        "tasks_update",
        "tasks_delete",
        "tasks_move",
        "tasks_render_planner",
        "daily_create_today",
        "daily_template",
        "daily_read_today",
        "daily_read",
        "daily_read_recent",
        "daily_write_today",
        "daily_append_log",
        "daily_append_inbox",
        "daily_archive_old",
        "weekly_latest",
        "weekly_read",
        "weekly_write_current",
        "digest_read",
        "digest_write",
        "freshrss_unread",
        "freshrss_contents",
        "calendar_list",
        "calendar_today",
        "calendar_week",
        "calendar_create_event",
        "calendar_update_event",
        "calendar_delete_event",
        "release_state_read",
        "release_state_update",
    }
    assert expected.issubset(names), f"Missing tools: {expected - names}"
    assert not {name for name in names if name.startswith("email_")}


def test_legacy_email_tools_can_be_enabled() -> None:
    """Operators can opt in to the old Proton tools for compatibility."""
    code = """
import asyncio
from personal_assistant_mcp.server import mcp

async def main():
    tools = await mcp.list_tools()
    names = {tool.name for tool in tools}
    assert "health" in names
    assert "tasks_list" in names
    assert "email_primary_unread" in names
    assert "email_ai_send" in names

asyncio.run(main())
"""
    env = os.environ.copy()
    env["ENABLE_LEGACY_EMAIL_TOOLS"] = "true"

    result = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("env_value", ["", "1", "yes", "enabled", "tru", "TRUE", " true "])
def test_legacy_email_tools_ignore_non_true_values(env_value: str) -> None:
    """Only the literal true opt-in exposes the old Proton tools."""
    code = """
import asyncio
from personal_assistant_mcp.server import mcp

async def main():
    tools = await mcp.list_tools()
    names = {tool.name for tool in tools}
    assert "health" in names
    assert "tasks_list" in names
    assert "email_primary_unread" not in names
    assert "email_ai_send" not in names

asyncio.run(main())
"""
    env = os.environ.copy()
    env["ENABLE_LEGACY_EMAIL_TOOLS"] = env_value

    result = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr


def test_inbox_route_accepts_text_plain(
    monkeypatch: pytest.MonkeyPatch,
    fake_vault: FakeVaultClient,
) -> None:
    fake_vault.notes[DAILY_TEMPLATE_PATH] = _TEMPLATE_BODY

    client = _inbox_client(monkeypatch, fake_vault)
    response = client.post(
        "/inbox",
        content="buy milk",
        headers={"Authorization": "Bearer test-key", "Content-Type": "text/plain"},
    )

    assert response.status_code == 200
    assert response.json()["body"] == "buy milk"
    assert "## Inbox\n- [ ] buy milk\n\n\n## Reflection" in fake_vault.notes[_TODAY_PATH]


def test_inbox_route_accepts_json_metadata(
    monkeypatch: pytest.MonkeyPatch,
    fake_vault: FakeVaultClient,
) -> None:
    fake_vault.notes[DAILY_TEMPLATE_PATH] = _TEMPLATE_BODY

    client = _inbox_client(monkeypatch, fake_vault)
    response = client.post(
        "/inbox",
        json={"text": "file taxes", "priority": "high", "due": "2026-06-15"},
        headers={"Authorization": "Bearer test-key"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["body"] == "file taxes"
    assert payload["priority_bucket"] == "high"
    assert payload["due"] == "2026-06-15"
    assert "- [ ] file taxes" in fake_vault.notes[_TODAY_PATH]
    assert "2026-06-15" in fake_vault.notes[_TODAY_PATH]


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer wrong"},
        {"Authorization": "Basic dGVzdC1rZXk="},
    ],
)
def test_inbox_route_rejects_missing_or_bad_token(
    monkeypatch: pytest.MonkeyPatch,
    fake_vault: FakeVaultClient,
    headers: dict[str, str],
) -> None:
    fake_vault.notes[DAILY_TEMPLATE_PATH] = _TEMPLATE_BODY

    client = _inbox_client(monkeypatch, fake_vault)
    response = client.post("/inbox", content="buy milk", headers=headers)

    assert response.status_code == 401
    assert _TODAY_PATH not in fake_vault.notes


def test_inbox_route_rejects_empty_body(
    monkeypatch: pytest.MonkeyPatch,
    fake_vault: FakeVaultClient,
) -> None:
    fake_vault.notes[DAILY_TEMPLATE_PATH] = _TEMPLATE_BODY

    client = _inbox_client(monkeypatch, fake_vault)
    response = client.post(
        "/inbox",
        content="   ",
        headers={"Authorization": "Bearer test-key", "Content-Type": "text/plain"},
    )

    assert response.status_code == 400
    assert _TODAY_PATH not in fake_vault.notes
