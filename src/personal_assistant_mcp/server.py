"""FastMCP server for personal-assistant tasks.

Transport: stdio by default, or streamable-http when ``MCP_TRANSPORT=streamable-http``.
In HTTP mode ``MCP_API_KEY`` is **required**; the server refuses to start an
unauthenticated HTTP listener because exposed tools can have side effects on
the configured vault, RSS reader, or calendar.

MCP tool calls use a lifespan-scoped vault client so stateless HTTP requests
cannot close a client that another concurrent request is still using. Routes
outside the MCP request lifespan use a lazy process fallback client.

Email operations should be handled by a dedicated email MCP server when needed.
"""

from __future__ import annotations

import hmac
import logging
import os
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, AsyncIterator

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .calendar import tools as calendar_tools
from .config import Settings
from .daily import note as daily_note
from .daily import tools as daily_tools
from .digests import tools as digests_tools
from .freshrss import tools as freshrss_tools
from .release import tools as release_tools
from .tasks import tools as tasks_tools
from .tool_errors import surface_tool_errors
from .vault import build_vault_client
from .weekly import tools as weekly_tools

if TYPE_CHECKING:
    from obsidian_livesync_mcp.client import ObsidianVaultClient

logger = logging.getLogger(__name__)


_transport = os.environ.get("MCP_TRANSPORT", "stdio")
_server_kwargs: dict = {}
_api_key = ""

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


@dataclass
class _VaultScope:
    client: ObsidianVaultClient | None = None


_vault_scope: ContextVar[_VaultScope | None] = ContextVar("vault_scope", default=None)
_vault: ObsidianVaultClient | None = None


def _get_vault() -> ObsidianVaultClient:
    """Return the active vault client, constructing it on first use."""
    scope = _vault_scope.get()
    if scope is not None:
        if scope.client is None:
            scope.client = build_vault_client(Settings.from_env())
        return scope.client

    global _vault
    if _vault is None:
        _vault = build_vault_client(Settings.from_env())
    return _vault


@asynccontextmanager
async def _lifespan(server: FastMCP) -> AsyncIterator[None]:
    """Scope MCP tool vault clients to the current server/request lifespan."""
    scope = _VaultScope()
    token = _vault_scope.set(scope)
    try:
        yield
    finally:
        try:
            if scope.client is not None:
                try:
                    await scope.client.close()
                except Exception:
                    logger.exception("Error closing vault client")
        finally:
            _vault_scope.reset(token)


_server_kwargs["lifespan"] = _lifespan

mcp = FastMCP("personal-assistant", **_server_kwargs)


@mcp.tool()
@surface_tool_errors("health")
async def health() -> dict:
    """Return server health status. Useful as a runtime smoke test."""
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


@mcp.custom_route("/inbox", methods=["POST"], include_in_schema=True)
async def inbox_route(request: Request) -> Response:
    """Append a captured item to today's daily-note Inbox."""
    if not _has_valid_bearer_token(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    try:
        payload = await _parse_inbox_request(request)
        result = await daily_note.append_inbox_task(_get_vault(), **payload)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    return JSONResponse(result)


def _has_valid_bearer_token(request: Request) -> bool:
    if not _api_key:
        return False

    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return False
    return hmac.compare_digest(token, _api_key)


async def _parse_inbox_request(request: Request) -> dict:
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type == "application/json":
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError("JSON body must be an object")
        text = body.get("text")
        if not isinstance(text, str):
            raise ValueError("JSON body must include a string 'text' field")
        return {
            "text": text,
            "priority": _optional_str(body.get("priority"), "priority"),
            "due": _optional_date(body.get("due"), "due"),
            "scheduled": _optional_date(body.get("scheduled"), "scheduled"),
            "start": _optional_date(body.get("start"), "start"),
            "recurrence": _optional_str(body.get("recurrence"), "recurrence"),
        }

    try:
        text = (await request.body()).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("Request body must be UTF-8 text") from exc
    return {"text": text}


def _optional_str(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field_name!r} must be a string")
    return value


def _optional_date(value: object, field_name: str) -> date | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field_name!r} must be an ISO date string")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field_name!r} must be an ISO date (YYYY-MM-DD): {value!r}") from exc


def main() -> None:
    """Entry point for ``python -m personal_assistant_mcp.server``."""
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    if _transport == "streamable-http":
        mcp.run(transport="streamable-http")
    else:
        mcp.run()


if __name__ == "__main__":
    main()
