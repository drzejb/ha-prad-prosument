"""Rebuild of the Prad state from recorder hourly statistics (service prad.inicjuj)."""
from __future__ import annotations

import asyncio
import copy
import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util

from .const import CONF_EXPORT, CONF_IMPORT, CONF_PV, CONF_TEMP
from .logic.engine import Engine
from .logic.state import State, inflow_from_dict, inflow_to_dict
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
    data = coordinator.config_entry.data
    import_id, export_id, pv_id = data.get(CONF_IMPORT), data.get(CONF_EXPORT), data.get(CONF_PV)
    consumer_ids = dict(coordinator.consumer_entities)
    wanted = {e for e in (import_id, export_id, pv_id, *consumer_ids.values()) if e}

    now = dt_util.utcnow()
    first_hour = start.astimezone(UTC) - _HOUR  # the first hour only sets the reference point
    stats = await get_instance(hass).async_add_executor_job(
        statistics_during_period, hass, first_hour, now, wanted, "hour", {"energy": "kWh"}, {"state"}
    )
    missing = [e for e in (import_id, export_id) if not stats.get(e)]
    if missing:
        raise ServiceValidationError(
            "Brak statystyk w rejestratorze dla: " + ", ".join(str(e) for e in missing)
        )
    by_entity: dict[str, dict[datetime, float]] = {}
    for entity_id, rows in stats.items():
        by_entity[entity_id] = {
            _utc_hour(r["start"]): float(r["state"]) for r in rows if r.get("state") is not None
        }

    old = coordinator.engine.state
    await coordinator.backup_store.async_save(
        {
            "state": old.to_dict(),
            "settings": {k: str(v) if isinstance(v, Decimal) else v for k, v in coordinator.settings.items()},
        }
    )

    from_month = month_key(start)
    new = State(storage_start=storage_start)
    new.history = [copy.deepcopy(r) for r in old.history if r.month < from_month]
    new.last_closed = max((r.month for r in new.history), default=None)
    od_key = start.date().isoformat()
    if old.rebuild_base and old.rebuild_base.get("od") == od_key:
        # Repeated rebuild: start from the same inflows as the first time (no drift).
        base = [inflow_from_dict(d) for d in old.rebuild_base["inflows"]]
    else:
        base = [i for i in old.inflows if i.month < from_month]
    new.inflows = list(base)
    new.rebuild_base = {"od": od_key, "inflows": [inflow_to_dict(i) for i in base]}
    if start.day != 1 and any(r.month == from_month for r in old.history):
        _LOGGER.warning("Month %s will be replaced by a partial month starting on %s", from_month, od_key)
    new.corrections = [dict(c) for c in old.corrections]
    new.weather = copy.deepcopy(old.weather)
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

    engine = Engine(new, dict(coordinator.consumers))
    tariff, before_cross, limits = coordinator.tariff(), coordinator.storage_before_cross(), coordinator.limits()
    last_hour = now.replace(minute=0, second=0, microsecond=0) - _HOUR
    hours = gaps = 0
    closed: list[str] = []
    hour = first_hour
    while hour <= last_hour:
        def value(entity_id: str | None, h: datetime = hour) -> float | None:
            return by_entity.get(entity_id, {}).get(h) if entity_id else None

        imp, exp = value(import_id), value(export_id)
        fox = None if imp is None or exp is None else (imp, exp)
        if fox is None:
            gaps += 1
        consumers = {slug: value(eid) for slug, eid in consumer_ids.items()}
        tick = hour + _HOUR
        engine.on_tick(tick, fox, value(pv_id), consumers, limits)
        closed.extend(r.month for r in engine.catch_up(tick, tariff, before_cross, limits))
        hours += 1
        hour = tick
        if hours % _YIELD_EVERY == 0:
            await asyncio.sleep(0)

    # Corrections for months from the start date: replay in their original order.
    for corr in old.corrections:
        if corr.get("month", "") < from_month:
            continue
        try:
            engine._apply_correction(Decimal(str(corr["amount"])), corr["month"])  # noqa: SLF001
        except (ValueError, KeyError, ArithmeticError):
            _LOGGER.warning("Storage correction %s kWh for %s skipped in rebuild", corr.get("amount"), corr.get("month"))
    new.corrections = [dict(c) for c in old.corrections]

    for row in new.history:
        if row.month in invoices:
            row.invoice = invoices.pop(row.month)
    if invoices:
        _LOGGER.warning("Invoices of months not closed by the rebuild were dropped: %s", sorted(invoices))

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
    _LOGGER.info("Rebuild finished: %d hours, %d gaps, closed months: %s", hours, gaps, closed or "none")
    return {"hours": hours, "gaps": gaps, "closed": closed}
