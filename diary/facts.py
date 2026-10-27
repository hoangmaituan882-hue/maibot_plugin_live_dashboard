"""Normalize dashboard evidence before persistence or model access."""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from datetime import timedelta
from typing import Any

from ..utils.config_parser import get_bool_value, get_text_value, parse_list_config
from ..utils.nsfw_filter import is_nsfw
from ..utils.query_time import day_bounds, parse_timestamp, query_zone
from ..utils.text_utils import clean_text


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def union_seconds(intervals: list[tuple[float, float]]) -> int:
    end = float("-inf")
    total = 0.0
    for left, right in sorted(intervals):
        total += max(0.0, right - max(left, end))
        end = max(end, right)
    return round(total)


def build_facts(payload: dict, day: str, config: dict, *, for_diary: bool = True) -> dict:
    zone = query_zone(config)
    left, right = day_bounds(day, zone)
    if payload.get("date") != day or not isinstance(payload.get("segments"), list):
        raise ValueError("时间线日期或片段结构无效")
    allowed_ids = (
        parse_list_config(config.get("diary", {}).get("device_ids", "")) if for_diary else []
    )
    white = parse_list_config(get_text_value(config, "device_whitelist_keywords"), to_lower=True)
    black = parse_list_config(get_text_value(config, "device_blacklist_keywords"), to_lower=True)
    hidden = parse_list_config(get_text_value(config, "info_blacklist_keywords"), to_lower=True)
    titles = (
        config.get("diary", {}).get("include_titles", False) if for_diary else True
    ) and get_bool_value(config, "show_display_title", True)
    show_apps = get_bool_value(config, "show_app_name", True)
    intervals: dict[tuple[str, str], list] = defaultdict(list)
    devices: dict[str, str] = {}
    observations: list[dict] = []
    notes = ["设备活动不等于主人全程参与；没有记录的时段不能推断活动或睡眠。"]
    rows = payload["segments"]
    if len(rows) > 20000 or payload.get("truncated") or payload.get("has_more"):
        notes.append("上游数据或本次输入存在截断，不能作为完整全天记录。")
    invalid = 0
    seen: set[str] = set()
    for row in rows[:20000]:
        if not isinstance(row, dict):
            invalid += 1
            continue
        did = clean_text(row.get("device_id"))[:200]
        name = clean_text(row.get("device_name"))[:200] or did
        if not did or (allowed_ids and did not in allowed_ids):
            continue
        if (white and not any(x in name.lower() for x in white)) or any(
            x in name.lower() for x in black
        ):
            continue
        if any(x in name.lower() for x in hidden):
            name = "已隐藏设备"
        public_id = digest(did)[:16]
        raw_app = clean_text(row.get("app_name"))[:200]
        app = raw_app if show_apps else "活动应用（已隐藏）"
        title = clean_text(row.get("display_title"))[:300] if titles else ""
        if any(x in raw_app.lower() for x in hidden) or is_nsfw(raw_app, title):
            app, title = "已隐藏应用", ""
        if any(x in title.lower() for x in hidden):
            title = ""
        # Keep no raw title, app_id, status prose, health information or secrets.
        start = parse_timestamp(row.get("started_at"), zone)
        end = parse_timestamp(row.get("ended_at"), zone)
        if not start:
            invalid += 1
            continue
        if end is None:
            duration = row.get("duration_minutes", 0)
            try:
                duration = float(duration)
                if not math.isfinite(duration) or not 0 <= duration <= 2880:
                    raise ValueError
                end = start + timedelta(minutes=duration)
            except (ValueError, TypeError, OverflowError):
                invalid += 1
                continue
        if end < start:
            invalid += 1
            continue
        if end < left or (end == left and start < left) or start >= right:
            continue
        start, end = max(start, left), min(end, right)
        if end < start:
            continue
        observation = {
            "device_id": public_id,
            "device_name": name,
            "app": app or "未知应用",
            "start": start.isoformat(),
            "end": end.isoformat(),
        }
        if title:
            observation["title"] = title
        key = digest(observation)
        if key in seen:
            continue
        seen.add(key)
        observations.append(observation)
        devices[public_id] = name
        intervals[public_id, observation["app"]].append((start.timestamp(), end.timestamp()))
    observations.sort(key=lambda x: (x["device_id"], x["start"], x["end"]))
    merged: list[dict] = []
    for item in observations:
        if (
            merged
            and all(merged[-1].get(k) == item.get(k) for k in ("device_id", "app", "title"))
            and item["start"] <= merged[-1]["end"]
        ):
            merged[-1]["end"] = max(merged[-1]["end"], item["end"])
        else:
            merged.append(dict(item))
    stats = [
        {"device_id": did, "device_name": devices[did], "app": app, "seconds": union_seconds(spans)}
        for (did, app), spans in intervals.items()
    ]
    stats.sort(key=lambda x: (-x["seconds"], x["device_id"], x["app"]))
    if invalid:
        notes.append(f"有 {invalid} 条无效片段未参与整理。")
    if not merged:
        notes.append("没有可用的活动片段；原始汇总不能替代缺失的片段证据。")
    return {
        "date": day,
        "timezone": str(zone),
        "day_start": left.isoformat(),
        "day_end": right.isoformat(),
        "segments": merged,
        "apps": stats,
        "observed_seconds": union_seconds([span for spans in intervals.values() for span in spans]),
        "coverage": "仅覆盖接口返回的允许片段，不能证明覆盖全天",
        "notes": notes,
    }


def facts_text(facts: dict) -> str:
    lines = [f"# 主人日记 · {facts['date']}", f"时区：{facts['timezone']}", facts["coverage"]]
    for item in facts["apps"][:40]:
        lines.append(
            f"- {item['device_name']} / {item['app']}：{item['seconds'] / 60:g} 分钟（设备记录）"
        )
    if len(facts["apps"]) > 40:
        lines.append("更多应用见结构化归档；此处只显示前40项。")
    if not facts["apps"]:
        lines.append("当天没有足够的设备活动记录。")
    lines.extend(f"- {note}" for note in facts["notes"])
    return "\n".join(lines)
