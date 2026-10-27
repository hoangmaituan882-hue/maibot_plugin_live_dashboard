from __future__ import annotations

import asyncio
import copy
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import httpx
import pytest
from maibot_plugin_live_dashboard.config import LiveDashboardConfig
from maibot_plugin_live_dashboard.diary.facts import build_facts, day_bounds
from maibot_plugin_live_dashboard.diary.service import DiaryError, DiaryService
from pydantic import ValidationError


@pytest.fixture
def diary_config():
    config = LiveDashboardConfig().model_dump()
    config["connection"]["base_url"] = "https://dashboard.test"
    config["connection"]["auth_token"] = "must-not-be-persisted"
    config["diary"].update(enabled=True, generation_stream_id="owner-private", admin_users="qq:123")
    return config


@pytest.fixture
async def diary(tmp_path, diary_config, http_mock):
    generate = AsyncMock(
        return_value={
            "success": True,
            "text": "今天的设备记录里主要是Code。记录之外的时段没有足够资料。",
        }
    )
    ctx = SimpleNamespace(
        maisaka=SimpleNamespace(reply=SimpleNamespace(generate=generate)),
        logger=SimpleNamespace(warning=lambda *args: None),
    )
    clock = [datetime(2026, 10, 28, 1, tzinfo=UTC)]
    service = DiaryService(diary_config, tmp_path, ctx, now=lambda: clock[0])
    service.test_clock = clock
    yield service
    await service.close()


@pytest.mark.parametrize(
    "zone,day,hours",
    [
        ("Asia/Shanghai", "2026-10-27", 24),
        ("America/New_York", "2026-03-08", 23),
        ("America/New_York", "2026-11-01", 25),
    ],
)
def test_natural_day_boundaries(zone, day, hours):
    start, end = day_bounds(day, ZoneInfo(zone))
    assert (end - start).total_seconds() == hours * 3600


@pytest.mark.parametrize(
    "field,value",
    [
        ("timezone", "invalid/zone"),
        ("generation_time", "25:00"),
        ("generation_time", "9:5"),
        ("retention_days", 0),
    ],
)
def test_bad_configuration_rejected(field, value):
    with pytest.raises(ValidationError):
        LiveDashboardConfig.model_validate({"diary": {field: value}})


def segment(start, end, *, device="pc", app="Code", title=""):
    return {
        "device_id": device,
        "device_name": device,
        "app_name": app,
        "started_at": start,
        "ended_at": end,
        "display_title": title,
    }


def test_fact_filtering_before_storage_and_no_overlap_inflation(diary_config):
    diary_config["output_scope"]["device_blacklist_keywords"] = "secret"
    diary_config["access_control"]["info_blacklist_keywords"] = "银行"
    payload = {
        "date": "2026-10-27",
        "segments": [
            segment(
                "2026-10-27T10:00:00+08:00", "2026-10-27T11:00:00+08:00", title="private title"
            ),
            segment(
                "2026-10-27T10:30:00+08:00", "2026-10-27T11:00:00+08:00", device="phone", app="QQ"
            ),
            segment(
                "2026-10-27T10:30:00+08:00",
                "2026-10-27T11:00:00+08:00",
                device="secret",
                app="secretapp",
            ),
            segment("2026-10-27T12:00:00+08:00", "2026-10-27T12:15:00+08:00", app="银行"),
        ],
        "summary": {"secret": {"should-never-survive": 12345}},
    }
    result = build_facts(payload, "2026-10-27", diary_config)
    text = json.dumps(result, ensure_ascii=False)
    assert "secretapp" not in text and "private title" not in text and "银行" not in text
    assert "should-never-survive" not in text
    assert result["observed_seconds"] == 75 * 60  # Phone and PC overlap.
    assert len(result["apps"]) == 3


def test_all_segments_not_limited_by_chat_display(diary_config):
    start = datetime(2026, 10, 27, tzinfo=ZoneInfo("Asia/Shanghai"))
    rows = [
        segment(
            (start + timedelta(minutes=i)).isoformat(),
            (start + timedelta(minutes=i + 1)).isoformat(),
            app=f"app{i % 2}",
        )
        for i in range(100)
    ]
    facts = build_facts({"date": "2026-10-27", "segments": rows}, "2026-10-27", diary_config)
    assert len(facts["segments"]) == 100
    assert facts["observed_seconds"] == 6000


def test_cross_midnight_clipping_and_duplicate_intervals(diary_config):
    row = segment("2026-10-26T23:50:00+08:00", "2026-10-27T00:10:00+08:00")
    result = build_facts({"date": "2026-10-27", "segments": [row, row]}, "2026-10-27", diary_config)
    assert result["observed_seconds"] == 600
    assert len(result["segments"]) == 1


async def test_generation_coalescing_read_only_and_restart(diary, http_mock, tmp_path):
    results = await asyncio.gather(*(diary.generate("2026-10-27") for _ in range(8)))
    assert all(x["revision"] == 1 for x in results)
    assert len(http_mock) == 1
    diary.ctx.maisaka.reply.generate.assert_awaited_once()
    assert "private title" not in json.dumps(results)
    assert "must-not-be-persisted" not in json.dumps(results)
    assert diary.read("昨天")[0]["status"] == "generated"
    assert diary.read("前天")[0]["status"] == "missing"
    assert len(http_mock) == 1
    await diary.close()
    reloaded = DiaryService(diary.config, tmp_path, diary.ctx, now=diary.now)
    try:
        assert reloaded.read("昨天")[0]["revision"] == 1
        assert len(http_mock) == 1
    finally:
        await reloaded.close()


async def test_unchanged_regeneration_skips_model_and_changed_data_revises(
    diary, payloads, http_mock
):
    initial = await diary.generate("昨天")
    assert (await diary.generate("昨天", force=True))["revision"] == 1
    assert diary.ctx.maisaka.reply.generate.await_count == 1
    payloads["timeline"]["segments"][1]["duration_minutes"] = 75
    revised = await diary.generate("昨天", force=True)
    assert revised["revision"] == 2
    assert revised["input_hash"] != initial["input_hash"]
    assert len(http_mock) == 3
    assert diary.store.db.execute("SELECT COUNT(*) FROM revisions").fetchone()[0] == 1
    assert await diary.delete("昨天") == 1
    assert diary.store.db.execute("SELECT COUNT(*) FROM revisions").fetchone()[0] == 0


async def test_empty_data_and_network_failure_are_distinct(diary, payloads):
    payloads["timeline"]["segments"] = []
    entry = await diary.generate("昨天")
    assert entry["status"] == "no_data"
    diary.ctx.maisaka.reply.generate.assert_not_called()
    await diary.delete("昨天")
    diary._client._request_json = AsyncMock(side_effect=httpx.ConnectError("never print secret"))
    with pytest.raises(DiaryError, match="无法取得有效时间线"):
        await diary.generate("昨天")
    assert diary.read("昨天")[0]["status"] == "missing"
    assert diary.store.job(diary.source, str(diary.zone), "2026-10-27")[0] == 1


@pytest.mark.parametrize(
    "result",
    [
        {"success": False},
        {"success": True, "text": "主人完成了项目，心情非常开心。"},
        {"success": True, "text": "主人打游戏了99999分钟。"},
        {"success": True, "text": "https://bad.example"},
    ],
)
async def test_invalid_model_output_falls_back_to_facts(diary, result):
    diary.ctx.maisaka.reply.generate.return_value = result
    entry = await diary.generate("昨天")
    assert entry["status"] == "facts_only"
    assert entry["prose"] == ""
    assert entry["facts"]["segments"]


async def test_stop_cancels_inflight_generation_without_committing(diary):
    entered = asyncio.Event()

    async def blocked(**kwargs):
        entered.set()
        await asyncio.Event().wait()

    diary.ctx.maisaka.reply.generate.side_effect = blocked
    pending = asyncio.create_task(diary.generate("昨天"))
    await entered.wait()
    store = diary.store
    await diary.close()
    with pytest.raises(DiaryError, match="停止"):
        await pending
    import sqlite3

    db = sqlite3.connect(diary.directory / "diaries.sqlite3")
    assert db.execute("SELECT COUNT(*) FROM entries").fetchone()[0] == 0
    db.close()
    assert store is not None


async def test_privacy_changes_invalidate_old_diary(diary, tmp_path):
    await diary.generate("昨天")
    config = copy.deepcopy(diary.config)
    config["access_control"]["info_blacklist_keywords"] = "Code"
    await diary.close()
    changed = DiaryService(config, tmp_path, diary.ctx, now=diary.now)
    try:
        assert changed.read("昨天")[0]["status"] == "missing"
        assert changed.store.db.execute("SELECT COUNT(*) FROM entries").fetchone()[0] == 0
    finally:
        await changed.close()


async def test_source_isolation_and_retention_cleans_revisions(diary, tmp_path):
    await diary.generate("昨天")
    diary.test_clock[0] += timedelta(days=100)
    assert diary.store.db.execute("SELECT COUNT(*) FROM entries").fetchone()[0] == 0
    assert diary.store.db.execute("SELECT COUNT(*) FROM revisions").fetchone()[0] == 0


@pytest.mark.parametrize(
    "value", ["today", "2026-10-28", "2026-10-29", "2026-02-30", "../../../secret"]
)
def test_unfinished_or_invalid_dates_rejected(diary, value):
    with pytest.raises(DiaryError):
        diary.resolve_date(value)


async def test_scheduler_respects_activation_time_and_no_duplicate(diary, http_mock):
    diary.test_clock[0] = datetime(2026, 10, 27, 3, tzinfo=UTC)
    _ = diary.store  # Activates on Oct 27, without generating earlier dates.
    await diary.run_due()
    assert not http_mock
    diary.test_clock[0] = datetime(2026, 10, 27, 16, 10, tzinfo=UTC)  # Oct 28 00:10.
    await diary.run_due()
    assert not http_mock
    diary.test_clock[0] += timedelta(minutes=6)
    await diary.run_due()
    await diary.run_due()
    assert len(http_mock) == 1
    assert diary.read("昨天")[0]["status"] == "generated"


async def test_scheduler_bounded_network_retries(diary, payloads):
    diary.test_clock[0] = datetime(2026, 10, 27, 3, tzinfo=UTC)
    _ = diary.store
    diary.test_clock[0] += timedelta(days=1)
    # Wrong date simulates an unusable upstream response.
    payloads["timeline"]["date"] = "2020-01-01"
    for _ in range(8):
        await diary.run_due()
        diary.test_clock[0] += timedelta(hours=1)
    assert diary.store.job(diary.source, str(diary.zone), "2026-10-27")[0] == 3


async def test_optional_summary_404_and_partial_summary_metadata(diary):
    diary.options.update(reuse_upstream_summary=True, include_titles=True)
    # _can_reuse uses those options; production config is fixed for a generation.
    timeline = {
        "date": "2026-10-27",
        "segments": [segment("2026-10-27T10:00:00+08:00", "2026-10-27T11:00:00+08:00")],
    }

    async def respond(path, params):
        if path == "/api/timeline":
            return timeline
        return {
            "date": "2026-10-27",
            "summary": "今天设备记录主要与编程有关。",
            "generated_at": "2026-10-27T13:00:00Z",
        }

    diary._client = SimpleNamespace(_request_json=AsyncMock(side_effect=respond), close=AsyncMock())
    entry = await diary.generate("昨天")
    assert entry["status"] == "upstream_partial"
    assert "不保证包含之后" in diary.render([entry])
    diary.ctx.maisaka.reply.generate.assert_not_called()


async def test_disabled_diary_creates_no_storage_and_no_http(plugin, http_mock):
    result = await plugin.query_owner_diary_tool(stream_id="anything")
    assert not result["success"]
    assert plugin._diary._store is None
    assert not http_mock


async def test_command_permissions_read_only_tool_and_export(plugin, context, payloads):
    config = plugin.get_plugin_config_data()
    config["diary"].update(enabled=True, admin_users="telegram:u1", allowed_streams="stream-123")
    await plugin.on_config_update("self", config, "1.2.0")
    plugin._diary.now = lambda: datetime(2026, 10, 28, 1, tzinfo=UTC)
    assert (await plugin.owner_diary_command(**context, text="/主人日记 生成 昨天"))[0]
    assert (await plugin.query_owner_diary_tool(**context, date="昨天"))["success"]
    denied = await plugin._diary_action("删除", {**context, "user_id": "u2"}, date="昨天")
    assert not denied["success"]
    assert "未授权" in denied["content"]
    exported = await plugin._diary_action("导出", context, date="昨天")
    assert "Markdown" in exported["content"]
    assert "插件开发" not in exported["content"]  # Titles disabled by default.
    assert (await plugin._diary_action("清空", context))["success"]
    assert "尚无" in (await plugin.query_owner_diary_tool(**context))["content"]
    assert not plugin._diary.authorized(stream="", user="u1", platform="qq", admin=True)


async def test_export_cleanup_and_deleted_days_do_not_reappear(diary):
    diary.test_clock[0] = datetime(2026, 10, 27, 1, tzinfo=UTC)
    _ = diary.store
    diary.test_clock[0] += timedelta(days=1)
    await diary.generate("昨天")
    paths = diary.export("昨天")
    assert len(paths) == 1 and paths[0].exists()
    assert "结构化活动事实" in paths[0].read_text(encoding="utf-8")
    await diary.delete("昨天")
    assert not paths[0].exists()
    await diary.run_due()
    assert diary.read("昨天")[0]["status"] == "missing"
    # Explicit generation is still possible after a deletion.
    await diary.generate("昨天")
    assert diary.read("昨天")[0]["status"] == "generated"
    await diary.delete(None)
    await diary.run_due()
    assert diary.read("昨天")[0]["status"] == "missing"


async def test_different_panel_cannot_read_previous_owner(diary, tmp_path):
    await diary.generate("昨天")
    config = copy.deepcopy(diary.config)
    config["connection"]["base_url"] = "https://another.test"
    await diary.close()
    changed = DiaryService(config, tmp_path, diary.ctx, now=diary.now)
    try:
        assert changed.read("昨天")[0]["status"] == "missing"
    finally:
        await changed.close()


async def test_one_day_retention_includes_yesterday(diary):
    diary.options["retention_days"] = 1
    assert (await diary.generate("昨天"))["date"] == "2026-10-27"
    with pytest.raises(DiaryError):
        diary.read("前天")


async def test_optional_missing_summary_falls_back_to_native_replyer(diary, payloads):
    from maibot_plugin_live_dashboard.services.api_client import HttpApiError

    diary.options.update(reuse_upstream_summary=True, include_titles=True)

    async def request(path, params):
        if path == "/api/daily-summary":
            raise HttpApiError(404, path)
        return copy.deepcopy(payloads["timeline"])

    diary._client = SimpleNamespace(_request_json=AsyncMock(side_effect=request), close=AsyncMock())
    assert (await diary.generate("昨天"))["status"] == "generated"
    diary.ctx.maisaka.reply.generate.assert_awaited_once()


async def test_filtered_data_cannot_be_bypassed_by_upstream_summary(diary, payloads):
    diary.options.update(reuse_upstream_summary=True, include_titles=True, device_ids="pc")
    diary._client = SimpleNamespace(
        _request_json=AsyncMock(return_value=payloads["timeline"]), close=AsyncMock()
    )
    await diary.generate("昨天")
    assert [call.args[0] for call in diary._client._request_json.call_args_list] == [
        "/api/timeline"
    ]


async def test_missing_generation_session_uses_fact_archive(diary):
    diary.options["generation_stream_id"] = ""
    assert (await diary.generate("昨天"))["status"] == "facts_only"
    diary.ctx.maisaka.reply.generate.assert_not_called()


async def test_wrong_server_date_is_not_saved(diary, payloads):
    payloads["timeline"]["date"] = "2020-01-01"
    with pytest.raises(DiaryError, match="日期"):
        await diary.generate("昨天")
    assert diary.read("昨天")[0]["status"] == "missing"


async def test_sdk_reply_capability_wire_contract_and_no_send(diary, tmp_path):
    from maibot_sdk import PluginContext, PluginPaths

    calls = []

    async def rpc(method, plugin_id, payload, **kwargs):
        calls.append(payload)
        assert payload["capability"] == "maisaka.reply.generate"
        assert payload["args"]["stream_id"] == "owner-private"
        material = json.loads(payload["args"]["reference"])
        assert material["date"] == "2026-10-27"
        assert "display_title" not in payload["args"]["reference"]
        return {"success": True, "text": "设备记录主要是Code。"}

    diary.ctx = PluginContext(
        "local.live-dashboard", rpc, PluginPaths(tmp_path / "data", tmp_path / "runtime")
    )
    assert (await diary.generate("昨天"))["status"] == "generated"
    assert len(calls) == 1


async def test_large_day_model_material_is_valid_json_and_includes_late_activity(diary, payloads):
    start = datetime(2026, 10, 27, tzinfo=ZoneInfo("Asia/Shanghai"))
    payloads["timeline"]["segments"] = [
        segment(
            (start + timedelta(minutes=i)).isoformat(),
            (start + timedelta(minutes=i + 1)).isoformat(),
            app=f"App{i}",
        )
        for i in range(100)
    ]
    await diary.generate("昨天")
    material = json.loads(diary.ctx.maisaka.reply.generate.call_args.kwargs["reference"])
    assert len(material["segments"]) == 30
    assert material["segments"][0]["app"] == "App0"
    assert material["segments"][-1]["app"] == "App99"


async def test_bad_archive_directory_does_not_break_status_queries(plugin, monkeypatch, context):
    def broken(*args, **kwargs):
        raise PermissionError

    monkeypatch.setattr("maibot_plugin_live_dashboard.diary.service.DiaryStore", broken)
    config = plugin.get_plugin_config_data()
    config["diary"]["enabled"] = True
    await plugin.on_config_update("self", config, "1.2.0")
    assert plugin._diary is None
    assert (await plugin.query_live_dashboard_status_tool(**context))["success"]


def test_read_date_ranges_are_bounded(diary):
    assert len(diary.read("2026-10-21", "2026-10-27")) == 7
    with pytest.raises(DiaryError):
        diary.read("2026-10-20", "2026-10-27")
    with pytest.raises(DiaryError):
        diary.read("2026-10-27", "2026-10-21")


async def test_diary_respects_global_blacklist(plugin, context, http_mock):
    config = plugin.get_plugin_config_data()
    config["diary"].update(enabled=True, admin_users="telegram:u1")
    config["access_control"]["user_blacklist_senders"] = "u1"
    await plugin.on_config_update("self", config, "1.2.0")
    result = await plugin._diary_action("生成", context)
    assert not result["success"]
    assert not http_mock
