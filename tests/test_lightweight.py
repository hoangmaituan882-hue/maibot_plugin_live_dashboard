from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest


def test_only_status_tool_enabled_and_no_prompt_hooks_registered(plugin):
    components = plugin.get_components()
    assert all(c["type"] != "HOOK_HANDLER" for c in components)
    enabled = [c["name"] for c in components if c["type"] == "TOOL" and c["metadata"]["enabled"]]
    assert enabled == ["query_live_dashboard_status"]
    assert plugin.config.commands.inject_tool_prompt is False
    assert (
        "reply_reference"
        in next(c for c in components if c["name"] == enabled[0])["metadata"]["description"]
    )


async def test_legacy_prompt_opt_in_cannot_restore_hook(plugin):
    config = plugin.get_plugin_config_data()
    config["commands"]["inject_tool_prompt"] = True
    await plugin.on_config_update("self", config, "1.0.0")
    assert all(c["type"] != "HOOK_HANDLER" for c in plugin.get_components())
    assert not hasattr(plugin, "inject_live_dashboard_tool_prompt")


async def test_model_receives_facts_and_does_not_send_reply(plugin, context):
    result = await plugin.query_live_dashboard_status_tool(**context)
    facts = json.loads(result["content"])
    assert facts["subject"].startswith("配置面板的主人")
    assert facts["devices"][0]["app_name"] == "Code"
    assert facts["devices"][0]["display_title"] == "插件开发"
    assert "晴天" in facts["devices"][0]["music"]
    assert "状态面板" not in result["content"]
    assert "根据人设" not in result["content"]
    assert plugin.sent == []


async def test_model_facts_filter_hidden_devices_and_recent_activities(plugin, context, payloads):
    payloads["current"]["recent_activities"].append(
        {
            "device_id": "phone",
            "device_name": "手机",
            "app_name": "私密应用",
            "display_title": "隐私窗口",
        }
    )
    config = plugin.get_plugin_config_data()
    config["output_scope"]["device_blacklist_keywords"] = "手机"
    config["output_fields"]["show_recent_activities"] = True
    config["access_control"].update(
        info_blacklist_keywords="Code,插件开发,晴天", info_blacklist_replacement="已隐藏"
    )
    await plugin.on_config_update("self", config, "1.1.0")
    result = await plugin.query_live_dashboard_status_tool(**context)
    for private in ("Code", "插件开发", "手机", "私密应用", "隐私窗口", "晴天"):
        assert private not in result["content"]
    assert "已隐藏" in result["content"]


@pytest.mark.parametrize(
    "toggle,field",
    [
        ("show_app_name", "app_name"),
        ("show_display_title", "display_title"),
        ("show_music", "music"),
        ("show_battery", "battery"),
        ("show_platform", "platform"),
        ("show_last_seen", "last_seen_at"),
    ],
)
async def test_model_facts_obey_field_toggles(plugin, context, toggle, field):
    config = plugin.get_plugin_config_data()
    config["output_fields"][toggle] = False
    await plugin.on_config_update("self", config, "1.1.0")
    facts = json.loads((await plugin.query_live_dashboard_status_tool(**context))["content"])
    assert all(field not in device for device in facts["devices"])


async def test_offline_observations_are_labelled_and_nsfw_masked(plugin, context, payloads):
    payloads["current"]["devices"][0]["display_title"] = "Pornhub explicit"
    config = plugin.get_plugin_config_data()
    config["output_scope"]["include_offline_devices"] = True
    await plugin.on_config_update("self", config, "1.1.0")
    result = await plugin.query_live_dashboard_status_tool(**context)
    facts = json.loads(result["content"])
    offline = next(device for device in facts["devices"] if not device["online"])
    assert "不能视为当前活动" in offline["observation_scope"]
    assert "battery" not in offline and "music" not in offline
    assert "Pornhub" not in result["content"]


def test_plugin_has_no_host_prompt_or_reply_interception():
    path = Path(__file__).resolve().parents[1] / "plugin.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    decorator_names = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "HookHandler" not in decorator_names
    assert "ReplyExtension" not in decorator_names
