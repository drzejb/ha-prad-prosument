"""Settlement engine: hourly ticks, month freeze/close, snapshots, forecast, corrections.

Pure Python, no Home Assistant imports.
"""
from __future__ import annotations

import copy
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from . import climate
from .amounts import Totals, compute_amounts
from .settlement import Inflow, Settlement, settle
from .state import Counters, HistoryRow, PendingClose, State, to_decimal
from .tariff import Tariff
from .tracker import HourDelta, advance, floor_hour
from .zones import (
    TZ, Zone, add_months, day_key, days_in_month, month_key, month_start, next_month_key, zone_at,
)

_ZERO = Decimal(0)
_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
KEEP_DAYS = 45
KEEP_WEATHER_DAYS = 400
KEEP_MARKS_HOURS = 48
FORECAST_DAYS = 7


@dataclass(frozen=True)
class Limits:
    fox: float = 30
    pv: float = 12
    consumer: float = 5
    degree_day_base: float = 18


@dataclass(frozen=True)
class Snapshot:
    settlement: Settlement
    totals: Totals
    counters: Counters


def _decimals(peak: float, offpeak: float) -> dict[Zone, Decimal]:
    return {Zone.PEAK: to_decimal(peak), Zone.OFFPEAK: to_decimal(offpeak)}


def _order(i: Inflow) -> tuple[str, int]:
    return (i.month, 0 if i.zone is Zone.PEAK else 1)


def _add_fox(c: Counters, h: HourDelta) -> None:
    net = h.deltas[0] - h.deltas[1]
    peak = zone_at(h.start) is Zone.PEAK
    if net > 0:
        attr = "import_peak" if peak else "import_offpeak"
        setattr(c, attr, getattr(c, attr) + net)
    else:
        attr = "export_peak" if peak else "export_offpeak"
        setattr(c, attr, getattr(c, attr) - net)


def _add_pv(c: Counters, h: HourDelta) -> None:
    c.pv += h.deltas[0]


class Engine:
    def __init__(self, state: State, consumers: dict[str, str]) -> None:
        self.state = state
        self.consumers = consumers

    # ---------------------------------------------------------------- tick

    def on_tick(
        self,
        now: datetime,
        fox: tuple[float, float] | None,
        pv: float | None,
        consumers: dict[str, float | None],
        limits: Limits,
    ) -> list[str]:
        now = now.astimezone(TZ)
        warnings: list[str] = []

        if self._track("fox", now, fox, limits.fox, _add_fox, warnings):
            self.state.last_tick = self.state.refs["fox"].time
        if fox is None:
            self._mark_missing("fox", now)
        self._track("pv", now, (pv,) if pv else None, limits.pv, _add_pv, warnings)
        for slug in {**dict.fromkeys(self.consumers), **consumers}:
            value = consumers.get(slug)

            def add_consumer(c: Counters, h: HourDelta, slug: str = slug) -> None:
                c.consumers[slug] = c.consumers.get(slug, 0.0) + h.deltas[0]

            self._track(slug, now, None if value is None else (value,), limits.consumer, add_consumer, warnings)
            if value is None:
                self._mark_missing(slug, now)
        self._trim(now)
        return warnings

    def _track(
        self,
        key: str,
        now: datetime,
        values: tuple[float, ...] | None,
        limit: float,
        add: Callable[[Counters, HourDelta], None],
        warnings: list[str],
    ) -> bool:
        """Advance one tracker and route its hours. Returns True when a reading was accepted."""
        result = advance(self.state.refs.get(key), now, values, limit)
        if result.new_ref is not None:
            self.state.refs[key] = result.new_ref
        if result.rejected:
            warnings.append(f"{key}: {result.reason}, new reference")
            return False
        dropped = 0
        for hour in result.hours:
            month_c, day_c = self._buckets(hour.start, now)
            if month_c is None:
                dropped += 1
            else:
                add(month_c, hour)
            if day_c is not None:
                add(day_c, hour)
        if dropped and sum(abs(x) for h in result.hours for x in h.deltas) >= 0.01:
            warnings.append(f"{key}: {dropped} hours for closed month dropped")
        return values is not None

    def _buckets(self, start: datetime, now: datetime) -> tuple[Counters | None, Counters | None]:
        """Month bucket (pending, none when the month is already closed, or open) and day bucket."""
        st = self.state
        m = month_key(start)
        if m in st.pending:
            month_c: Counters | None = st.pending[m].counters
        elif st.last_closed is not None and m <= st.last_closed:
            month_c = None
        else:
            month_c = st.months.setdefault(m, Counters())
        d = day_key(start)
        too_old = d < day_key(now - timedelta(days=KEEP_DAYS - 1))
        return month_c, None if too_old else st.days.setdefault(d, Counters())

    def _mark_missing(self, key: str, now: datetime) -> None:
        ref = self.state.refs.get(key)
        current_hour = floor_hour(now)
        if ref is None or ref.time >= current_hour:
            return  # tracker never started, or that hour is already accounted for
        hour = current_hour - timedelta(hours=1)
        month_c, day_c = self._buckets(hour, now)
        if month_c is None:
            return
        mark = f"{key}|{hour.isoformat()}"
        if mark in month_c.missing_marks:
            return
        month_c.missing_marks.append(mark)
        month_c.missing_hours[key] = month_c.missing_hours.get(key, 0) + 1
        if day_c is not None:
            day_c.missing_hours[key] = day_c.missing_hours.get(key, 0) + 1

    def _trim(self, now: datetime) -> None:
        st = self.state
        day_cut = day_key(now - timedelta(days=KEEP_DAYS - 1))
        st.days = {k: v for k, v in st.days.items() if k >= day_cut}
        weather_cut = day_key(now - timedelta(days=KEEP_WEATHER_DAYS - 1))
        st.weather = {k: v for k, v in st.weather.items() if k >= weather_cut}
        mark_cut = now - timedelta(hours=KEEP_MARKS_HOURS)
        buckets = [*st.months.values(), *st.days.values(), *(p.counters for p in st.pending.values())]
        for c in buckets:
            c.missing_marks = [
                mk for mk in c.missing_marks if datetime.fromisoformat(mk.rsplit("|", 1)[1]) >= mark_cut
            ]

    # ----------------------------------------------------- freeze and close

    def months_ready_to_freeze(self, now: datetime) -> list[str]:
        fox = self.state.refs.get("fox")
        if fox is None:
            return []
        current = month_key(now)
        return sorted(
            m for m in self.state.months if m < current and fox.time >= month_start(next_month_key(m))
        )

    def freeze_month(self, month: str, tariff: Tariff, storage_before_cross: bool) -> None:
        counters = self.state.months.pop(month)
        self.state.pending[month] = PendingClose(month, counters, tariff.to_dict(), storage_before_cross)

    def months_ready_to_close(self, now: datetime) -> list[str]:
        return sorted(
            m for m in self.state.pending if now >= month_start(next_month_key(m)) + timedelta(hours=1)
        )

    def close_month(self, month: str, now: datetime, limits: Limits) -> HistoryRow:
        """Close a frozen month for good. `now` is part of the interface but currently unused."""
        st = self.state
        for existing in st.history:
            if existing.month == month:
                st.pending.pop(month, None)
                return existing
        if month not in st.pending:
            raise ValueError(f"miesiąc {month} nie jest zamrożony")
        if any(m < month for m in (*st.pending, *st.months)):
            raise ValueError(f"wcześniejszy miesiąc nie jest jeszcze zamknięty (przed {month})")
        pending = st.pending[month]
        c = pending.counters
        tariff = Tariff.from_dict(pending.tariff)
        before_start = month < st.storage_start
        s = settle(
            month,
            _decimals(c.import_peak, c.import_offpeak),
            _decimals(c.export_peak, c.export_offpeak),
            () if before_start else tuple(st.inflows),
            tariff.credit_ratio,
            pending.storage_before_cross,
            True,
        )
        totals = compute_amounts(s.consumption, s.billable, tariff)
        temp, dd, radiation = self._weather_of(month, limits.degree_day_base)
        peak, off = Zone.PEAK, Zone.OFFPEAK
        row = HistoryRow(
            month=month,
            pobor_droga=s.consumption[peak], pobor_tania=s.consumption[off],
            oddanie_droga=s.export[peak], oddanie_tania=s.export[off],
            magazyn_wplyw=_ZERO if before_start else sum((i.amount for i in s.new_inflows), _ZERO),
            magazyn_uzyte=_ZERO if before_start else s.storage_used,
            magazyn_wygasle=_ZERO if before_start else s.expired,
            magazyn_stan=self.storage_kwh() if before_start else s.storage_after,
            do_zaplaty_droga=s.billable[peak], do_zaplaty_tania=s.billable[off],
            koszt_droga=totals.peak_gross, koszt_tania=totals.offpeak_gross,
            oplaty_stale=totals.fixed_gross, koszt_netto=totals.net, koszt_razem=totals.gross,
            zuzycie_domu=c.house(), produkcja_pv=c.pv, autokonsumpcja=c.self_consumption(), reszta=c.rest(),
            temp_srednia=temp, stopniodni=dd, naslonecznienie_mj=radiation,
            wersja_cennika=tariff.version,
            consumers=dict(c.consumers), consumers_missing=dict(c.missing_hours),
            tariff=dict(pending.tariff), storage_before_cross=pending.storage_before_cross,
        )
        st.history.append(row)
        if not before_start:
            st.inflows = list(s.inflows_after)
        if st.last_closed is None or month > st.last_closed:
            st.last_closed = month
        del st.pending[month]
        return row

    def _weather_of(self, month: str, base: float) -> tuple[float | None, float | None, float | None]:
        days = [self.state.weather.get(f"{month}-{n:02d}", {}) for n in range(1, days_in_month(month) + 1)]
        temps = [d.get("temp") for d in days]
        rads = [d.get("radiation") for d in days]
        temp = dd = radiation = None
        if all(t is not None for t in temps):
            temp = sum(temps) / len(temps)
            dd = climate.degree_days(temps, base)
        if all(r is not None for r in rads):
            radiation = sum(rads)
        return temp, dd, radiation

    def catch_up(
        self, now: datetime, tariff: Tariff, storage_before_cross: bool, limits: Limits
    ) -> list[HistoryRow]:
        for m in self.months_ready_to_freeze(now):
            self.freeze_month(m, tariff, storage_before_cross)
        return [self.close_month(m, now, limits) for m in self.months_ready_to_close(now)]

    # ------------------------------------------------------------ snapshots

    def _snapshot(
        self, month: str, counters: Counters, tariff: Tariff, storage_before_cross: bool
    ) -> Snapshot:
        inflows = () if month < self.state.storage_start else tuple(self.state.inflows)
        s = settle(
            month,
            _decimals(counters.import_peak, counters.import_offpeak),
            _decimals(counters.export_peak, counters.export_offpeak),
            inflows,
            tariff.credit_ratio,
            storage_before_cross,
            False,
        )
        return Snapshot(s, compute_amounts(s.consumption, s.billable, tariff), counters)

    def current_snapshot(self, now: datetime, tariff: Tariff, storage_before_cross: bool) -> Snapshot:
        m = month_key(now)
        counters = copy.deepcopy(self.state.months.get(m, Counters()))
        return self._snapshot(m, counters, tariff, storage_before_cross)

    def forecast_snapshot(
        self, now: datetime, tariff: Tariff, storage_before_cross: bool
    ) -> Snapshot | None:
        m = month_key(now)
        today = day_key(now)
        keys = sorted(k for k in self.state.days if k < today)[-FORECAST_DAYS:]
        if not keys:
            return None
        days = [self.state.days[k] for k in keys]
        remaining = (month_start(next_month_key(m)) - now) / timedelta(days=1)
        counters = copy.deepcopy(self.state.months.get(m, Counters()))
        for attr in ("import_peak", "import_offpeak", "export_peak", "export_offpeak"):
            mean = sum(getattr(d, attr) for d in days) / len(days)
            setattr(counters, attr, getattr(counters, attr) + mean * remaining)
        return self._snapshot(m, counters, tariff, storage_before_cross)

    def storage_kwh(self) -> Decimal:
        return sum((i.amount for i in self.state.inflows), _ZERO)

    def month_inflow(self, now: datetime, tariff: Tariff, storage_before_cross: bool) -> Decimal:
        snap = self.current_snapshot(now, tariff, storage_before_cross)
        return sum((i.amount for i in snap.settlement.new_inflows), _ZERO)

    # ---------------------------------------------------------- user actions

    def correct_storage(self, amount: Decimal, month: str, now: datetime) -> None:
        if not _MONTH_RE.match(month):
            raise ValueError("Miesiąc musi mieć format RRRR-MM")
        current = month_key(now)
        if month > current:
            raise ValueError("Miesiąc z przyszłości")
        if add_months(month, 12) < current:
            raise ValueError("Wpływ już wygasł")
        if amount == 0:
            raise ValueError("Ilość nie może być zerem")
        self._apply_correction(amount, month)
        self.state.corrections.append(
            {"time": now.astimezone(TZ).isoformat(), "month": month, "amount": str(amount)}
        )

    def _apply_correction(self, amount: Decimal, month: str) -> None:
        """Change the inflows by `amount` kWh; no validation of the month, no log entry."""
        st = self.state
        if amount > 0:
            st.inflows.append(Inflow(month, Zone.PEAK, amount, add_months(month, 12)))
            st.inflows.sort(key=_order)
        else:
            to_remove = -amount
            if to_remove > self.storage_kwh():
                raise ValueError("Korekta większa niż stan magazynu")
            kept: list[Inflow] = []
            for inflow in sorted(st.inflows, key=_order):
                take = min(inflow.amount, to_remove)
                to_remove -= take
                left = inflow.amount - take
                if left > 0:
                    kept.append(Inflow(inflow.month, inflow.zone, left, inflow.valid_until))
            st.inflows = kept

    def set_invoice(self, month: str, amount: Decimal) -> HistoryRow:
        if amount < 0:
            raise ValueError("Kwota faktury nie może być ujemna")
        for row in self.state.history:
            if row.month == month:
                row.invoice = amount
                return row
        raise ValueError(f"Brak miesiąca {month} w historii")
