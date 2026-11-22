"""Coordinator: storage, tick, month close and settings for Prad."""
from __future__ import annotations

import asyncio
import logging
import math
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_UNIT_OF_MEASUREMENT,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util
from homeassistant.util import slugify

from .const import (
    BACKUP_KEY,
    CONF_CONSUMERS,
    CONF_EXPORT,
    CONF_IMPORT,
    CONF_PV,
    CSV_CONSUMERS,
    CSV_DIR,
    CSV_MONTHS,
    DOMAIN,
    RESERVED_SLUGS,
    SETTING_DEFAULTS,
    STORAGE_KEY,
    STORAGE_MINOR,
    STORAGE_VERSION,
    TARIFF_FIELDS,
)
from .logic import csvio
from .logic.engine import Engine, Limits
from .logic.state import HistoryRow, State
from .logic.tariff import Tariff
from .logic.zones import month_key

_LOGGER = logging.getLogger(__name__)

type PradConfigEntry = ConfigEntry[PradCoordinator]
type SettingValue = Decimal | str | bool | None


class PradStore(Store[dict[str, Any]]):
    """Store with a hook for future schema migrations."""

    async def _async_migrate_func(
        self, old_major_version: int, old_minor_version: int, old_data: dict[str, Any]
    ) -> dict[str, Any]:
        return old_data


def coerce_setting(key: str, raw: Any) -> SettingValue:
    """Convert a raw value to the type of the setting; raise ValueError when invalid."""
    default = SETTING_DEFAULTS[key]
    if isinstance(default, bool):
        if not isinstance(raw, bool):
            raise ValueError(f"{key}: expected bool")
        return raw
    if isinstance(default, Decimal):
        if isinstance(raw, bool) or raw is None:
            raise ValueError(f"{key}: expected number")
        try:
            value = Decimal(str(raw))
        except InvalidOperation as err:
            raise ValueError(f"{key}: invalid number") from err
        if not value.is_finite():
            raise ValueError(f"{key}: not finite")
        return value
    if raw is None:
        if default is None:
            return None
        raise ValueError(f"{key}: expected text")
    return str(raw)


def _serialize(value: SettingValue) -> Any:
    return str(value) if isinstance(value, Decimal) else value


class PradCoordinator(DataUpdateCoordinator[None]):
    """Owns the engine, the Store and all calls into the engine."""

    config_entry: PradConfigEntry

    def __init__(self, hass: HomeAssistant, entry: PradConfigEntry) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, config_entry=entry)
        self.lock = asyncio.Lock()
        self.store = PradStore(hass, STORAGE_VERSION, STORAGE_KEY, minor_version=STORAGE_MINOR)
        self.backup_store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, BACKUP_KEY)
        self.settings: dict[str, SettingValue] = dict(SETTING_DEFAULTS)
        self.consumers: dict[str, str] = {}  # slug -> display name
        self.consumer_entities: dict[str, str] = {}  # slug -> entity_id
        self.engine = Engine(State(), {})

    async def _async_update_data(self) -> None:
        return None

    # ----------------------------------------------------------- storage

    async def async_load(self) -> None:
        for item in self.config_entry.options.get(CONF_CONSUMERS, []):
            slug = slugify(item["name"])
            if not slug or slug in RESERVED_SLUGS:
                _LOGGER.warning("Skipping consumer with reserved or empty name %r", item["name"])
                continue
            self.consumers[slug] = item["name"]
            self.consumer_entities[slug] = item["entity_id"]

        data = await self.store.async_load()
        if data is None:
            state = State(storage_start=month_key(dt_util.now()))
        else:
            try:
                state = State.from_dict(data["state"])
            except (KeyError, TypeError, ValueError, ArithmeticError, AttributeError) as err:
                # Never overwrite data that cannot be read: keep a copy and refuse to start.
                await self.backup_store.async_save(data)
                raise ConfigEntryError(f"Stored state of {DOMAIN} cannot be read: {err}") from err
            stored = data.get("settings")
            if isinstance(stored, dict):
                for key in SETTING_DEFAULTS:
                    if key not in stored:
                        continue
                    try:
                        self.settings[key] = coerce_setting(key, stored[key])
                    except (ValueError, TypeError, ArithmeticError):
                        _LOGGER.warning("Invalid stored setting %s, using the default value", key)
        self.engine = Engine(state, dict(self.consumers))

    async def async_save(self) -> None:
        await self.store.async_save(
            {
                "state": self.engine.state.to_dict(),
                "settings": {k: _serialize(v) for k, v in self.settings.items()},
            }
        )

    async def async_save_locked(self) -> None:
        async with self.lock:
            await self.async_save()

    # ---------------------------------------------------------- settings

    def tariff(self) -> Tariff:
        values: dict[str, Any] = {}
        for key, field_name in TARIFF_FIELDS.items():
            values[field_name] = self._setting_or_default(key)
        return Tariff(**values)

    def limits(self) -> Limits:
        return Limits(
            fox=float(self._setting_or_default("bezpiecznik_fox")),
            pv=float(self._setting_or_default("bezpiecznik_pv")),
            consumer=float(self._setting_or_default("bezpiecznik_odbiorniki")),
            degree_day_base=float(self._setting_or_default("baza_stopniodni")),
        )

    def storage_before_cross(self) -> bool:
        return bool(self.settings.get("magazyn_przed_przeniesieniem"))

    def _setting_or_default(self, key: str) -> Any:
        try:
            return coerce_setting(key, self.settings.get(key))
        except (ValueError, TypeError, ArithmeticError):
            _LOGGER.warning("Invalid setting %s, using the default value", key)
            return SETTING_DEFAULTS[key]

    async def async_set_setting(self, key: str, value: Any) -> None:
        if key not in SETTING_DEFAULTS:
            raise ValueError(f"Unknown setting {key}")
        coerced = coerce_setting(key, value)
        async with self.lock:
            self.settings[key] = coerced
            await self.async_save()
        self.async_update_listeners()

    # -------------------------------------------------------------- tick

    def _read(self, entity_id: str | None) -> float | None:
        if not entity_id:
            return None
        st = self.hass.states.get(entity_id)
        if st is None or st.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            return None
        try:
            value = float(st.state)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(value):
            return None
        if st.attributes.get(ATTR_UNIT_OF_MEASUREMENT) == "Wh":
            value /= 1000
        return value

    async def async_tick(self, now: datetime) -> None:
        data = self.config_entry.data
        imp = self._read(data.get(CONF_IMPORT))
        exp = self._read(data.get(CONF_EXPORT))
        fox = None if imp is None or exp is None else (imp, exp)
        pv = self._read(data.get(CONF_PV))
        consumers = {slug: self._read(eid) for slug, eid in self.consumer_entities.items()}
        async with self.lock:
            warnings = self.engine.on_tick(now, fox, pv, consumers, self.limits())
            for message in warnings:
                _LOGGER.warning("%s", message)
            tariff = self.tariff()
            before_cross = self.storage_before_cross()
            for month in self.engine.months_ready_to_freeze(now):
                self.engine.freeze_month(month, tariff, before_cross)
            await self.async_save()
        self.async_update_listeners()

    # ------------------------------------------------------------- close

    async def async_close_pending(self, now: datetime) -> None:
        # Task 11: fetch weather before catch_up
        async with self.lock:
            self.engine.catch_up(now, self.tariff(), self.storage_before_cross(), self.limits())
            unwritten = [row for row in self.engine.state.history if not row.csv_written]
            # Store first (rows still csv_written=False), then CSV, then the flag.
            # Saved unconditionally: catch_up may have frozen months without closing them.
            await self.async_save()
            if not unwritten:
                return
            for row in unwritten:
                await self.hass.async_add_executor_job(self._write_csv, row)
                row.csv_written = True
                await self.async_save()
        self.async_update_listeners()

    def _write_csv(self, row: HistoryRow) -> None:
        base = Path(self.hass.config.path(CSV_DIR))
        csvio.append_month_row(base / CSV_MONTHS, row)
        csvio.append_consumer_rows(base / CSV_CONSUMERS, row)
