from __future__ import annotations

import ast
import json
from pathlib import Path

import httpx
import pytest
from aiohttp import web
from maibot_plugin_live_dashboard.config import LiveDashboardConfig
from maibot_plugin_live_dashboard.plugin import create_plugin
from maibot_plugin_live_dashboard.services.api_client import (
    ApiClient,
    AuthenticationError,
    HttpApiError,
    InvalidResponseError,
    NetworkApiError,
    TimeoutApiError,
)
from maibot_plugin_live_dashboard.services.message_renderer import (
    render_dashboard_message_with_count,
)
from maibot_plugin_live_dashboard.services.telemetry_service import TelemetryManager
from maibot_plugin_live_dashboard.services.timeline_renderer import render_timeline_message
from maibot_sdk import PluginContext, PluginPaths

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "status,body,error",
    [
        (401, {}, AuthenticationError),
        (403, {}, AuthenticationError),
        (500, {}, HttpApiError),
        (302, {}, HttpApiError),
        (200, [], InvalidResponseError),
        (200, "not json", InvalidResponseError),
    ],
)
async def test_http_error_mapping(status, body, error):
    client = ApiClient("https://dashboard.test")
    await client._client.aclose()
    response = (
        httpx.Response(status, text=body)
        if isinstance(body, str)
        else httpx.Response(status, json=body)
    )
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: response))
    try:
        with pytest.raises(error):
            await client.get_current()
    finally:
        await client.close()


@pytest.mark.parametrize(
    "raised,expected",
    [
        (httpx.ReadTimeout("timeout"), TimeoutApiError),
        (httpx.ConnectError("connection failed"), NetworkApiError),
    ],
)
async def test_network_error_mapping(raised, expected):
    client = ApiClient("https://dashboard.test")
    await client._client.aclose()

    def respond(request):
        raise raised

    client._client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    try:
        with pytest.raises(expected):
            await client.get_current()
    finally:
        await client.close()


async def test_no_configuration_is_clear_and_does_not_request(plugin, context, http_mock):
    config = plugin.get_plugin_config_data()
    config["connection"]["base_url"] = ""
    await plugin.on_config_update("self", config, "1.0.0")
    for kind in ("status", "timeline", "health", "system_status", "friend"):
        text, ok, _, _ = await plugin._query(kind, context)
        assert not ok and "未配置" in text
    assert http_mock == []


def test_device_and_information_filters(payloads):
    config = LiveDashboardConfig().model_dump()
    config["output_scope"]["device_whitelist_keywords"] = "电脑,手机"
    config["output_scope"]["device_blacklist_keywords"] = "手机"
    config["access_control"]["info_blacklist_keywords"] = "Code,插件开发"
    config["access_control"]["info_blacklist_replacement"] = "已隐藏"
    text, count = render_dashboard_message_with_count(payloads["current"], config)
    assert count == 1
    assert "手机" not in text
    assert "Code" not in text and "插件开发" not in text
    assert "已隐藏" in text


@pytest.mark.parametrize(
    "field,absent",
    [
        ("show_platform", "windows"),
        ("show_app_name", "应用："),
        ("show_display_title", "标题："),
        ("show_battery", "电量："),
        ("show_music", "音乐："),
        ("show_last_seen", "上报："),
    ],
)
def test_output_field_toggles(payloads, field, absent):
    config = LiveDashboardConfig().model_dump()
    config["output_fields"][field] = False
    text, _ = render_dashboard_message_with_count(payloads["current"], config)
    assert absent not in text


def test_nsfw_mask_applies_to_status_and_timeline(payloads):
    config = LiveDashboardConfig().model_dump()
    payloads["current"]["devices"][0]["display_title"] = "Pornhub explicit"
    payloads["timeline"]["segments"][0]["display_title"] = "Pornhub explicit"
    assert "Pornhub" not in render_dashboard_message_with_count(payloads["current"], config)[0]
    assert "Pornhub" not in render_timeline_message(payloads["timeline"], config)[0]


def test_all_original_config_fields_exist():
    original = json.loads(
        (Path(__file__).parent / "fixtures/upstream_config_schema.json").read_text(encoding="utf-8")
    )
    model = LiveDashboardConfig().model_dump()
    for group, spec in original.items():
        for key, field in spec["items"].items():
            assert key in model[group], (group, key)
            if group != "telemetry_config":
                assert model[group][key] == field["default"], (group, key)


def test_runtime_does_not_import_astrbot_or_host():
    for file in ROOT.rglob("*.py"):
        if "tests" in file.parts or "tools" in file.parts:
            continue
        tree = ast.parse(file.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = (
                [a.name for a in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else []
            )
            assert not any(n == "astrbot" or n.startswith(("astrbot.", "src.")) for n in names), (
                file
            )


async def test_telemetry_disabled_is_silent(tmp_path):
    telemetry = TelemetryManager({}, "1.0.0", data_dir=tmp_path)
    assert not telemetry.enabled
    assert not await telemetry.track_startup()
    assert not list(tmp_path.iterdir())
    assert telemetry._session is None
    await telemetry.close()
    await telemetry.close()


async def test_opt_in_telemetry_ingest_and_redaction(tmp_path):
    received = []

    async def ingest(request):
        received.append((dict(request.headers), await request.json()))
        return web.json_response({"ok": True})

    app = web.Application()
    app.router.add_post("/ingest", ingest)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    config = LiveDashboardConfig().model_dump()
    config["telemetry_config"].update(
        enabled=True, endpoint=f"http://127.0.0.1:{port}/ingest", app_key="private-key"
    )
    config["connection"].update(
        base_url="https://private-dashboard.test", auth_token="private-token"
    )
    config["access_control"]["user_blacklist_senders"] = "private-user"
    telemetry = TelemetryManager(config, "1.0.0", data_dir=tmp_path)
    telemetry._MIN_REQUEST_INTERVAL = 0
    try:
        assert await telemetry.track_config(config)
        try:
            raise ValueError("private-user private-token device-window-title")
        except ValueError as exc:
            await telemetry.track_error(exc, "test.module")
        await telemetry.flush()
        payload = json.dumps([data for _, data in received], ensure_ascii=False)
        for secret in (
            "private-key",
            "private-token",
            "private-user",
            "private-dashboard",
            "device-window-title",
        ):
            assert secret not in payload
        assert received[0][0]["X-App-Key"] == "private-key"
        assert (tmp_path / ".telemetry_id").exists()
    finally:
        await telemetry.close()
        await runner.cleanup()


async def test_real_http_end_to_end(tmp_path, payloads):
    """Exercise actual sockets, token auth, query params, formatting, and SDK send RPC."""
    requests, sent = [], []

    async def endpoint(request):
        requests.append(request)
        assert request.headers.get("Authorization") == "Bearer local-test-token"
        key = {
            "/api/current": "current",
            "/api/timeline": "timeline",
            "/api/health-data": "health",
            "/api/health": "service",
            "/api/config": "config",
        }.get(request.path)
        if request.path == "/api/proxy":
            assert request.query["dashboard_id"] == "alice"
            key = {
                "current": "current",
                "timeline": "timeline",
                "health-data": "health",
                "config": "friend_config",
            }[request.query["endpoint"]]
        return web.json_response(payloads[key])

    app = web.Application()
    app.router.add_get("/api/{endpoint}", endpoint)
    server = web.AppRunner(app)
    await server.setup()
    site = web.TCPSite(server, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    instance = create_plugin()
    config = instance.get_default_config()
    config["connection"].update(base_url=f"http://127.0.0.1:{port}", auth_token="local-test-token")
    instance.set_plugin_config(config)

    async def rpc(method, plugin_id, payload, **kwargs):
        sent.append(payload)
        return {"success": True}

    instance._set_context(
        PluginContext("local.live-dashboard", rpc, PluginPaths(tmp_path, tmp_path / "tmp"))
    )
    await instance.on_load()
    context = {"stream_id": "test", "platform": "telegram", "user_id": "user"}
    try:
        for method in (
            "query_live_dashboard",
            "query_timeline",
            "query_health",
            "query_system_status",
        ):
            assert (await getattr(instance, method)(**context))[0]
        for action in ("status", "timeline", "health", "config"):
            assert (
                await instance.query_friend_dashboard_status_tool(
                    name="小明", action=action, date="2026-10-27", **context
                )
            )["success"]
        assert len(sent) >= 4
        assert {r.path for r in requests} == {
            "/api/current",
            "/api/timeline",
            "/api/health-data",
            "/api/health",
            "/api/config",
            "/api/proxy",
        }
    finally:
        await instance.on_unload()
        await server.cleanup()
