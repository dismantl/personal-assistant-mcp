"""Unit tests for the FreshRSS REST client."""

from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest
import respx

from personal_assistant_mcp.freshrss.client import (
    FreshRSSConfig,
    contents,
    login,
    unread,
)

_CONFIG = FreshRSSConfig(url="https://fresh.example", user="user@x", password="secret")
_FIXED_NOW = datetime(2026, 5, 11, 12, 0, tzinfo=timezone.utc)


# -----------------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------------


def test_config_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRESHRSS_URL", "https://x.example")
    monkeypatch.setenv("FRESHRSS_USER", "u")
    monkeypatch.setenv("FRESHRSS_PASSWORD", "p")
    config = FreshRSSConfig.from_env()
    assert config.url == "https://x.example"
    assert config.user == "u"
    assert config.password == "p"


def test_config_from_env_rejects_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FRESHRSS_URL", raising=False)
    with pytest.raises(ValueError, match="FRESHRSS_URL"):
        FreshRSSConfig.from_env()


# -----------------------------------------------------------------------------
# login
# -----------------------------------------------------------------------------


@respx.mock
async def test_login_extracts_auth_token() -> None:
    respx.get("https://fresh.example/accounts/ClientLogin").mock(
        return_value=httpx.Response(200, text="SID=abc\nLSID=def\nAuth=token-value\n")
    )
    assert await login(_CONFIG) == "token-value"


@respx.mock
async def test_login_sends_credentials_as_query_params() -> None:
    route = respx.get("https://fresh.example/accounts/ClientLogin").mock(
        return_value=httpx.Response(200, text="Auth=tok\n")
    )
    await login(_CONFIG)
    sent = route.calls.last.request
    assert "Email=user%40x" in str(sent.url)
    assert "Passwd=secret" in str(sent.url)


@respx.mock
async def test_login_raises_when_no_auth_line() -> None:
    respx.get("https://fresh.example/accounts/ClientLogin").mock(
        return_value=httpx.Response(200, text="No Auth here\n")
    )
    with pytest.raises(RuntimeError, match="did not return an auth token"):
        await login(_CONFIG)


# -----------------------------------------------------------------------------
# unread
# -----------------------------------------------------------------------------


@respx.mock
async def test_unread_returns_parsed_payload() -> None:
    respx.get("https://fresh.example/accounts/ClientLogin").mock(
        return_value=httpx.Response(200, text="Auth=tok\n")
    )
    respx.get(url__startswith="https://fresh.example/reader/api/0/stream/items/ids").mock(
        return_value=httpx.Response(200, text='{"itemRefs": [{"id": "1"}, {"id": "2"}]}')
    )
    payload = await unread(_CONFIG, now=_FIXED_NOW)
    assert payload == {"itemRefs": [{"id": "1"}, {"id": "2"}]}


@respx.mock
async def test_unread_uses_correct_query_parameters() -> None:
    respx.get("https://fresh.example/accounts/ClientLogin").mock(
        return_value=httpx.Response(200, text="Auth=tok\n")
    )
    ids_route = respx.get(
        url__startswith="https://fresh.example/reader/api/0/stream/items/ids"
    ).mock(return_value=httpx.Response(200, text='{"itemRefs": []}'))
    await unread(_CONFIG, now=_FIXED_NOW)
    sent_url = str(ids_route.calls.last.request.url)
    assert "s=user%2F-%2Fstate%2Fcom.google%2Freading-list" in sent_url
    assert "xt=user%2F-%2Fstate%2Fcom.google%2Fread" in sent_url
    assert "n=100" in sent_url
    assert "output=json" in sent_url
    # 7d window from _FIXED_NOW (2026-05-11 12:00 UTC) -> 2026-05-04 12:00 UTC
    expected_ts = int(datetime(2026, 5, 4, 12, 0, tzinfo=timezone.utc).timestamp())
    assert f"ot={expected_ts}" in sent_url


@respx.mock
async def test_unread_sends_bearer_authorization() -> None:
    respx.get("https://fresh.example/accounts/ClientLogin").mock(
        return_value=httpx.Response(200, text="Auth=tok\n")
    )
    ids_route = respx.get(
        url__startswith="https://fresh.example/reader/api/0/stream/items/ids"
    ).mock(return_value=httpx.Response(200, text='{"itemRefs": []}'))
    await unread(_CONFIG, now=_FIXED_NOW)
    sent = ids_route.calls.last.request
    assert sent.headers["Authorization"] == "GoogleLogin auth=tok"


# -----------------------------------------------------------------------------
# contents
# -----------------------------------------------------------------------------


@respx.mock
async def test_contents_returns_empty_when_no_unread_items() -> None:
    respx.get("https://fresh.example/accounts/ClientLogin").mock(
        return_value=httpx.Response(200, text="Auth=tok\n")
    )
    respx.get(url__startswith="https://fresh.example/reader/api/0/stream/items/ids").mock(
        return_value=httpx.Response(200, text='{"itemRefs": []}')
    )
    assert await contents(_CONFIG, now=_FIXED_NOW) == {"items": []}


@respx.mock
async def test_contents_fetches_payloads_for_unread_items() -> None:
    respx.get("https://fresh.example/accounts/ClientLogin").mock(
        return_value=httpx.Response(200, text="Auth=tok\n")
    )
    respx.get(url__startswith="https://fresh.example/reader/api/0/stream/items/ids").mock(
        return_value=httpx.Response(200, text='{"itemRefs": [{"id": "a"}, {"id": "b"}]}')
    )
    contents_route = respx.post("https://fresh.example/reader/api/0/stream/items/contents").mock(
        return_value=httpx.Response(200, text='{"items": [{"id": "a", "title": "T1"}]}')
    )
    result = await contents(_CONFIG, now=_FIXED_NOW)
    assert result == {"items": [{"id": "a", "title": "T1"}]}
    sent = contents_route.calls.last.request
    body = sent.content.decode("utf-8")
    assert "i=a" in body and "i=b" in body
    assert sent.headers["Content-Type"] == "application/x-www-form-urlencoded"
