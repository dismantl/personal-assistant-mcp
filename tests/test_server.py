"""Smoke tests for the FastMCP server bootstrap, health tool, and tool registry."""

import os
import subprocess
import sys
from typing import Any, cast

import pytest
from mcp.server.fastmcp.exceptions import ToolError
from starlette.testclient import TestClient

from personal_assistant_mcp import server as server_module
from personal_assistant_mcp.daily import note as daily_note
from personal_assistant_mcp.daily.note import DAILY_TEMPLATE_PATH
from personal_assistant_mcp.server import health, mcp
from personal_assistant_mcp.tasks.cache import CACHE_PATH
from tests.conftest import FakeVaultClient

_TODAY_PATH = "0 Logs/2026-05-11.md"
_TEMPLATE_BODY = "## Priorities\n\n\n## Schedule\n\n\n## Inbox\n\n\n## Reflection\n\n\n## Log\n"


def _planner_spec_fm() -> dict:
    return {
        "type": "todo-planner-spec",
        "version": 1,
        "sourceSelection": {
            "include": {
                "roots": ["0 Logs"],
                "basenamesCaseInsensitive": [],
            },
            "exclude": {
                "pathsContaining": [],
                "tags": [],
            },
        },
        "priorities": {
            "buckets": {
                "high": ["⏫"],
                "medium": ["\U0001f53c"],
                "low": ["\U0001f53d"],
            }
        },
        "tasks": {"includeStatuses": [" ", "/"]},
        "sections": [
            {
                "kind": "static",
                "id": "all",
                "title": "All",
                "taskMatch": {},
            }
        ],
    }


class BlankWriteFailureVault(FakeVaultClient):
    async def write_note(self, path: str, content: str) -> bool:
        await super().write_note(path, content)
        raise RuntimeError()


class ClosingVault(FakeVaultClient):
    def __init__(self) -> None:
        super().__init__()
        self.closed = False

    async def close(self) -> None:
        self.closed = True


def _inbox_client(monkeypatch: pytest.MonkeyPatch, fake_vault: FakeVaultClient) -> TestClient:
    monkeypatch.setattr(server_module, "_api_key", "test-key", raising=False)
    monkeypatch.setattr(server_module, "_vault", fake_vault)
    monkeypatch.setattr(daily_note, "today_in_vault_tz", lambda: daily_note.date(2026, 5, 11))
    return TestClient(server_module.mcp.streamable_http_app())


async def _call_tool_payload(name: str, args: dict[str, Any]) -> dict[str, Any]:
    _, payload = await mcp.call_tool(name, args)
    assert isinstance(payload, dict)
    return cast(dict[str, Any], payload)


def test_mcp_server_name() -> None:
    assert mcp.name == "personal-assistant"


@pytest.mark.asyncio
async def test_health_returns_ok() -> None:
    result = await health()
    assert result["status"] == "ok"
    assert result["service"] == "personal-assistant-mcp"
    assert "transport" in result
    assert "legacy_email_tools_enabled" not in result


@pytest.mark.asyncio
async def test_lifespan_uses_isolated_vault_clients(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clients: list[ClosingVault] = []

    def build_client(_settings: object) -> ClosingVault:
        client = ClosingVault()
        clients.append(client)
        return client

    monkeypatch.setattr(server_module, "build_vault_client", build_client)
    monkeypatch.setattr(server_module.Settings, "from_env", lambda: object())
    monkeypatch.setattr(server_module, "_vault", None, raising=False)

    async with server_module._lifespan(server_module.mcp):
        outer = server_module._get_vault()

        async with server_module._lifespan(server_module.mcp):
            inner = server_module._get_vault()

            assert inner is not outer
            assert not outer.closed

        assert inner.closed
        assert not outer.closed
        assert server_module._get_vault() is outer

    assert outer.closed
    assert clients == [outer, inner]


@pytest.mark.asyncio
async def test_default_tools_registered_without_legacy_email() -> None:
    """Core tools register by default, with no email tools exposed."""
    tools = await mcp.list_tools()
    names = {t.name for t in tools}
    expected = {
        "health",
        "tasks_list",
        "tasks_search",
        "tasks_compute",
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
        "calendar_rsvp",
        "release_state_read",
        "release_state_update",
    }
    assert expected.issubset(names), f"Missing tools: {expected - names}"
    assert not {name for name in names if name.startswith("email_")}


@pytest.mark.asyncio
async def test_task_cache_smoke_via_mcp_call_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_vault = FakeVaultClient()
    fake_vault.frontmatters["TODO.md"] = _planner_spec_fm()
    fake_vault.notes["0 Logs/cache-smoke.md"] = "- [ ] via mcp\n"
    monkeypatch.setattr(server_module, "_vault", fake_vault)

    computed = await _call_tool_payload("tasks_compute", {})
    listed = await _call_tool_payload("tasks_list", {})

    assert computed["task_count"] == 1
    assert listed["source"] == "cache"
    assert listed["computed_at"] == computed["computed_at"]
    assert [task["body"] for task in listed["tasks"]] == ["via mcp"]

    await mcp.call_tool("tasks_add", {"text": "patched", "file_path": "0 Logs/cache-smoke.md"})
    after_add = await _call_tool_payload("tasks_list", {})
    assert [task["body"] for task in after_add["tasks"]] == ["via mcp", "patched"]

    fake_vault.notes[CACHE_PATH] = "{corrupt"
    fallback = await _call_tool_payload("tasks_list", {})
    assert fallback["source"] == "live"
    assert [task["body"] for task in fallback["tasks"]] == ["via mcp", "patched"]


@pytest.mark.asyncio
async def test_tool_errors_include_exception_type_when_exception_message_is_empty(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake_vault = BlankWriteFailureVault()
    monkeypatch.setattr(server_module, "_vault", fake_vault)

    with caplog.at_level("ERROR", logger="personal_assistant_mcp.tasks.tools"):
        with pytest.raises(ToolError) as excinfo:
            await mcp.call_tool("tasks_add", {"text": "buy milk", "file_path": "x.md"})

    assert "tasks_add failed: RuntimeError" in str(excinfo.value)
    assert "MCP tool tasks_add failed" in caplog.text


def test_removed_email_tools_stay_absent_when_old_flag_is_set() -> None:
    """The retired email opt-in flag must not expose email tools."""
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
    env["ENABLE_LEGACY_EMAIL_TOOLS"] = "true"

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


def test_inbox_route_rejects_json_metadata_newlines(
    monkeypatch: pytest.MonkeyPatch,
    fake_vault: FakeVaultClient,
) -> None:
    fake_vault.notes[DAILY_TEMPLATE_PATH] = _TEMPLATE_BODY

    client = _inbox_client(monkeypatch, fake_vault)
    response = client.post(
        "/inbox",
        json={"text": "file taxes", "recurrence": "every week\n- [ ] injected"},
        headers={"Authorization": "Bearer test-key"},
    )

    assert response.status_code == 400
    assert "recurrence" in response.json()["error"]
    assert _TODAY_PATH not in fake_vault.notes


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
