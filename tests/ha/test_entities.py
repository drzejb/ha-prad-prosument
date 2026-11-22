"""Entity tests for the Prad integration."""
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.prad.const import DOMAIN

SENSORS = {
    "import_entity": "sensor.test_import",
    "export_entity": "sensor.test_export",
    "pv_entity": "sensor.test_pv",
    "temperature_entity": "sensor.test_temperature",
}


async def _setup_entry(hass, options=None) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data=SENSORS, options=options or {"consumers": []})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _tick_at(hass, freezer, when: str) -> None:
    freezer.move_to(when)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_setup_creates_entities(hass, freezer):
    freezer.move_to("2026-11-23 09:30:00+01:00")
    await _setup_entry(hass)
    assert hass.states.get("sensor.prad_mc_koszt") is not None
    assert hass.states.get("binary_sensor.prad_strefa_droga") is not None
    assert hass.states.get("sensor.prad_cena_teraz") is not None
    assert hass.states.get("sensor.prad_dni") is not None
    assert hass.states.get("select.prad_porownanie_a") is not None
    assert hass.states.get("switch.prad_magazyn_przed_przeniesieniem").state == "off"
    assert hass.states.get("text.prad_wersja_cennika").state == "2026-09 faktura"
    assert float(hass.states.get("number.prad_energia_droga").state) == pytest.approx(0.627)


async def test_day_sensor_keeps_last_hour(hass, freezer):
    freezer.move_to("2026-11-30 22:30:00+01:00")
    await _setup_entry(hass)
    hass.states.async_set("sensor.test_import", "100")
    hass.states.async_set("sensor.test_export", "50")
    await _tick_at(hass, freezer, "2026-11-30 23:00:10+01:00")
    hass.states.async_set("sensor.test_import", "101")
    await _tick_at(hass, freezer, "2026-12-01 00:00:10+01:00")
    # The 23-24 hour (off-peak) still belongs to 30.11 after the tick at 00:00:10 on 1.12.
    assert float(hass.states.get("sensor.prad_dzis_pobor_tania").state) == pytest.approx(1.0)
    assert float(hass.states.get("sensor.prad_mc_pobor_tania").state) == pytest.approx(1.0)
    assert hass.states.get("sensor.prad_dzis_pobor_tania").attributes["last_reset"].startswith(
        "2026-11-30T00:00:00"
    )


async def test_number_change_persists_and_recomputes(hass, freezer):
    freezer.move_to("2026-11-23 09:30:00+01:00")
    entry = await _setup_entry(hass)
    hass.states.async_set("sensor.test_import", "100")
    hass.states.async_set("sensor.test_export", "50")
    await _tick_at(hass, freezer, "2026-11-23 10:00:10+01:00")
    hass.states.async_set("sensor.test_import", "110")
    await _tick_at(hass, freezer, "2026-11-23 11:00:10+01:00")
    before = float(hass.states.get("sensor.prad_mc_koszt").state)
    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": "number.prad_energia_droga", "value": 1.0},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert float(hass.states.get("sensor.prad_mc_koszt").state) > before
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert float(hass.states.get("number.prad_energia_droga").state) == pytest.approx(1.0)


async def test_consumer_sensor_created_from_options(hass, freezer):
    freezer.move_to("2026-11-23 09:30:00+01:00")
    await _setup_entry(
        hass, {"consumers": [{"name": "Sauna", "entity_id": "sensor.test_sauna"}]}
    )
    state = hass.states.get("sensor.prad_mc_odb_sauna")
    assert state is not None
    assert state.name == "Prąd – Sauna w tym miesiącu"


async def test_history_attribute_not_recorded():
    from custom_components.prad.select import PradCompareSelect
    from custom_components.prad.sensor import PradDays, PradHistory, PradStorage

    assert "miesiace" in PradHistory._unrecorded_attributes
    assert "dni" in PradDays._unrecorded_attributes
    assert {"wplywy", "korekty"} <= PradStorage._unrecorded_attributes
    assert "wiersz" in PradCompareSelect._unrecorded_attributes
