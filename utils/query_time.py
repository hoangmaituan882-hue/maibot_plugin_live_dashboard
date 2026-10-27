"""One explicit calendar for queries and diaries; never change process TZ."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo


def utc_now() -> datetime:
    return datetime.now(UTC)


def query_zone(config: dict) -> ZoneInfo:
    name = config.get("time_settings", {}).get("timezone", "").strip()
    legacy = config.get("diary", {}).get("timezone", "Asia/Shanghai")
    return ZoneInfo(name or legacy or "Asia/Shanghai")


def render_zone(config: dict) -> ZoneInfo | None:
    # Standalone upstream renderers without a time configuration retain their contract.
    return query_zone(config) if "time_settings" in config or "diary" in config else None


def resolve_query_date(value: str | None, config: dict, *, now: datetime | None = None) -> str:
    if value is not None and not isinstance(value, str):
        raise ValueError("日期必须是文本")
    value = (value or "").strip()
    relative = {"": 0, "今天": 0, "今日": 0, "昨天": 1, "昨日": 1, "前天": 2, "大前天": 3}
    if value in relative:
        today = (now or utc_now()).astimezone(query_zone(config)).date()
        return (today - timedelta(days=relative[value])).isoformat()
    match = re.fullmatch(r"(\d{4})年(\d{1,2})月(\d{1,2})日?", value)
    if match:
        return date(*map(int, match.groups())).isoformat()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("日期应为 YYYY-MM-DD、今天、昨天或前天")
    return date.fromisoformat(value).isoformat()


def day_bounds(day: str, zone: ZoneInfo) -> tuple[datetime, datetime]:
    value = date.fromisoformat(day)
    return (
        datetime.combine(value, time.min, zone).astimezone(UTC),
        datetime.combine(value + timedelta(days=1), time.min, zone).astimezone(UTC),
    )


def day_offsets(day: str, zone: ZoneInfo) -> list[int]:
    start, end = day_bounds(day, zone)
    return sorted(
        {
            -int(point.astimezone(zone).utcoffset().total_seconds() / 60)
            for point in (start, end - timedelta(microseconds=1))
        }
    )


def parse_timestamp(value, zone: ZoneInfo) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return (result if result.tzinfo else result.replace(tzinfo=zone)).astimezone(UTC)
    except ValueError:
        return None
