"""Tests for MCP tool error surfacing."""

from __future__ import annotations

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from personal_assistant_mcp.tool_errors import surface_tool_errors


@pytest.mark.asyncio
async def test_surface_tool_errors_redacts_sensitive_details(
    caplog: pytest.LogCaptureFixture,
) -> None:
    @surface_tool_errors("freshrss_unread")
    async def failing_tool() -> None:
        raise RuntimeError(
            "GET https://rss.example.test/api/greader.php/accounts/ClientLogin"
            "?Email=dan@example.test&Passwd=hunter2&service=reader "
            "Authorization: Bearer abc123 api_key=secret-token password=letmein"
        )

    with caplog.at_level("ERROR", logger=__name__):
        with pytest.raises(ToolError) as excinfo:
            await failing_tool()

    error_text = str(excinfo.value)
    assert "Passwd=[redacted]" in error_text
    assert "Authorization: Bearer [redacted]" in caplog.text
    assert "Traceback (most recent call last):" in caplog.text
    for secret in ("hunter2", "abc123", "secret-token", "letmein"):
        assert secret not in error_text
        assert secret not in caplog.text
