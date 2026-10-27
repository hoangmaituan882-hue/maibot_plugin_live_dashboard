"""Filtered device observations for the native planner, without a prewritten chat reply."""

from __future__ import annotations

import json
from typing import Any

from ..utils.config_parser import get_bool_value, get_int_value, get_text_value, parse_list_config
from ..utils.nsfw_filter import is_nsfw
from ..utils.query_time import query_zone
from ..utils.text_utils import NSFW_MASK_TEXT, clean_text, mask_sensitive_text
from .message_renderer import (
    _format_battery,
    _format_music,
    _friendly_app_name,
    _is_online,
    _normalize_display_title,
    select_devices_for_render,
)


def render_status_facts(payload: dict[str, Any], config: dict[str, Any]) -> tuple[str, int]:
    selected, online_count, total_count = select_devices_for_render(payload, config)
    keywords = parse_list_config(get_text_value(config, "info_blacklist_keywords"), to_lower=True)
    replacement = (
        get_text_value(config, "info_blacklist_replacement", "不想让你看到我在干什么喵~")
        or "已隐藏"
    )

    def masked(value: Any) -> str:
        return mask_sensitive_text(clean_text(value), keywords, replacement)

    facts: dict[str, Any] = {
        "subject": "配置面板的主人；主人身份以人设说明为准",
        "online_devices": online_count,
        "total_devices": total_count,
        "devices": [],
        "timezone": str(query_zone(config)),
    }
    cache_info = getattr(payload, "cache_info", None)
    if isinstance(cache_info, dict):
        facts["query_freshness"] = dict(cache_info)
    if get_bool_value(config, "show_server_time", False) and payload.get("server_time"):
        facts["server_time"] = payload["server_time"]
    if get_bool_value(config, "show_viewer_count", False) and isinstance(
        payload.get("viewer_count"), int
    ):
        facts["viewer_count"] = payload["viewer_count"]

    for item in selected:
        online = _is_online(item)
        app = clean_text(item.get("app_name"))
        extra = item.get("extra") if isinstance(item.get("extra"), dict) else {}
        device: dict[str, Any] = {
            "name": clean_text(item.get("device_name")) or "未知设备",
            "online": online,
            "observation_scope": "当前上报" if online else "离线设备的最后上报，不能视为当前活动",
        }
        if get_bool_value(config, "show_platform", True):
            device["platform"] = clean_text(item.get("platform")) or "unknown"
        if get_bool_value(config, "show_app_name", True):
            device["app_name"] = masked(_friendly_app_name(app))
        if item.get("status_text"):
            device["reported_activity"] = masked(item["status_text"])
        if get_bool_value(config, "show_display_title", True):
            title = _normalize_display_title(clean_text(item.get("display_title")))
            music = extra.get("music") if isinstance(extra.get("music"), dict) else {}
            music_title = clean_text(music.get("title"))
            redundant = bool(music_title and music_title.lower() in title.lower()) or bool(
                app and app.lower() == title.lower()
            )
            if title and not redundant:
                device["display_title"] = NSFW_MASK_TEXT if is_nsfw(app, title) else masked(title)
        if online and get_bool_value(config, "show_battery", True):
            device["battery"] = _format_battery(extra)
        if online and get_bool_value(config, "show_music", True):
            device["music"] = masked(_format_music(extra))
        if get_bool_value(config, "show_last_seen", True):
            device["last_seen_at"] = clean_text(item.get("last_seen_at")) or None
        facts["devices"].append(device)

    if get_bool_value(config, "show_recent_activities", False):
        allowed_ids = {clean_text(item.get("device_id")) for item in selected} - {""}
        allowed_names = {clean_text(item.get("device_name")) for item in selected} - {""}
        recent = []
        activities = payload.get("recent_activities")
        for item in activities if isinstance(activities, list) else []:
            if not isinstance(item, dict):
                continue
            device_id, device_name = (
                clean_text(item.get("device_id")),
                clean_text(item.get("device_name")),
            )
            if (device_id and device_id not in allowed_ids) or (
                not device_id and device_name not in allowed_names
            ):
                continue
            activity: dict[str, Any] = {
                "device": device_name,
                "started_at": clean_text(item.get("started_at")) or None,
            }
            if get_bool_value(config, "show_app_name", True):
                activity["app_name"] = masked(item.get("app_name"))
            if item.get("status_text"):
                activity["reported_activity"] = masked(item["status_text"])
            if get_bool_value(config, "show_display_title", True):
                title = _normalize_display_title(clean_text(item.get("display_title")))
                if title:
                    activity["display_title"] = (
                        NSFW_MASK_TEXT
                        if is_nsfw(clean_text(item.get("app_name")), title)
                        else masked(title)
                    )
            recent.append(activity)
            if len(recent) >= get_int_value(
                config, "recent_activities_max", 5, min_value=1, max_value=20
            ):
                break
        facts["recent_activities"] = recent
    return json.dumps(facts, ensure_ascii=False), len(selected)
