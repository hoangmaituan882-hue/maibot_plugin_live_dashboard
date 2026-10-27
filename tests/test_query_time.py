from __future__ import annotations

import copy
import json
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from maibot_plugin_live_dashboard.config import LiveDashboardConfig
from maibot_plugin_live_dashboard.services.day_query import fetch_day
from maibot_plugin_live_dashboard.services.response_cache import ApiPayload
from maibot_plugin_live_dashboard.services.timeline_facts import render_timeline_facts
from maibot_plugin_live_dashboard.utils import query_time
from maibot_plugin_live_dashboard.utils.time_formatter import format_time_text
from pydantic import ValidationError


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, "2026-10-28"),
        ("今天", "2026-10-28"),
        ("昨天", "2026-10-27"),
        ("前天", "2026-10-26"),
        ("大前天", "2026-10-25"),
        ("2026年10月2日", "2026-10-02"),
        ("2026-10-03", "2026-10-03"),
    ],
)
def test_relative_dates_use_selected_calendar(value, expected):
    config = LiveDashboardConfig().model_dump()
    now = datetime(2026, 10, 27, 17, 30, tzinfo=UTC)
    assert query_time.resolve_query_date(value, config, now=now) == expected


@pytest.mark.parametrize("value", ["2026-02-30", "2026-1-2", "2026-W44-1", "昨天下午", {}, 0])
def test_ambiguous_or_invalid_dates_are_rejected(value):
    with pytest.raises(ValueError):
        query_time.resolve_query_date(value, {})


def test_timezone_precedence_legacy_and_invalid_config():
    config = LiveDashboardConfig.model_validate(
        {"diary": {"timezone": "Europe/Berlin"}}
    ).model_dump()
    assert str(query_time.query_zone(config)) == "Europe/Berlin"
    config["time_settings"]["timezone"] = "America/Los_Angeles"
    assert str(query_time.query_zone(config)) == "America/Los_Angeles"
    assert (
        query_time.resolve_query_date("昨天", config, now=datetime(2026, 10, 27, 17, tzinfo=UTC))
        == "2026-10-26"
    )
    with pytest.raises(ValidationError):
        LiveDashboardConfig.model_validate({"time_settings": {"timezone": "invalid/zone"}})


@pytest.mark.parametrize(
    "day,offsets",
    [
        ("2026-01-01", [300]),
        ("2026-07-01", [240]),
        ("2026-03-08", [240, 300]),
        ("2026-11-01", [240, 300]),
    ],
)
def test_offsets_follow_queried_day_not_current_season(day, offsets):
    assert query_time.day_offsets(day, ZoneInfo("America/New_York")) == offsets


def test_time_display_uses_explicit_zone_and_keeps_naive_local_times():
    assert format_time_text("2026-10-26T18:00:00Z", ZoneInfo("Asia/Shanghai")) == "10-27 02:00:00"
    assert (
        format_time_text("2026-10-26T18:00:00Z", ZoneInfo("America/Los_Angeles"))
        == "10-26 11:00:00"
    )
    assert format_time_text("2026-10-27T08:30:00", ZoneInfo("Asia/Shanghai")) == "10-27 08:30:00"


async def test_timeline_tool_is_compact_and_uses_all_application_summary(plugin, payloads, context):
    timeline = payloads["timeline"]
    timeline["segments"] = []
    start = datetime(2026, 10, 27, 0, tzinfo=UTC)
    for i in range(100):
        timeline["segments"].append(
            {
                "device_id": "pc",
                "device_name": "工作电脑",
                "app_name": f"App{i % 2}",
                "started_at": (start + timedelta(minutes=i)).isoformat(),
                "duration_minutes": 1,
            }
        )
    timeline["summary"] = {"pc": {"App0": 50, "App1": 50, "最后应用": 3}}
    config = plugin.get_plugin_config_data()
    config["commands"]["timeline_max_segments"] = 5
    await plugin.on_config_update("self", config, "1.2.1")
    result = await plugin.query_live_dashboard_timeline_tool(date="2026-10-27", **context)
    assert result["success"]
    facts = json.loads(result["content"])
    assert facts["date"] == "2026-10-27" and facts["timezone"] == "Asia/Shanghai"
    assert facts["source_segment_count"] == 100
    assert facts["normalized_segment_count"] == 100
    assert len(facts["activities"]) == 20 and facts["details_truncated"]
    assert facts["activities"][-1]["ended_at"].startswith("2026-10-27T09:40")
    assert "最后应用" in {a["app_name"] for a in facts["app_usage"]}
    assert "Live Dashboard 时间线" not in result["content"]
    assert plugin.sent == []


async def test_command_and_timeline_tool_share_cache(plugin, context, http_mock):
    command = await plugin.query_timeline(**context, text="/视奸时间线 2026-10-27")
    assert command[0] and "Live Dashboard 时间线" in command[1]
    result = await plugin.query_live_dashboard_timeline_tool(date="2026-10-27", **context)
    assert json.loads(result["content"])["query_freshness"]["hit"]
    assert sum(r.url.path == "/api/timeline" for r in http_mock) == 1


async def test_relative_query_dates_and_friend_health_use_unified_timezone(
    plugin, payloads, context, http_mock, monkeypatch
):
    monkeypatch.setattr(query_time, "utc_now", lambda: datetime(2026, 10, 27, 17, tzinfo=UTC))
    payloads["timeline"]["date"] = payloads["health"]["date"] = "2026-10-26"
    config = plugin.get_plugin_config_data()
    config["time_settings"]["timezone"] = "America/Los_Angeles"
    await plugin.on_config_update("self", config, "1.2.1")
    assert (await plugin.query_live_dashboard_timeline_tool(date="昨天", **context))["success"]
    assert (await plugin.query_live_dashboard_health_tool(date="昨天", **context))["success"]
    assert (
        await plugin.query_friend_dashboard_status_tool(
            name="小明", action="timeline", date="昨天", **context
        )
    )["success"]
    assert (
        await plugin.query_friend_dashboard_status_tool(
            name="小明", action="health", date="昨天", **context
        )
    )["success"]
    dated = [r for r in http_mock if "date" in r.url.params]
    assert len(dated) == 4
    assert all(r.url.params["date"] == "2026-10-26" and r.url.params["tz"] == "420" for r in dated)
    assert str(plugin._diary.zone) == "America/Los_Angeles"


async def test_global_timezone_changes_display_dates_and_diary_evidence(plugin, context, payloads):
    config = plugin.get_plugin_config_data()
    config["time_settings"]["timezone"] = "Asia/Shanghai"
    config["output_fields"]["show_server_time"] = True
    payloads["current"]["server_time"] = payloads["current"]["devices"][0]["last_seen_at"] = (
        "2026-10-26T18:00:00Z"
    )
    await plugin.on_config_update("self", config, "1.2.1")
    result = await plugin.query_live_dashboard(**context)
    assert result[1].count("10-27 02:00:00") == 2
    config["time_settings"]["timezone"] = "America/Los_Angeles"
    await plugin.on_config_update("self", config, "1.2.1")
    result = await plugin.query_live_dashboard(**context)
    assert result[1].count("10-26 11:00:00") == 2


def test_timeline_privacy_filters_apply_to_summary_and_activities(payloads):
    config = LiveDashboardConfig().model_dump()
    config["output_scope"]["device_blacklist_keywords"] = "私密"
    config["access_control"]["info_blacklist_keywords"] = "Code,插件开发"
    payload = copy.deepcopy(payloads["timeline"])
    payload["segments"].append(
        {
            "device_id": "secret",
            "device_name": "私密电脑",
            "app_name": "SecretApp",
            "started_at": "2026-10-27T10:00:00+08:00",
            "duration_minutes": 30,
        }
    )
    payload["summary"]["secret"] = {"SecretApp": 30}
    payload["summary"]["unresolved"] = {"UnknownSecret": 10}
    text, _ = render_timeline_facts(payload, config)
    assert not any(
        word in text for word in ("SecretApp", "UnknownSecret", "Code", "插件开发", "私密电脑")
    )


def test_timeline_hidden_app_and_title_fields(payloads):
    config = LiveDashboardConfig().model_dump()
    config["output_fields"].update(show_app_name=False, show_display_title=False)
    text, _ = render_timeline_facts(payloads["timeline"], config)
    assert "Code" not in text and "插件开发" not in text
    assert all(
        "app_name" not in row and "display_title" not in row
        for row in json.loads(text)["activities"]
    )


@pytest.mark.parametrize("kind", ["segments", "records"])
async def test_dst_fetch_deduplicates_and_keeps_only_real_local_day(kind):
    zone = ZoneInfo("America/New_York")
    calls = []

    async def get(day, offset):
        calls.append(offset)
        if kind == "segments":
            rows = [
                {
                    "device_id": "pc",
                    "app_name": "Code",
                    "started_at": "2026-11-01T05:00:00Z",
                    "ended_at": "2026-11-01T07:00:00Z",
                },
                {
                    "device_id": "pc",
                    "app_name": "QQ",
                    "started_at": "2026-11-02T05:30:00Z",
                    "ended_at": "2026-11-02T06:00:00Z",
                },
            ]
        else:
            rows = [
                {
                    "device_id": "pc",
                    "type": "steps",
                    "value": 1,
                    "recorded_at": "2026-11-01T05:00:00Z",
                },
                {
                    "device_id": "pc",
                    "type": "steps",
                    "value": 2,
                    "recorded_at": "2026-11-01T07:00:00Z",
                },
                {
                    "device_id": "pc",
                    "type": "steps",
                    "value": 3,
                    "recorded_at": "2026-11-02T05:30:00Z",
                },
            ]
        return ApiPayload({"date": day, kind: rows}, {"hit": True, "ttl_seconds": 30})

    payload = await fetch_day(get, "2026-11-01", zone, kind=kind)
    assert calls == [240, 300]
    assert payload.cache_info["hit"]
    if kind == "segments":
        assert len(payload[kind]) == 1 and payload["summary"]["pc"]["Code"] == 120
    else:
        assert len(payload[kind]) == 2 and payload[kind][0]["value"] == 2
