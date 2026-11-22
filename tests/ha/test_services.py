"""Services, rebuild from recorder statistics and Open-Meteo tolerance."""
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
import voluptuous as vol
from homeassistant.components.recorder.models import StatisticData, StatisticMeanType, StatisticMetaData
from homeassistant.components.recorder.statistics import async_import_statistics
from homeassistant.exceptions import ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.recorder.common import async_wait_recording_done

from custom_components.prad.const import DOMAIN
from custom_components.prad.logic.settlement import Inflow
from custom_components.prad.logic.zones import Zone
from custom_components.prad.weather import async_fetch_radiation

from test_setup import SENSORS, _history_row

NOW = "2026-11-26 12:00:00+01:00"
FIRST_HOUR = datetime(2026, 11, 22, 22, 0, tzinfo=UTC)  # 23:00 local, the day before the rebuild start
HOURS = 3 * 24 + 13  # reference hour + 84 settled hours until 12:00 local
OPEN_METEO = "https://api.open-meteo.com/v1/forecast"


@pytest.fixture(autouse=True)
def _no_retry_delay(monkeypatch):
    monkeypatch.setattr("custom_components.prad.weather.RETRY_DELAY", 0)


async def _setup_entry(hass, tmp_path) -> MockConfigEntry:
    hass.config.config_dir = str(tmp_path)  # isolated CSV directory
    entry = MockConfigEntry(domain=DOMAIN, data=SENSORS, options={"consumers": []})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _import_stats(hass, entity_id: str, step: float) -> None:
    metadata = StatisticMetaData(
        mean_type=StatisticMeanType.NONE,
        has_sum=True,
        name=None,
        source="recorder",
        statistic_id=entity_id,
        unit_class="energy",
        unit_of_measurement="kWh",
    )
    stats = [
        StatisticData(start=FIRST_HOUR + timedelta(hours=n), state=100 + step * n, sum=step * n)
        for n in range(HOURS)
    ]
    async_import_statistics(hass, metadata, stats)
    await async_wait_recording_done(hass)


async def _call(hass, service: str, data: dict) -> None:
    await hass.services.async_call(DOMAIN, service, data, blocking=True)


async def test_services_require_loaded_entry(hass):
    from custom_components.prad.services import async_register_services

    async_register_services(hass)
    with pytest.raises(ServiceValidationError, match="nie jest skonfigurowana"):
        await _call(hass, "magazyn_korekta", {"ilosc": 1, "miesiac": "2026-11"})


async def test_storage_correction_service(hass, freezer, tmp_path):
    freezer.move_to(NOW)
    entry = await _setup_entry(hass, tmp_path)
    await _call(hass, "magazyn_korekta", {"ilosc": 12.5, "miesiac": "2026-11"})
    assert entry.runtime_data.engine.storage_kwh() == Decimal("12.5")
    await _call(hass, "magazyn_korekta", {"ilosc": -2.5, "miesiac": "2026-11"})
    assert entry.runtime_data.engine.storage_kwh() == Decimal("10.0")
    with pytest.raises(ServiceValidationError):
        await _call(hass, "magazyn_korekta", {"ilosc": -50, "miesiac": "2026-11"})


async def test_invoice_service_validation(hass, freezer, tmp_path):
    freezer.move_to(NOW)
    entry = await _setup_entry(hass, tmp_path)
    with pytest.raises(ServiceValidationError):  # month not in history
        await _call(hass, "wpisz_fakture", {"miesiac": "2026-10", "kwota": 100})
    entry.runtime_data.engine.state.history.append(_history_row("2026-10"))
    await _call(hass, "wpisz_fakture", {"miesiac": "2026-10", "kwota": 447.37})
    assert entry.runtime_data.engine.state.history[0].invoice == Decimal("447.37")
    csv_text = (tmp_path / "prad" / "prad_faktury.csv").read_text(encoding="utf-8")
    assert "2026-10" in csv_text
    with pytest.raises(vol.Invalid):
        await _call(hass, "wpisz_fakture", {"miesiac": "2026-10", "kwota": -1})


async def test_inicjuj_requires_confirmation_when_history_exists(hass, freezer, tmp_path):
    freezer.move_to(NOW)
    entry = await _setup_entry(hass, tmp_path)
    entry.runtime_data.engine.state.history.append(_history_row("2026-09"))
    with pytest.raises(ServiceValidationError, match="potwierdz"):
        await _call(hass, "inicjuj", {"od": "2026-11-23", "poczatek_magazynu": "2026-11"})


async def test_inicjuj_missing_statistics(hass, freezer, tmp_path, aioclient_mock):
    freezer.move_to(NOW)
    aioclient_mock.get(OPEN_METEO, status=500)
    await _setup_entry(hass, tmp_path)
    with pytest.raises(ServiceValidationError, match="sensor.test_import"):
        await _call(hass, "inicjuj", {"od": "2026-11-23", "poczatek_magazynu": "2026-11"})


async def test_inicjuj_builds_months_from_statistics(hass, freezer, tmp_path, aioclient_mock):
    freezer.move_to(NOW)
    aioclient_mock.get(OPEN_METEO, status=500)
    entry = await _setup_entry(hass, tmp_path)
    await _import_stats(hass, "sensor.test_import", 0.5)
    await _import_stats(hass, "sensor.test_export", 0.0)
    data = {"od": "2026-11-23", "poczatek_magazynu": "2026-11"}
    await _call(hass, "inicjuj", data)
    counters = entry.runtime_data.engine.state.months["2026-11"]
    total = counters.import_peak + counters.import_offpeak
    assert total == pytest.approx(0.5 * 84)
    assert counters.export_peak + counters.export_offpeak == pytest.approx(0)
    await _call(hass, "inicjuj", {**data, "potwierdz": True})
    again = entry.runtime_data.engine.state.months["2026-11"]
    assert again.import_peak + again.import_offpeak == pytest.approx(total)


async def test_inicjuj_keeps_older_history_and_inflows(hass, freezer, tmp_path, aioclient_mock):
    freezer.move_to(NOW)
    aioclient_mock.get(OPEN_METEO, status=500)
    entry = await _setup_entry(hass, tmp_path)
    state = entry.runtime_data.engine.state
    state.history.append(_history_row("2026-09"))
    state.history.append(_history_row("2026-11"))
    state.history[1].invoice = Decimal("12.34")
    state.inflows.append(Inflow("2026-09", Zone.PEAK, Decimal("5"), "2027-09"))
    state.inflows.append(Inflow("2026-11", Zone.PEAK, Decimal("7"), "2027-11"))
    state.corrections.append({"time": "2026-11-01T00:00:00+01:00", "month": "2026-09", "amount": "5"})
    await _import_stats(hass, "sensor.test_import", 0.5)
    await _import_stats(hass, "sensor.test_export", 0.0)
    await _call(
        hass, "inicjuj", {"od": "2026-11-23", "poczatek_magazynu": "2026-09", "potwierdz": True}
    )
    new = entry.runtime_data.engine.state
    assert [r.month for r in new.history if r.month < "2026-11"] == ["2026-09"]
    assert [i.month for i in new.inflows] == ["2026-09"]
    assert len(new.corrections) == 1
    assert new.storage_start == "2026-09"


async def test_open_meteo_failure_is_tolerated(hass, aioclient_mock):
    aioclient_mock.get(OPEN_METEO, status=500)
    assert await async_fetch_radiation(hass, 5) == {}


async def test_open_meteo_parses_daily_values(hass, aioclient_mock):
    aioclient_mock.get(
        OPEN_METEO,
        json={"daily": {"time": ["2026-11-24", "2026-11-25"], "shortwave_radiation_sum": [3.5, None]}},
    )
    assert await async_fetch_radiation(hass, 5) == {"2026-11-24": 3.5}
