"""Coordinator: storage, tick, month close and settings for Prad."""
from __future__ import annotations

import asyncio
import logging
import math
from datetime import date, datetime, timedelta
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
    CONF_TEMP,
    CSV_CONSUMERS,
    CSV_DIR,
    CSV_INVOICES,
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
from .logic.zones import TZ, days_in_month, month_key
from .weather import async_collect_weather, merge_weather

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
        self.prev_backup_store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"{BACKUP_KEY}_prev")
        self.settings: dict[str, SettingValue] = dict(SETTING_DEFAULTS)
        self.consumers: dict[str, str] = {}  # slug -> display name
        self.consumer_entities: dict[str, str] = {}  # slug -> entity_id
        self.engine = Engine(State(), {})
        self._unloaded = False  # set on unload: later saves would overwrite the state of the new instance
        # Invoice form draft (select + number + button); in memory only, never stored.
        self.invoice_month: str | None = None
        self.invoice_amount: Decimal | None = None

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
                await self.async_save_backup(data)
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

    async def async_save_backup(self, data: dict[str, Any]) -> None:
        """Keep two backups: the latest in BACKUP_KEY and the one before it in BACKUP_KEY_prev."""
        previous = await self.backup_store.async_load()
        if previous is not None:
            await self.prev_backup_store.async_save(previous)
        await self.backup_store.async_save(data)

    def mark_unloaded(self) -> None:
        self._unloaded = True

    async def async_save(self, *, final: bool = False) -> None:
        if self._unloaded and not final:
            return
        await self.store.async_save(
            {
                "state": self.engine.state.to_dict(),
                "settings": {k: _serialize(v) for k, v in self.settings.items()},
            }
        )

    async def async_save_locked(self, *, final: bool = False) -> None:
        async with self.lock:
            await self.async_save(final=final)

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
        unit = st.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
        if unit == "Wh":
            value /= 1000
        elif unit == "MWh":
            value *= 1000
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
        try:
            await self._async_fill_weather_for_closing(now)
        except Exception:  # noqa: BLE001 - closing must not wait for weather
            _LOGGER.warning("Weather for closing months could not be fetched, closing anyway")
        try:
            async with self.lock:
                self.engine.catch_up(now, self.tariff(), self.storage_before_cross(), self.limits())
                unwritten = [row for row in self.engine.state.history if not row.csv_written]
                # Store first (rows still csv_written=False), then CSV, then the flag.
                # Saved unconditionally: catch_up may have frozen months without closing them.
                await self.async_save()
                for row in unwritten:
                    await self.hass.async_add_executor_job(self._write_csv, row)
                    row.csv_written = True
                    await self.async_save()
        finally:
            self.async_update_listeners()  # entities must reflect closed months even after a CSV failure

    async def async_fill_weather(self, days: list[date]) -> None:
        """Fill missing temperature and radiation for completed days (network outside the lock)."""
        today = dt_util.now().astimezone(TZ).date()
        found = await async_collect_weather(
            self.hass, self.config_entry.data.get(CONF_TEMP), self.engine.state.weather, days, today
        )
        if found:
            async with self.lock:
                merge_weather(self.engine.state.weather, found)
                await self.async_save()

    async def _async_fill_weather_for_closing(self, now: datetime) -> None:
        state = self.engine.state
        current = month_key(now)
        days = [
            date(int(m[:4]), int(m[5:]), n)
            for m in {*state.pending, *state.months}
            if m < current
            for n in range(1, days_in_month(m) + 1)
        ]
        if days:
            await self.async_fill_weather(days)

    async def async_update_weather(self, now: datetime) -> None:
        """Fill weather gaps of the last 7 completed days."""
        today = now.astimezone(TZ).date()
        await self.async_fill_weather([today - timedelta(days=n) for n in range(1, 8)])

    # ------------------------------------------------------------ invoices

    def history_months(self) -> list[str]:
        """Closed months of the history, newest first."""
        return sorted({row.month for row in self.engine.state.history}, reverse=True)

    def _history_row(self, month: str | None) -> HistoryRow | None:
        return next((row for row in self.engine.state.history if row.month == month), None)

    def invoice_form_month(self) -> str | None:
        """Chosen month, else the newest month without an invoice, else the newest month."""
        months = self.history_months()
        if self.invoice_month in months:
            return self.invoice_month
        for month in months:
            row = self._history_row(month)
            if row is not None and row.invoice is None:
                return month
        return months[0] if months else None

    def invoice_form_amount(self) -> Decimal | None:
        """Typed amount, else the invoice already stored for the form month."""
        if self.invoice_amount is not None:
            return self.invoice_amount
        row = self._history_row(self.invoice_form_month())
        return None if row is None else row.invoice

    def select_invoice_month(self, month: str) -> None:
        self.invoice_month = month
        self.invoice_amount = None  # show the stored invoice of that month, if any
        self.async_update_listeners()

    def set_invoice_amount(self, amount: Decimal) -> None:
        self.invoice_amount = amount
        self.async_update_listeners()

    async def async_set_invoice(self, month: str, amount: Decimal) -> None:
        """Store the invoice of a closed month and append it to the invoices CSV.

        Raises ValueError (unknown month, negative amount) and OSError (CSV write failed;
        the invoice is already stored in that case).
        """
        async with self.lock:
            self.engine.set_invoice(month, amount)
            await self.async_save()  # Store first, so the invoice survives a CSV failure
            path = Path(self.hass.config.path(CSV_DIR)) / CSV_INVOICES
            try:
                await self.hass.async_add_executor_job(
                    csvio.append_invoice, path, month, amount, dt_util.now()
                )
            finally:
                self.async_update_listeners()

    def _write_csv(self, row: HistoryRow) -> None:
        base = Path(self.hass.config.path(CSV_DIR))
        csvio.append_month_row(base / CSV_MONTHS, row)
        csvio.append_consumer_rows(base / CSV_CONSUMERS, row)
