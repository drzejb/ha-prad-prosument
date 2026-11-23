"""G12w (Tauron) time zones and Polish public holidays. Pure Python, no Home Assistant imports."""
from __future__ import annotations

import calendar
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from enum import StrEnum
from typing import Any
from zoneinfo import ZoneInfo

import holidays

TZ = ZoneInfo("Europe/Warsaw")

TARIFFS: tuple[str, ...] = ("G11", "G12", "G12w")
WEEKEND_OFFPEAK = "tania"
AS_WORKDAY = "robocze"
HOLIDAY_AS_WEEKEND = "weekend"

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


def is_public_holiday(d: date) -> bool:
    """Polish public holiday, or 24 December from 2025 on (weekends are not considered)."""
    if d.year >= 2025 and d.month == 12 and d.day == 24:
        return True
    return d in _holidays(d.year)


def is_free_day(d: date) -> bool:
    """Saturday, Sunday, Polish public holiday, or 24 December from 2025 on."""
    return d.weekday() >= 5 or is_public_holiday(d)


def _range_hours(start: int, end: int) -> list[int]:
    hours: list[int] = []
    h = start
    while h != end:
        hours.append(h)
        h = (h + 1) % 24
    return hours


def _canonical(hours: frozenset[int]) -> tuple[tuple[int, int], ...]:
    result: list[tuple[int, int]] = []
    for h in sorted(hours):
        if (h - 1) % 24 in hours:
            continue
        end = (h + 1) % 24
        while end in hours:
            end = (end + 1) % 24
        result.append((h, end))
    return tuple(result)


@dataclass(frozen=True)
class ZoneSchedule:
    id: str
    nazwa: str
    taryfa: str
    tanie_robocze: tuple[tuple[int, int], ...]
    weekend: str
    swieta: str

    def offpeak_hours(self) -> frozenset[int]:
        hours: set[int] = set()
        for start, end in self.tanie_robocze:
            hours.update(_range_hours(start, end))
        return frozenset(hours)

    def key(self) -> tuple:
        return (self.taryfa, tuple(sorted(self.offpeak_hours())), self.weekend, self.swieta)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "nazwa": self.nazwa,
            "taryfa": self.taryfa,
            "tanie_robocze": [[a, b] for a, b in self.tanie_robocze],
            "weekend": self.weekend,
            "swieta": self.swieta,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ZoneSchedule:
        return make_schedule(
            d["id"], d["nazwa"], d["taryfa"],
            [(a, b) for a, b in d["tanie_robocze"]], d["weekend"], d["swieta"],
        )


def make_schedule(
    id: str, nazwa: str, taryfa: str, tanie_robocze: Iterable[tuple[int, int]],
    weekend: str, swieta: str,
) -> ZoneSchedule:
    if taryfa not in TARIFFS:
        raise ValueError("Nieznana taryfa")
    if weekend not in (WEEKEND_OFFPEAK, AS_WORKDAY) or swieta not in (HOLIDAY_AS_WEEKEND, AS_WORKDAY):
        raise ValueError("Nieznane ustawienie weekendu lub świąt")
    ranges = [tuple(r) for r in tanie_robocze]
    hours: set[int] = set()
    for r in ranges:
        if len(r) != 2:
            raise ValueError("Godziny muszą być pełne, od 0 do 23")
        for x in r:
            if type(x) is not int or not 0 <= x <= 23:
                raise ValueError("Godziny muszą być pełne, od 0 do 23")
        if r[0] == r[1]:
            raise ValueError("Godzina początku i końca przedziału nie może być taka sama")
        hours.update(_range_hours(r[0], r[1]))
    if taryfa == "G11":
        if ranges or weekend != AS_WORKDAY or swieta != AS_WORKDAY:
            raise ValueError("Taryfa G11 nie ma strefy taniej")
    else:
        if not ranges:
            raise ValueError("Podaj co najmniej jeden przedział godzin taniej strefy")
        if len(hours) == 24:
            raise ValueError("Strefa tania nie może obejmować całej doby")
    return ZoneSchedule(id, nazwa, taryfa, _canonical(frozenset(hours)), weekend, swieta)


TAURON_G12W = make_schedule("tauron_g12w", "Tauron G12w", "G12w", [(13, 15), (22, 6)], "tania", "weekend")
G11 = make_schedule("g11", "G11 (całodobowa)", "G11", [], "robocze", "robocze")
PRESETS: dict[str, ZoneSchedule] = {"tauron_g12w": TAURON_G12W, "g11": G11}


def zone_at(hour_start: datetime, schedule: ZoneSchedule) -> Zone:
    local = hour_start.astimezone(TZ)
    if not schedule.tanie_robocze:
        return Zone.PEAK
    hourly = Zone.OFFPEAK if local.hour in schedule.offpeak_hours() else Zone.PEAK
    d = local.date()
    if d.weekday() >= 5:
        return Zone.OFFPEAK if schedule.weekend == WEEKEND_OFFPEAK else hourly
    if is_public_holiday(d) and schedule.swieta == HOLIDAY_AS_WEEKEND:
        return Zone.OFFPEAK if schedule.weekend == WEEKEND_OFFPEAK else hourly
    return hourly


def next_zone_change(now: datetime, schedule_for: Callable[[date], ZoneSchedule]) -> datetime | None:
    """Nearest full hour from which the zone differs from the current one (searches up to 240 h)."""
    current = zone_at(now, schedule_for(now.astimezone(TZ).date()))
    candidate = now.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    for _ in range(240):
        candidate += timedelta(hours=1)
        if zone_at(candidate, schedule_for(candidate.astimezone(TZ).date())) is not current:
            return candidate.astimezone(TZ)
    return None


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
