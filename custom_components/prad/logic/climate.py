"""Heating degree days and kWh per degree day. Pure Python, no Home Assistant imports."""
from __future__ import annotations

MIN_DEGREE_DAYS_FOR_RATIO = 10


def degree_days(daily_means: list[float], base: float) -> float:
    """Sum of max(0, base - t) over the daily mean temperatures."""
    return sum(max(0.0, base - t) for t in daily_means)


def kwh_per_degree_day(kwh: float, dd: float) -> float | None:
    """Energy per heating degree day, or None when there are too few degree days to be meaningful."""
    if dd < MIN_DEGREE_DAYS_FOR_RATIO:
        return None
    return kwh / dd
