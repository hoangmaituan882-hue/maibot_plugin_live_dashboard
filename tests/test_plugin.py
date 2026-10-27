from __future__ import annotations

import asyncio
import copy
import re
from unittest.mock import AsyncMock

import pytest


@pytest.mark.parametrize(
    "method,args,expected",
    [
        ("query_live_dashboard", {}, "在线设备：2/3"),
        ("query_timeline", {"matched_groups": {"date": "2026-10-27"}}, "1小时30分钟"),
        ("query_health", {}, "7小时30分"),
        ("query_system_status", {}, "1天1小时1分1秒"),
        ("query_friend", {}, "小明"),
        ("query_friend", {"text": "/好友面板 alice"}, "好友面板「小明」状态"),
        ("query_friend", {"text": "/视奸好友 小明 时间线 2026-10-27"}, "时间线"),
        ("query_friend", {"text": "/视奸好友 小明 健康 2026-10-27"}, "12,345"),
        ("query_friend", {"text": "/好友面板 alice config"}, "小明面板"),
    ],
)
async def test_all_commands(plugin, context, method, args, expected, http_mock):
    result = await getattr(plugin, method)(**{**context, **args})
    assert result[0] is True
    assert result[2] is True  # Host's third tuple member intercepts subsequent processing.
    assert expected in result[1]
    assert expected in "".join(item["args"].get("text", "") for item in plugin.sent)
    assert all(r.headers["authorization"] == "Bearer test-token" for r in http_mock)


@pytest.mark.parametrize(
    "method,args,expected",
    [
        ("query_live_dashboard_status_tool", {}, "正在写代码"),
        ("query_live_dashboard_timeline_tool", {"date": "2026-10-27"}, "时间线"),
        ("query_live_dashboard_health_tool", {}, "72 bpm"),
        ("query_friend_dashboard_status_tool", {}, "小明"),
        ("query_friend_dashboard_status_tool", {"name": "ALI"}, "好友面板"),
        ("query_friend_dashboard_status_tool", {"name": "小明", "action": "timeline"}, "时间线"),
        ("query_friend_dashboard_status_tool", {"name": "小明", "action": "health"}, "12,345"),
        ("query_friend_dashboard_status_tool", {"name": "小明", "action": "config"}, "公开活动"),
    ],
)
async def test_all_tools(plugin, context, method, args, expected):
    result = await getattr(plugin, method)(**context, **args)
    assert result["success"] is True
    assert expected in result["content"]
    assert plugin.sent == []  # Tool data goes to the planner, which composes the reply.


@pytest.mark.parametrize(
    "config_key,value,context_change",
    [
        ("user_blacklist_senders", "u1", {}),
        ("user_blacklist_senders", "telegram:u1", {}),
        ("group_blacklist_sessions", "g1", {}),
        ("group_blacklist_sessions", "stream-123", {}),
        ("group_blacklist_sessions", "default:GroupMessage:g1", {}),
        ("user_blacklist_senders", "someone", {"user_id": ""}),
    ],
)
async def test_access_controls_cover_every_route(
    plugin, context, http_mock, config_key, value, context_change
):
    config = plugin.get_plugin_config_data()
    config["access_control"][config_key] = value
    await plugin.on_config_update("self", config, "1.0.0")
    context.update(context_change)
    for kind in ("status", "timeline", "health", "system_status", "friend"):
        text, ok, _, _ = await plugin._query(kind, context, name="小明")
        assert not ok
        assert any(word in text for word in ("禁用", "禁止", "拒绝"))
    tool = await plugin.query_live_dashboard_status_tool(**context)
    assert tool["success"] is False
    assert http_mock == []


@pytest.mark.parametrize("kind", ["timeline", "health", "friend", "system_status"])
async def test_disabled_features(plugin, context, http_mock, kind):
    config = plugin.get_plugin_config_data()
    config["commands"][f"enable_{kind}_command"] = False
    await plugin.on_config_update("self", config, "1.0.0")
    assert (await plugin._query(kind, context))[1] is False
    assert http_mock == []


async def test_chinese_friend_action_does_not_bypass_disabled_feature(plugin, context, http_mock):
    config = plugin.get_plugin_config_data()
    config["commands"]["enable_health_command"] = False
    await plugin.on_config_update("self", config, "1.0.0")
    assert not (
        await plugin.query_friend_dashboard_status_tool(name="小明", action="健康", **context)
    )["success"]
    assert http_mock == []


@pytest.mark.parametrize("method", ["query_timeline", "query_health"])
@pytest.mark.parametrize("date", ["2026-02-30", "27/10/2026", "nope"])
async def test_invalid_dates(plugin, context, method, date, http_mock):
    result = await getattr(plugin, method)(**context, matched_groups={"date": date})
    assert not result[0]
    assert "日期格式不正确" in result[1]
    assert http_mock == []


async def test_proxy_routes_and_cache(plugin, context, http_mock):
    for action in ("status", "timeline", "health", "config"):
        assert (
            await plugin.query_friend_dashboard_status_tool(
                name="小明", action=action, date="2026-10-27", **context
            )
        )["success"]
    assert sum(r.url.path == "/api/config" for r in http_mock) == 1
    proxy = [r for r in http_mock if r.url.path == "/api/proxy"]
    assert {r.url.params["endpoint"] for r in proxy} == {
        "current",
        "timeline",
        "health-data",
        "config",
    }
    assert all(r.url.params["dashboard_id"] == "alice" for r in proxy)
    assert all(r.url.host == "dashboard.test" for r in proxy)
    assert all(r.url.params["date"] == "2026-10-27" for r in proxy if "date" in r.url.params)


async def test_friend_cache_can_be_disabled(plugin, context, http_mock):
    config = plugin.get_plugin_config_data()
    config["caching"]["friend_config_cache_ttl_sec"] = 0
    await plugin.on_config_update("self", config, "1.0.0")
    for _ in range(2):
        assert (await plugin.query_friend_dashboard_status_tool(**context))["success"]
    assert sum(r.url.path == "/api/config" for r in http_mock) == 2


async def test_qq_uses_merged_forward_and_falls_back(plugin, context):
    context["platform"] = "qq"
    assert (await plugin.query_live_dashboard(**context))[0]
    assert plugin.sent[0]["capability"] == "send.forward"
    assert len(plugin.sent[0]["args"]["messages"]) >= 3
    plugin.sent.clear()
    plugin.ctx.send.forward = AsyncMock(return_value=False)
    assert (await plugin.query_live_dashboard(**context))[0]
    assert plugin.sent[0]["capability"] == "send.text"
    assert plugin.sent[0]["args"]["set_reply"] is True


async def test_chunk_fallback_keeps_all_content(plugin, context):
    config = plugin.get_plugin_config_data()
    config["commands"]["text_chunk_size"] = 200
    text = "甲乙\n" * 3000
    assert await plugin._send(text, context, config)
    assert "".join(s["args"]["text"] for s in plugin.sent) == text


async def test_config_reload_does_not_close_inflight_clients(plugin, context):
    old = plugin._runtime
    started, finish = asyncio.Event(), asyncio.Event()

    async def delayed(**kwargs):
        started.set()
        await finish.wait()
        return "旧请求已完成", 1

    old.dashboard.query_and_render = delayed
    close_spy = AsyncMock(wraps=old.dashboard.close)
    old.dashboard.close = close_spy
    pending = asyncio.create_task(plugin.query_live_dashboard_status_tool(**context))
    await started.wait()
    config = copy.deepcopy(plugin.get_plugin_config_data())
    config["connection"]["base_url"] = "https://new-dashboard.test"
    await plugin.on_config_update("self", config, "1.0.0")
    await asyncio.sleep(0)
    close_spy.assert_not_called()
    assert plugin._runtime is not old
    finish.set()
    assert (await pending)["content"] == "旧请求已完成"
    await asyncio.gather(*tuple(plugin._retiring))
    close_spy.assert_awaited_once()


def test_command_matching_and_component_registration(plugin):
    components = plugin.get_components()
    assert len(components) == 11
    commands = [c for c in components if c["type"] == "COMMAND"]
    for text in (
        "/视奸",
        "/ldb",
        "设备状态",
        "/livedashboard",
        "/状态面板",
        "/视奸时间线 2026-10-27",
        "/视奸历史",
        "/健康数据",
        "/视奸健康 2026-10-27",
        "/视奸服务状态",
        "/好友面板 alice health",
        "/视奸好友",
    ):
        assert sum(bool(re.match(c["metadata"]["command_pattern"], text)) for c in commands) == 1
    assert not any(re.match(c["metadata"]["command_pattern"], "/视奸其他东西") for c in commands)
