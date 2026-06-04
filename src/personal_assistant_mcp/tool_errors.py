"""Error handling helpers for FastMCP tool handlers."""

from __future__ import annotations

import functools
import logging
from collections.abc import Awaitable, Callable
from typing import ParamSpec, TypeVar

from mcp.server.fastmcp.exceptions import ToolError

P = ParamSpec("P")
T = TypeVar("T")


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
                logging.getLogger(func.__module__).exception(
                    "MCP tool %s failed: %s",
                    tool_name,
                    detail,
                )
                raise ToolError(f"{tool_name} failed: {detail}") from exc

        return wrapper

    return decorator


def _exception_detail(exc: Exception) -> str:
    message = str(exc).strip()
    exc_type = type(exc).__name__
    return f"{exc_type}: {message}" if message else exc_type


__all__ = ["surface_tool_errors"]
