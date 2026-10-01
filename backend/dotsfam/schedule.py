"""Cron evaluation in IANA time zones."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter


def valid_timezone(value: str) -> bool:
    try:
        ZoneInfo(value)
        return True
    except (ZoneInfoNotFoundError, ValueError):
        return False


def validate_cron(cron: str) -> str:
    cron = cron.strip()
    if len(cron.split()) != 5:
        raise ValueError("Use a five-field cron expression: minute hour day month weekday.")
    if not croniter.is_valid(cron):
        raise ValueError("Invalid cron expression.")
    return cron


def next_cron_run(cron: str, timezone: str | None, after_ms: int) -> int:
    """Next run (epoch ms) strictly after `after_ms`, evaluated in `timezone`."""
    cron = validate_cron(cron)
    tz_name = timezone or "UTC"
    if not valid_timezone(tz_name):
        raise ValueError("Unknown time zone.")
    start = datetime.fromtimestamp(after_ms / 1000, tz=UTC).astimezone(ZoneInfo(tz_name))
    nxt = croniter(cron, start).get_next(datetime)
    return int(nxt.timestamp() * 1000)


def describe(at_ms: int, timezone: str | None) -> str:
    when = datetime.fromtimestamp(at_ms / 1000, tz=UTC).astimezone(ZoneInfo(timezone or "UTC"))
    return when.strftime("%d %b %Y, %H:%M %Z")
