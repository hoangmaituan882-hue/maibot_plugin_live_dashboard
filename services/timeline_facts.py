"""Compact, filtered timeline evidence for the native Planner and Replyer."""

from __future__ import annotations

import json
import math

from ..diary.facts import build_facts
from ..utils.config_parser import get_bool_value, get_text_value, parse_list_config
from ..utils.nsfw_filter import is_nsfw
from ..utils.query_time import parse_timestamp, query_zone
from ..utils.text_utils import clean_text, mask_sensitive_text


def render_timeline_facts(
    payload: dict, config: dict, *, subject="配置面板的主人；主人身份以人设说明为准"
) -> tuple[str, int]:
    day = payload["date"]
    evidence = build_facts(payload, day, config, for_diary=False)
    zone = query_zone(config)
    hidden = parse_list_config(get_text_value(config, "info_blacklist_keywords"), to_lower=True)
    replacement = get_text_value(config, "info_blacklist_replacement", "已隐藏") or "已隐藏"
    white = parse_list_config(get_text_value(config, "device_whitelist_keywords"), to_lower=True)
    black = parse_list_config(get_text_value(config, "device_blacklist_keywords"), to_lower=True)
    names = {}
    for row in payload["segments"]:
        if isinstance(row, dict) and row.get("device_id") and row.get("device_name"):
            names[str(row["device_id"])] = clean_text(row["device_name"])
    usage = []
    summary = payload.get("summary")
    if isinstance(summary, dict):
        for did, apps in summary.items():
            known = names.get(str(did))
            name = known or str(did)
            if (
                ((white or black) and not known)
                or (white and not any(x in name.lower() for x in white))
                or any(x in name.lower() for x in black)
            ):
                continue
            if not isinstance(apps, dict):
                continue
            for app, duration in apps.items():
                try:
                    minutes = float(duration)
                except (TypeError, ValueError):
                    continue
                if not math.isfinite(minutes) or not 0 <= minutes <= 2880:
                    continue
                app = clean_text(app)[:120]
                app = (
                    replacement
                    if is_nsfw(app, "")
                    else mask_sensitive_text(app, hidden, replacement)
                )
                usage.append(
                    {
                        "device": mask_sensitive_text(name, hidden, replacement)[:80],
                        "app_name": app
                        if get_bool_value(config, "show_app_name", True)
                        else "应用名已隐藏",
                        "minutes": minutes,
                        "source": "服务端应用时长汇总",
                    }
                )
    if not usage:
        usage = [
            {
                "device": x["device_name"][:80],
                "app_name": x["app"],
                "minutes": x["seconds"] / 60,
                "source": "有效活动片段整理",
            }
            for x in evidence["apps"]
        ]
    usage.sort(key=lambda x: -x["minutes"])
    segments = sorted(evidence["segments"], key=lambda x: (x["start"], x["device_id"]))
    chosen = (
        segments
        if len(segments) <= 20
        else [segments[round(i * (len(segments) - 1) / 19)] for i in range(20)]
    )
    activities = []
    for row in chosen:
        activity = {
            "device": row["device_name"][:80],
            "started_at": parse_timestamp(row["start"], zone).astimezone(zone).isoformat(),
            "ended_at": parse_timestamp(row["end"], zone).astimezone(zone).isoformat(),
        }
        if get_bool_value(config, "show_app_name", True):
            activity["app_name"] = row["app"][:120]
        if "title" in row:
            activity["display_title"] = row["title"][:200]
        activities.append(activity)
    result = {
        "kind": "活动时间线",
        "subject": subject[:200],
        "date": day,
        "timezone": str(zone),
        "query_freshness": dict(getattr(payload, "cache_info", {})),
        "app_usage": usage[:30],
        "usage_count": len(usage),
        "usage_truncated": len(usage) > 30,
        "activities": activities,
        "source_segment_count": len(payload["segments"]),
        "normalized_segment_count": len(segments),
        "details_truncated": len(segments) > len(activities)
        or bool(payload.get("truncated") or payload.get("has_more"))
        or len(payload["segments"]) > 20000,
        "observed_seconds": evidence["observed_seconds"],
        "observation_scope": "仅为所选日期的设备记录；不是当前状态，不能证明主人全程参与或覆盖全天。",
        "duration_semantics": "应用时长按设备统计，多设备可能重叠；observed_seconds是有效片段区间并集，不代表人的总工作或游戏时长。",
        "notes": evidence["notes"],
    }
    # Keep one bounded tool result even if names or titles are exceptionally long.
    while len(json.dumps(result, ensure_ascii=False)) > 18000:
        if result["activities"]:
            result["activities"].pop()
            result["details_truncated"] = True
        elif result["app_usage"]:
            result["app_usage"].pop()
            result["usage_truncated"] = True
        else:
            break
    result["shown_segments"] = len(result["activities"])
    return json.dumps(result, ensure_ascii=False), len(segments)
