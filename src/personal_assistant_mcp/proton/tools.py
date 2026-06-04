"""MCP tool decorators for Proton mail operations."""

from __future__ import annotations

from typing import Any

from ..tool_errors import surface_tool_errors
from . import client as proton


def _config() -> proton.ProtonConfig:
    return proton.ProtonConfig.from_env()


def register(mcp: Any) -> None:
    """Attach Proton mail tools to the FastMCP server."""

    @mcp.tool()
    @surface_tool_errors("email_primary_unread")
    async def email_primary_unread() -> dict[str, Any]:
        """List unread messages in the primary inbox (7-day window)."""
        return {"messages": await proton.list_unread(_config(), "primary")}

    @mcp.tool()
    @surface_tool_errors("email_primary_recent")
    async def email_primary_recent() -> dict[str, Any]:
        """List recent messages in the primary inbox (3-day window)."""
        return {"messages": await proton.list_recent(_config(), "primary")}

    @mcp.tool()
    @surface_tool_errors("email_primary_folders")
    async def email_primary_folders() -> dict[str, Any]:
        """List IMAP folders for the primary account."""
        return await proton.list_folders(_config(), "primary")

    @mcp.tool()
    @surface_tool_errors("email_primary_read")
    async def email_primary_read(message_id: str) -> dict[str, Any]:
        """Read a primary-mailbox message by Message-ID (includes body)."""
        return await proton.read_message(_config(), "primary", message_id)

    @mcp.tool()
    @surface_tool_errors("email_ai_unread")
    async def email_ai_unread() -> dict[str, Any]:
        """List unread messages in the AI inbox (7-day window)."""
        return {"messages": await proton.list_unread(_config(), "ai")}

    @mcp.tool()
    @surface_tool_errors("email_ai_recent")
    async def email_ai_recent() -> dict[str, Any]:
        """List recent messages in the AI inbox (3-day window)."""
        return {"messages": await proton.list_recent(_config(), "ai")}

    @mcp.tool()
    @surface_tool_errors("email_ai_folders")
    async def email_ai_folders() -> dict[str, Any]:
        """List IMAP folders for the AI account."""
        return await proton.list_folders(_config(), "ai")

    @mcp.tool()
    @surface_tool_errors("email_ai_read")
    async def email_ai_read(message_id: str) -> dict[str, Any]:
        """Read an AI-mailbox message by Message-ID (includes body)."""
        return await proton.read_message(_config(), "ai", message_id)

    @mcp.tool()
    @surface_tool_errors("email_ai_send")
    async def email_ai_send(to: str, subject: str, body: str) -> dict[str, Any]:
        """Send an email from the AI mailbox via Proton Bridge SMTP."""
        return await proton.send_message_ai(_config(), to, subject, body)

    @mcp.tool()
    @surface_tool_errors("email_ai_archive")
    async def email_ai_archive(message_id: str) -> dict[str, Any]:
        """Move an AI-mailbox message to the Archive folder."""
        return await proton.archive_message_ai(_config(), message_id)

    @mcp.tool()
    @surface_tool_errors("email_ai_delete")
    async def email_ai_delete(message_id: str) -> dict[str, Any]:
        """Move an AI-mailbox message to the Trash folder."""
        return await proton.delete_message_ai(_config(), message_id)

    @mcp.tool()
    @surface_tool_errors("email_unsubscribe_check")
    async def email_unsubscribe_check(message_id: str) -> dict[str, Any]:
        """Inspect ``List-Unsubscribe`` headers on a primary-mailbox message."""
        return await proton.check_unsubscribe(_config(), message_id)

    @mcp.tool()
    @surface_tool_errors("email_unsubscribe_url")
    async def email_unsubscribe_url(message_id: str) -> dict[str, Any]:
        """Return the HTTP unsubscribe URL from a primary-mailbox message, if present."""
        return await proton.unsubscribe_url(_config(), message_id)


__all__ = ["register"]
