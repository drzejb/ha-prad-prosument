"""Monthly net-metering settlement (old rules, ratio 0.8) with storage and expiry.

Pure Python, no Home Assistant imports.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from decimal import ROUND_HALF_UP, Decimal

from .amounts import split_largest_remainder, split_proportional
from .zones import Zone, add_months

_ZONES = (Zone.PEAK, Zone.OFFPEAK)
_ZERO = Decimal(0)


@dataclass(frozen=True)
class Inflow:
    month: str
    zone: Zone
    amount: Decimal
    valid_until: str  # last month of validity = add_months(month, 12)


@dataclass(frozen=True)
class Settlement:
    billable: dict[Zone, Decimal]
    consumption: dict[Zone, Decimal]
    export: dict[Zone, Decimal]
    credit: dict[Zone, Decimal]
    cross_moved: Decimal
    storage_used: Decimal
    new_inflows: tuple[Inflow, ...]
    expired: Decimal
    inflows_after: tuple[Inflow, ...]
    storage_after: Decimal


def round_kwh(value: Decimal) -> Decimal:
    return value.quantize(Decimal("1"), ROUND_HALF_UP)


def credit_final(
    export: dict[Zone, Decimal], segments: Sequence[tuple[Decimal, dict[Zone, Decimal]]]
) -> dict[Zone, Decimal]:
    """Credit of a closed month; ``export`` must already be rounded to whole kWh."""
    result: dict[Zone, Decimal] = {}
    for z in _ZONES:
        parts = split_largest_remainder(int(export[z]), [weights[z] for _, weights in segments])
        result[z] = round_kwh(sum((Decimal(p) * ratio for p, (ratio, _) in zip(parts, segments)), _ZERO))
    return result


def credit_running(
    export: dict[Zone, Decimal], segments: Sequence[tuple[Decimal, dict[Zone, Decimal]]]
) -> dict[Zone, Decimal]:
    """Credit of a running month (no rounding)."""
    result: dict[Zone, Decimal] = {}
    for z in _ZONES:
        parts = split_proportional(export[z], [weights[z] for _, weights in segments])
        result[z] = sum((p * ratio for p, (ratio, _) in zip(parts, segments)), _ZERO)
    return result


def settle(
    month: str,
    consumption: dict[Zone, Decimal],
    export: dict[Zone, Decimal],
    inflows: tuple[Inflow, ...],
    credit_ratio: Decimal,
    storage_before_cross: bool,
    final: bool,
    *,
    credit: dict[Zone, Decimal] | None = None,
) -> Settlement:
    """Settle one month.

    Order: (1) same-zone use of credit; (2) cross-zone move and storage use, in the order given by
    ``storage_before_cross``; (3) expiry, only when ``final`` and only of the pre-existing inflows
    (new inflows never expire in the month they are created).

    The caller must not pass this month's own inflows in ``inflows``: settling a month twice would
    duplicate them. With ``final=False`` stale inflows are not removed, so they stay in
    ``storage_after`` until the next final close.
    """
    if final:
        consumption = {z: round_kwh(consumption[z]) for z in _ZONES}
        export = {z: round_kwh(export[z]) for z in _ZONES}
    else:
        consumption = {z: consumption[z] for z in _ZONES}
        export = {z: export[z] for z in _ZONES}

    if credit is None:
        credit = {z: export[z] * credit_ratio for z in _ZONES}
        if final:
            credit = {z: round_kwh(credit[z]) for z in _ZONES}

    own = {z: min(consumption[z], credit[z]) for z in _ZONES}
    missing = {z: consumption[z] - own[z] for z in _ZONES}
    surplus = {z: credit[z] - own[z] for z in _ZONES}

    cross_moved = _ZERO
    storage_used = _ZERO
    # Working copy of old inflows, FIFO: oldest month first, PEAK before OFFPEAK.
    old = sorted(inflows, key=lambda i: (i.month, 0 if i.zone is Zone.PEAK else 1))

    def cross_step() -> None:
        nonlocal cross_moved
        for z in _ZONES:
            other = Zone.OFFPEAK if z is Zone.PEAK else Zone.PEAK
            moved = min(surplus[other], missing[z])
            surplus[other] -= moved
            missing[z] -= moved
            cross_moved += moved

    def storage_step() -> None:
        nonlocal storage_used, old
        updated: list[Inflow] = []
        for inflow in old:
            usable = inflow.month < month <= inflow.valid_until
            if usable:
                for z in _ZONES:
                    take = min(inflow.amount, missing[z])
                    if take > 0:
                        inflow = replace(inflow, amount=inflow.amount - take)
                        missing[z] -= take
                        storage_used += take
            updated.append(inflow)
        old = updated

    if storage_before_cross:
        storage_step()
        cross_step()
    else:
        cross_step()
        storage_step()

    new_inflows = tuple(
        Inflow(month, z, surplus[z], add_months(month, 12)) for z in _ZONES if surplus[z] > 0
    )

    expired = _ZERO
    if final:
        kept: list[Inflow] = []
        for inflow in old:
            if inflow.valid_until <= month and inflow.amount > 0:
                expired += inflow.amount
            else:
                kept.append(inflow)
        old = kept

    inflows_after = tuple(i for i in old if i.amount > 0) + new_inflows
    storage_after = sum((i.amount for i in inflows_after), _ZERO)

    return Settlement(
        billable=dict(missing),
        consumption=consumption,
        export=export,
        credit=credit,
        cross_moved=cross_moved,
        storage_used=storage_used,
        new_inflows=new_inflows,
        expired=expired,
        inflows_after=inflows_after,
        storage_after=storage_after,
    )
