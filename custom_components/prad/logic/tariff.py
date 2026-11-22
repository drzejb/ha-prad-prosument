"""Tariff model (net prices). Pure Python, no Home Assistant imports."""
from __future__ import annotations

from dataclasses import dataclass, fields
from decimal import Decimal


@dataclass(frozen=True)
class Tariff:
    # PLN/kWh net
    energy_peak: Decimal
    energy_offpeak: Decimal
    dist_peak: Decimal
    dist_offpeak: Decimal
    quality: Decimal
    oze: Decimal
    cogeneration: Decimal
    # PLN/month net
    fixed_network: Decimal
    subscription: Decimal
    capacity: Decimal
    trade: Decimal
    transition: Decimal
    vat_percent: Decimal
    credit_ratio: Decimal
    version: str

    def to_dict(self) -> dict[str, str]:
        return {f.name: str(getattr(self, f.name)) for f in fields(self)}

    @classmethod
    def from_dict(cls, d: dict[str, object]) -> Tariff:
        values: dict[str, object] = {}
        for f in fields(cls):
            if f.name in d and d[f.name] is not None:
                raw = d[f.name]
                values[f.name] = str(raw) if f.name == "version" else Decimal(str(raw))
            else:
                values[f.name] = getattr(DEFAULT_TARIFF, f.name)
        return cls(**values)  # type: ignore[arg-type]


DEFAULT_TARIFF = Tariff(
    energy_peak=Decimal("0.627"),
    energy_offpeak=Decimal("0.418"),
    dist_peak=Decimal("0.3298"),
    dist_offpeak=Decimal("0.0512"),
    quality=Decimal("0.0332"),
    oze=Decimal("0.0073"),
    cogeneration=Decimal("0.003"),
    fixed_network=Decimal("10.86"),
    subscription=Decimal("4.56"),
    capacity=Decimal("24.05"),
    trade=Decimal("0"),
    transition=Decimal("0"),
    vat_percent=Decimal("23"),
    credit_ratio=Decimal("0.8"),
    version="2026-09 faktura",
)
