"""Store and config entry migration from v0.2."""
import asyncio
import copy
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from homeassistant.components.recorder.models import StatisticData, StatisticMeanType, StatisticMetaData
from homeassistant.components.recorder.statistics import async_import_statistics
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.recorder.common import async_wait_recording_done

from custom_components.prad.const import DOMAIN
from custom_components.prad.logic.state import inflow_to_dict
from v02_fixture import v02_payload

V02_DATA = {"import_entity": "sensor.test_import", "export_entity": "sensor.test_export",
            "pv_entity": "sensor.test_pv", "temperature_entity": "sensor.test_temperature"}

def _store(hass_storage, payload):
    hass_storage["prad"] = {"version": 1, "minor_version": 1, "key": "prad", "data": payload}

async def _setup(hass, **kw):
    entry = MockConfigEntry(domain=DOMAIN, data=dict(V02_DATA), options={"consumers": []}, version=1, **kw)
    entry.add_to_hass(hass)
    ok = await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry, ok

async def test_store_migration_keeps_amounts_and_writes_backup_once(hass, hass_storage, freezer, tmp_path):
    freezer.move_to("2026-11-23 12:00:00+01:00")
    hass.config.config_dir = str(tmp_path)
    _store(hass_storage, v02_payload())
    entry, ok = await _setup(hass, minor_version=1)
    assert ok
    assert hass_storage["prad_backup_v02"]["data"] == v02_payload()
    c = entry.runtime_data
    assert [v.od.isoformat() for v in c.prices] == ["2026-09-01"] and c.prices[0].nazwa == "2026-09 faktura"
    assert c.opust[0].wspolczynnik == Decimal("0.8")
    assert c.engine.state.history[0].koszt_razem == Decimal("55.81") and c.engine.state.history[0].invoice == Decimal("55.81")
    assert c.engine.storage_kwh() == Decimal("5")
    assert float(hass.states.get("number.prad_energia_droga").state) == pytest.approx(0.627)
    assert hass.states.get("text.prad_wersja_cennika").state == "2026-09 faktura"
    assert float(hass.states.get("number.prad_wspolczynnik_opustu").state) == pytest.approx(0.8)
    assert c.engine.state.zrodla["fox"] == "dwa|sensor.test_import|sensor.test_export"
    assert set(c.engine.state.refs) == {"fox", "pv"}             # pierwsze ładowanie nie kasuje punktów odniesienia
    assert c.engine.state.zmiana_licznika is None
    await c.async_save()
    assert hass_storage["prad"]["minor_version"] == 2 and "cenniki" in hass_storage["prad"]["data"]["settings"]
    hass_storage["prad_backup_v02"]["data"] = {"marker": 1}
    assert await hass.config_entries.async_unload(entry.entry_id)
    _store(hass_storage, v02_payload())                # znowu plik v0.2 → migracja biegnie drugi raz
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass_storage["prad_backup_v02"]["data"] == {"marker": 1}      # kopia nie jest nadpisywana

async def test_pending_october_closes_with_same_amount_after_migration(hass, hass_storage, freezer, tmp_path):
    freezer.move_to("2026-11-23 12:00:00+01:00")
    hass.config.config_dir = str(tmp_path)
    _store(hass_storage, v02_payload())
    entry, _ = await _setup(hass, minor_version=1)
    await entry.runtime_data.async_close_pending(dt_util.now())
    row = next(r for r in entry.runtime_data.engine.state.history if r.month == "2026-10")
    assert row.koszt_razem == Decimal("451.10")

async def test_store_migration_failure_keeps_file(hass, hass_storage, freezer):
    freezer.move_to("2026-11-23 12:00:00+01:00")
    broken = v02_payload()
    del broken["state"]["history"][0]["pobor_droga"]
    _store(hass_storage, broken)
    entry, ok = await _setup(hass, minor_version=1)
    assert not ok and entry.state is ConfigEntryState.SETUP_ERROR
    assert hass_storage["prad"]["data"] == broken and hass_storage["prad"]["minor_version"] == 1
    assert hass_storage["prad_backup_v02"]["data"] == broken

async def test_store_migration_invalid_result_keeps_file(hass, hass_storage, freezer):
    freezer.move_to("2026-11-23 12:00:00+01:00")
    bad = v02_payload()
    bad["settings"]["vat"] = "abc"                     # migracja słownikowa przejdzie, walidacja wyniku – nie
    _store(hass_storage, bad)
    entry, ok = await _setup(hass, minor_version=1)
    assert not ok and entry.state is ConfigEntryState.SETUP_ERROR
    assert hass_storage["prad"]["data"] == bad and hass_storage["prad"]["minor_version"] == 1
    assert hass_storage["prad_backup_v02"]["data"] == bad

async def test_entry_migration_adds_grid_mode_and_renames_fuse(hass, freezer):
    freezer.move_to("2026-11-23 12:00:00+01:00")
    registry = er.async_get(hass)
    entry = MockConfigEntry(domain=DOMAIN, data=dict(V02_DATA), options={"consumers": []}, version=1, minor_version=1)
    entry.add_to_hass(hass)
    registry.async_get_or_create("number", DOMAIN, "prad_bezpiecznik_fox", suggested_object_id="prad_bezpiecznik_fox", config_entry=entry)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.minor_version == 2 and entry.data["tryb_sieci"] == "dwa"
    assert registry.async_get_entity_id("number", DOMAIN, "prad_bezpiecznik_fox") == "number.prad_bezpiecznik_siec"
    assert hass.states.get("number.prad_bezpiecznik_siec") is not None and hass.states.get("number.prad_bezpiecznik_fox") is None

async def test_entry_migration_keeps_manually_renamed_fuse(hass, freezer):
    freezer.move_to("2026-11-23 12:00:00+01:00")
    registry = er.async_get(hass)
    entry = MockConfigEntry(domain=DOMAIN, data=dict(V02_DATA), options={"consumers": []}, version=1, minor_version=1)
    entry.add_to_hass(hass)
    registry.async_get_or_create("number", DOMAIN, "prad_bezpiecznik_fox", suggested_object_id="moj_bezpiecznik", config_entry=entry)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert registry.async_get_entity_id("number", DOMAIN, "prad_bezpiecznik_fox") == "number.moj_bezpiecznik"

async def test_new_install_uses_new_fuse_entity_id_and_example_prices(hass, freezer):
    freezer.move_to("2026-11-23 12:00:00+01:00")
    entry = MockConfigEntry(domain=DOMAIN, data={**V02_DATA, "tryb_sieci": "dwa"}, options={"consumers": []}, version=1, minor_version=2)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("number.prad_bezpiecznik_siec") is not None
    c = entry.runtime_data
    assert c.prices[0].od.isoformat() == "2026-11-01" and c.prices[0].strefy.id == "tauron_g12w" and c.opust[0].od.isoformat() == "2026-11-01"

async def test_number_edits_version_valid_today_without_reload(hass, freezer):
    freezer.move_to("2026-11-23 12:00:00+01:00")
    entry = MockConfigEntry(domain=DOMAIN, data={**V02_DATA, "tryb_sieci": "dwa"}, options={"consumers": []}, version=1, minor_version=2)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    c = entry.runtime_data
    await hass.services.async_call("number", "set_value", {"entity_id": "number.prad_wspolczynnik_opustu", "value": 0.7}, blocking=True)
    await hass.services.async_call("text", "set_value", {"entity_id": "text.prad_wersja_cennika", "value": "Mój cennik"}, blocking=True)
    assert c.opust[0].wspolczynnik == Decimal("0.7") and c.prices[0].nazwa == "Mój cennik" and entry.runtime_data is c

async def test_store_payload_without_state_keeps_file(hass, hass_storage, freezer):
    freezer.move_to("2026-11-23 12:00:00+01:00")
    bad = {"settings": v02_payload()["settings"]}
    _store(hass_storage, bad)
    entry, ok = await _setup(hass, minor_version=1)
    assert not ok and entry.state is ConfigEntryState.SETUP_ERROR
    assert hass_storage["prad"]["data"] == bad and hass_storage["prad"]["minor_version"] == 1

async def test_concurrent_number_edits_are_both_kept(hass, freezer):
    freezer.move_to("2026-11-23 12:00:00+01:00")
    entry = MockConfigEntry(domain=DOMAIN, data={**V02_DATA, "tryb_sieci": "dwa"}, options={"consumers": []}, version=1, minor_version=2)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    c = entry.runtime_data
    await asyncio.gather(
        hass.services.async_call("number", "set_value", {"entity_id": "number.prad_energia_droga", "value": 0.7}, blocking=True),
        hass.services.async_call("number", "set_value", {"entity_id": "number.prad_energia_tania", "value": 0.5}, blocking=True),
        hass.services.async_call("number", "set_value", {"entity_id": "number.prad_vat", "value": 8}, blocking=True),
    )
    assert c.prices[0].energy_peak == Decimal("0.7") and c.prices[0].energy_offpeak == Decimal("0.5")
    assert c.prices[0].vat_percent == Decimal("8")
