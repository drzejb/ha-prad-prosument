"""Rebuild of the Prad state from recorder hourly statistics (service prad.inicjuj)."""
from __future__ import annotations

import asyncio
import copy
import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from homeassistant.components import persistent_notification
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util

from .const import CONF_EXPORT, CONF_IMPORT, CONF_NET, CONF_PV, CONF_TEMP, GRID_NET, NOTIFICATION_REBUILD
from .logic.engine import Engine
from .logic.state import State, inflow_from_dict, inflow_to_dict
from .logic.tariff import OpustEntry, Pricing
from .logic.zones import TZ, month_key
from .weather import async_collect_weather, merge_weather

if TYPE_CHECKING:
    from .coordinator import PradCoordinator

_LOGGER = logging.getLogger(__name__)
_HOUR = timedelta(hours=1)
_YIELD_EVERY = 500  # hours between event loop yields


def _utc_hour(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(UTC)
    return datetime.fromtimestamp(float(value), tz=UTC)


async def async_rebuild(
    hass: HomeAssistant, coordinator: PradCoordinator, start: datetime, storage_start: str
) -> dict[str, Any]:
    """Replace the coordinator state with one rebuilt from `start` (aware local midnight).

    Must be called with coordinator.lock held.
    """
    data = coordinator.source_data
    net_mode = coordinator.grid_mode() == GRID_NET
    import_id, export_id, pv_id = data.get(CONF_IMPORT), data.get(CONF_EXPORT), data.get(CONF_PV)
    net_id = data.get(CONF_NET)
    consumer_ids = dict(coordinator.consumer_entities)
    if net_mode:
        # Judged from the state attributes, so it works even while the meter is unavailable.
        net_state = hass.states.get(net_id) if net_id else None
        if net_state is None or net_state.attributes.get("state_class") != "total":
            raise ServiceValidationError(
                "Licznik netto musi mieć state_class: total – licznik malejący z total_increasing "
                "Home Assistant traktuje jako reset"
            )
        grid_ids = [net_id]
    else:
        grid_ids = [import_id, export_id]
    wanted = {e for e in (*grid_ids, pv_id, *consumer_ids.values()) if e}

    now = dt_util.utcnow()
    first_hour = start.astimezone(UTC) - _HOUR  # the first hour only sets the reference point
    stats = await get_instance(hass).async_add_executor_job(
        statistics_during_period, hass, first_hour, now, wanted, "hour", {"energy": "kWh"}, {"state"}
    )
    missing = [e for e in grid_ids if not stats.get(e)]
    if missing:
        raise ServiceValidationError(
            "Brak statystyk w rejestratorze dla: " + ", ".join(str(e) for e in missing)
        )
    by_entity: dict[str, dict[datetime, float]] = {}
    for entity_id, rows in stats.items():
        by_entity[entity_id] = {
            _utc_hour(r["start"]): float(r["state"]) for r in rows if r.get("state") is not None
        }

    if not set.intersection(*(set(by_entity.get(e, {})) for e in grid_ids)):
        raise ServiceValidationError(
            "Statystyki licznika sieci nie zawierają odczytów stanu licznika (state); "
            "wybierz liczniki energii z sumą narastającą"
        )

    old = coordinator.engine.state
    await coordinator.async_save_backup(coordinator.payload())
    warnings: list[str] = []

    from_month = month_key(start)
    new = State(storage_start=storage_start)
    new.zrodla = dict(old.zrodla)
    new.zmiana_licznika = old.zmiana_licznika
    new.history = [copy.deepcopy(r) for r in old.history if r.month < from_month]
    new.last_closed = max((r.month for r in new.history), default=None)
    od_key = start.date().isoformat()
    if old.rebuild_base and old.rebuild_base.get("od") == od_key:
        # Repeated rebuild: start from the same inflows as the first time (no drift).
        base = [inflow_from_dict(d) for d in old.rebuild_base["inflows"]]
        created = str(old.rebuild_base.get("created") or now.isoformat())
    else:
        base = [i for i in old.inflows if i.month < from_month]
        created = now.isoformat()
    new.inflows = list(base)
    new.rebuild_base = {"od": od_key, "created": created, "inflows": [inflow_to_dict(i) for i in base]}
    if start.day != 1 and any(r.month == from_month for r in old.history):
        message = f"Miesiąc {from_month} zostanie zastąpiony miesiącem częściowym od {od_key}"
        warnings.append(message)
        _LOGGER.warning("Month %s will be replaced by a partial month starting on %s", from_month, od_key)
    new.corrections = [dict(c) for c in old.corrections]
    new.weather = copy.deepcopy(old.weather)
    prior = {r.month: r for r in old.history if r.month >= from_month and r.segmenty}
    invoices = {r.month: r.invoice for r in old.history if r.month >= from_month and r.invoice is not None}

    # Weather first, so the months closed in the loop below get temperature and radiation.
    today = dt_util.now().astimezone(TZ).date()
    first_day = start.date()
    days = [first_day + timedelta(days=n) for n in range((today - first_day).days)]
    try:
        merge_weather(
            new.weather, await async_collect_weather(hass, data.get(CONF_TEMP), new.weather, days, today)
        )
    except Exception:  # noqa: BLE001 - weather must never block the rebuild
        _LOGGER.warning("Weather for the rebuild could not be fetched")

    engine = Engine(new, dict(coordinator.consumers), grid_mode=coordinator.grid_mode(), has_pv=coordinator.has_pv())
    # Closed months keep their frozen price versions and ratios: they are not re-priced.
    engine.month_pricing = {
        m: Pricing(
            tuple(s.cennik for s in row.segmenty),
            tuple(OpustEntry(r.od, r.wspolczynnik) for r in (row.segmenty_opustu or [])),
        )
        for m, row in prior.items()
    }
    # Corrections to replay: months from the start date, or entered after the base was taken.
    # They are applied in time order, each before the first tick later than its time.
    created_at = datetime.fromisoformat(created)
    replay: list[tuple[datetime, dict]] = []
    for corr in old.corrections:
        try:
            when = datetime.fromisoformat(str(corr["time"]))
        except (KeyError, ValueError):
            when = datetime.max.replace(tzinfo=UTC)
        if corr.get("month", "") >= from_month or when > created_at:
            replay.append((when, corr))
    replay.sort(key=lambda item: item[0])

    def apply_correction(corr: dict) -> None:
        try:
            engine._apply_correction(Decimal(str(corr["amount"])), corr["month"])  # noqa: SLF001
        except (ValueError, KeyError, ArithmeticError):
            warnings.append(f"Korekta magazynu {corr.get('amount')} kWh dla {corr.get('month')} pominięta")
            _LOGGER.warning(
                "Storage correction %s kWh for %s skipped in rebuild", corr.get("amount"), corr.get("month")
            )

    pricing, before_cross, limits = coordinator.pricing(), coordinator.storage_before_cross(), coordinator.limits()
    last_hour = now.replace(minute=0, second=0, microsecond=0) - _HOUR
    hours = gaps = 0
    closed: list[str] = []
    hour = first_hour
    while hour <= last_hour:
        def value(entity_id: str | None, h: datetime = hour) -> float | None:
            return by_entity.get(entity_id, {}).get(h) if entity_id else None

        if net_mode:
            net = value(net_id)
            fox = None if net is None else (net,)
        else:
            imp, exp = value(import_id), value(export_id)
            fox = None if imp is None or exp is None else (imp, exp)
        if fox is None:
            gaps += 1
        consumers = {slug: value(eid) for slug, eid in consumer_ids.items()}
        tick = hour + _HOUR
        while replay and replay[0][0] < tick:
            apply_correction(replay.pop(0)[1])
        engine.on_tick(tick, fox, value(pv_id), consumers, limits, pricing)
        for month in engine.months_ready_to_freeze(tick):
            old_row = prior.get(month)
            if old_row is not None:
                # Keep the price versions and the switch the month was closed with, do not re-price it.
                month_cross = before_cross if old_row.storage_before_cross is None else old_row.storage_before_cross
                engine.freeze_month(month, pricing, month_cross)
        closed.extend(r.month for r in engine.catch_up(tick, pricing, before_cross, limits))
        hours += 1
        hour = tick
        if hours % _YIELD_EVERY == 0:
            await asyncio.sleep(0)

    for _when, corr in replay:
        apply_correction(corr)

    old_by_month = {r.month: r for r in old.history}
    for row in new.history:
        previous = old_by_month.get(row.month)
        if previous is not None and row.month >= from_month and previous.koszt_razem != row.koszt_razem:
            warnings.append(
                f"Miesiąc {row.month}: koszt zmienił się z {previous.koszt_razem} na {row.koszt_razem}; "
                "plik CSV zachowuje starą linię"
            )
            _LOGGER.warning(
                "Rebuilt month %s changed cost from %s to %s; the CSV keeps the old line",
                row.month, previous.koszt_razem, row.koszt_razem,
            )
    for row in new.history:
        if row.month in invoices:
            row.invoice = invoices.pop(row.month)
    if invoices:
        warnings.append("Faktury miesięcy niezamkniętych przez odbudowę zostały pominięte: " + ", ".join(sorted(invoices)))
        _LOGGER.warning("Invoices of months not closed by the rebuild were dropped: %s", sorted(invoices))

    engine.month_pricing = {}
    coordinator.engine = engine
    await coordinator.async_save()
    try:
        for row in [r for r in new.history if not r.csv_written]:
            await hass.async_add_executor_job(coordinator._write_csv, row)  # noqa: SLF001
            row.csv_written = True
            await coordinator.async_save()
    except OSError as err:
        coordinator.async_update_listeners()  # state is already swapped and saved
        raise ServiceValidationError("Nie udało się zapisać pliku CSV; spróbuj ponownie") from err
    coordinator.async_update_listeners()
    persistent_notification.async_dismiss(hass, NOTIFICATION_REBUILD)  # "run prad.inicjuj" is done
    if gaps:
        warnings.append(f"Brak odczytów licznika w {gaps} godzinach")
        _LOGGER.warning("Rebuild had %d hours without a grid meter reading", gaps)
    _LOGGER.info("Rebuild finished: %d hours, %d gaps, closed months: %s", hours, gaps, closed or "none")
    return {"godziny": hours, "przerwy": gaps, "zamkniete_miesiace": closed, "ostrzezenia": warnings}
