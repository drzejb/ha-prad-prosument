"""Setup, config flow, tick and persistence tests for the Prad integration."""
from decimal import Decimal

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.prad.const import DOMAIN

SENSORS = {
    "tryb_sieci": "dwa",
    "import_entity": "sensor.test_import",
    "export_entity": "sensor.test_export",
    "pv_entity": "sensor.test_pv",
    "temperature_entity": "sensor.test_temperature",
}


async def _setup_entry(hass) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data=SENSORS, options={"consumers": []}, version=1, minor_version=2)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _tick_at(hass, freezer, when: str) -> None:
    freezer.move_to(when)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_config_flow_two_steps_tauron(hass):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "user"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {
        "tryb_sieci": "dwa", "import_entity": "sensor.test_import", "export_entity": "sensor.test_export"})
    assert result["step_id"] == "taryfa"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"rozklad": "tauron_g12w", "opust": "0.7"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {"tryb_sieci": "dwa", "import_entity": "sensor.test_import", "export_entity": "sensor.test_export",
                              "start": {"rozklad": "tauron_g12w", "strefy": None, "opust": "0.7"}}
    await hass.async_block_till_done()
    c = result["result"].runtime_data
    assert c.opust[0].wspolczynnik == Decimal("0.7") and c.prices[0].nazwa == "Tauron G12w 2026 (przykład – sprawdź z fakturą)"
    assert c.has_pv() is False


async def test_config_flow_net_meter_with_custom_g12_hours(hass):
    hass.states.async_set("sensor.test_net", "10", {"unit_of_measurement": "kWh", "state_class": "total"})
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"tryb_sieci": "netto", "net_entity": "sensor.test_net"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"rozklad": "wlasne_g12", "opust": "inny", "opust_inny": 0.75})
    assert result["step_id"] == "strefy"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"tanie1_od": 22, "tanie1_do": 22, "weekend": "robocze", "swieta": "robocze"})
    assert result["errors"] == {"base": "zones_invalid"}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"tanie1_od": 13, "tanie1_do": 15, "tanie2_od": 22, "tanie2_do": 6, "weekend": "robocze", "swieta": "robocze"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    c = result["result"].runtime_data
    v = c.prices[0]
    assert v.taryfa == "G12" and v.strefy.id == "wlasne" and v.strefy.tanie_robocze == ((13, 15), (22, 6))
    assert v.energy_peak == Decimal("0") and v.vat_percent == Decimal("23") and c.opust[0].wspolczynnik == Decimal("0.75")
    assert c.grid_mode() == "netto"


async def test_config_flow_validates_meters(hass):
    hass.states.async_set("sensor.test_power", "10", {"unit_of_measurement": "W"})
    hass.states.async_set("sensor.test_net", "10", {"unit_of_measurement": "kWh", "state_class": "total_increasing"})
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    fid = result["flow_id"]
    assert (await hass.config_entries.flow.async_configure(fid, {"tryb_sieci": "dwa", "import_entity": "sensor.test_import"}))["errors"] == {"base": "grid_required_dwa"}
    assert (await hass.config_entries.flow.async_configure(fid, {"tryb_sieci": "netto"}))["errors"] == {"base": "grid_required_netto"}
    res = await hass.config_entries.flow.async_configure(fid, {"tryb_sieci": "dwa", "import_entity": "sensor.test_power", "export_entity": "sensor.test_export"})
    assert res["errors"] == {"import_entity": "not_energy"}                  # export without state passes
    res = await hass.config_entries.flow.async_configure(fid, {"tryb_sieci": "netto", "net_entity": "sensor.test_net"})
    assert res["errors"] == {"net_entity": "net_state_class"}
    res = await hass.config_entries.flow.async_configure(fid, {"tryb_sieci": "dwa", "import_entity": "sensor.test_import", "export_entity": "sensor.test_export"})
    assert res["step_id"] == "taryfa"
    assert (await hass.config_entries.flow.async_configure(fid, {"rozklad": "g11", "opust": "inny"}))["errors"] == {"opust_inny": "ratio_required"}


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


async def test_tick_converts_wh_and_mwh_to_kwh(hass, freezer):
    freezer.move_to("2026-11-23 09:30:00+01:00")
    entry = await _setup_entry(hass)
    hass.states.async_set("sensor.test_import", "100000", {"unit_of_measurement": "Wh"})
    hass.states.async_set("sensor.test_export", "0.05", {"unit_of_measurement": "MWh"})
    await _tick_at(hass, freezer, "2026-11-23 10:00:10+01:00")
    hass.states.async_set("sensor.test_import", "101500", {"unit_of_measurement": "Wh"})
    hass.states.async_set("sensor.test_export", "0.0502", {"unit_of_measurement": "MWh"})
    await _tick_at(hass, freezer, "2026-11-23 11:00:10+01:00")
    counters = entry.runtime_data.engine.state.months["2026-11"]
    assert counters.import_peak == pytest.approx(1.3)  # 1.5 kWh import, 0.2 kWh export


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


def _history_row(month: str):
    from decimal import Decimal

    from custom_components.prad.logic.state import HistoryRow

    d = Decimal(0)
    return HistoryRow(
        month=month, pobor_droga=d, pobor_tania=d, oddanie_droga=d, oddanie_tania=d,
        magazyn_wplyw=d, magazyn_uzyte=d, magazyn_wygasle=d, magazyn_stan=d,
        do_zaplaty_droga=d, do_zaplaty_tania=d, koszt_droga=d, koszt_tania=d,
        oplaty_stale=d, koszt_netto=d, koszt_razem=d, zuzycie_domu=0.0, produkcja_pv=0.0,
        autokonsumpcja=0.0, reszta=0.0, temp_srednia=None, stopniodni=None,
        naslonecznienie_mj=None, wersja_cennika="test", csv_written=False,
    )


async def test_close_pending_retries_unwritten_csv_row(hass, hass_storage, tmp_path):
    from pathlib import Path

    from homeassistant.util import dt as dt_util

    hass.config.config_dir = str(tmp_path)  # isolated dir: a second local run still writes
    entry = await _setup_entry(hass)
    coordinator = entry.runtime_data
    coordinator.engine.state.history.append(_history_row("2026-10"))
    await coordinator.async_close_pending(dt_util.now())
    path = Path(hass.config.path("prad")) / "prad_miesiace.csv"
    assert path.exists()
    assert "2026-10" in path.read_text(encoding="utf-8")
    assert coordinator.engine.state.history[0].csv_written is True
    stored = hass_storage["prad"]["data"]["state"]["history"]
    assert stored[0]["csv_written"] is True


async def _options_step(hass, flow_id, user_input):
    return await hass.config_entries.options.async_configure(flow_id, user_input)


async def test_options_flow_add_duplicate_reserved_remove(hass):
    from homeassistant.helpers import entity_registry as er

    entry = await _setup_entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    flow_id = result["flow_id"]

    async def add(name):
        res = await _options_step(hass, flow_id, {"next_step_id": "add_consumer"})
        assert res["type"] is FlowResultType.FORM
        return await _options_step(hass, flow_id, {"name": name, "entity_id": "sensor.test_boiler"})

    res = await add("Boiler")
    assert res["type"] is FlowResultType.MENU
    res = await add("boiler")
    assert res["type"] is FlowResultType.FORM
    assert res["errors"] == {"name": "invalid_name"}
    res = await _options_step(hass, flow_id, {"name": "Fox", "entity_id": "sensor.test_boiler"})
    assert res["errors"] == {"name": "invalid_name"}
    # Back to the menu is not offered from a form; finish by submitting a valid, distinct name.
    res = await _options_step(hass, flow_id, {"name": "Pompa", "entity_id": "sensor.test_pump"})
    assert res["type"] is FlowResultType.MENU
    res = await _options_step(hass, flow_id, {"next_step_id": "finish"})
    assert res["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert {c["name"] for c in entry.options["consumers"]} == {"Boiler", "Pompa"}
    assert set(entry.runtime_data.consumers) == {"boiler", "pompa"}  # reloaded

    registry = er.async_get(hass)
    registry.async_get_or_create(
        "sensor", DOMAIN, "prad_mc_odb_pompa", suggested_object_id="prad_mc_odb_pompa"
    )
    assert registry.async_get("sensor.prad_mc_odb_pompa") is not None
    result = await hass.config_entries.options.async_init(entry.entry_id)
    flow_id = result["flow_id"]
    res = await _options_step(hass, flow_id, {"next_step_id": "remove_consumer"})
    res = await _options_step(hass, flow_id, {"remove": "pompa"})
    assert res["type"] is FlowResultType.MENU
    res = await _options_step(hass, flow_id, {"next_step_id": "finish"})
    assert res["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert registry.async_get("sensor.prad_mc_odb_pompa") is None
    assert set(entry.runtime_data.consumers) == {"boiler"}


async def test_reserved_consumer_slug_skipped_on_load(hass):
    entry = MockConfigEntry(
        domain=DOMAIN, data=SENSORS,
        options={"consumers": [{"name": "Fox", "entity_id": "sensor.test_x"}]},
        version=1, minor_version=2,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data.consumers == {}


async def test_options_flow_remove_then_readd_keeps_entity(hass):
    from homeassistant.helpers import entity_registry as er

    entry = await _setup_entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    flow_id = result["flow_id"]
    await _options_step(hass, flow_id, {"next_step_id": "add_consumer"})
    await _options_step(hass, flow_id, {"name": "Pompa", "entity_id": "sensor.test_pump"})
    res = await _options_step(hass, flow_id, {"next_step_id": "finish"})
    assert res["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    assert registry.async_get("sensor.prad_mc_odb_pompa") is not None

    result = await hass.config_entries.options.async_init(entry.entry_id)
    flow_id = result["flow_id"]
    await _options_step(hass, flow_id, {"next_step_id": "remove_consumer"})
    await _options_step(hass, flow_id, {"remove": "pompa"})
    await _options_step(hass, flow_id, {"next_step_id": "add_consumer"})
    await _options_step(hass, flow_id, {"name": "Pompa", "entity_id": "sensor.test_pump"})
    res = await _options_step(hass, flow_id, {"next_step_id": "finish"})
    assert res["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert registry.async_get("sensor.prad_mc_odb_pompa") is not None


async def test_unloaded_coordinator_does_not_save(hass, freezer, hass_storage):
    freezer.move_to("2026-11-23 09:30:00+01:00")
    entry = await _setup_entry(hass)
    coordinator = entry.runtime_data
    await coordinator.async_save()
    before = str(hass_storage["prad"]["data"])
    coordinator.engine.state.storage_start = "2020-01"
    coordinator.mark_unloaded()
    await coordinator.async_save()
    assert str(hass_storage["prad"]["data"]) == before
    await coordinator.async_save(final=True)
    assert "2020-01" in str(hass_storage["prad"]["data"])
