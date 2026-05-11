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
    }
    assert expected.issubset(names), f"Missing tools: {expected - names}"
