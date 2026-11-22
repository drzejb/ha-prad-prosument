"""G12w (Tauron) time zones and Polish public holidays. Pure Python, no Home Assistant imports."""
from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta, timezone
from enum import StrEnum
from zoneinfo import ZoneInfo

import holidays

TZ = ZoneInfo("Europe/Warsaw")

_HOLIDAYS_BY_YEAR: dict[int, holidays.HolidayBase] = {}


class Zone(StrEnum):
    PEAK = "droga"
    OFFPEAK = "tania"


def _holidays(year: int) -> holidays.HolidayBase:
    cached = _HOLIDAYS_BY_YEAR.get(year)
    if cached is None:
        cached = holidays.country_holidays("PL", years=year)
        _HOLIDAYS_BY_YEAR[year] = cached
    return cached


def is_free_day(d: date) -> bool:
    """Saturday, Sunday, Polish public holiday, or 24 December from 2025 on."""
    if d.weekday() >= 5:
        return True
    if d.year >= 2025 and d.month == 12 and d.day == 24:
        return True
    return d in _holidays(d.year)


def zone_at(hour_start: datetime) -> Zone:
    local = hour_start.astimezone(TZ)
    if is_free_day(local.date()):
        return Zone.OFFPEAK
    hour = local.hour
    if 6 <= hour < 13 or 15 <= hour < 22:
        return Zone.PEAK
    return Zone.OFFPEAK


def next_zone_change(now: datetime) -> datetime:
    """Nearest full hour from which the zone differs from zone_at(now) (searches up to 96 h)."""
    current = zone_at(now)
    utc = now.astimezone(timezone.utc)
    candidate = utc.replace(minute=0, second=0, microsecond=0)
    for _ in range(96):
        candidate += timedelta(hours=1)
        if zone_at(candidate) is not current:
            return candidate.astimezone(TZ)
    raise ValueError("no zone change within 96 hours")


def month_key(dt: datetime) -> str:
    return dt.astimezone(TZ).strftime("%Y-%m")


def day_key(dt: datetime) -> str:
    return dt.astimezone(TZ).strftime("%Y-%m-%d")


def _parse_month(key: str) -> tuple[int, int]:
    year, month = key.split("-")
    return int(year), int(month)


def month_start(key: str) -> datetime:
    year, month = _parse_month(key)
    return datetime(year, month, 1, tzinfo=TZ)


def add_months(key: str, n: int) -> str:
    year, month = _parse_month(key)
    index = year * 12 + (month - 1) + n
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def next_month_key(key: str) -> str:
    return add_months(key, 1)


def days_in_month(key: str) -> int:
    year, month = _parse_month(key)
    return calendar.monthrange(year, month)[1]
