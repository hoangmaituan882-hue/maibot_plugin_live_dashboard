from __future__ import annotations

import copy
import sys
from pathlib import Path

import httpx
import pytest
from maibot_sdk import PluginContext, PluginPaths

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from maibot_plugin_live_dashboard.plugin import create_plugin  # noqa: E402


@pytest.fixture
def payloads():
    return {
        "current": {
            "viewer_count": 3,
            "server_time": "2026-10-27T09:00:00",
            "devices": [
                {
                    "device_id": "pc",
                    "device_name": "工作电脑",
                    "platform": "windows",
                    "is_online": 1,
                    "app_name": "Code",
                    "status_text": "正在写代码",
                    "display_title": "插件开发",
                    "last_seen_at": "2026-10-27T09:00:00",
                    "extra": {
                        "battery_percent": 85,
                        "battery_charging": True,
                        "music": {"title": "晴天", "artist": "歌手", "app": "播放器"},
                    },
                },
                {
                    "device_id": "phone",
                    "device_name": "手机",
                    "platform": "android",
                    "is_online": True,
                    "app_name": "QQ",
                    "status_text": "正在聊天",
                    "display_title": "QQ",
                    "extra": {"battery_percent": 50},
                },
                {"device_id": "offline", "device_name": "旧电脑", "is_online": 0},
            ],
            "recent_activities": [
                {
                    "device_id": "pc",
                    "device_name": "工作电脑",
                    "app_name": "Code",
                    "status_text": "正在写代码",
                    "display_title": "插件开发",
                }
            ],
        },
        "timeline": {
            "date": "2026-10-27",
            "summary": {"pc": {"Code": 90, "QQ": 15}},
            "segments": [
                {
                    "device_id": "pc",
                    "device_name": "工作电脑",
                    "app_name": "Code",
                    "status_text": "正在写代码",
                    "display_title": "插件开发",
                    "started_at": "2026-10-27T08:00:00",
                    "duration_minutes": 30,
                },
                {
                    "device_id": "pc",
                    "device_name": "工作电脑",
                    "app_name": "Code",
                    "status_text": "正在写代码",
                    "display_title": "插件开发",
                    "started_at": "2026-10-27T08:30:00",
                    "duration_minutes": 60,
                },
            ],
        },
        "health": {
            "date": "2026-10-27",
            "records": [
                {"device_id": "pc", "type": "heart_rate", "value": 72},
                {"device_id": "pc", "type": "steps", "value": 12345},
                {"device_id": "pc", "type": "sleep", "value": 450},
                {"device_id": "pc", "type": "distance", "value": 2500},
            ],
        },
        "config": {"dashboards": [{"id": "alice", "name": "小明"}]},
        "service": {"status": "ok", "uptime": 90061, "timestamp": "2026-10-27T09:00:00"},
        "friend_config": {
            "displayName": "小明",
            "siteTitle": "小明面板",
            "siteDescription": "公开活动",
        },
    }


@pytest.fixture
def http_mock(monkeypatch, payloads):
    calls = []
    original = httpx.AsyncClient

    def respond(request):
        calls.append(request)
        path = request.url.path
        if path == "/api/proxy":
            key = {
                "current": "current",
                "timeline": "timeline",
                "health-data": "health",
                "config": "friend_config",
            }[request.url.params["endpoint"]]
        else:
            key = {
                "/api/current": "current",
                "/api/timeline": "timeline",
                "/api/health-data": "health",
                "/api/config": "config",
                "/api/health": "service",
            }[path]
        return httpx.Response(200, json=copy.deepcopy(payloads[key]))

    monkeypatch.setattr(
        "maibot_plugin_live_dashboard.services.api_client.httpx.AsyncClient",
        lambda **kw: original(**kw, transport=httpx.MockTransport(respond)),
    )
    return calls


@pytest.fixture
async def plugin(tmp_path, http_mock):
    instance = create_plugin()
    config = instance.get_default_config()
    config["connection"]["base_url"] = "https://dashboard.test"
    config["connection"]["auth_token"] = "test-token"
    instance.set_plugin_config(config)
    instance.sent = []

    async def rpc(method, plugin_id, payload, **kwargs):
        assert method == "cap.call"
        assert plugin_id == "local.live-dashboard"
        assert payload["capability"] in ("send.text", "send.forward")
        instance.sent.append(copy.deepcopy(payload))
        return {"success": True}

    instance._set_context(
        PluginContext(
            "local.live-dashboard", rpc, PluginPaths(tmp_path / "data", tmp_path / "runtime")
        )
    )
    await instance.on_load()
    yield instance
    await instance.on_unload()


@pytest.fixture
def context():
    return {"stream_id": "stream-123", "user_id": "u1", "group_id": "g1", "platform": "telegram"}
