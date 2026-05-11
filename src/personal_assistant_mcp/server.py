"""FastMCP server for personal-assistant tasks.

Phase 1 scaffold: only the `health` tool is registered. Business logic lands
in subsequent phases. Transport mirrors obsidian-livesync-mcp: stdio by default,
streamable-http with optional static Bearer auth when MCP_TRANSPORT is set.
"""

import logging
import os

from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)

_transport = os.environ.get("MCP_TRANSPORT", "stdio")
_server_kwargs: dict = {}

if _transport == "streamable-http":
    _server_kwargs["host"] = os.environ.get("MCP_HOST", "0.0.0.0")
    _server_kwargs["port"] = int(os.environ.get("MCP_PORT", "8080"))
    _server_kwargs["stateless_http"] = True
    _server_kwargs["json_response"] = True

    _api_key = os.environ.get("MCP_API_KEY", "")
    _port = int(os.environ.get("MCP_PORT", "8080"))
    _resource_url = os.environ.get("MCP_RESOURCE_URL", f"http://localhost:{_port}")

    if _api_key:
        from mcp.server.auth.provider import AccessToken, TokenVerifier
        from mcp.server.auth.settings import AuthSettings
        from pydantic import AnyHttpUrl

        class _APIKeyVerifier(TokenVerifier):
            """Verify Bearer tokens against MCP_API_KEY env var."""

            async def verify_token(self, token: str) -> AccessToken | None:
                if token != _api_key:
                    return None
                return AccessToken(
                    token=token,
                    client_id="api-key",
                    scopes=[],
                    expires_at=None,
                )

        _server_kwargs["token_verifier"] = _APIKeyVerifier()
        _server_kwargs["auth"] = AuthSettings(
            issuer_url=AnyHttpUrl(_resource_url),
            resource_server_url=AnyHttpUrl(_resource_url),
            required_scopes=[],
        )

mcp = FastMCP("personal-assistant", **_server_kwargs)


@mcp.tool()
async def health() -> dict:
    """Return server health status. Useful as a deployment smoke test."""
    return {
        "status": "ok",
        "service": "personal-assistant-mcp",
        "transport": _transport,
    }


def main() -> None:
    """Entry point for ``python -m personal_assistant_mcp.server``."""
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    if _transport == "streamable-http":
        mcp.run(transport="streamable-http")
    else:
        mcp.run()


if __name__ == "__main__":
    main()
