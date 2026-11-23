"""Invoice line amounts (Decimal, ROUND_HALF_UP). Pure Python, no Home Assistant imports."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal

from .tariff import PriceVersion, Tariff
from .zones import Zone

_CENT = Decimal("0.01")
_FOUR = Decimal("0.0001")
_MILLI = Decimal("0.001")
_ZERO = Decimal("0")
_HUNDRED = Decimal("100")


def round2(x: Decimal) -> Decimal:
    return x.quantize(_CENT, ROUND_HALF_UP)


@dataclass(frozen=True)
class Line:
    key: str
    zone: Zone | None
    quantity: Decimal
    net: Decimal
    vat: Decimal
    gross: Decimal
    segment: int = 0


@dataclass(frozen=True)
class Totals:
    lines: tuple[Line, ...]
    peak_gross: Decimal
    offpeak_gross: Decimal
    fixed_gross: Decimal
    energy_gross: Decimal
    distribution_gross: Decimal
    net: Decimal
    vat: Decimal
    gross: Decimal


def _line(
    key: str, zone: Zone | None, quantity: Decimal, price: Decimal, vat_percent: Decimal, segment: int = 0
) -> Line:
    net = round2(quantity * price)
    vat = round2(net * vat_percent / _HUNDRED)
    return Line(key, zone, quantity, net, vat, net + vat, segment)


def split_largest_remainder(total: int, weights: list[Decimal]) -> list[int]:
    """Split a whole number of units by weights (largest remainder, ties -> earlier index)."""
    if total < 0 or not weights or any(w < 0 for w in weights):
        raise ValueError("Niepoprawny podział")
    weight_sum = sum(weights, _ZERO)
    if weight_sum == 0:
        return [total] + [0] * (len(weights) - 1)
    shares = [Decimal(total) * w / weight_sum for w in weights]
    parts = [int(sh.to_integral_value(ROUND_FLOOR)) for sh in shares]
    left = total - sum(parts)
    order = sorted(range(len(weights)), key=lambda i: (-(shares[i] - parts[i]), i))
    for i in order[:left]:
        parts[i] += 1
    return parts


def split_kwh(total: Decimal, weights: list[Decimal]) -> list[Decimal]:
    """Split kWh: whole part by largest remainder, the fraction goes to the heaviest segment."""
    if total < 0:
        raise ValueError("Niepoprawny podział")
    whole = total.to_integral_value(ROUND_FLOOR)
    frac = total - whole
    parts = [Decimal(p) for p in split_largest_remainder(int(whole), weights)]
    if frac > 0:
        if sum(weights, _ZERO) > 0:
            heaviest = max(range(len(weights)), key=lambda i: (weights[i], -i))
        else:
            heaviest = 0
        parts[heaviest] += frac
    return parts


def split_proportional(total: Decimal, weights: list[Decimal]) -> list[Decimal]:
    """Proportional split to 0.001 kWh; the last segment takes the remainder."""
    if len(weights) == 1:
        return [total]
    weight_sum = sum(weights, _ZERO)
    if weight_sum == 0:
        return [total] + [_ZERO] * (len(weights) - 1)
    parts = [(total * w / weight_sum).quantize(_MILLI, ROUND_HALF_UP) for w in weights[:-1]]
    parts.append(total - sum(parts, _ZERO))
    return parts


@dataclass(frozen=True)
class SegmentAmounts:
    price: PriceVersion | Tariff  # only price fields and vat_percent are read
    days: int
    consumption: dict[Zone, Decimal]
    billable: dict[Zone, Decimal]


def compute_amounts_segments(segments: Sequence[SegmentAmounts], month_days: int) -> Totals:
    lines: list[Line] = []
    for index, seg in enumerate(segments):
        t = seg.price
        v = t.vat_percent
        prices: dict[tuple[str, Zone], Decimal] = {
            ("energy", Zone.PEAK): t.energy_peak,
            ("energy", Zone.OFFPEAK): t.energy_offpeak,
            ("distribution", Zone.PEAK): t.dist_peak,
            ("distribution", Zone.OFFPEAK): t.dist_offpeak,
            ("quality", Zone.PEAK): t.quality,
            ("quality", Zone.OFFPEAK): t.quality,
            ("oze", Zone.PEAK): t.oze,
            ("oze", Zone.OFFPEAK): t.oze,
            ("cogeneration", Zone.PEAK): t.cogeneration,
            ("cogeneration", Zone.OFFPEAK): t.cogeneration,
        }
        for key in ("energy", "distribution", "quality", "oze", "cogeneration"):
            source = seg.consumption if key in ("oze", "cogeneration") else seg.billable
            for zone in (Zone.PEAK, Zone.OFFPEAK):
                lines.append(_line(key, zone, source.get(zone, _ZERO), prices[(key, zone)], v, index))
        days, month = Decimal(seg.days), Decimal(month_days)
        fixed = (
            ("fixed_network", t.fixed_network),
            ("subscription", t.subscription),
            ("capacity", t.capacity),
            ("trade", t.trade),
            ("transition", t.transition),
        )
        for key, price in fixed:
            net = round2(price * days / month)
            vat = round2(net * v / _HUNDRED)
            lines.append(Line(key, None, days / month, net, vat, net + vat, index))

    def total(pred) -> Decimal:
        return sum((ln.gross for ln in lines if pred(ln)), _ZERO)

    return Totals(
        lines=tuple(lines),
        peak_gross=total(lambda ln: ln.zone == Zone.PEAK),
        offpeak_gross=total(lambda ln: ln.zone == Zone.OFFPEAK),
        fixed_gross=total(lambda ln: ln.zone is None),
        energy_gross=total(lambda ln: ln.key in ("energy", "trade")),
        distribution_gross=total(lambda ln: ln.key not in ("energy", "trade")),
        net=sum((ln.net for ln in lines), _ZERO),
        vat=sum((ln.vat for ln in lines), _ZERO),
        gross=sum((ln.gross for ln in lines), _ZERO),
    )


def compute_amounts(
    consumption: dict[Zone, Decimal], billable: dict[Zone, Decimal], tariff: Tariff
) -> Totals:
    return compute_amounts_segments([SegmentAmounts(tariff, 1, consumption, billable)], 1)


def price_now_gross(zone: Zone, tariff: Tariff) -> Decimal:
    if zone == Zone.PEAK:
        net = tariff.energy_peak + tariff.dist_peak
    else:
        net = tariff.energy_offpeak + tariff.dist_offpeak
    net += tariff.quality + tariff.oze + tariff.cogeneration
    return (net * (1 + tariff.vat_percent / _HUNDRED)).quantize(_FOUR, ROUND_HALF_UP)
