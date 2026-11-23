"""Month segments: runs of days with the same price version or credit ratio and the energy in each.

Pure Python, no Home Assistant imports.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from decimal import Decimal

from .state import Counters, PriceSegment, RatioSegment, to_decimal
from .tariff import Pricing, PriceVersion
from .zones import Zone, days_in_month

COUNTER_KEYS = ("import_peak", "import_offpeak", "export_peak", "export_offpeak")


def month_bounds(month: str) -> tuple[date, date]:
    year, mon = (int(x) for x in month.split("-"))
    return date(year, mon, 1), date(year, mon, days_in_month(month))


def _month_days(month: str) -> list[date]:
    first, last = month_bounds(month)
    return [first + timedelta(days=n) for n in range((last - first).days + 1)]


def price_runs(month: str, pricing: Pricing) -> list[tuple[date, date, PriceVersion]]:
    runs: list[tuple[date, date, PriceVersion]] = []
    for day in _month_days(month):
        version = pricing.price_at(day)
        if runs and runs[-1][2].od == version.od:
            runs[-1] = (runs[-1][0], day, runs[-1][2])
        else:
            runs.append((day, day, version))
    return runs


def ratio_runs(month: str, pricing: Pricing) -> list[tuple[date, date, Decimal]]:
    runs: list[tuple[date, date, Decimal]] = []
    for day in _month_days(month):
        ratio = pricing.ratio_at(day)
        if runs and runs[-1][2] == ratio:
            runs[-1] = (runs[-1][0], day, ratio)
        else:
            runs.append((day, day, ratio))
    return runs


def run_energy(
    counters: Counters,
    days: Mapping[str, Counters],
    runs: Sequence[tuple[date, date]],
    extra: Mapping[date, dict[str, float]] | None = None,
) -> list[dict[str, Decimal]]:
    """Energy of each run for the four counters.

    Days that have a daily counter contribute exactly; the rest of the month counter (never negative)
    is spread over the days without a counter, proportionally to their number in each run.
    """
    available = [dict.fromkeys(COUNTER_KEYS, 0.0) for _ in runs]
    missing = [0] * len(runs)
    added = [dict.fromkeys(COUNTER_KEYS, 0.0) for _ in runs]
    for i, (first, last) in enumerate(runs):
        day = first
        while day <= last:
            counter = days.get(day.isoformat())
            if counter is None:
                missing[i] += 1
            else:
                for key in COUNTER_KEYS:
                    available[i][key] += getattr(counter, key)
            if extra is not None and day in extra:
                for key in COUNTER_KEYS:
                    added[i][key] += extra[day][key]
            day += timedelta(days=1)
    total_missing = sum(missing)
    result: list[dict[str, Decimal]] = []
    shares = [dict.fromkeys(COUNTER_KEYS, 0.0) for _ in runs]
    if total_missing > 0:
        for key in COUNTER_KEYS:
            rest = max(0.0, getattr(counters, key) - sum(a[key] for a in available))
            for i in range(len(runs)):
                shares[i][key] = rest * missing[i] / total_missing
    for i in range(len(runs)):
        result.append({key: to_decimal(available[i][key] + shares[i][key] + added[i][key]) for key in COUNTER_KEYS})
    return result


def _zones(energy: dict[str, Decimal], prefix: str) -> dict[Zone, Decimal]:
    return {Zone.PEAK: energy[f"{prefix}_peak"], Zone.OFFPEAK: energy[f"{prefix}_offpeak"]}


def freeze_segments(
    month: str, counters: Counters, days: Mapping[str, Counters], pricing: Pricing
) -> tuple[list[PriceSegment], list[RatioSegment]]:
    pruns = price_runs(month, pricing)
    rruns = ratio_runs(month, pricing)
    p_energy = run_energy(counters, days, [(a, b) for a, b, _ in pruns])
    r_energy = run_energy(counters, days, [(a, b) for a, b, _ in rruns])
    price = [
        PriceSegment(a, b, version, _zones(e, "import"), _zones(e, "export"))
        for (a, b, version), e in zip(pruns, p_energy)
    ]
    ratio = [RatioSegment(a, b, value, _zones(e, "export")) for (a, b, value), e in zip(rruns, r_energy)]
    return price, ratio
