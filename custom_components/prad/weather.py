"""Daily weather for Prad: mean temperature from recorder statistics, radiation from Open-Meteo."""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import aiohttp
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .logic.zones import TZ

_LOGGER = logging.getLogger(__name__)

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
MAX_PAST_DAYS = 92  # Open-Meteo forecast endpoint limit
ARCHIVE_AFTER_DAYS = 90  # days older than this come from the archive endpoint
ARCHIVE_LAG_DAYS = 7  # the archive has no data for the last days; never ask it for them
REQUEST_TIMEOUT = 20
RETRY_DELAY = 2.0  # seconds between the two attempts


def local_midnight(day: date) -> datetime:
    return datetime.combine(day, time(0), tzinfo=TZ)


def _row_start(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromtimestamp(float(value), tz=UTC)


async def _async_fetch_daily(
    hass: HomeAssistant, url: str, params: dict[str, Any], with_temp: bool
) -> dict[str, dict[str, float]]:
    """One Open-Meteo daily request with a retry; {} on any failure.

    Result: day -> {"radiation": x, "temp": y}; None values and a missing temperature list are skipped.
    """
    session = async_get_clientsession(hass)
    timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
    for attempt in (1, 2):
        try:
            async with session.get(url, params=params, timeout=timeout) as resp:
                resp.raise_for_status()
                data = await resp.json()
            daily = data["daily"]
            days = daily["time"]
            result: dict[str, dict[str, float]] = {}
            for key, field in (("shortwave_radiation_sum", "radiation"), ("temperature_2m_mean", "temp")):
                if key == "temperature_2m_mean" and (not with_temp or key not in daily):
                    continue
                for day, value in zip(days, daily[key], strict=True):
                    if value is not None:
                        result.setdefault(str(day), {})[field] = float(value)
            return result
        except (aiohttp.ClientError, TimeoutError, ValueError, KeyError, TypeError) as err:
            if attempt == 1:
                await asyncio.sleep(RETRY_DELAY)
                continue
            # No URL, no coordinates, no traceback: they would reveal the home location.
            _LOGGER.warning(
                "Open-Meteo request failed: %s %s", type(err).__name__, getattr(err, "status", "-")
            )
    return {}


def _daily_param(with_temp: bool) -> str:
    return "shortwave_radiation_sum,temperature_2m_mean" if with_temp else "shortwave_radiation_sum"


async def async_fetch_weather(
    hass: HomeAssistant, past_days: int, with_temp: bool
) -> dict[str, dict[str, float]]:
    """Daily radiation (MJ/m2) and optionally mean temperature keyed by local day; {} on failure."""
    params = {
        "latitude": hass.config.latitude,
        "longitude": hass.config.longitude,
        "daily": _daily_param(with_temp),
        "past_days": max(0, min(int(past_days), MAX_PAST_DAYS)),
        "forecast_days": 1,
        "timezone": "Europe/Warsaw",
    }
    return await _async_fetch_daily(hass, OPEN_METEO_URL, params, with_temp)


async def async_fetch_weather_archive(
    hass: HomeAssistant, first: date, last: date, with_temp: bool
) -> dict[str, dict[str, float]]:
    """Daily weather for old days from the Open-Meteo archive; {} on any failure."""
    params = {
        "latitude": hass.config.latitude,
        "longitude": hass.config.longitude,
        "start_date": first.isoformat(),
        "end_date": last.isoformat(),
        "daily": _daily_param(with_temp),
        "timezone": "Europe/Warsaw",
    }
    return await _async_fetch_daily(hass, OPEN_METEO_ARCHIVE_URL, params, with_temp)


async def async_fetch_radiation(hass: HomeAssistant, past_days: int) -> dict[str, float]:
    """Daily shortwave radiation sums (MJ/m2) keyed by local day; {} on any failure."""
    weather = await async_fetch_weather(hass, past_days, False)
    return {day: v["radiation"] for day, v in weather.items() if "radiation" in v}


async def async_fetch_radiation_archive(hass: HomeAssistant, first: date, last: date) -> dict[str, float]:
    """Daily radiation for old days from the Open-Meteo archive; {} on any failure."""
    weather = await async_fetch_weather_archive(hass, first, last, False)
    return {day: v["radiation"] for day, v in weather.items() if "radiation" in v}


async def async_daily_temperatures(
    hass: HomeAssistant, entity_id: str, first: date, last: date
) -> dict[str, float]:
    """Mean temperature per local day from `first` to `last` inclusive (one query)."""
    start = local_midnight(first)
    end = local_midnight(last + timedelta(days=1))
    stats = await get_instance(hass).async_add_executor_job(
        statistics_during_period, hass, start, end, {entity_id}, "day", None, {"mean"}
    )
    out: dict[str, float] = {}
    for row in stats.get(entity_id, []):
        mean = row.get("mean")
        if mean is not None:
            out[_row_start(row["start"]).astimezone(TZ).date().isoformat()] = float(mean)
    return out


async def async_daily_temperature(hass: HomeAssistant, entity_id: str, day: date) -> float | None:
    return (await async_daily_temperatures(hass, entity_id, day, day)).get(day.isoformat())


async def async_collect_weather(
    hass: HomeAssistant,
    temp_entity: str | None,
    weather: dict[str, dict],
    days: list[date],
    today: date,
) -> dict[str, dict[str, float]]:
    """Values missing in `weather` for the given completed days (before `today`)."""
    days = [d for d in days if d < today]
    found: dict[str, dict[str, float]] = {}
    need_temp = [d for d in days if weather.get(d.isoformat(), {}).get("temp") is None]
    need_rad = [d for d in days if weather.get(d.isoformat(), {}).get("radiation") is None]
    if temp_entity and need_temp:
        temps = await async_daily_temperatures(hass, temp_entity, min(need_temp), max(need_temp))
        for d in need_temp:
            if d.isoformat() in temps:
                found.setdefault(d.isoformat(), {})["temp"] = temps[d.isoformat()]
    with_temp = not temp_entity
    wanted = sorted(set(need_rad) | set(need_temp)) if with_temp else sorted(need_rad)
    old_days = [d for d in wanted if (today - d).days > ARCHIVE_AFTER_DAYS]
    recent_days = [d for d in wanted if (today - d).days <= ARCHIVE_AFTER_DAYS]
    fetched: dict[str, dict[str, float]] = {}

    def merge(source: dict[str, dict[str, float]], over: bool) -> None:
        # Per field: values already present win unless `over` (older source overwritten by newer).
        for day, values in source.items():
            target = fetched.setdefault(day, {})
            for field, value in values.items():
                if over or field not in target:
                    target[field] = value

    if old_days:
        merge(await async_fetch_weather_archive(hass, min(old_days), max(old_days), with_temp), True)
    if recent_days:
        merge(await async_fetch_weather(hass, (today - min(recent_days)).days, with_temp), True)
        # The forecast endpoint does not always serve the full past_days range; ask the
        # archive for the older days it left out.
        rad_set, temp_set = set(need_rad), set(need_temp) if with_temp else set()
        gaps = [
            d for d in recent_days
            if (today - d).days > ARCHIVE_LAG_DAYS
            and (
                (d in rad_set and "radiation" not in fetched.get(d.isoformat(), {}))
                or (d in temp_set and "temp" not in fetched.get(d.isoformat(), {}))
            )
        ]
        if gaps:
            merge(await async_fetch_weather_archive(hass, min(gaps), max(gaps), with_temp), False)
    for d in need_rad:
        value = fetched.get(d.isoformat(), {}).get("radiation")
        if value is not None:
            found.setdefault(d.isoformat(), {})["radiation"] = value
    if with_temp:
        for d in need_temp:
            value = fetched.get(d.isoformat(), {}).get("temp")
            if value is not None:
                found.setdefault(d.isoformat(), {})["temp"] = value
    return found


def merge_weather(weather: dict[str, dict], found: dict[str, dict[str, float]]) -> None:
    for day, values in found.items():
        weather.setdefault(day, {}).update(values)
