"""FastMCP server for personal-assistant tasks.

Transport: stdio by default, or streamable-http when ``MCP_TRANSPORT=streamable-http``.
In HTTP mode ``MCP_API_KEY`` is **required**; the server refuses to start an
unauthenticated HTTP listener because every exposed tool has side effects on
the user's vault, mail, or calendar.

The vault client is constructed lazily on the first tool that needs it and
closed cleanly on server shutdown via the FastMCP lifespan hook.
"""

from __future__ import annotations

import hmac
import logging
import os
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, AsyncIterator

from mcp.server.fastmcp import FastMCP

from .calendar import tools as calendar_tools
from .config import Settings
from .daily import tools as daily_tools
from .digests import tools as digests_tools
from .freshrss import tools as freshrss_tools
from .proton import tools as proton_tools
from .release import tools as release_tools
from .tasks import tools as tasks_tools
from .vault import build_vault_client
from .weekly import tools as weekly_tools

if TYPE_CHECKING:
    from obsidian_livesync_mcp.client import ObsidianVaultClient

logger = logging.getLogger(__name__)

_transport = os.environ.get("MCP_TRANSPORT", "stdio")
_server_kwargs: dict = {}

if _transport == "streamable-http":
    _api_key = os.environ.get("MCP_API_KEY", "")
    if not _api_key:
        raise RuntimeError(
            "MCP_API_KEY must be set when MCP_TRANSPORT=streamable-http; "
            "the server refuses to run an unauthenticated HTTP listener."
        )

    _server_kwargs["host"] = os.environ.get("MCP_HOST", "0.0.0.0")
    _server_kwargs["port"] = int(os.environ.get("MCP_PORT", "8080"))
    _server_kwargs["stateless_http"] = True
    _server_kwargs["json_response"] = True

    _port = int(os.environ.get("MCP_PORT", "8080"))
    _resource_url = os.environ.get("MCP_RESOURCE_URL", f"http://localhost:{_port}")

    from mcp.server.auth.provider import AccessToken, TokenVerifier
    from mcp.server.auth.settings import AuthSettings
    from pydantic import AnyHttpUrl

    class _APIKeyVerifier(TokenVerifier):
        """Verify Bearer tokens against the ``MCP_API_KEY`` env var.

        Uses ``hmac.compare_digest`` for constant-time comparison to avoid
        leaking key length / prefix through response timing.
        """

        async def verify_token(self, token: str) -> AccessToken | None:
            if not hmac.compare_digest(token, _api_key):
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


_vault: ObsidianVaultClient | None = None


def _get_vault() -> ObsidianVaultClient:
    """Return the shared vault client, constructing it on first call."""
    global _vault
    if _vault is None:
        _vault = build_vault_client(Settings.from_env())
    return _vault


@asynccontextmanager
async def _lifespan(server: FastMCP) -> AsyncIterator[None]:
    """Close the vault client on shutdown if it was constructed."""
    global _vault
    try:
        yield
    finally:
        if _vault is not None:
            try:
                await _vault.close()
            except Exception:
                logger.exception("Error closing vault client")
            _vault = None


_server_kwargs["lifespan"] = _lifespan

mcp = FastMCP("personal-assistant", **_server_kwargs)


@mcp.tool()
async def health() -> dict:
    """Return server health status. Useful as a deployment smoke test."""
    return {
        "status": "ok",
        "service": "personal-assistant-mcp",
        "transport": _transport,
    }


tasks_tools.register(mcp, _get_vault)
daily_tools.register(mcp, _get_vault)
weekly_tools.register(mcp, _get_vault)
digests_tools.register(mcp, _get_vault)
release_tools.register(mcp, _get_vault)
freshrss_tools.register(mcp)
calendar_tools.register(mcp)
proton_tools.register(mcp)


def main() -> None:
    """Entry point for ``python -m personal_assistant_mcp.server``."""
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    if _transport == "streamable-http":
        mcp.run(transport="streamable-http")
    else:
        mcp.run()


if __name__ == "__main__":
    main()
