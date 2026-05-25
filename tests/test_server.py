"""Smoke tests for the FastMCP server bootstrap, health tool, and tool registry."""

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


@pytest.mark.asyncio
async def test_task_tools_registered() -> None:
    """The task CRUD tools are attached to the FastMCP server at import time."""
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
        "email_primary_unread",
        "email_primary_recent",
        "email_primary_folders",
        "email_primary_read",
        "email_ai_unread",
        "email_ai_recent",
        "email_ai_folders",
        "email_ai_read",
        "email_ai_send",
        "email_ai_archive",
        "email_ai_delete",
        "email_unsubscribe_check",
        "email_unsubscribe_url",
    }
    assert expected.issubset(names), f"Missing tools: {expected - names}"
