"""Invoice line amounts (Decimal, ROUND_HALF_UP). Pure Python, no Home Assistant imports."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from .tariff import Tariff
from .zones import Zone

_CENT = Decimal("0.01")
_FOUR = Decimal("0.0001")
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


def _line(key: str, zone: Zone | None, quantity: Decimal, price: Decimal, vat_percent: Decimal) -> Line:
    net = round2(quantity * price)
    vat = round2(net * vat_percent / _HUNDRED)
    return Line(key, zone, quantity, net, vat, net + vat)


def compute_amounts(
    consumption: dict[Zone, Decimal], billable: dict[Zone, Decimal], tariff: Tariff
) -> Totals:
    v = tariff.vat_percent
    prices: dict[tuple[str, Zone], Decimal] = {
        ("energy", Zone.PEAK): tariff.energy_peak,
        ("energy", Zone.OFFPEAK): tariff.energy_offpeak,
        ("distribution", Zone.PEAK): tariff.dist_peak,
        ("distribution", Zone.OFFPEAK): tariff.dist_offpeak,
        ("quality", Zone.PEAK): tariff.quality,
        ("quality", Zone.OFFPEAK): tariff.quality,
        ("oze", Zone.PEAK): tariff.oze,
        ("oze", Zone.OFFPEAK): tariff.oze,
        ("cogeneration", Zone.PEAK): tariff.cogeneration,
        ("cogeneration", Zone.OFFPEAK): tariff.cogeneration,
    }
    lines: list[Line] = []
    for key in ("energy", "distribution", "quality", "oze", "cogeneration"):
        source = consumption if key in ("oze", "cogeneration") else billable
        for zone in (Zone.PEAK, Zone.OFFPEAK):
            lines.append(_line(key, zone, source.get(zone, _ZERO), prices[(key, zone)], v))
    fixed = (
        ("fixed_network", tariff.fixed_network),
        ("subscription", tariff.subscription),
        ("capacity", tariff.capacity),
        ("trade", tariff.trade),
        ("transition", tariff.transition),
    )
    for key, price in fixed:
        lines.append(_line(key, None, Decimal("1"), price, v))

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


def price_now_gross(zone: Zone, tariff: Tariff) -> Decimal:
    if zone == Zone.PEAK:
        net = tariff.energy_peak + tariff.dist_peak
    else:
        net = tariff.energy_offpeak + tariff.dist_offpeak
    net += tariff.quality + tariff.oze + tariff.cogeneration
    return (net * (1 + tariff.vat_percent / _HUNDRED)).quantize(_FOUR, ROUND_HALF_UP)
