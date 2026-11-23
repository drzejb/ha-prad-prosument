"""Meter change in the options flow and net meter mode."""
import pytest
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.prad.const import DOMAIN
from test_setup import _setup_entry, _tick_at


async def test_changing_meter_keeps_history_and_drops_reference(hass, freezer):
    freezer.move_to("2026-11-23 09:30:00+01:00")
    entry = await _setup_entry(hass)
    hass.states.async_set("sensor.test_import", "100"); hass.states.async_set("sensor.test_export", "50")
    await _tick_at(hass, freezer, "2026-11-23 10:00:10+01:00")
    hass.states.async_set("sensor.test_import", "101.5")
    await _tick_at(hass, freezer, "2026-11-23 11:00:10+01:00")
    assert entry.runtime_data.engine.state.months["2026-11"].import_peak == pytest.approx(1.5)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    fid = result["flow_id"]
    await hass.config_entries.options.async_configure(fid, {"next_step_id": "instalacja"})
    result = await hass.config_entries.options.async_configure(fid, {"next_step_id": "liczniki"})
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "liczniki"
    result = await hass.config_entries.options.async_configure(fid, {
        "tryb_sieci": "dwa", "import_entity": "sensor.test_import_new", "export_entity": "sensor.test_export"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.data["import_entity"] == "sensor.test_import_new" and "pv_entity" not in entry.data
    assert entry.options == {"consumers": []}
    state = entry.runtime_data.engine.state                                  # entry reloaded
    assert state.months["2026-11"].import_peak == pytest.approx(1.5)
    assert "fox" not in state.refs and "pv" not in state.refs and state.zmiana_licznika == "2026-11-23"
    hass.states.async_set("sensor.test_import_new", "5000")
    await _tick_at(hass, freezer, "2026-11-23 12:00:10+01:00")              # this hour only sets the reference point
    assert entry.runtime_data.engine.state.months["2026-11"].import_peak == pytest.approx(1.5)


async def test_net_meter_tick(hass, freezer):
    freezer.move_to("2026-11-23 09:30:00+01:00")
    entry = MockConfigEntry(domain=DOMAIN, data={"tryb_sieci": "netto", "net_entity": "sensor.test_net"},
                            options={"consumers": []}, version=1, minor_version=2)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    hass.states.async_set("sensor.test_net", "100")
    await _tick_at(hass, freezer, "2026-11-23 10:00:10+01:00")
    hass.states.async_set("sensor.test_net", "98")
    await _tick_at(hass, freezer, "2026-11-23 11:00:10+01:00")
    m = entry.runtime_data.engine.state.months["2026-11"]
    assert m.export_peak == pytest.approx(2.0) and m.pv_brak is True
    assert entry.runtime_data.engine.state.zrodla["fox"] == "netto|sensor.test_net"
