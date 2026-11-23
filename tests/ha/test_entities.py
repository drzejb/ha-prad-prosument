"""Entity tests for the Prad integration."""
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.prad.const import DOMAIN

SENSORS = {
    "tryb_sieci": "dwa",
    "import_entity": "sensor.test_import",
    "export_entity": "sensor.test_export",
    "pv_entity": "sensor.test_pv",
    "temperature_entity": "sensor.test_temperature",
}


async def _setup_entry(hass, options=None) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data=SENSORS, options=options or {"consumers": []}, version=1, minor_version=2)
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
    assert hass.states.get("sensor.prad_cennik") is not None
    assert hass.states.get("select.prad_porownanie_a") is not None
    assert hass.states.get("switch.prad_magazyn_przed_przeniesieniem").state == "off"
    assert hass.states.get("text.prad_wersja_cennika").state == "Tauron G12w 2026 (przykład – sprawdź z fakturą)"
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


async def test_month_sensor_follows_calendar_after_close(hass, freezer, tmp_path):
    from homeassistant.util import dt as dt_util

    hass.config.config_dir = str(tmp_path)
    freezer.move_to("2026-11-30 22:30:00+01:00")
    entry = await _setup_entry(hass)
    hass.states.async_set("sensor.test_import", "100")
    hass.states.async_set("sensor.test_export", "50")
    await _tick_at(hass, freezer, "2026-11-30 23:00:10+01:00")
    hass.states.async_set("sensor.test_import", "101")
    await _tick_at(hass, freezer, "2026-12-01 00:00:10+01:00")
    hass.states.async_set("sensor.test_import", "unavailable")
    await _tick_at(hass, freezer, "2026-12-01 01:00:10+01:00")
    freezer.move_to("2026-12-01 01:05:00+01:00")
    await entry.runtime_data.async_close_pending(dt_util.now())
    await hass.async_block_till_done()
    assert entry.runtime_data.engine.state.last_closed == "2026-11"
    state = hass.states.get("sensor.prad_mc_pobor_tania")
    assert state.attributes["last_reset"].startswith("2026-12-01")
    assert float(state.state) == pytest.approx(0.0)


def _row(month, **changes):
    from dataclasses import replace
    from decimal import Decimal

    from custom_components.prad.logic.state import HistoryRow

    d = Decimal(0)
    row = HistoryRow(
        month=month, pobor_droga=d, pobor_tania=d, oddanie_droga=d, oddanie_tania=d,
        magazyn_wplyw=d, magazyn_uzyte=d, magazyn_wygasle=d, magazyn_stan=d,
        do_zaplaty_droga=d, do_zaplaty_tania=d, koszt_droga=d, koszt_tania=d,
        oplaty_stale=d, koszt_netto=d, koszt_razem=d, zuzycie_domu=0.0, produkcja_pv=0.0,
        autokonsumpcja=0.0, reszta=0.0, temp_srednia=None, stopniodni=None,
        naslonecznienie_mj=None, wersja_cennika="test", csv_written=True,
    )
    return replace(row, **changes)


async def test_history_derived_entities(hass, freezer):
    from decimal import Decimal

    from custom_components.prad.logic.state import Counters

    freezer.move_to("2026-11-23 09:30:00+01:00")
    entry = await _setup_entry(hass)
    coordinator = entry.runtime_data
    state = coordinator.engine.state
    state.history.append(_row("2026-09"))
    state.history.append(
        _row(
            "2026-10", koszt_razem=Decimal("110"), invoice=Decimal("100"),
            zuzycie_domu=300.0, stopniodni=150.0,
        )
    )
    state.last_closed = "2026-10"
    state.days["2026-11-22"] = Counters(import_peak=1.5, pv=2.0)
    coordinator.async_update_listeners()
    await hass.async_block_till_done()

    a = hass.states.get("select.prad_porownanie_a")
    b = hass.states.get("select.prad_porownanie_b")
    assert a.state == "2026-09"
    assert b.state == "2026-10"
    assert b.attributes["wiersz"]["month"] == "2026-10"
    assert float(hass.states.get("sensor.prad_model_vs_faktura").state) == pytest.approx(10.0)
    assert hass.states.get("sensor.prad_model_vs_faktura").attributes["miesiac"] == "2026-10"
    assert float(hass.states.get("sensor.prad_mc_kwh_na_stopniodzien").state) == pytest.approx(2.0)

    state.history[-1].stopniodni = 5.0
    coordinator.async_update_listeners()
    await hass.async_block_till_done()
    assert hass.states.get("sensor.prad_mc_kwh_na_stopniodzien").state == "unknown"

    days = hass.states.get("sensor.prad_dni").attributes["dni"]
    assert hass.states.get("sensor.prad_dni").state == "1"
    assert set(days[0]) == {
        "dzien", "pobor_droga", "pobor_tania", "oddanie_droga", "oddanie_tania",
        "pv", "autokonsumpcja", "temp",
    }
    assert days[0]["dzien"] == "2026-11-22"
    assert days[0]["pobor_droga"] == pytest.approx(1.5)


async def _invoice_form_entry(hass, freezer, tmp_path):
    from decimal import Decimal

    freezer.move_to("2026-11-23 09:30:00+01:00")
    hass.config.config_dir = str(tmp_path)  # isolated CSV directory
    entry = await _setup_entry(hass)
    coordinator = entry.runtime_data
    coordinator.engine.state.history.append(_row("2026-09", invoice=Decimal("55.81")))
    coordinator.engine.state.history.append(_row("2026-10"))
    coordinator.async_update_listeners()
    await hass.async_block_till_done()
    return coordinator


async def test_invoice_form_defaults_to_newest_month_without_invoice(hass, freezer, tmp_path):
    await _invoice_form_entry(hass, freezer, tmp_path)
    month = hass.states.get("select.prad_faktura_miesiac")
    assert month.state == "2026-10"
    assert month.attributes["options"] == ["2026-10", "2026-09"]
    assert hass.states.get("number.prad_faktura_kwota").state == "unknown"
    assert hass.states.get("button.prad_faktura_zapisz") is not None


async def test_invoice_form_selecting_month_shows_stored_invoice(hass, freezer, tmp_path):
    await _invoice_form_entry(hass, freezer, tmp_path)
    await hass.services.async_call(
        "select", "select_option", {"entity_id": "select.prad_faktura_miesiac", "option": "2026-09"}, blocking=True
    )
    assert float(hass.states.get("number.prad_faktura_kwota").state) == pytest.approx(55.81)


async def test_invoice_form_button_saves_invoice(hass, freezer, tmp_path):
    from decimal import Decimal

    coordinator = await _invoice_form_entry(hass, freezer, tmp_path)
    await hass.services.async_call(
        "number", "set_value", {"entity_id": "number.prad_faktura_kwota", "value": 447.12}, blocking=True
    )
    await hass.services.async_call("button", "press", {"entity_id": "button.prad_faktura_zapisz"}, blocking=True)
    await hass.async_block_till_done()
    row = next(r for r in coordinator.engine.state.history if r.month == "2026-10")
    assert row.invoice == Decimal("447.12")
    csv_text = (tmp_path / "prad" / "prad_faktury.csv").read_text(encoding="utf-8")
    assert "2026-10;447.12;" in csv_text
    # every month has an invoice now: the form shows the newest one with its stored amount
    assert hass.states.get("select.prad_faktura_miesiac").state == "2026-10"
    assert float(hass.states.get("number.prad_faktura_kwota").state) == pytest.approx(447.12)
    assert float(hass.states.get("sensor.prad_model_vs_faktura").state) == pytest.approx(-100.0)


async def test_invoice_form_button_requires_amount(hass, freezer, tmp_path):
    from homeassistant.exceptions import ServiceValidationError

    coordinator = await _invoice_form_entry(hass, freezer, tmp_path)
    with pytest.raises(ServiceValidationError, match="Wpisz kwotę"):
        await hass.services.async_call(
            "button", "press", {"entity_id": "button.prad_faktura_zapisz"}, blocking=True
        )
    assert next(r for r in coordinator.engine.state.history if r.month == "2026-10").invoice is None


async def test_invoice_form_without_history(hass, freezer, tmp_path):
    from homeassistant.exceptions import ServiceValidationError

    freezer.move_to("2026-11-23 09:30:00+01:00")
    hass.config.config_dir = str(tmp_path)
    await _setup_entry(hass)
    assert hass.states.get("select.prad_faktura_miesiac").state == "unknown"
    with pytest.raises(ServiceValidationError, match="Brak zamkniętych"):
        await hass.services.async_call(
            "number", "set_value", {"entity_id": "number.prad_faktura_kwota", "value": 10}, blocking=True
        )
    with pytest.raises(ServiceValidationError, match="Brak zamkniętych"):
        await hass.services.async_call(
            "button", "press", {"entity_id": "button.prad_faktura_zapisz"}, blocking=True
        )


async def test_invoice_form_draft_stays_with_its_month(hass, freezer, tmp_path):
    from decimal import Decimal

    coordinator = await _invoice_form_entry(hass, freezer, tmp_path)
    await hass.services.async_call(
        "number", "set_value", {"entity_id": "number.prad_faktura_kwota", "value": 447.123}, blocking=True
    )
    assert coordinator.invoice_amount == Decimal("447.12")
    coordinator.engine.state.history.append(_row("2026-11"))  # a newer month closes meanwhile
    coordinator.async_update_listeners()
    await hass.async_block_till_done()
    assert hass.states.get("select.prad_faktura_miesiac").state == "2026-10"
    assert float(hass.states.get("number.prad_faktura_kwota").state) == pytest.approx(447.12)

    coordinator.engine.state.history[:] = [r for r in coordinator.engine.state.history if r.month != "2026-10"]
    coordinator.async_update_listeners()
    await hass.async_block_till_done()
    assert hass.states.get("select.prad_faktura_miesiac").state == "2026-11"
    assert hass.states.get("number.prad_faktura_kwota").state == "unknown"


async def test_invoice_form_rejects_nan(hass, freezer, tmp_path):
    from homeassistant.exceptions import ServiceValidationError

    coordinator = await _invoice_form_entry(hass, freezer, tmp_path)
    entity = hass.data["entity_components"]["number"].get_entity("number.prad_faktura_kwota")
    with pytest.raises(ServiceValidationError):
        await entity.async_set_native_value(float("nan"))
    assert coordinator.invoice_amount is None


async def test_price_list_sensor_and_future_version_in_forecast(hass, freezer):
    from dataclasses import replace
    from datetime import date
    from decimal import Decimal
    from custom_components.prad.logic.state import Counters
    from custom_components.prad.logic.tariff import OpustEntry

    freezer.move_to("2026-11-10 00:30:00+01:00")
    entry = await _setup_entry(hass)
    c = entry.runtime_data
    state = c.engine.state
    for d in range(1, 10):
        state.days[f"2026-11-{d:02d}"] = Counters(import_peak=10.0)
    state.months["2026-11"] = Counters(import_peak=90.0)
    c.async_update_listeners()
    await hass.async_block_till_done()
    before = float(hass.states.get("sensor.prad_mc_prognoza").state)
    future = replace(c.prices[0], od=date(2026, 11, 15), nazwa="Od 15.11", energy_peak=Decimal("0.70"))
    await c.async_apply_prices([*c.prices, future])
    await c.async_apply_opust([*c.opust, OpustEntry(date(2027, 1, 1), Decimal("0.7"))])
    await hass.async_block_till_done()
    assert float(hass.states.get("sensor.prad_mc_prognoza").state) > before       # zapowiedziana cena jest w prognozie
    s = hass.states.get("sensor.prad_cennik")
    assert s.state == "Tauron G12w 2026 (przykład – sprawdź z fakturą)"
    assert s.attributes["od"] == "2026-11-01" and s.attributes["taryfa"] == "G12w"
    assert s.attributes["nastepna_zmiana_cen"] == "2026-11-15" and s.attributes["nastepna_zmiana_opustu"] == "2027-01-01"
    assert [w["nazwa"] for w in s.attributes["wersje"]] == [s.state, "Od 15.11"]
    freezer.move_to("2026-11-15 00:00:30+01:00")
    c.async_update_listeners()
    await hass.async_block_till_done()
    assert hass.states.get("sensor.prad_cennik").state == "Od 15.11"
    assert hass.states.get("sensor.prad_cennik").attributes["nastepna_zmiana_cen"] is None
    assert float(hass.states.get("number.prad_energia_droga").state) == pytest.approx(0.70)

async def test_g11_offpeak_numbers_unavailable_and_zone_label(hass, freezer):
    from dataclasses import replace
    from custom_components.prad.logic.zones import G11

    freezer.move_to("2026-11-23 09:30:00+01:00")
    entry = await _setup_entry(hass)
    c = entry.runtime_data
    await c.async_apply_prices([replace(c.prices[0], taryfa="G11", strefy=G11)])
    await hass.async_block_till_done()
    assert hass.states.get("number.prad_energia_tania").state == "unavailable"
    assert hass.states.get("number.prad_dystrybucja_tania").state == "unavailable"
    assert hass.states.get("number.prad_energia_droga").state != "unavailable"
    assert hass.states.get("sensor.prad_cena_teraz").attributes["strefa"] == "całodobowa"
    assert hass.states.get("binary_sensor.prad_strefa_droga").state == "on"
    assert hass.states.get("binary_sensor.prad_strefa_droga").attributes["nastepna_zmiana"] is None

async def test_without_pv_energy_sensors_are_unknown(hass, freezer):
    freezer.move_to("2026-11-23 09:30:00+01:00")
    entry = MockConfigEntry(domain=DOMAIN, data={"tryb_sieci": "dwa", "import_entity": "sensor.test_import",
                                                 "export_entity": "sensor.test_export"},
                            options={"consumers": [{"name": "Sauna", "entity_id": "sensor.test_sauna"}]}, version=1, minor_version=2)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    hass.states.async_set("sensor.test_import", "100"); hass.states.async_set("sensor.test_export", "50")
    await _tick_at(hass, freezer, "2026-11-23 10:00:10+01:00")
    hass.states.async_set("sensor.test_import", "101.5")
    await _tick_at(hass, freezer, "2026-11-23 11:00:10+01:00")
    for eid in ("sensor.prad_mc_produkcja_pv", "sensor.prad_mc_zuzycie_domu", "sensor.prad_mc_autokonsumpcja",
                "sensor.prad_mc_reszta", "sensor.prad_dzis_zuzycie_domu"):
        assert hass.states.get(eid).state == "unknown", eid
    assert float(hass.states.get("sensor.prad_mc_pobor_droga").state) == pytest.approx(1.5)
    day = hass.states.get("sensor.prad_dni").attributes["dni"][-1]
    assert day["pv"] is None and day["autokonsumpcja"] is None

async def test_without_pv_and_without_tick_pv_views_are_unknown(hass, freezer):
    freezer.move_to("2026-11-23 09:30:00+01:00")
    entry = MockConfigEntry(domain=DOMAIN, data={"tryb_sieci": "dwa", "import_entity": "sensor.test_import",
                                                 "export_entity": "sensor.test_export"}, version=1, minor_version=2)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    for eid in ("sensor.prad_mc_produkcja_pv", "sensor.prad_mc_zuzycie_domu", "sensor.prad_mc_autokonsumpcja",
                "sensor.prad_mc_reszta", "sensor.prad_dzis_produkcja_pv", "sensor.prad_dzis_zuzycie_domu"):
        state = hass.states.get(eid)
        assert state is not None and state.state == "unknown", eid


async def test_fuse_name_has_no_owner_hardware(hass, freezer):
    freezer.move_to("2026-11-23 09:30:00+01:00")
    await _setup_entry(hass)
    assert hass.states.get("number.prad_bezpiecznik_siec").name == "Prąd – bezpiecznik odczytu licznika sieci"
