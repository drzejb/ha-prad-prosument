"""Prad - net-metering prosumer integration for Home Assistant."""
from __future__ import annotations

CONFIG_SCHEMA = None
try:
    from homeassistant.helpers import config_validation as cv

    CONFIG_SCHEMA = cv.config_entry_only_config_schema("prad")
except ModuleNotFoundError:  # logic tests on Windows run without Home Assistant
    pass


async def async_setup(hass, config) -> bool:
    """Set up the integration: register the services once."""
    from .services import async_register_services

    async_register_services(hass)
    return True


async def async_setup_entry(hass, entry) -> bool:
    """Set up Prad from a config entry."""
    from homeassistant.const import EVENT_HOMEASSISTANT_STOP
    from homeassistant.core import CoreState
    from homeassistant.helpers.event import async_track_time_change
    from homeassistant.helpers.start import async_at_started
    from homeassistant.util import dt as dt_util

    from .const import PLATFORMS
    from .coordinator import PradCoordinator

    coordinator = PradCoordinator(hass, entry)
    await coordinator.async_load()
    entry.runtime_data = coordinator

    async def _tick(now) -> None:
        if hass.state is not CoreState.running:
            return
        await coordinator.async_tick(now)

    async def _refresh(now) -> None:
        # Zone and price change on the hour; entities recompute from the clock.
        coordinator.async_update_listeners()

    async def _close(now) -> None:
        if hass.state is not CoreState.running:
            return
        await coordinator.async_close_pending(now)

    async def _weather(now) -> None:
        if hass.state is not CoreState.running:
            return
        _start_weather(now)

    def _start_weather(now) -> None:
        # Background task: cancelled automatically when the entry unloads.
        entry.async_create_background_task(hass, coordinator.async_update_weather(now), "prad weather")

    async def _started(_hass) -> None:
        now = dt_util.now()
        await coordinator.async_tick(now)
        _start_weather(now)
        await coordinator.async_close_pending(now)

    async def _stop(_event) -> None:
        await coordinator.async_save_locked()

    entry.async_on_unload(async_track_time_change(hass, _tick, minute=0, second=10))
    entry.async_on_unload(async_track_time_change(hass, _refresh, minute=0, second=0))
    entry.async_on_unload(async_track_time_change(hass, _close, hour=1, minute=0, second=30))
    entry.async_on_unload(async_track_time_change(hass, _close, minute=5, second=0))
    entry.async_on_unload(async_track_time_change(hass, _weather, hour=0, minute=30, second=0))
    entry.async_on_unload(async_track_time_change(hass, _weather, minute=20, second=0))
    unsub_started = async_at_started(hass, _started)
    if unsub_started is not None:
        entry.async_on_unload(unsub_started)
    entry.async_on_unload(hass.bus.async_listen(EVENT_HOMEASSISTANT_STOP, _stop))
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_update_listener(hass, entry) -> None:
    """Reload the entry after options changed."""
    hass.config_entries.async_schedule_reload(entry.entry_id)


async def async_unload_entry(hass, entry) -> bool:
    """Unload a config entry."""
    from .const import PLATFORMS

    await entry.runtime_data.async_save_locked()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
