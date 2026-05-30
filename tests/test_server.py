"""Smoke tests for the FastMCP server bootstrap, health tool, and tool registry."""

import os
import subprocess
import sys

import pytest

from personal_assistant_mcp.server import health, mcp


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
