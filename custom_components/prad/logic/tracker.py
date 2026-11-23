"""Meter tracker: turns cumulative meter readings into per-hour deltas. Pure Python, no Home Assistant imports."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .zones import TZ


@dataclass
class TrackerRef:
    """Last accepted reading: state of the meter at the full hour `time`."""

    time: datetime
    values: tuple[float, ...]


@dataclass(frozen=True)
class HourDelta:
    start: datetime  # full hour in TZ
    deltas: tuple[float, ...]


@dataclass(frozen=True)
class AdvanceResult:
    hours: tuple[HourDelta, ...]
    rejected: bool
    reason: str | None
    new_ref: TrackerRef | None


def _floor_hour_utc(moment: datetime) -> datetime:
    """Floor to the full hour in UTC (Warsaw offsets are whole hours, so this is DST-safe)."""
    utc = moment.astimezone(timezone.utc)
    return utc.replace(minute=0, second=0, microsecond=0)


def floor_hour(moment: datetime) -> datetime:
    """Floor to the full hour, returned in TZ."""
    return _floor_hour_utc(moment).astimezone(TZ)


def advance(
    ref: TrackerRef | None,
    now: datetime,
    values: tuple[float, ...] | None,
    limit_per_hour: float,
    *,
    signed: bool = False,
    limit_negative: float | None = None,
) -> AdvanceResult:
    """Advance the tracker to `now`, splitting any gap equally into full hours.

    With `signed=True` (a single net meter) a negative delta is valid; it is rejected only when
    it exceeds `n * limit_negative` (default `limit_per_hour`) in absolute value.
    """
    if values is None:
        return AdvanceResult((), False, None, ref)

    t_utc = _floor_hour_utc(now)
    t = t_utc.astimezone(TZ)

    if ref is None:
        return AdvanceResult((), False, None, TrackerRef(t, values))

    ref_utc = ref.time.astimezone(timezone.utc)
    if t_utc <= ref_utc:
        return AdvanceResult((), False, None, ref)

    n = int((t_utc - ref_utc) / timedelta(hours=1))
    diffs = tuple(v - r for v, r in zip(values, ref.values))
    new_ref = TrackerRef(t, values)

    if signed:
        neg_limit = limit_per_hour if limit_negative is None else limit_negative
        if any(d > n * limit_per_hour or d < -n * neg_limit for d in diffs):
            return AdvanceResult((), True, "over_limit", new_ref)
    else:
        if any(d < 0 for d in diffs):
            return AdvanceResult((), True, "negative", new_ref)
        if any(d > n * limit_per_hour for d in diffs):
            return AdvanceResult((), True, "over_limit", new_ref)

    per_hour = tuple(d / n for d in diffs)
    hours = tuple(
        HourDelta((ref_utc + timedelta(hours=k)).astimezone(TZ), per_hour) for k in range(n)
    )
    return AdvanceResult(hours, False, None, new_ref)
