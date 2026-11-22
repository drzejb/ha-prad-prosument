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
from custom_components.prad.weather import async_collect_weather, async_fetch_radiation

from test_setup import SENSORS, _history_row

NOW = "2026-11-26 12:00:00+01:00"
FIRST_HOUR = datetime(2026, 11, 22, 22, 0, tzinfo=UTC)  # 23:00 local, the day before the rebuild start
HOURS = 3 * 24 + 13  # reference hour + 84 settled hours until 12:00 local
OPEN_METEO = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"


def _mock_meteo(aioclient_mock, **kwargs) -> None:
    # The first registered match wins, so drop the default mock from conftest.py.
    aioclient_mock.clear_requests()
    aioclient_mock.get(OPEN_METEO, **kwargs)
    aioclient_mock.get(OPEN_METEO_ARCHIVE, **kwargs)


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


async def _import_stats(
    hass, entity_id: str, step: float, first: datetime = FIRST_HOUR, hours: int = HOURS
) -> None:
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
        StatisticData(start=first + timedelta(hours=n), state=100 + step * n, sum=step * n)
        for n in range(hours)
    ]
    async_import_statistics(hass, metadata, stats)
    await async_wait_recording_done(hass)


async def _call(hass, service: str, data: dict):
    return await hass.services.async_call(DOMAIN, service, data, blocking=True, return_response=service == "inicjuj")


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
    await _call(hass, "wpisz_fakture", {"miesiac": "2026-10", "kwota": 123.45})
    assert entry.runtime_data.engine.state.history[0].invoice == Decimal("123.45")
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
    _mock_meteo(aioclient_mock, status=500)
    await _setup_entry(hass, tmp_path)
    with pytest.raises(ServiceValidationError, match="sensor.test_import"):
        await _call(hass, "inicjuj", {"od": "2026-11-23", "poczatek_magazynu": "2026-11"})


async def test_inicjuj_builds_months_from_statistics(hass, freezer, tmp_path, aioclient_mock):
    freezer.move_to(NOW)
    _mock_meteo(aioclient_mock, status=500)
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


async def test_inicjuj_keeps_older_history_and_inflows(hass, freezer, tmp_path, aioclient_mock, caplog):
    freezer.move_to(NOW)
    _mock_meteo(aioclient_mock, status=500)
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
    assert [r.month for r in new.history] == ["2026-09"]
    assert [i.month for i in new.inflows] == ["2026-09"]
    assert len(new.corrections) == 1
    assert new.storage_start == "2026-09"
    assert "Invoices of months not closed by the rebuild were dropped" in caplog.text
    assert "2026-11" in caplog.text


async def test_open_meteo_failure_is_tolerated(hass, aioclient_mock, caplog):
    _mock_meteo(aioclient_mock, status=500)
    assert await async_fetch_radiation(hass, 5) == {}
    assert aioclient_mock.call_count == 2  # one retry
    assert "Open-Meteo request failed" in caplog.text
    assert "open-meteo.com" not in caplog.text.lower()
    assert "latitude" not in caplog.text.lower()


async def test_open_meteo_parses_daily_values(hass, aioclient_mock):
    _mock_meteo(
        aioclient_mock,
        json={"daily": {"time": ["2026-11-24", "2026-11-25"], "shortwave_radiation_sum": [3.5, None]}},
    )
    assert await async_fetch_radiation(hass, 5) == {"2026-11-24": 3.5}


async def test_inicjuj_across_month_end_is_idempotent(hass, freezer, tmp_path, hass_storage, aioclient_mock):
    freezer.move_to("2026-11-02 12:00:00+01:00")
    entry = await _setup_entry(hass, tmp_path)
    first = datetime(2026, 10, 29, 22, 0, tzinfo=UTC)  # reference hour: 23:00 local on 29 October
    last = datetime(2026, 11, 2, 10, 0, tzinfo=UTC)
    hours = int((last - first) / timedelta(hours=1)) + 1
    await _import_stats(hass, "sensor.test_import", 0.5, first, hours)
    await _import_stats(hass, "sensor.test_export", 0.0, first, hours)
    coordinator = entry.runtime_data
    state = coordinator.engine.state
    october = _history_row("2026-10")
    october.invoice = Decimal("123.45")
    october.csv_written = True
    state.history.append(october)
    state.inflows.append(Inflow("2026-09", Zone.PEAK, Decimal("5"), "2027-09"))
    state.corrections.append({"time": "2026-10-31T10:00:00+01:00", "month": "2026-10", "amount": "3"})
    for day in range(1, 32):  # every day of October seeded, so the closed row gets a temperature
        state.weather[f"2026-10-{day:02d}"] = {"temp": 10.0, "radiation": 5.0}
    data = {"od": "2026-10-30", "poczatek_magazynu": "2026-09", "potwierdz": True}

    await _call(hass, "inicjuj", data)
    backup = hass_storage["prad_backup"]["data"]["state"]
    assert [h["month"] for h in backup["history"]] == ["2026-10"]  # the state before the rebuild
    new = coordinator.engine.state
    row = next(r for r in new.history if r.month == "2026-10")
    assert row.invoice == Decimal("123.45")
    assert row.temp_srednia == pytest.approx(10.0)
    first_rows = [r.to_dict() for r in new.history]
    first_inflows = list(new.inflows)

    await _call(hass, "inicjuj", data)
    again = coordinator.engine.state
    assert [r.to_dict() for r in again.history] == first_rows
    assert again.inflows == first_inflows
    assert len(again.corrections) == 1
    lines = (tmp_path / "prad" / "prad_miesiace.csv").read_text(encoding="utf-8").splitlines()
    assert sum(1 for line in lines if "2026-10" in line) == 1


@pytest.mark.parametrize("seed", ["inflow", "correction"])
async def test_inicjuj_requires_confirmation_for_storage_state(hass, freezer, tmp_path, seed):
    freezer.move_to(NOW)
    entry = await _setup_entry(hass, tmp_path)
    state = entry.runtime_data.engine.state
    if seed == "inflow":
        state.inflows.append(Inflow("2026-09", Zone.PEAK, Decimal("5"), "2027-09"))
    else:
        state.corrections.append({"time": "2026-11-01T00:00:00+01:00", "month": "2026-09", "amount": "5"})
    with pytest.raises(ServiceValidationError, match="Stan już istnieje"):
        await _call(hass, "inicjuj", {"od": "2026-11-23", "poczatek_magazynu": "2026-11"})


async def _two_month_stats(hass) -> None:
    first = datetime(2026, 10, 29, 22, 0, tzinfo=UTC)  # reference hour before the start 2026-10-30
    last = datetime(2026, 12, 2, 10, 0, tzinfo=UTC)
    hours = int((last - first) / timedelta(hours=1)) + 1
    await _import_stats(hass, "sensor.test_import", 0.5, first, hours)
    await _import_stats(hass, "sensor.test_export", 0.0, first, hours)


async def test_inicjuj_replays_correction_before_the_month_close(hass, freezer, tmp_path):
    freezer.move_to("2026-12-02 12:00:00+01:00")
    entry = await _setup_entry(hass, tmp_path)
    await _two_month_stats(hass)
    state = entry.runtime_data.engine.state
    state.corrections.append({"time": "2026-11-10T10:00:00+01:00", "month": "2026-10", "amount": "3"})
    await _call(hass, "inicjuj", {"od": "2026-10-30", "poczatek_magazynu": "2026-09", "potwierdz": True})
    rows = {r.month: r for r in entry.runtime_data.engine.state.history}
    assert rows["2026-10"].magazyn_uzyte == 0  # the correction came after October closed
    assert rows["2026-11"].magazyn_uzyte == Decimal("3")  # and before November closed


async def test_inicjuj_keeps_correction_made_after_the_first_rebuild(hass, freezer, tmp_path):
    freezer.move_to("2026-12-02 12:00:00+01:00")
    entry = await _setup_entry(hass, tmp_path)
    await _two_month_stats(hass)
    data = {"od": "2026-10-30", "poczatek_magazynu": "2026-09", "potwierdz": True}
    await _call(hass, "inicjuj", data)
    coordinator = entry.runtime_data
    assert coordinator.engine.storage_kwh() == 0
    # A correction for a month before the start date, entered after the first rebuild.
    coordinator.engine.state.corrections.append(
        {"time": "2026-12-02T12:30:00+01:00", "month": "2026-09", "amount": "4"}
    )
    await _call(hass, "inicjuj", data)
    assert coordinator.engine.storage_kwh() == Decimal("4")


async def test_inicjuj_returns_summary_and_warns_about_gaps(hass, freezer, tmp_path, aioclient_mock, caplog):
    freezer.move_to(NOW)
    _mock_meteo(aioclient_mock, status=500)
    await _setup_entry(hass, tmp_path)
    await _import_stats(hass, "sensor.test_import", 0.5)
    await _import_stats(hass, "sensor.test_export", 0.0, hours=HOURS - 5)  # export ends 5 hours early
    result = await _call(hass, "inicjuj", {"od": "2026-11-23", "poczatek_magazynu": "2026-11"})
    assert result["godziny"] == HOURS
    assert result["przerwy"] == 5
    assert result["zamkniete_miesiace"] == []
    assert any("5" in w for w in result["ostrzezenia"])
    assert "without a Fox meter reading" in caplog.text


async def test_inicjuj_without_any_fox_reading_raises(hass, freezer, tmp_path, aioclient_mock):
    freezer.move_to(NOW)
    _mock_meteo(aioclient_mock, status=500)
    await _setup_entry(hass, tmp_path)
    for entity_id in ("sensor.test_import", "sensor.test_export"):
        metadata = StatisticMetaData(
            mean_type=StatisticMeanType.NONE, has_sum=True, name=None, source="recorder",
            statistic_id=entity_id, unit_class="energy", unit_of_measurement="kWh",
        )
        # Rows with a sum but no state: nothing the rebuild can use.
        async_import_statistics(
            hass, metadata, [StatisticData(start=FIRST_HOUR + timedelta(hours=n), sum=float(n)) for n in range(HOURS)]
        )
    await async_wait_recording_done(hass)
    with pytest.raises(ServiceValidationError, match="odczytów"):
        await _call(hass, "inicjuj", {"od": "2026-11-23", "poczatek_magazynu": "2026-11"})


async def test_inicjuj_keeps_old_month_cost_after_tariff_change(hass, freezer, tmp_path, aioclient_mock, caplog):
    freezer.move_to("2026-11-02 12:00:00+01:00")
    _mock_meteo(aioclient_mock, status=500)
    entry = await _setup_entry(hass, tmp_path)
    first = datetime(2026, 10, 29, 22, 0, tzinfo=UTC)
    last = datetime(2026, 11, 2, 10, 0, tzinfo=UTC)
    hours = int((last - first) / timedelta(hours=1)) + 1
    await _import_stats(hass, "sensor.test_import", 0.5, first, hours)
    await _import_stats(hass, "sensor.test_export", 0.0, first, hours)
    data = {"od": "2026-10-30", "poczatek_magazynu": "2026-09", "potwierdz": True}
    await _call(hass, "inicjuj", data)
    coordinator = entry.runtime_data
    old_cost = next(r for r in coordinator.engine.state.history if r.month == "2026-10").koszt_razem
    assert old_cost > 0

    await coordinator.async_set_setting("energia_droga", "9.99")  # the tariff changes after the close
    await _call(hass, "inicjuj", data)
    row = next(r for r in coordinator.engine.state.history if r.month == "2026-10")
    assert row.koszt_razem == old_cost
    assert "changed cost" not in caplog.text


async def test_collect_weather_uses_archive_for_old_days(hass, aioclient_mock):
    aioclient_mock.clear_requests()
    aioclient_mock.get(
        OPEN_METEO_ARCHIVE,
        json={"daily": {"time": ["2026-06-01"], "shortwave_radiation_sum": [7.5]}},
    )
    aioclient_mock.get(
        OPEN_METEO,
        json={"daily": {"time": ["2026-11-20"], "shortwave_radiation_sum": [2.5]}},
    )
    from datetime import date

    today = date(2026, 11, 22)
    found = await async_collect_weather(hass, None, {}, [date(2026, 6, 1), date(2026, 11, 20)], today)
    assert found == {"2026-06-01": {"radiation": 7.5}, "2026-11-20": {"radiation": 2.5}}
    urls = [str(call[1]) for call in aioclient_mock.mock_calls]
    assert sum(u.startswith(OPEN_METEO_ARCHIVE) for u in urls) == 1
    assert "start_date=2026-06-01" in next(u for u in urls if u.startswith(OPEN_METEO_ARCHIVE))
    assert "past_days=2" in next(u for u in urls if u.startswith(OPEN_METEO + "?"))
