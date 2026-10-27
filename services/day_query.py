"""Query natural days even when the upstream accepts only fixed-offset 24h windows."""

from __future__ import annotations

from copy import deepcopy
from datetime import timedelta

from ..utils.query_time import day_bounds, day_offsets, parse_timestamp
from .api_client import InvalidResponseError
from .response_cache import ApiPayload


async def fetch_day(getter, day: str, zone, *, kind="segments") -> dict:
    parts = []
    for offset in day_offsets(day, zone):
        payload = await getter(day, offset)
        if payload.get("date") != day or not isinstance(payload.get(kind), list):
            raise InvalidResponseError("日期或日期数据结构不正确")
        parts.append(payload)
    if len(parts) == 1:
        return parts[0]
    start, end = day_bounds(day, zone)
    selected = {}
    for part in parts:
        for item in part[kind]:
            if not isinstance(item, dict):
                continue
            row = deepcopy(item)
            if kind == "records":
                stamp = parse_timestamp(row.get("recorded_at"), zone)
                if not stamp or not start <= stamp < end:
                    continue
                key = tuple(
                    str(row.get(k, "")) for k in ("device_id", "type", "recorded_at", "end_time")
                )
            else:
                left = parse_timestamp(row.get("started_at"), zone)
                right = parse_timestamp(row.get("ended_at"), zone)
                if not left:
                    continue
                if right is None:
                    try:
                        minutes = float(row.get("duration_minutes", 0))
                        if not 0 <= minutes <= 2880:
                            continue
                        right = left + timedelta(minutes=minutes)
                    except (ValueError, TypeError, OverflowError):
                        continue
                if (
                    right < left
                    or right < start
                    or (right == start and left < start)
                    or left >= end
                ):
                    continue
                left, right = max(left, start), min(right, end)
                if right < left:
                    continue
                row.update(
                    started_at=left.isoformat(),
                    ended_at=right.isoformat(),
                    duration_minutes=max(0, round((right - left).total_seconds() / 60)),
                )
                key = tuple(
                    str(row.get(k, ""))
                    for k in ("device_id", "app_id", "app_name", "started_at", "display_title")
                )
                if key in selected and selected[key]["ended_at"] >= row["ended_at"]:
                    continue
            selected[key] = row
    merged = {"date": day, kind: list(selected.values())}
    if kind == "records":
        merged[kind].sort(key=lambda x: x["recorded_at"], reverse=True)
    if kind == "segments":
        merged[kind].sort(key=lambda x: x["started_at"])
        summary = {}
        for row in merged[kind]:
            apps = summary.setdefault(str(row.get("device_id", "")), {})
            name = str(row.get("app_name", ""))
            apps[name] = apps.get(name, 0) + row["duration_minutes"]
        merged["summary"] = summary
    for flag in ("truncated", "has_more"):
        if any(part.get(flag) for part in parts):
            merged[flag] = True
    info = [dict(getattr(part, "cache_info", {})) for part in parts]
    return ApiPayload(
        merged,
        {
            "hit": all(p.get("hit", False) for p in info),
            "coalesced": any(p.get("coalesced", False) for p in info),
            "age_seconds": max(p.get("age_seconds", 0) for p in info),
            "ttl_seconds": min(p.get("ttl_seconds", 0) for p in info),
            "parts": info,
        },
    )
