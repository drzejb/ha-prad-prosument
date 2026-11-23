"""Options flow: dated price lists and net-metering ratio."""
from decimal import Decimal
from unittest.mock import patch

import pytest
from homeassistant.data_entry_flow import FlowResultType

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.prad.const import DOMAIN
from test_entities import SENSORS, _setup_entry, _tick_at

PRICES = {
    "energia_droga": 0.7, "energia_tania": 0.45, "dystrybucja_droga": 0.3298, "dystrybucja_tania": 0.0512,
    "oplata_jakosciowa": 0.0332, "oplata_oze": 0.008, "oplata_kogeneracyjna": 0.003,
    "oplata_sieciowa_stala": 12.0, "oplata_abonamentowa": 4.56, "oplata_mocowa": 24.05,
    "oplata_handlowa": 0, "oplata_przejsciowa": 0, "vat": 23,
}


async def _open(hass, entry, *menu):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    fid = result["flow_id"]
    for step in menu:
        result = await hass.config_entries.options.async_configure(fid, {"next_step_id": step})
    return fid, result


async def test_options_prices_keep_consumers_and_do_not_reload(hass, freezer):
    freezer.move_to("2026-11-23 09:30:00+01:00")
    entry = await _setup_entry(hass, {"consumers": [{"name": "Sauna", "entity_id": "sensor.test_sauna"}]})
    c = entry.runtime_data
    fid, res = await _open(hass, entry, "cenniki", "cennik_add")
    assert res["step_id"] == "cennik_dane"
    res = await hass.config_entries.options.async_configure(fid, {"od": "2026-12-15", "nazwa": "Nowe ceny", "rozklad": "tauron_g12w", "force": False})
    assert res["step_id"] == "cennik_ceny"
    res = await hass.config_entries.options.async_configure(fid, PRICES)
    assert res["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.runtime_data is c                                             # bez przeładowania
    assert entry.options == {"consumers": [{"name": "Sauna", "entity_id": "sensor.test_sauna"}]}
    assert [v.nazwa for v in c.prices][1] == "Nowe ceny" and c.prices[1].energy_peak == Decimal("0.7")
    assert float(hass.states.get("number.prad_energia_droga").state) == pytest.approx(0.627)   # dziś nadal stara wersja

async def test_options_prices_after_consumer_edit(hass, freezer):
    freezer.move_to("2026-11-23 09:30:00+01:00")
    entry = await _setup_entry(hass)
    fid, _ = await _open(hass, entry, "add_consumer")
    await hass.config_entries.options.async_configure(fid, {"name": "Pompa", "entity_id": "sensor.test_pump"})
    await hass.config_entries.options.async_configure(fid, {"next_step_id": "cenniki"})
    await hass.config_entries.options.async_configure(fid, {"next_step_id": "cennik_add"})
    await hass.config_entries.options.async_configure(fid, {"od": "2026-12-15", "nazwa": "Nowe", "rozklad": "tauron_g12w", "force": False})
    res = await hass.config_entries.options.async_configure(fid, PRICES)
    assert res["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert [x["name"] for x in entry.options["consumers"]] == ["Pompa"] and len(entry.runtime_data.prices) == 2

async def test_options_edit_and_remove_price(hass, freezer):
    freezer.move_to("2026-11-23 09:30:00+01:00")
    entry = await _setup_entry(hass)
    c = entry.runtime_data
    fid, res = await _open(hass, entry, "cenniki", "cennik_edit")
    res = await hass.config_entries.options.async_configure(fid, {"wersja": "2026-11-01"})
    res = await hass.config_entries.options.async_configure(fid, {"od": "2026-11-01", "nazwa": "Poprawiony", "rozklad": "tauron_g12w", "force": False})
    res = await hass.config_entries.options.async_configure(fid, {**PRICES, "energia_droga": 0.65})
    assert res["type"] is FlowResultType.CREATE_ENTRY and c.prices[0].nazwa == "Poprawiony" and c.prices[0].energy_peak == Decimal("0.65")
    _, res = await _open(hass, entry, "cenniki")
    assert "cennik_remove" not in res["menu_options"]                          # jedna wersja – nie ma czego usuwać
    fid, _ = await _open(hass, entry, "cenniki", "cennik_add")
    await hass.config_entries.options.async_configure(fid, {"od": "2026-12-15", "nazwa": "Nowe", "rozklad": "tauron_g12w", "force": False})
    await hass.config_entries.options.async_configure(fid, PRICES)
    fid, res = await _open(hass, entry, "cenniki", "cennik_remove")
    res = await hass.config_entries.options.async_configure(fid, {"wersja": "2026-12-15", "force": False})
    assert res["type"] is FlowResultType.CREATE_ENTRY and len(c.prices) == 1

async def test_options_price_validations(hass, freezer):
    freezer.move_to("2026-11-23 09:30:00+01:00")
    entry = await _setup_entry(hass)
    fid, _ = await _open(hass, entry, "cenniki", "cennik_add")
    res = await hass.config_entries.options.async_configure(fid, {"od": "2026-11-01", "nazwa": "x", "rozklad": "g11", "force": False})
    assert res["errors"] == {"od": "duplicate_date"}
    res = await hass.config_entries.options.async_configure(fid, {"od": "2026-12-15", "nazwa": "G11", "rozklad": "g11", "force": False})
    assert res["step_id"] == "cennik_ceny" and "energia_tania" not in res["data_schema"].schema
    g11 = {k: v for k, v in PRICES.items() if k not in ("energia_tania", "dystrybucja_tania")}
    res = await hass.config_entries.options.async_configure(fid, g11)
    assert res["errors"] == {"base": "zone_change_not_first"} and len(entry.runtime_data.prices) == 1

async def test_options_zone_change_of_counted_days_needs_force(hass, freezer):
    freezer.move_to("2026-11-23 09:30:00+01:00")
    entry = await _setup_entry(hass)
    hass.states.async_set("sensor.test_import", "100"); hass.states.async_set("sensor.test_export", "50")
    await _tick_at(hass, freezer, "2026-11-23 10:00:10+01:00")
    hass.states.async_set("sensor.test_import", "101")
    await _tick_at(hass, freezer, "2026-11-23 11:00:10+01:00")
    g11 = {k: v for k, v in PRICES.items() if k not in ("energia_tania", "dystrybucja_tania")}
    fid, _ = await _open(hass, entry, "cenniki", "cennik_edit")
    await hass.config_entries.options.async_configure(fid, {"wersja": "2026-11-01"})
    await hass.config_entries.options.async_configure(fid, {"od": "2026-11-01", "nazwa": "G11", "rozklad": "g11", "force": False})
    with patch("custom_components.prad.coordinator.persistent_notification.async_create") as notify:
        res = await hass.config_entries.options.async_configure(fid, {**g11, "energia_droga": 0.61})
    assert res["step_id"] == "cennik_dane" and res["errors"] == {"base": "counted_days"} and notify.call_count == 0
    assert entry.runtime_data.prices[0].taryfa == "G12w"
    res = await hass.config_entries.options.async_configure(fid, {"od": "2026-11-01", "nazwa": "G11", "rozklad": "g11", "force": True})
    assert res["step_id"] == "cennik_ceny"
    with patch("custom_components.prad.coordinator.persistent_notification.async_create") as notify:
        res = await hass.config_entries.options.async_configure(fid, g11)     # prices entered earlier are only pre-filled
    assert res["type"] is FlowResultType.CREATE_ENTRY and entry.runtime_data.prices[0].taryfa == "G11"
    assert notify.call_count == 1 and "prad.inicjuj" in notify.call_args.args[1]


async def test_options_prices_prefill_after_counted_days(hass, freezer):
    freezer.move_to("2026-11-23 09:30:00+01:00")
    entry = await _setup_entry(hass)
    hass.states.async_set("sensor.test_import", "100"); hass.states.async_set("sensor.test_export", "50")
    await _tick_at(hass, freezer, "2026-11-23 10:00:10+01:00")
    hass.states.async_set("sensor.test_import", "101")
    await _tick_at(hass, freezer, "2026-11-23 11:00:10+01:00")
    fid, _ = await _open(hass, entry, "cenniki", "cennik_edit")
    await hass.config_entries.options.async_configure(fid, {"wersja": "2026-11-01"})
    await hass.config_entries.options.async_configure(fid, {"od": "2026-11-01", "nazwa": "G11", "rozklad": "g11", "force": False})
    g11 = {k: v for k, v in PRICES.items() if k not in ("energia_tania", "dystrybucja_tania")}
    await hass.config_entries.options.async_configure(fid, {**g11, "energia_droga": 0.61})
    res = await hass.config_entries.options.async_configure(fid, {"od": "2026-11-01", "nazwa": "G11", "rozklad": "g11", "force": True})
    key = next(k for k in res["data_schema"].schema if k == "energia_droga")
    assert key.default() == pytest.approx(0.61)


async def test_options_own_zones_weekend_default_follows_layout(hass, freezer):
    freezer.move_to("2026-11-23 09:30:00+01:00")
    entry = await _setup_entry(hass)
    fid, _ = await _open(hass, entry, "cenniki", "cennik_add")
    res = await hass.config_entries.options.async_configure(fid, {"od": "2026-12-01", "nazwa": "Wlasne", "rozklad": "wlasne_g12", "force": False})
    assert res["step_id"] == "cennik_strefy"
    key = next(k for k in res["data_schema"].schema if k == "weekend")
    assert key.default() == "robocze"


async def test_options_empty_options_never_reload(hass, freezer):
    freezer.move_to("2026-11-23 09:30:00+01:00")
    entry = MockConfigEntry(domain=DOMAIN, data=SENSORS, options={}, version=1, minor_version=2)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    c = entry.runtime_data
    fid, _ = await _open(hass, entry, "cenniki", "cennik_add")
    await hass.config_entries.options.async_configure(fid, {"od": "2026-12-15", "nazwa": "Nowe", "rozklad": "tauron_g12w", "force": False})
    res = await hass.config_entries.options.async_configure(fid, PRICES)
    assert res["type"] is FlowResultType.CREATE_ENTRY
    fid, _ = await _open(hass, entry, "instalacja", "opust", "opust_add")
    res = await hass.config_entries.options.async_configure(fid, {"od": "2026-12-15", "wspolczynnik": 0.7})
    assert res["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.runtime_data is c and entry.options == {}
    assert len(c.prices) == 2 and len(c.opust) == 2


async def test_options_ratio_add_edit_remove(hass, freezer):
    freezer.move_to("2026-11-23 09:30:00+01:00")
    entry = await _setup_entry(hass)
    c = entry.runtime_data
    fid, res = await _open(hass, entry, "instalacja", "opust", "opust_add")
    res = await hass.config_entries.options.async_configure(fid, {"od": "2026-11-01", "wspolczynnik": 0.7})
    assert res["errors"] == {"base": "duplicate_date"}
    res = await hass.config_entries.options.async_configure(fid, {"od": "2026-12-15", "wspolczynnik": 0.7})
    assert res["type"] is FlowResultType.CREATE_ENTRY and [e.wspolczynnik for e in c.opust] == [Decimal("0.8"), Decimal("0.7")]
    assert entry.runtime_data is c
    fid, _ = await _open(hass, entry, "instalacja", "opust", "opust_edit")
    await hass.config_entries.options.async_configure(fid, {"pozycja": "2026-12-15"})
    res = await hass.config_entries.options.async_configure(fid, {"od": "2026-12-01", "wspolczynnik": 0.75})
    assert res["type"] is FlowResultType.CREATE_ENTRY and c.opust[1].od.isoformat() == "2026-12-01" and c.opust[1].wspolczynnik == Decimal("0.75")
    fid, _ = await _open(hass, entry, "instalacja", "opust", "opust_remove")
    res = await hass.config_entries.options.async_configure(fid, {"pozycja": "2026-12-01"})
    assert res["type"] is FlowResultType.CREATE_ENTRY and len(c.opust) == 1
    assert float(hass.states.get("number.prad_wspolczynnik_opustu").state) == pytest.approx(0.8)

async def test_options_prices_survive_reload(hass, freezer):
    freezer.move_to("2026-11-23 09:30:00+01:00")
    entry = await _setup_entry(hass)
    fid, _ = await _open(hass, entry, "cenniki", "cennik_add")
    await hass.config_entries.options.async_configure(fid, {"od": "2026-12-15", "nazwa": "Nowe", "rozklad": "tauron_g12w", "force": False})
    await hass.config_entries.options.async_configure(fid, PRICES)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert [v.od.isoformat() for v in entry.runtime_data.prices] == ["2026-11-01", "2026-12-15"]


async def test_options_prices_abort_when_not_loaded(hass, freezer):
    freezer.move_to("2026-11-23 09:30:00+01:00")
    entry = await _setup_entry(hass)
    fid, _ = await _open(hass, entry)
    assert await hass.config_entries.async_unload(entry.entry_id)
    result = await hass.config_entries.options.async_configure(fid, {"next_step_id": "cenniki"})
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "not_loaded"
