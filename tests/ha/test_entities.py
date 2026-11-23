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
