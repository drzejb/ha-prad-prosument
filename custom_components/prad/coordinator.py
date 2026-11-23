"""Coordinator: storage, tick, month close and settings for Prad."""
from __future__ import annotations

import asyncio
import copy
import logging
import math
from datetime import date, datetime, timedelta
from dataclasses import replace
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from homeassistant.components import persistent_notification
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
    BACKUP_V02_KEY,
    CONF_CONSUMERS,
    CONF_EXPORT,
    CONF_GRID_MODE,
    CONF_IMPORT,
    CONF_NET,
    CONF_PV,
    CONF_START,
    CONF_TEMP,
    CSV_CONSUMERS,
    CSV_DIR,
    CSV_INVOICES,
    CSV_MONTHS,
    DOMAIN,
    GRID_NET,
    GRID_TWO,
    KEY_PRICE_NAME,
    KEY_RATIO,
    NOTIFICATION_REBUILD,
    PRICE_KEYS,
    RESERVED_SLUGS,
    SETTING_DEFAULTS,
    STORAGE_KEY,
    STORAGE_MINOR,
    STORAGE_VERSION,
)
from .logic import csvio
from .logic.engine import Engine, Limits
from .logic.migrate import migrate_v1_to_v2
from .logic.state import HistoryRow, State
from .logic.tariff import (
    OpustEntry,
    PriceListError,
    PriceVersion,
    Pricing,
    empty_price,
    example_price,
    price_at,
    validate_opust,
    validate_prices,
    with_opust,
    with_version,
    zones_differ,
)
from .logic.zones import G11, TZ, ZoneSchedule, days_in_month, month_key
from .weather import async_collect_weather, merge_weather

_LOGGER = logging.getLogger(__name__)

type PradConfigEntry = ConfigEntry[PradCoordinator]
type SettingValue = Decimal | str | bool | None


class PradStore(Store[dict[str, Any]]):
    """Store with the migration of the v0.2 file (price versions, segments)."""

    async def _async_migrate_func(
        self, old_major_version: int, old_minor_version: int, old_data: dict[str, Any]
    ) -> dict[str, Any]:
        if not (old_major_version == 1 and old_minor_version < 2):
            return old_data
        # One-off copy of the v0.2 file, never overwritten.
        backup: Store[dict[str, Any]] = Store(self.hass, STORAGE_VERSION, BACKUP_V02_KEY)
        if await backup.async_load() is None:
            await backup.async_save(copy.deepcopy(old_data))
        if not isinstance(old_data, dict) or "state" not in old_data:
            raise ValueError("v0.2 payload without state")  # never turn it into an empty state
        new = migrate_v1_to_v2(old_data, dt_util.now().astimezone(TZ).date())
        # HA saves the file right after this function returns: validate the result first. Any exception
        # propagates, so the file and its minor_version stay untouched (async_load turns it into an error).
        State.from_dict(new["state"])
        settings = new["settings"]
        validate_prices([PriceVersion.from_dict(v) for v in settings["cenniki"]])
        validate_opust([OpustEntry.from_dict(o) for o in settings["opust"]])
        return new


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
        self.prices: list[PriceVersion] = [example_price(date(2026, 1, 1))]
        self.opust: list[OpustEntry] = [OpustEntry(date(2026, 1, 1), Decimal("0.8"))]
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

        try:
            data = await self.store.async_load()
        except Exception as err:  # noqa: BLE001 - HA does not write the file when async_load raises
            raise ConfigEntryError(
                "Migracja stanu prad nie powiodła się; plik stanu nie został zmieniony"
            ) from err
        if data is None:
            state = State(storage_start=month_key(dt_util.now()))
            first = self.today().replace(day=1)
            start = self.config_entry.data.get(CONF_START) or {}
            layout = start.get("rozklad", "tauron_g12w")
            try:
                if layout == "tauron_g12w":
                    price = example_price(first)
                elif layout == "g11":
                    price = empty_price(first, G11)
                else:
                    price = empty_price(first, ZoneSchedule.from_dict(start["strefy"]))
                self.prices = [price]
                self.opust = [OpustEntry(first, Decimal(str(start.get("opust", "0.8"))))]
            except (KeyError, TypeError, ValueError, ArithmeticError, AttributeError) as err:
                raise ConfigEntryError(f"Początkowe ustawienia cennika są niepoprawne: {err}") from err
        else:
            try:
                state = State.from_dict(data["state"])
                stored = data.get("settings")
                self.prices = sorted(
                    (PriceVersion.from_dict(v) for v in stored["cenniki"]), key=lambda v: v.od
                )
                self.opust = sorted((OpustEntry.from_dict(o) for o in stored["opust"]), key=lambda e: e.od)
                validate_prices(self.prices)
                validate_opust(self.opust)
            except (KeyError, TypeError, ValueError, ArithmeticError, AttributeError) as err:
                # Never overwrite data that cannot be read: keep a copy and refuse to start.
                await self.async_save_backup(data)
                raise ConfigEntryError(f"Stored state of {DOMAIN} cannot be read: {err}") from err
            for key in SETTING_DEFAULTS:
                if key not in stored:
                    continue
                try:
                    self.settings[key] = coerce_setting(key, stored[key])
                except (ValueError, TypeError, ArithmeticError):
                    _LOGGER.warning("Invalid stored setting %s, using the default value", key)
        self.engine = Engine(
            state, dict(self.consumers), grid_mode=self.grid_mode(), has_pv=self.has_pv()
        )
        # Before the first tick: a changed meter set must drop its reference (net mode, PV, consumers).
        if self.engine.sync_sources(self._source_signatures(), self.today()) or data is None:
            await self.async_save()

    def _source_signatures(self) -> dict[str, str]:
        data = self.config_entry.data
        if self.grid_mode() == GRID_NET:
            signatures = {"fox": f"{GRID_NET}|{data.get(CONF_NET)}"}
        else:
            signatures = {"fox": f"{GRID_TWO}|{data.get(CONF_IMPORT)}|{data.get(CONF_EXPORT)}"}
        if self.has_pv():
            signatures["pv"] = str(data.get(CONF_PV))
        for slug, entity_id in self.consumer_entities.items():
            signatures[slug] = entity_id
        return signatures

    # ------------------------------------------------------------- config

    def today(self) -> date:
        return dt_util.now().astimezone(TZ).date()

    def grid_mode(self) -> str:
        return self.config_entry.data.get(CONF_GRID_MODE, GRID_TWO)

    def has_pv(self) -> bool:
        return bool(self.config_entry.data.get(CONF_PV))

    def pricing(self) -> Pricing:
        return Pricing(tuple(self.prices), tuple(self.opust))

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
        await self.store.async_save(self.payload())

    def payload(self) -> dict[str, Any]:
        """The whole content of the Store (also used for the backup before prad.inicjuj)."""
        return {
            "state": self.engine.state.to_dict(),
            "settings": {
                **{k: _serialize(v) for k, v in self.settings.items()},
                "cenniki": [v.to_dict() for v in self.prices],
                "opust": [e.to_dict() for e in self.opust],
            },
        }

    async def async_save_locked(self, *, final: bool = False) -> None:
        async with self.lock:
            await self.async_save(final=final)

    # ---------------------------------------------------------- settings

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

    def setting_value(self, key: str) -> Decimal | str | bool | None:
        today = self.today()
        if key in PRICE_KEYS:
            return getattr(price_at(self.prices, today), PRICE_KEYS[key])
        if key == KEY_PRICE_NAME:
            return price_at(self.prices, today).nazwa
        if key == KEY_RATIO:
            return self.pricing().ratio_at(today)
        return self.settings.get(key)

    def _opust_today(self) -> OpustEntry:
        today = self.today()
        current = self.opust[0]
        for entry in self.opust:
            if entry.od <= today:
                current = entry
        return current

    async def async_apply_prices(self, versions: list[PriceVersion], *, force: bool = False) -> None:
        async with self.lock:
            versions = sorted(versions, key=lambda v: v.od)
            validate_prices(versions)
            rng = self.engine.counted_range()
            touched = rng is not None and zones_differ(self.prices, versions, *rng)
            if touched and not force:
                raise PriceListError(
                    "counted_days",
                    "Strefy tych dni są już policzone – dodaj nowy cennik od pierwszego dnia następnego "
                    "miesiąca albo zaznacz »Zmieniam mimo policzonych dni« i potem uruchom prad.inicjuj",
                )
            self.prices = versions
            await self.async_save()
        self.async_update_listeners()
        if touched and force:
            persistent_notification.async_create(
                self.hass,
                "Uruchom prad.inicjuj, żeby przeliczyć strefy",
                title="Prąd",
                notification_id=NOTIFICATION_REBUILD,
            )

    async def async_apply_opust(self, entries: list[OpustEntry]) -> None:
        async with self.lock:
            entries = sorted(entries, key=lambda e: e.od)
            validate_opust(entries)
            self.opust = entries
            await self.async_save()
        self.async_update_listeners()

    async def async_set_setting(self, key: str, value: Any) -> None:
        if key in PRICE_KEYS or key == KEY_PRICE_NAME:
            current = price_at(self.prices, self.today())
            if key == KEY_PRICE_NAME:
                new = replace(current, nazwa=str(value).strip())
            else:
                try:
                    number = Decimal(str(value))
                except InvalidOperation as err:
                    raise ValueError(f"{key}: invalid number") from err
                new = replace(current, **{PRICE_KEYS[key]: number})
            await self.async_apply_prices(with_version(self.prices, current.od, new))
            return
        if key == KEY_RATIO:
            current_entry = self._opust_today()
            try:
                ratio = Decimal(str(value))
            except InvalidOperation as err:
                raise ValueError(f"{key}: invalid number") from err
            new_entry = OpustEntry(current_entry.od, ratio)
            await self.async_apply_opust(with_opust(self.opust, current_entry.od, new_entry))
            return
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
        if self.grid_mode() == GRID_NET:
            net = self._read(data.get(CONF_NET))
            fox = None if net is None else (net,)
        else:
            imp = self._read(data.get(CONF_IMPORT))
            exp = self._read(data.get(CONF_EXPORT))
            fox = None if imp is None or exp is None else (imp, exp)
        pv = self._read(data.get(CONF_PV))
        consumers = {slug: self._read(eid) for slug, eid in self.consumer_entities.items()}
        async with self.lock:
            pricing = self.pricing()
            warnings = self.engine.on_tick(now, fox, pv, consumers, self.limits(), pricing)
            for message in warnings:
                _LOGGER.warning("%s", message)
            before_cross = self.storage_before_cross()
            for month in self.engine.months_ready_to_freeze(now):
                self.engine.freeze_month(month, pricing, before_cross)
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
                self.engine.catch_up(now, self.pricing(), self.storage_before_cross(), self.limits())
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
        if self.invoice_month is not None and self.invoice_month not in months:
            # The chosen month left the history (e.g. a rebuild): drop the whole draft.
            self.invoice_month = None
            self.invoice_amount = None
        if self.invoice_month is not None:
            return self.invoice_month
        for month in months:
            row = self._history_row(month)
            if row is not None and row.invoice is None:
                return month
        return months[0] if months else None

    def invoice_form_amount(self) -> Decimal | None:
        """Typed amount, else the invoice already stored for the form month."""
        month = self.invoice_form_month()  # first: drops a draft whose month left the history
        if self.invoice_amount is not None:
            return self.invoice_amount
        row = self._history_row(month)
        return None if row is None else row.invoice

    def select_invoice_month(self, month: str) -> None:
        self.invoice_month = month
        self.invoice_amount = None  # show the stored invoice of that month, if any
        self.async_update_listeners()

    def set_invoice_amount(self, amount: Decimal) -> None:
        # Pin the month the amount was typed for, so a newly closed month cannot take it over.
        month = self.invoice_form_month()
        if month is None:
            raise ValueError("Brak zamkniętych miesięcy – nie ma do czego wpisać faktury")
        self.invoice_month = month
        self.invoice_amount = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        self.async_update_listeners()

    def clear_invoice_draft(self) -> None:
        self.invoice_month = None
        self.invoice_amount = None
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
