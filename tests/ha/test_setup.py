"""Setup, config flow, tick and persistence tests for the Prad integration."""
import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.prad.const import DOMAIN

SENSORS = {
    "import_entity": "sensor.test_import",
    "export_entity": "sensor.test_export",
    "pv_entity": "sensor.test_pv",
    "temperature_entity": "sensor.test_temperature",
}


async def _setup_entry(hass) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data=SENSORS, options={"consumers": []})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _tick_at(hass, freezer, when: str) -> None:
    freezer.move_to(when)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_config_flow_creates_entry(hass):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(result["flow_id"], SENSORS)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == SENSORS


async def test_second_entry_aborted(hass):
    await _setup_entry(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.ABORT


async def test_tick_updates_state(hass, freezer):
    freezer.move_to("2026-11-23 09:30:00+01:00")
    entry = await _setup_entry(hass)
    hass.states.async_set("sensor.test_import", "100")
    hass.states.async_set("sensor.test_export", "50")
    await _tick_at(hass, freezer, "2026-11-23 10:00:10+01:00")
    hass.states.async_set("sensor.test_import", "101.5")
    hass.states.async_set("sensor.test_export", "50.2")
    await _tick_at(hass, freezer, "2026-11-23 11:00:10+01:00")
    state = entry.runtime_data.engine.state
    assert state.months["2026-11"].import_peak == pytest.approx(1.3)


async def test_state_persists_after_reload(hass, freezer):
    freezer.move_to("2026-11-23 09:30:00+01:00")
    entry = await _setup_entry(hass)
    hass.states.async_set("sensor.test_import", "100")
    hass.states.async_set("sensor.test_export", "50")
    await _tick_at(hass, freezer, "2026-11-23 10:00:10+01:00")
    hass.states.async_set("sensor.test_import", "101.5")
    hass.states.async_set("sensor.test_export", "50.2")
    await _tick_at(hass, freezer, "2026-11-23 11:00:10+01:00")
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    state = entry.runtime_data.engine.state
    assert state.months["2026-11"].import_peak == pytest.approx(1.3)
