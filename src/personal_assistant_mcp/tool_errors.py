"""Error handling helpers for FastMCP tool handlers."""

from __future__ import annotations

import functools
import logging
import re
import traceback
from collections.abc import Awaitable, Callable
from typing import ParamSpec, TypeVar

from mcp.server.fastmcp.exceptions import ToolError

P = ParamSpec("P")
T = TypeVar("T")

_AUTH_BEARER_RE = re.compile(r"(authorization\s*[:=]\s*bearer\s+)([^\s,;]+)", re.IGNORECASE)
_SENSITIVE_ASSIGNMENT_RE = re.compile(
    r"\b("
    r"passwd|password|token|api[_-]?key|access[_-]?token|refresh[_-]?token|"
    r"client[_-]?secret|secret"
    r")(\s*[:=]\s*)([^&\s,;]+)",
    re.IGNORECASE,
)


def surface_tool_errors(
    tool_name: str,
) -> Callable[[Callable[P, Awaitable[T]]], Callable[P, Awaitable[T]]]:
    def decorator(func: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]:
        @functools.wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            try:
                return await func(*args, **kwargs)
            except ToolError:
                raise
            except Exception as exc:
                detail = _exception_detail(exc)
                redacted_traceback = _redact_sensitive_text(
                    "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
                ).rstrip()
                logging.getLogger(func.__module__).error(
                    "MCP tool %s failed: %s\n%s",
                    tool_name,
                    detail,
                    redacted_traceback,
                )
                raise ToolError(f"{tool_name} failed: {detail}") from exc

        return wrapper

    return decorator


def _exception_detail(exc: Exception) -> str:
    message = _redact_sensitive_text(str(exc).strip())
    exc_type = type(exc).__name__
    return f"{exc_type}: {message}" if message else exc_type


def _redact_sensitive_text(text: str) -> str:
    text = _AUTH_BEARER_RE.sub(r"\1[redacted]", text)
    return _SENSITIVE_ASSIGNMENT_RE.sub(r"\1\2[redacted]", text)


__all__ = ["surface_tool_errors"]
