"""Tariff model (net prices). Pure Python, no Home Assistant imports."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, fields
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from .zones import TAURON_G12W, TZ, ZoneSchedule


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


PRICE_FIELDS: tuple[str, ...] = (
    "energy_peak", "energy_offpeak", "dist_peak", "dist_offpeak", "quality", "oze",
    "cogeneration", "fixed_network", "subscription", "capacity", "trade", "transition", "vat_percent",
)
EXAMPLE_PRICE_NAME = "Tauron G12w 2026 (przykład – sprawdź z fakturą)"
EMPTY_PRICE_NAME = "Cennik – uzupełnij ceny"


def _dec(value: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except InvalidOperation as err:
        raise ValueError(f"Zła wartość liczbowa: {value!r}") from err


@dataclass(frozen=True)
class PriceVersion:
    od: date
    nazwa: str
    taryfa: str
    strefy: ZoneSchedule
    energy_peak: Decimal
    energy_offpeak: Decimal
    dist_peak: Decimal
    dist_offpeak: Decimal
    quality: Decimal
    oze: Decimal
    cogeneration: Decimal
    fixed_network: Decimal
    subscription: Decimal
    capacity: Decimal
    trade: Decimal
    transition: Decimal
    vat_percent: Decimal

    def __post_init__(self) -> None:
        if self.taryfa != self.strefy.taryfa:
            raise ValueError("Taryfa cennika nie zgadza się z rozkładem stref")
        for name in PRICE_FIELDS:
            value = getattr(self, name)
            if not value.is_finite() or value < 0:
                raise ValueError("Cena nie może być ujemna")
        if not 0 <= self.vat_percent <= 100:
            raise ValueError("VAT musi być między 0 a 100")
        if self.nazwa.strip() == "":
            raise ValueError("Podaj nazwę cennika")

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "od": self.od.isoformat(),
            "nazwa": self.nazwa,
            "taryfa": self.taryfa,
            "strefy": self.strefy.to_dict(),
        }
        for name in PRICE_FIELDS:
            d[name] = str(getattr(self, name))
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PriceVersion:
        prices = {name: _dec(d[name]) for name in PRICE_FIELDS}
        return cls(
            od=date.fromisoformat(d["od"]),
            nazwa=d["nazwa"],
            taryfa=d["taryfa"],
            strefy=ZoneSchedule.from_dict(d["strefy"]),
            **prices,
        )


@dataclass(frozen=True)
class OpustEntry:
    od: date
    wspolczynnik: Decimal

    def __post_init__(self) -> None:
        if not self.wspolczynnik.is_finite() or not 0 <= self.wspolczynnik <= 1:
            raise ValueError("Współczynnik opustu musi być między 0 a 1")

    def to_dict(self) -> dict[str, str]:
        return {"od": self.od.isoformat(), "wspolczynnik": str(self.wspolczynnik)}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> OpustEntry:
        return cls(date.fromisoformat(d["od"]), _dec(d["wspolczynnik"]))


class PriceListError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def example_price(od: date) -> PriceVersion:
    t = DEFAULT_TARIFF
    return PriceVersion(
        od=od, nazwa=EXAMPLE_PRICE_NAME, taryfa="G12w", strefy=TAURON_G12W,
        **{name: getattr(t, name) for name in PRICE_FIELDS},
    )


def empty_price(od: date, strefy: ZoneSchedule) -> PriceVersion:
    values = {name: Decimal("0") for name in PRICE_FIELDS}
    values["vat_percent"] = Decimal("23")
    return PriceVersion(od=od, nazwa=EMPTY_PRICE_NAME, taryfa=strefy.taryfa, strefy=strefy, **values)


def _pick(items: Sequence[Any], day: date) -> Any:
    chosen = items[0]
    for item in items:
        if item.od <= day:
            chosen = item
        else:
            break
    return chosen


def price_at(versions: Sequence[PriceVersion], day: date) -> PriceVersion:
    return _pick(versions, day)


def ratio_at(entries: Sequence[OpustEntry], day: date) -> Decimal:
    return _pick(entries, day).wspolczynnik


def next_change(items: Sequence[PriceVersion] | Sequence[OpustEntry], day: date) -> date | None:
    later = [i.od for i in items if i.od > day]
    return min(later) if later else None


def validate_prices(versions: Sequence[PriceVersion]) -> None:
    if not versions:
        raise PriceListError("empty", "Musi zostać co najmniej jeden cennik")
    ordered = sorted(versions, key=lambda v: v.od)
    for a, b in zip(ordered, ordered[1:]):
        if a.od == b.od:
            raise PriceListError("duplicate_date", "Cennik od tej daty już istnieje")
        if a.strefy.key() != b.strefy.key() and b.od.day != 1:
            raise PriceListError(
                "zone_change_not_first", "Zmiana taryfy lub stref tylko od pierwszego dnia miesiąca"
            )


def validate_opust(entries: Sequence[OpustEntry]) -> None:
    if not entries:
        raise PriceListError("empty", "Musi zostać co najmniej jeden współczynnik")
    dates = [e.od for e in entries]
    if len(set(dates)) != len(dates):
        raise PriceListError("duplicate_date", "Współczynnik od tej daty już istnieje")


def with_version(
    versions: Sequence[PriceVersion], old_od: date | None, new: PriceVersion | None
) -> list[PriceVersion]:
    result = list(versions)
    if old_od is not None:
        index = next((i for i, v in enumerate(result) if v.od == old_od), None)
        if index is None:
            raise PriceListError("unknown_version", "Nie ma takiego cennika")
        if new is None and len(result) == 1:
            raise PriceListError("last_version", "Nie można usunąć ostatniego cennika")
        del result[index]
    if new is not None:
        result.append(new)
    result.sort(key=lambda v: v.od)
    validate_prices(result)
    return result


def with_opust(
    entries: Sequence[OpustEntry], old_od: date | None, new: OpustEntry | None
) -> list[OpustEntry]:
    result = list(entries)
    if old_od is not None:
        index = next((i for i, e in enumerate(result) if e.od == old_od), None)
        if index is None:
            raise PriceListError("unknown_version", "Nie ma takiej pozycji")
        if new is None and len(result) == 1:
            raise PriceListError("last_version", "Nie można usunąć ostatniego współczynnika")
        del result[index]
    if new is not None:
        result.append(new)
    result.sort(key=lambda e: e.od)
    validate_opust(result)
    return result


def zones_differ(
    old: Sequence[PriceVersion], new: Sequence[PriceVersion], first: date, last: date
) -> bool:
    day = first
    while day <= last:
        if price_at(old, day).strefy.key() != price_at(new, day).strefy.key():
            return True
        day += timedelta(days=1)
    return False


@dataclass(frozen=True)
class Pricing:
    prices: tuple[PriceVersion, ...]
    opust: tuple[OpustEntry, ...]

    def price_at(self, day: date) -> PriceVersion:
        return price_at(self.prices, day)

    def ratio_at(self, day: date) -> Decimal:
        return ratio_at(self.opust, day)

    def schedule_at(self, hour_start: datetime) -> ZoneSchedule:
        return self.price_at(hour_start.astimezone(TZ).date()).strefy

    def schedule_for(self, day: date) -> ZoneSchedule:
        return self.price_at(day).strefy


DEFAULT_PRICING = Pricing((example_price(date(2026, 1, 1)),), (OpustEntry(date(2026, 1, 1), Decimal("0.8")),))
