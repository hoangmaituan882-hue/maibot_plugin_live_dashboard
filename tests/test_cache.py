from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from maibot_plugin_live_dashboard.config import LiveDashboardConfig
from maibot_plugin_live_dashboard.services.api_client import (
    ApiError,
    make_client_from_config,
)


async def configure_client(respond, config=None):
    cfg = LiveDashboardConfig().model_dump() if config is None else config
    cfg["connection"]["base_url"] = "https://dashboard.test"
    client = make_client_from_config(cfg)
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    return client


@pytest.mark.parametrize(
    "path,params,ttl",
    [
        ("/api/current", None, 10),
        ("/api/timeline", {"date": "2026-10-27", "tz": "-480"}, 30),
        ("/api/health-data", {"date": "2026-10-27", "tz": "-480"}, 60),
        ("/api/health", None, 30),
    ],
)
async def test_each_query_cache_expires_without_sliding_expiry(path, params, ttl):
    calls, clock = [], [100.0]

    def respond(request):
        calls.append(request)
        return httpx.Response(200, json={"revision": len(calls), "nested": {"value": "original"}})

    client = await configure_client(respond)
    client._cache._clock = lambda: clock[0]
    try:
        first = await client._get_json(path, params)
        assert first.cache_info["hit"] is False
        first["nested"]["value"] = "changed by caller"
        clock[0] += ttl - 1
        cached = await client._get_json(path, params)
        assert len(calls) == 1
        assert cached["nested"]["value"] == "original"
        assert cached.cache_info["hit"] is True
        assert cached.cache_info["age_seconds"] == ttl - 1
        assert cached.cache_info["fetched_at"] == first.cache_info["fetched_at"]
        clock[0] += 1
        refreshed = await client._get_json(path, params)
        assert len(calls) == 2
        assert refreshed["revision"] == 2
        assert refreshed.cache_info["hit"] is False
        assert refreshed.cache_info["ttl_seconds"] == ttl
    finally:
        await client.close()


@pytest.mark.parametrize(
    "setting,path",
    [
        ("status_cache_ttl_sec", "/api/current"),
        ("timeline_cache_ttl_sec", "/api/timeline"),
        ("health_cache_ttl_sec", "/api/health-data"),
        ("system_status_cache_ttl_sec", "/api/health"),
    ],
)
async def test_each_cache_can_be_disabled(setting, path):
    config = LiveDashboardConfig().model_dump()
    config["caching"][setting] = 0
    calls = []
    client = await configure_client(
        lambda r: (calls.append(r), httpx.Response(200, json={"ok": True}))[1], config
    )
    try:
        await client._get_json(path)
        await client._get_json(path)
        assert len(calls) == 2
        assert not client._cache._entries
    finally:
        await client.close()


@pytest.mark.parametrize(
    "status,body",
    [
        (401, {}),
        (403, {}),
        (500, {}),
        (200, []),
        (200, {"success": False}),
        (200, {"error": "backend failed"}),
    ],
)
async def test_failed_responses_are_retried_not_cached(status, body):
    calls = []

    def respond(request):
        calls.append(request)
        return (
            httpx.Response(status, json=body)
            if len(calls) == 1
            else httpx.Response(200, json={"revision": 2})
        )

    client = await configure_client(respond)
    try:
        with pytest.raises(ApiError):
            await client.get_current()
        assert (await client.get_current())["revision"] == 2
        assert len(calls) == 2
    finally:
        await client.close()


async def test_expired_result_is_not_returned_when_refresh_fails():
    calls, clock = [], [0.0]

    def respond(request):
        calls.append(request)
        return httpx.Response(200, json={"old": True}) if len(calls) == 1 else httpx.Response(503)

    client = await configure_client(respond)
    client._cache._clock = lambda: clock[0]
    try:
        await client.get_current()
        clock[0] = 10
        for _ in range(2):
            with pytest.raises(ApiError):
                await client.get_current()
        assert len(calls) == 3
        assert not client._cache._entries
    finally:
        await client.close()


async def test_cache_keys_separate_dates_devices_timezones_and_friend_panels():
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(200, json={"revision": len(calls)})

    client = await configure_client(respond)
    try:
        # Reordered parameters identify the same request.
        first = await client._get_json("/api/timeline", {"date": "2026-10-27", "tz": "-480"})
        same = await client._get_json("/api/timeline", {"tz": "-480", "date": "2026-10-27"})
        assert first == same and len(calls) == 1
        await client.get_timeline("2026-10-28", -480)
        await client.get_timeline("2026-10-27", 0)
        await client.get_timeline("2026-10-27", -480, "pc")
        await client.get_timeline("2026-10-27", -480, "phone")
        alice = await client.get_proxy_current("alice")
        bob = await client.get_proxy_current("bob")
        assert alice != bob
        assert (await client.get_proxy_current("alice")) == alice
        assert len(calls) == 7
    finally:
        await client.close()


async def test_concurrent_queries_share_one_request_and_one_cancel_does_not_break_others():
    calls, started, release = [], asyncio.Event(), asyncio.Event()

    async def respond(request):
        calls.append(request)
        started.set()
        await release.wait()
        return httpx.Response(200, json={"revision": 1})

    client = await configure_client(respond)
    first = asyncio.create_task(client.get_current())
    await started.wait()
    others = [asyncio.create_task(client.get_current()) for _ in range(10)]
    await asyncio.sleep(0)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    release.set()
    try:
        results = await asyncio.gather(*others)
        assert len(calls) == 1
        assert all(r["revision"] == 1 for r in results)
        assert all(r.cache_info["coalesced"] for r in results)
        assert not client._cache._inflight
    finally:
        await client.close()


async def test_closing_client_cancels_orphaned_request_and_clears_cache():
    started = asyncio.Event()

    async def respond(request):
        started.set()
        await asyncio.Event().wait()

    client = await configure_client(respond)
    waiter = asyncio.create_task(client.get_current())
    await started.wait()
    await client.close()
    assert isinstance(
        (await asyncio.gather(waiter, return_exceptions=True))[0], asyncio.CancelledError
    )
    assert not client._cache._inflight and not client._cache._entries
    await client.close()


async def test_cache_is_bounded():
    client = await configure_client(lambda r: httpx.Response(200, json={"url": str(r.url)}))
    client._cache._max_entries = 2
    try:
        for date in ("2026-10-25", "2026-10-26", "2026-10-27"):
            await client.get_timeline(date, -480)
        assert len(client._cache._entries) == 2
        assert not any("2026-10-25" in str(key) for key in client._cache._entries)
    finally:
        await client.close()


@pytest.mark.parametrize(
    "method,args,path",
    [
        ("query_timeline", {"matched_groups": {"date": "2026-10-27"}}, "/api/timeline"),
        ("query_health", {}, "/api/health-data"),
        ("query_system_status", {}, "/api/health"),
    ],
)
async def test_commands_reuse_successful_queries(plugin, context, http_mock, method, args, path):
    await getattr(plugin, method)(**context, **args)
    await getattr(plugin, method)(**context, **args)
    assert sum(r.url.path == path for r in http_mock) == 1


async def test_command_and_model_tool_share_status_cache_with_explicit_freshness(
    plugin, context, http_mock
):
    assert (await plugin.query_live_dashboard(**context))[0]
    result = await plugin.query_live_dashboard_status_tool(**context)
    facts = json.loads(result["content"])
    assert facts["query_freshness"]["hit"] is True
    assert facts["query_freshness"]["ttl_seconds"] == 10
    assert facts["devices"][0]["last_seen_at"] == "2026-10-27T09:00:00"
    assert sum(r.url.path == "/api/current" for r in http_mock) == 1


async def test_reload_discards_cached_data_and_replaces_address_and_credentials(
    plugin, context, http_mock
):
    await plugin.query_live_dashboard_status_tool(**context)
    config = plugin.get_plugin_config_data()
    config["connection"].update(base_url="https://new.test", auth_token="new-token")
    await plugin.on_config_update("self", config, "1.1.0")
    result = await plugin.query_live_dashboard_status_tool(**context)
    assert json.loads(result["content"])["query_freshness"]["hit"] is False
    assert len(http_mock) == 2
    assert http_mock[-1].url.host == "new.test"
    assert http_mock[-1].headers["authorization"] == "Bearer new-token"


async def test_warm_cache_does_not_bypass_blacklist(plugin, context, http_mock):
    await plugin.query_live_dashboard_status_tool(**context)
    config = plugin.get_plugin_config_data()
    config["access_control"]["user_blacklist_senders"] = "u1"
    await plugin.on_config_update("self", config, "1.1.0")
    result = await plugin.query_live_dashboard_status_tool(**context)
    assert result["success"] is False
    assert "Code" not in result["content"]
    assert len(http_mock) == 1
