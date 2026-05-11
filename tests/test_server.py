"""Phase 1 smoke tests for the FastMCP server bootstrap and health tool."""

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
