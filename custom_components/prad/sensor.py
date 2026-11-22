"""Sensors: counters, costs, forecast, storage, history and diagnostics."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.util import dt as dt_util

from .const import NAME_PREFIX
from .coordinator import PradCoordinator
from .entity import PradEntity
from .logic import climate
from .logic.amounts import Totals, price_now_gross
from .logic.state import Counters
from .logic.zones import TZ, day_key, month_key, month_start, zone_at

KWH = "kWh"
PLN = "PLN"
HISTORY_ROWS = 36
DAYS_SHOWN = 45


def _round(value: float | Decimal) -> float:
    return round(float(value), 3)


def _ref_time(coordinator: PradCoordinator) -> datetime | None:
    """Start of the last settled hour (last_tick - 1 h); None before the first settled reading."""
    last = coordinator.engine.state.last_tick
    return None if last is None else last - timedelta(hours=1)


def _month_view(coordinator: PradCoordinator, now: datetime) -> tuple[Counters, datetime]:
    """Counters and start of the month bucket of the last settled hour."""
    ref = _ref_time(coordinator)
    if ref is None:
        return Counters(), month_start(month_key(now))
    key = month_key(ref)
    state = coordinator.engine.state
    if key in state.months:
        counters = state.months[key]
    elif key in state.pending:
        counters = state.pending[key].counters
    elif state.last_closed is not None and key <= state.last_closed:
        # The bucket month is already closed (no new settled hour yet): follow the calendar
        # month so that value and last_reset change together.
        current = month_key(now)
        return state.months.get(current, Counters()), month_start(current)
    else:
        counters = Counters()
    return counters, month_start(key)


def _day_view(coordinator: PradCoordinator, now: datetime) -> tuple[Counters, datetime]:
    """Counters and midnight of the day bucket of the last settled hour."""
    ref = _ref_time(coordinator)
    if ref is None:
        return Counters(), _midnight(day_key(now))
    key = day_key(ref)
    return coordinator.engine.state.days.get(key, Counters()), _midnight(key)


def _midnight(key: str) -> datetime:
    return datetime.fromisoformat(key).replace(tzinfo=TZ)


@dataclass(frozen=True)
class CounterSpec:
    key: str
    name: str
    value: Callable[[Counters], float]
    missing: str | None = None  # tracker key reported as `godziny_bez_danych`


def _house(c: Counters) -> float:
    return c.house()


def _self(c: Counters) -> float:
    return c.self_consumption()


def _rest(c: Counters) -> float:
    return c.rest()


def _zone_specs(prefix: str, period: str) -> list[CounterSpec]:
    return [
        CounterSpec(f"{prefix}_pobor_droga", f"pobór {period} (droga)", lambda c: c.import_peak),
        CounterSpec(f"{prefix}_pobor_tania", f"pobór {period} (tania)", lambda c: c.import_offpeak),
        CounterSpec(f"{prefix}_oddanie_droga", f"oddanie {period} (droga)", lambda c: c.export_peak),
        CounterSpec(f"{prefix}_oddanie_tania", f"oddanie {period} (tania)", lambda c: c.export_offpeak),
    ]


MONTH_SPECS: list[CounterSpec] = [
    *_zone_specs("mc", "w tym miesiącu"),
    CounterSpec("mc_produkcja_pv", "produkcja PV w tym miesiącu", lambda c: c.pv),
    CounterSpec("mc_zuzycie_domu", "zużycie domu w tym miesiącu", _house),
    CounterSpec("mc_autokonsumpcja", "autokonsumpcja w tym miesiącu", _self),
    CounterSpec("mc_reszta", "reszta zużycia w tym miesiącu", _rest),
]

DAY_SPECS: list[CounterSpec] = [
    *_zone_specs("dzis", "dziś"),
    CounterSpec("dzis_produkcja_pv", "produkcja PV dziś", lambda c: c.pv),
    CounterSpec("dzis_zuzycie_domu", "zużycie domu dziś", _house),
    CounterSpec("dzis_autokonsumpcja", "autokonsumpcja dziś", _self),
]


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    coordinator: PradCoordinator = entry.runtime_data
    entities: list[SensorEntity] = [PradPrice(coordinator)]
    entities += [PradCounterSensor(coordinator, spec, "month") for spec in MONTH_SPECS]
    entities += [PradCounterSensor(coordinator, spec, "day") for spec in DAY_SPECS]
    for slug, name in coordinator.consumers.items():
        spec = CounterSpec(
            f"mc_odb_{slug}",
            f"{name} w tym miesiącu",
            lambda c, slug=slug: c.consumers.get(slug, 0.0),
            slug,
        )
        entities.append(PradCounterSensor(coordinator, spec, "month"))
    entities += [
        PradCostSensor(coordinator, "mc_koszt", "koszt w tym miesiącu", lambda t: t.gross, True),
        PradCostSensor(coordinator, "mc_koszt_droga", "koszt w tym miesiącu (droga)", lambda t: t.peak_gross),
        PradCostSensor(coordinator, "mc_koszt_tania", "koszt w tym miesiącu (tania)", lambda t: t.offpeak_gross),
        PradCostSensor(
            coordinator, "mc_oplaty_stale", "opłaty stałe w tym miesiącu", lambda t: t.fixed_gross
        ),
        PradForecast(coordinator),
        PradStorage(coordinator),
        PradKwhPerDegreeDay(coordinator),
        PradHistory(coordinator),
        PradModelVsInvoice(coordinator),
        PradDays(coordinator),
    ]
    async_add_entities(entities)


class PradPrice(PradEntity, SensorEntity):
    """Gross price of one kWh in the current zone."""

    _attr_name = f"{NAME_PREFIX}cena teraz"
    _attr_native_unit_of_measurement = "zł/kWh"
    _attr_suggested_display_precision = 4
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: PradCoordinator) -> None:
        super().__init__(coordinator, "sensor", "cena_teraz")

    @property
    def native_value(self) -> float:
        return float(price_now_gross(zone_at(dt_util.now()), self.coordinator.tariff()))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"strefa": zone_at(dt_util.now()).value}


class PradCounterSensor(PradEntity, SensorEntity):
    """Energy counter of the month or day bucket of the last settled hour."""

    _attr_native_unit_of_measurement = KWH
    _attr_suggested_display_precision = 1
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL

    def __init__(self, coordinator: PradCoordinator, spec: CounterSpec, period: str) -> None:
        super().__init__(coordinator, "sensor", spec.key)
        self._spec = spec
        self._period = period
        self._attr_name = f"{NAME_PREFIX}{spec.name}"

    def _view(self) -> tuple[Counters, datetime]:
        now = dt_util.now()
        if self._period == "month":
            return _month_view(self.coordinator, now)
        return _day_view(self.coordinator, now)

    @property
    def native_value(self) -> float:
        return _round(self._spec.value(self._view()[0]))

    @property
    def last_reset(self) -> datetime:
        return self._view()[1]

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self._spec.missing is None:
            return None
        return {"godziny_bez_danych": self._view()[0].missing_hours.get(self._spec.missing, 0)}


class PradCostSensor(PradEntity, SensorEntity):
    """Amount (gross) for the current month from the engine snapshot."""

    _attr_native_unit_of_measurement = PLN
    _attr_suggested_display_precision = 2
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.TOTAL

    def __init__(
        self,
        coordinator: PradCoordinator,
        key: str,
        name: str,
        value: Callable[[Totals], Decimal],
        with_net_vat: bool = False,
    ) -> None:
        super().__init__(coordinator, "sensor", key)
        self._value = value
        self._with_net_vat = with_net_vat
        self._attr_name = f"{NAME_PREFIX}{name}"

    def _totals(self) -> Totals:
        # Follows the calendar month of now (current_snapshot), so in the first hour of a month
        # it differs from the energy bucket (last settled hour): intended.
        c = self.coordinator
        snap = c.engine.current_snapshot(dt_util.now(), c.tariff(), c.storage_before_cross())
        return snap.totals

    @property
    def native_value(self) -> float:
        return float(self._value(self._totals()))

    @property
    def last_reset(self) -> datetime:
        return month_start(month_key(dt_util.now()))

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if not self._with_net_vat:
            return None
        totals = self._totals()
        return {"netto": float(totals.net), "vat": float(totals.vat)}


class PradForecast(PradEntity, SensorEntity):
    """Gross cost of the whole month extrapolated from the last full days."""

    _attr_name = f"{NAME_PREFIX}prognoza kosztu miesiąca"
    _attr_native_unit_of_measurement = PLN
    _attr_suggested_display_precision = 2
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.TOTAL

    def __init__(self, coordinator: PradCoordinator) -> None:
        super().__init__(coordinator, "sensor", "mc_prognoza")

    def _snapshot(self):
        c = self.coordinator
        return c.engine.forecast_snapshot(dt_util.now(), c.tariff(), c.storage_before_cross())

    @property
    def native_value(self) -> float | None:
        snap = self._snapshot()
        return None if snap is None else float(snap.totals.gross)

    @property
    def last_reset(self) -> datetime:
        return month_start(month_key(dt_util.now()))

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        snap = self._snapshot()
        if snap is None:
            return None
        c = snap.counters
        return {
            "pobor_droga": _round(c.import_peak),
            "pobor_tania": _round(c.import_offpeak),
            "oddanie_droga": _round(c.export_peak),
            "oddanie_tania": _round(c.export_offpeak),
        }


class PradStorage(PradEntity, SensorEntity):
    """Energy kept in the net-metering storage."""

    _unrecorded_attributes = frozenset({"wplywy", "korekty"})
    _attr_name = f"{NAME_PREFIX}magazyn"
    _attr_native_unit_of_measurement = KWH
    _attr_suggested_display_precision = 1
    _attr_device_class = SensorDeviceClass.ENERGY_STORAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: PradCoordinator) -> None:
        super().__init__(coordinator, "sensor", "magazyn_kwh")

    @property
    def native_value(self) -> float:
        return _round(self.coordinator.engine.storage_kwh())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        c = self.coordinator
        state = c.engine.state
        inflow = c.engine.month_inflow(dt_util.now(), c.tariff(), c.storage_before_cross())
        return {
            "wplywy": [
                {
                    "miesiac": i.month,
                    "strefa": i.zone.value,
                    "pozostalo": float(i.amount),
                    "wazny_do": i.valid_until,
                }
                for i in state.inflows
            ],
            "korekty": [
                {"czas": k.get("time"), "miesiac": k.get("month"), "ilosc": k.get("amount")}
                for k in state.corrections
            ],
            "wplyw_mc": float(inflow),
        }


class PradKwhPerDegreeDay(PradEntity, SensorEntity):
    """House consumption per heating degree day in the last closed month."""

    _attr_name = f"{NAME_PREFIX}kWh na stopniodzień (ostatni miesiąc)"
    _attr_native_unit_of_measurement = "kWh/°C·d"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: PradCoordinator) -> None:
        super().__init__(coordinator, "sensor", "mc_kwh_na_stopniodzien")

    def _row(self):
        state = self.coordinator.engine.state
        if state.last_closed is None:
            return None
        for row in state.history:
            if row.month == state.last_closed:
                return row
        return None

    @property
    def native_value(self) -> float | None:
        row = self._row()
        if row is None or row.stopniodni is None:
            return None
        ratio = climate.kwh_per_degree_day(row.zuzycie_domu, row.stopniodni)
        return None if ratio is None else round(ratio, 3)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        row = self._row()
        return {"stopniodni": None if row is None else row.stopniodni}


class PradHistory(PradEntity, SensorEntity):
    """Number of closed months; attributes carry the history for the dashboard."""

    _unrecorded_attributes = frozenset({"miesiace"})
    _attr_name = f"{NAME_PREFIX}historia miesięcy"

    def __init__(self, coordinator: PradCoordinator) -> None:
        super().__init__(coordinator, "sensor", "historia")

    @property
    def native_value(self) -> int:
        return len(self.coordinator.engine.state.history)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        state = self.coordinator.engine.state
        current = month_key(dt_util.now())
        return {
            "miesiace": [row.to_dict() for row in state.history[-HISTORY_ROWS:]],
            "ostatni_zamkniety_mc": state.last_closed,
            "oczekuje_na_dane": sorted(m for m in state.months if m < current),
        }


class PradModelVsInvoice(PradEntity, SensorEntity):
    """Difference between the model and the last invoice entered, in percent."""

    _attr_name = f"{NAME_PREFIX}model a faktura"
    _attr_native_unit_of_measurement = "%"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: PradCoordinator) -> None:
        super().__init__(coordinator, "sensor", "model_vs_faktura")

    def _row(self):
        for row in reversed(self.coordinator.engine.state.history):
            if row.invoice is not None:
                return row
        return None

    @property
    def native_value(self) -> float | None:
        row = self._row()
        if row is None or row.invoice == 0:
            return None
        return round(float((row.koszt_razem - row.invoice) / row.invoice * 100), 1)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        row = self._row()
        return {"miesiac": None if row is None else row.month}


class PradDays(PradEntity, SensorEntity):
    """Number of days kept; the `dni` attribute feeds the daily chart."""

    _unrecorded_attributes = frozenset({"dni"})
    _attr_name = f"{NAME_PREFIX}dni (ostatnie 45)"

    def __init__(self, coordinator: PradCoordinator) -> None:
        super().__init__(coordinator, "sensor", "dni")

    @property
    def native_value(self) -> int:
        return len(self.coordinator.engine.state.days)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        state = self.coordinator.engine.state
        keys = sorted(state.days)[-DAYS_SHOWN:]
        days = []
        for key in keys:
            c = state.days[key]
            days.append(
                {
                    "dzien": key,
                    "pobor_droga": _round(c.import_peak),
                    "pobor_tania": _round(c.import_offpeak),
                    "oddanie_droga": _round(c.export_peak),
                    "oddanie_tania": _round(c.export_offpeak),
                    "pv": _round(c.pv),
                    "autokonsumpcja": _round(c.self_consumption()),
                    "temp": state.weather.get(key, {}).get("temp"),
                }
            )
        return {"dni": days}
