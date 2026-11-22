"""Persistent integration state (JSON-serialisable). Pure Python, no Home Assistant imports."""
from __future__ import annotations

import copy
from dataclasses import dataclass, field, fields
from datetime import datetime
from decimal import Decimal
from typing import Any

from .settlement import Inflow
from .tracker import TrackerRef
from .zones import TZ, Zone

SCHEMA = 1


def to_decimal(x: float) -> Decimal:
    """The only kWh -> settlement conversion."""
    return Decimal(str(round(x, 3)))


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(TZ)


@dataclass
class Counters:
    import_peak: float = 0.0
    import_offpeak: float = 0.0
    export_peak: float = 0.0
    export_offpeak: float = 0.0
    pv: float = 0.0
    consumers: dict[str, float] = field(default_factory=dict)
    missing_hours: dict[str, int] = field(default_factory=dict)
    missing_marks: list[str] = field(default_factory=list)

    def _import(self) -> float:
        return self.import_peak + self.import_offpeak

    def _export(self) -> float:
        return self.export_peak + self.export_offpeak

    def house(self) -> float:
        return max(0.0, self.pv + self._import() - self._export())

    def self_consumption(self) -> float:
        return max(0.0, self.pv - self._export())

    def rest(self) -> float:
        return max(0.0, self.house() - sum(self.consumers.values()))

    def to_dict(self) -> dict[str, Any]:
        return {
            "import_peak": self.import_peak,
            "import_offpeak": self.import_offpeak,
            "export_peak": self.export_peak,
            "export_offpeak": self.export_offpeak,
            "pv": self.pv,
            "consumers": dict(self.consumers),
            "missing_hours": dict(self.missing_hours),
            "missing_marks": list(self.missing_marks),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Counters:
        return cls(
            import_peak=float(d.get("import_peak", 0.0)),
            import_offpeak=float(d.get("import_offpeak", 0.0)),
            export_peak=float(d.get("export_peak", 0.0)),
            export_offpeak=float(d.get("export_offpeak", 0.0)),
            pv=float(d.get("pv", 0.0)),
            consumers={k: float(v) for k, v in d.get("consumers", {}).items()},
            missing_hours={k: int(v) for k, v in d.get("missing_hours", {}).items()},
            missing_marks=list(d.get("missing_marks", [])),
        )


@dataclass
class PendingClose:
    month: str
    counters: Counters
    tariff: dict[str, str]
    storage_before_cross: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "month": self.month,
            "counters": self.counters.to_dict(),
            "tariff": dict(self.tariff),
            "storage_before_cross": self.storage_before_cross,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PendingClose:
        return cls(
            d["month"],
            Counters.from_dict(d["counters"]),
            {k: str(v) for k, v in d["tariff"].items()},
            bool(d["storage_before_cross"]),
        )


_DECIMAL_FIELDS = (
    "pobor_droga", "pobor_tania", "oddanie_droga", "oddanie_tania",
    "magazyn_wplyw", "magazyn_uzyte", "magazyn_wygasle", "magazyn_stan",
    "do_zaplaty_droga", "do_zaplaty_tania", "koszt_droga", "koszt_tania",
    "oplaty_stale", "koszt_netto", "koszt_razem",
)


@dataclass
class HistoryRow:
    month: str
    pobor_droga: Decimal
    pobor_tania: Decimal
    oddanie_droga: Decimal
    oddanie_tania: Decimal
    magazyn_wplyw: Decimal
    magazyn_uzyte: Decimal
    magazyn_wygasle: Decimal
    magazyn_stan: Decimal
    do_zaplaty_droga: Decimal
    do_zaplaty_tania: Decimal
    koszt_droga: Decimal
    koszt_tania: Decimal
    oplaty_stale: Decimal
    koszt_netto: Decimal
    koszt_razem: Decimal
    zuzycie_domu: float
    produkcja_pv: float
    autokonsumpcja: float
    reszta: float
    temp_srednia: float | None
    stopniodni: float | None
    naslonecznienie_mj: float | None
    wersja_cennika: str
    consumers: dict[str, float] = field(default_factory=dict)
    consumers_missing: dict[str, int] = field(default_factory=dict)
    invoice: Decimal | None = None
    csv_written: bool = False

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for f in fields(self):
            value = getattr(self, f.name)
            if f.name in _DECIMAL_FIELDS or (f.name == "invoice" and value is not None):
                value = str(value)
            elif isinstance(value, dict):
                value = dict(value)
            out[f.name] = value
        return out

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> HistoryRow:
        values = dict(d)
        for name in _DECIMAL_FIELDS:
            values[name] = Decimal(str(values[name]))
        if values.get("invoice") is not None:
            values["invoice"] = Decimal(str(values["invoice"]))
        return cls(**values)


def _inflow_to_dict(i: Inflow) -> dict[str, str]:
    return {"month": i.month, "zone": i.zone.value, "amount": str(i.amount), "valid_until": i.valid_until}


def _inflow_from_dict(d: dict[str, Any]) -> Inflow:
    return Inflow(d["month"], Zone(d["zone"]), Decimal(str(d["amount"])), d["valid_until"])


inflow_to_dict = _inflow_to_dict
inflow_from_dict = _inflow_from_dict


@dataclass
class State:
    schema: int = SCHEMA
    refs: dict[str, TrackerRef] = field(default_factory=dict)
    months: dict[str, Counters] = field(default_factory=dict)
    days: dict[str, Counters] = field(default_factory=dict)
    pending: dict[str, PendingClose] = field(default_factory=dict)
    inflows: list[Inflow] = field(default_factory=list)
    corrections: list[dict] = field(default_factory=list)
    history: list[HistoryRow] = field(default_factory=list)
    last_closed: str | None = None
    storage_start: str = "2026-11"
    weather: dict[str, dict] = field(default_factory=dict)
    last_tick: datetime | None = None
    # Pre-rebuild inflows kept so that repeating prad.inicjuj with the same start gives the same result.
    rebuild_base: dict | None = None  # {"od": "YYYY-MM-DD", "inflows": [inflow dicts]}

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "refs": {k: {"time": r.time.isoformat(), "values": list(r.values)} for k, r in self.refs.items()},
            "months": {k: c.to_dict() for k, c in self.months.items()},
            "days": {k: c.to_dict() for k, c in self.days.items()},
            "pending": {k: p.to_dict() for k, p in self.pending.items()},
            "inflows": [_inflow_to_dict(i) for i in self.inflows],
            "corrections": [dict(c) for c in self.corrections],
            "history": [h.to_dict() for h in self.history],
            "last_closed": self.last_closed,
            "storage_start": self.storage_start,
            "weather": {k: dict(v) for k, v in self.weather.items()},
            "last_tick": self.last_tick.isoformat() if self.last_tick else None,
            "rebuild_base": copy.deepcopy(self.rebuild_base),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> State:
        if d.get("schema") != SCHEMA:
            raise ValueError("unsupported schema")
        last_tick = d.get("last_tick")
        return cls(
            schema=SCHEMA,
            refs={
                k: TrackerRef(_dt(r["time"]), tuple(float(v) for v in r["values"]))
                for k, r in d.get("refs", {}).items()
            },
            months={k: Counters.from_dict(c) for k, c in d.get("months", {}).items()},
            days={k: Counters.from_dict(c) for k, c in d.get("days", {}).items()},
            pending={k: PendingClose.from_dict(p) for k, p in d.get("pending", {}).items()},
            inflows=[_inflow_from_dict(i) for i in d.get("inflows", [])],
            corrections=[dict(c) for c in d.get("corrections", [])],
            history=[HistoryRow.from_dict(h) for h in d.get("history", [])],
            last_closed=d.get("last_closed"),
            storage_start=d.get("storage_start", "2026-11"),
            weather={k: dict(v) for k, v in d.get("weather", {}).items()},
            last_tick=_dt(last_tick) if last_tick else None,
            rebuild_base=copy.deepcopy(d.get("rebuild_base")),
        )
