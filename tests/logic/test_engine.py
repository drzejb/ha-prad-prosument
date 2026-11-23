from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from custom_components.prad.logic.amounts import compute_amounts
from custom_components.prad.logic.engine import Engine, Limits
from custom_components.prad.logic.settlement import Inflow
from custom_components.prad.logic.state import Counters, PendingClose, State
from custom_components.prad.logic.segments import freeze_segments
from custom_components.prad.logic.tariff import DEFAULT_PRICING, EXAMPLE_PRICE_NAME, Pricing
from custom_components.prad.logic.tracker import TrackerRef
from custom_components.prad.logic.zones import TZ, Zone

D = Decimal
LIM = Limits()
T = DEFAULT_PRICING


def priced(**changes):
    return Pricing((replace(T.prices[0], **changes),), T.opust)


def dt(y, m, d, h=0, mi=0, s=0):
    return datetime(y, m, d, h, mi, s, tzinfo=TZ)


def engine(consumers=None, **state_kwargs):
    return Engine(State(**state_kwargs), consumers or {})


def pending(month, **counters):
    c = Counters(**counters)
    return PendingClose(month, c, *freeze_segments(month, c, {}, T), False)


def test_hour_routed_to_zone_and_month():
    e = engine()
    e.state.refs["fox"] = TrackerRef(dt(2026, 11, 23, 10), (100.0, 50.0))
    warnings = e.on_tick(dt(2026, 11, 23, 11, 0, 10), (101.5, 50.0), None, {}, LIM, T)
    assert warnings == []
    m = e.state.months["2026-11"]
    assert m.import_peak == pytest.approx(1.5) and m.import_offpeak == 0 and m.export_peak == 0
    assert e.state.days["2026-11-23"].import_peak == pytest.approx(1.5)
    assert e.state.last_tick == dt(2026, 11, 23, 11)
    assert e.state.refs["fox"].values == (101.5, 50.0)


def test_net_export_hour():
    e = engine()
    e.state.refs["fox"] = TrackerRef(dt(2026, 11, 23, 12), (100.0, 50.0))
    e.on_tick(dt(2026, 11, 23, 13, 0, 5), (100.2, 52.0), None, {}, LIM, T)
    m = e.state.months["2026-11"]
    assert m.export_peak == pytest.approx(1.8) and m.import_peak == 0 and m.export_offpeak == 0


def test_missing_hours_counted_for_unavailable_consumer():
    e = engine({"sauna": "Sauna"})
    e.state.refs["fox"] = TrackerRef(dt(2026, 11, 23, 9), (0.0, 0.0))
    e.state.refs["sauna"] = TrackerRef(dt(2026, 11, 23, 9), (0.0,))
    e.on_tick(dt(2026, 11, 23, 10, 0, 10), None, None, {"sauna": None}, LIM, T)
    assert e.state.months["2026-11"].missing_hours == {"fox": 1, "sauna": 1}
    assert e.state.days["2026-11-23"].missing_hours == {"fox": 1, "sauna": 1}
    assert e.state.months["2026-11"].missing_marks == [
        "fox|2026-11-23T09:00:00+01:00", "sauna|2026-11-23T09:00:00+01:00"
    ]


def test_missing_hour_counted_once():
    e = engine({"sauna": "Sauna"})
    e.state.refs["sauna"] = TrackerRef(dt(2026, 11, 23, 9), (0.0,))
    e.on_tick(dt(2026, 11, 23, 10, 0, 10), (1.0, 0.0), None, {"sauna": None}, LIM, T)
    e.on_tick(dt(2026, 11, 23, 10, 30), (1.0, 0.0), None, {"sauna": None}, LIM, T)
    assert e.state.months["2026-11"].missing_hours["sauna"] == 1
    assert e.state.days["2026-11-23"].missing_hours["sauna"] == 1
    assert "fox" not in e.state.months["2026-11"].missing_hours


def test_gap_over_month_boundary_closes_after_data():
    e = engine()
    e.state.refs["fox"] = TrackerRef(dt(2026, 11, 30, 23), (0.0, 0.0))
    e.state.months["2026-11"] = Counters()
    assert e.months_ready_to_freeze(dt(2026, 12, 1, 1, 30)) == []
    assert e.catch_up(dt(2026, 12, 1, 1, 30), T, False, LIM) == [] and e.state.history == []
    assert "2026-11" in e.state.months
    e.on_tick(dt(2026, 12, 1, 2, 0, 10), (3.0, 0.0), None, {}, LIM, T)
    assert e.state.months["2026-11"].import_offpeak == pytest.approx(1.0)
    assert e.state.months["2026-12"].import_offpeak == pytest.approx(2.0)
    assert e.months_ready_to_freeze(dt(2026, 12, 1, 2, 0, 10)) == ["2026-11"]
    rows = e.catch_up(dt(2026, 12, 1, 2, 0, 10), T, False, LIM)
    assert [r.month for r in rows] == ["2026-11"]
    assert rows[0].pobor_tania == D("1") and rows[0].pobor_droga == D("0")
    assert e.state.months["2026-12"].import_offpeak == pytest.approx(2.0)


def test_pv_zero_treated_as_unavailable():
    e = engine()
    e.state.refs["pv"] = TrackerRef(dt(2026, 11, 23, 10), (500.0,))
    warnings = e.on_tick(dt(2026, 11, 23, 11, 0, 10), None, 0.0, {}, LIM, T)
    assert warnings == []
    assert e.state.refs["pv"].time == dt(2026, 11, 23, 10) and e.state.refs["pv"].values == (500.0,)
    assert "pv" not in e.state.months.get("2026-11", Counters()).missing_hours


def test_secondary_tracker_hours_for_frozen_month():
    e = engine()
    e.state.pending["2026-11"] = pending("2026-11")
    e.state.refs["pv"] = TrackerRef(dt(2026, 11, 30, 16), (100.0,))
    warnings = e.on_tick(dt(2026, 12, 1, 8, 0, 10), None, 116.0, {}, LIM, T)
    assert warnings == []
    assert e.state.pending["2026-11"].counters.pv == pytest.approx(8.0)
    assert e.state.months["2026-12"].pv == pytest.approx(8.0)
    e.close_month("2026-11", dt(2026, 12, 1, 8), LIM)
    e.state.refs["pv"] = TrackerRef(dt(2026, 11, 30, 22), (116.0,))
    warnings = e.on_tick(dt(2026, 12, 1, 0, 0, 10), None, 118.0, {}, LIM, T)
    assert warnings == ["pv: 2 hours for closed month dropped"]
    assert "2026-11" not in e.state.months and "2026-11" not in e.state.pending
    assert e.state.months["2026-12"].pv == pytest.approx(8.0)


def test_no_dropped_warning_when_dropped_energy_is_zero():
    e = engine()
    e.state.pending["2026-11"] = pending("2026-11")
    e.state.refs["pv"] = TrackerRef(dt(2026, 11, 30, 22), (116.0,))
    e.state.refs["fox"] = TrackerRef(dt(2026, 12, 1, 1), (0.0, 0.0))
    e.state.months["2026-12"] = Counters()
    e.close_month("2026-11", dt(2026, 12, 1, 8), LIM)
    warnings = e.on_tick(dt(2026, 12, 1, 0, 0, 10), None, 116.0, {}, LIM, T)
    assert warnings == []


def test_close_fills_frozen_inputs():
    e = engine()
    e.state.months["2026-11"] = Counters(import_peak=10.0)
    e.state.refs["fox"] = TrackerRef(dt(2026, 12, 1), (0.0, 0.0))
    e.freeze_month("2026-11", T, True)
    row = e.catch_up(dt(2026, 12, 1, 1), priced(energy_peak=D("9")), False, LIM)[0]
    assert row.segmenty[0].cennik == T.prices[0]
    assert row.segmenty_opustu[0].wspolczynnik == D("0.8")
    assert row.storage_before_cross is True


def test_close_uses_frozen_tariff():
    e = engine()
    e.state.months["2026-11"] = Counters(import_peak=100.0)
    e.state.refs["fox"] = TrackerRef(dt(2026, 12, 1), (0.0, 0.0))
    e.freeze_month("2026-11", T, False)
    new_pricing = priced(energy_peak=D("9"), nazwa="new")
    rows = e.catch_up(dt(2026, 12, 1, 1), new_pricing, False, LIM)
    assert len(rows) == 1
    row = rows[0]
    expected = compute_amounts(
        {Zone.PEAK: D("100"), Zone.OFFPEAK: D("0")}, {Zone.PEAK: D("100"), Zone.OFFPEAK: D("0")}, T.prices[0]
    ).gross
    changed = compute_amounts(
        {Zone.PEAK: D("100"), Zone.OFFPEAK: D("0")}, {Zone.PEAK: D("100"), Zone.OFFPEAK: D("0")},
        replace(T.prices[0], energy_peak=D("9")),
    ).gross
    assert expected != changed
    assert row.koszt_razem == expected and row.wersja_cennika == EXAMPLE_PRICE_NAME


def test_september_close_reproduces_invoice():
    e = engine(inflows=[Inflow("2026-10", Zone.PEAK, D("5"), "2027-10")])
    e.state.pending["2026-09"] = pending(
        "2026-09", import_peak=195.0, import_offpeak=280.0, export_peak=279.0, export_offpeak=312.0
    )
    row = e.close_month("2026-09", dt(2026, 10, 1, 1), LIM)
    assert row.koszt_razem == D("55.81") and row.koszt_netto == D("45.37")
    assert row.pobor_droga == D("195") and row.oddanie_tania == D("312")
    assert row.do_zaplaty_droga == D("0") and row.do_zaplaty_tania == D("2")
    assert row.magazyn_wplyw == D("0") and row.magazyn_stan == D("5")
    assert e.state.inflows == [Inflow("2026-10", Zone.PEAK, D("5"), "2027-10")]
    assert row.temp_srednia is None and row.stopniodni is None and row.naslonecznienie_mj is None
    assert e.state.last_closed == "2026-09" and "2026-09" not in e.state.pending


def test_close_is_idempotent():
    e = engine()
    e.state.pending["2026-11"] = pending("2026-11", import_peak=10.0)
    first = e.close_month("2026-11", dt(2026, 12, 1, 1), LIM)
    second = e.close_month("2026-11", dt(2026, 12, 1, 2), LIM)
    assert second is first and len(e.state.history) == 1


def test_close_weather_all_days_present_and_missing():
    e = engine()
    e.state.pending["2026-11"] = pending("2026-11")
    e.state.weather = {f"2026-11-{d:02d}": {"temp": 8.0, "radiation": 2.0} for d in range(1, 31)}
    row = e.close_month("2026-11", dt(2026, 12, 1, 1), LIM)
    assert row.temp_srednia == pytest.approx(8.0)
    assert row.stopniodni == pytest.approx(300.0)  # 30 * (18 - 8)
    assert row.naslonecznienie_mj == pytest.approx(60.0)
    e.state.pending["2026-12"] = pending("2026-12")
    e.state.weather = {f"2026-12-{d:02d}": {"temp": 1.0, "radiation": None} for d in range(1, 32)}
    row = e.close_month("2026-12", dt(2027, 1, 1, 1), LIM)
    assert row.temp_srednia == pytest.approx(1.0) and row.stopniodni == pytest.approx(31 * 17.0)
    assert row.naslonecznienie_mj is None
    e.state.pending["2027-01"] = pending("2027-01")
    e.state.weather = {"2027-01-01": {"temp": 1.0, "radiation": 1.0}}
    row = e.close_month("2027-01", dt(2027, 2, 1, 1), LIM)
    assert row.temp_srednia is None and row.stopniodni is None and row.naslonecznienie_mj is None


def test_catch_up_closes_months_in_order():
    e = engine()
    e.state.months["2026-12"] = Counters(import_peak=30.0)
    e.state.months["2026-11"] = Counters(export_offpeak=100.0)
    e.state.refs["fox"] = TrackerRef(dt(2027, 1, 2), (0.0, 0.0))
    rows = e.catch_up(dt(2027, 1, 2), T, False, LIM)
    assert [r.month for r in rows] == ["2026-11", "2026-12"]
    assert [r.month for r in e.state.history] == ["2026-11", "2026-12"]
    assert rows[0].magazyn_wplyw == D("80") and rows[0].magazyn_stan == D("80")
    assert rows[1].magazyn_uzyte == D("30") and rows[1].magazyn_stan == D("50")
    assert e.storage_kwh() == D("50")
    assert e.state.months == {} and e.state.pending == {} and e.state.last_closed == "2026-12"


def test_close_not_ready_before_one_hour_after_month_end():
    e = engine()
    e.state.pending["2026-11"] = pending("2026-11")
    assert e.months_ready_to_close(dt(2026, 12, 1, 0, 59)) == []
    assert e.months_ready_to_close(dt(2026, 12, 1, 1, 0)) == ["2026-11"]


def test_before_storage_start_does_not_touch_inflows():
    e = engine()
    e.state.pending["2026-10"] = pending("2026-10", export_offpeak=100.0)
    row = e.close_month("2026-10", dt(2026, 11, 1, 1), LIM)
    assert e.state.inflows == [] and row.magazyn_wplyw == D("0") and row.magazyn_stan == D("0")
    assert row.oddanie_tania == D("100")


def _forecast_engine():
    e = engine()
    for d in range(10, 21):
        e.state.days[f"2026-11-{d:02d}"] = Counters(import_peak=10.0 if d >= 14 else 999.0)
    e.state.days["2026-11-21"] = Counters(import_peak=999.0)
    e.state.months["2026-11"] = Counters(import_peak=50.0, pv=7.0)
    return e


def test_forecast_uses_last_7_full_days():
    snap = _forecast_engine().forecast_snapshot(dt(2026, 11, 21), T, False)
    assert snap.counters.import_peak == pytest.approx(150.0)  # 50 + 10 * 10 days
    assert snap.counters.pv == pytest.approx(7.0)
    assert snap.settlement.consumption[Zone.PEAK] == D("150")
    assert snap.settlement.billable[Zone.PEAK] == D("150")


def test_forecast_none_without_full_days():
    e = engine()
    e.state.days["2026-11-21"] = Counters(import_peak=5.0)
    assert e.forecast_snapshot(dt(2026, 11, 21, 12), T, False) is None


def test_current_snapshot_and_month_inflow():
    e = engine()
    e.state.months["2026-11"] = Counters(import_peak=10.0, export_peak=50.0)
    snap = e.current_snapshot(dt(2026, 11, 15), T, False)
    assert snap.settlement.billable[Zone.PEAK] == D("0")
    assert snap.counters.import_peak == 10.0
    assert e.month_inflow(dt(2026, 11, 15), T, False) == D("30")  # credit 40 - own 10
    assert e.storage_kwh() == D("0")
    empty = engine().current_snapshot(dt(2026, 11, 15), T, False)
    assert empty.counters.import_peak == 0.0 and empty.totals.fixed_gross > 0


def test_correct_storage_rules():
    e = engine()
    now = dt(2026, 12, 10)
    for amount, month in [
        (D("5"), "2027-01"),  # future
        (D("5"), "2025-11"),  # expired
        (D("0"), "2026-12"),  # zero
        (D("-4"), "2026-12"),  # more than storage
        (D("5"), "2026-13"),  # bad format
        (D("5"), "26-12"),
    ]:
        with pytest.raises(ValueError):
            e.correct_storage(amount, month, now)
    assert e.storage_kwh() == D("0") and e.state.corrections == []
    e.correct_storage(D("10"), "2026-12", now)
    assert e.storage_kwh() == D("10")
    assert e.state.inflows == [Inflow("2026-12", Zone.PEAK, D("10"), "2027-12")]
    e.correct_storage(D("-4"), "2026-12", now)
    assert e.storage_kwh() == D("6")
    with pytest.raises(ValueError):
        e.correct_storage(D("-7"), "2026-12", now)
    assert e.storage_kwh() == D("6")
    assert [c["amount"] for c in e.state.corrections] == ["10", "-4"]
    assert e.state.corrections[0]["month"] == "2026-12"


def test_correct_storage_negative_removes_oldest_first():
    e = engine(inflows=[Inflow("2026-12", Zone.OFFPEAK, D("5"), "2027-12"), Inflow("2026-11", Zone.PEAK, D("3"), "2027-11")])
    e.correct_storage(D("-4"), "2026-12", dt(2026, 12, 10))
    assert e.state.inflows == [Inflow("2026-12", Zone.OFFPEAK, D("4"), "2027-12")]


def test_set_invoice_rules():
    e = engine()
    with pytest.raises(ValueError):
        e.set_invoice("2026-09", D("55.81"))
    e.state.pending["2026-09"] = pending("2026-09", import_peak=195.0)
    e.close_month("2026-09", dt(2026, 10, 1, 1), LIM)
    with pytest.raises(ValueError):
        e.set_invoice("2026-09", D("-1"))
    assert e.set_invoice("2026-09", D("55.81")).invoice == D("55.81")
    assert e.set_invoice("2026-09", D("56")).invoice == D("56")
    assert e.state.history[0].invoice == D("56")


def test_rejected_reading_warns_and_resets_reference():
    e = engine()
    e.state.refs["fox"] = TrackerRef(dt(2026, 11, 23, 10), (100.0, 50.0))
    warnings = e.on_tick(dt(2026, 11, 23, 11, 0, 10), (90.0, 50.0), None, {}, LIM, T)
    assert warnings == ["fox: negative, new reference"]
    assert e.state.refs["fox"].values == (90.0, 50.0)
    assert e.state.last_tick is None


def test_consumer_and_pv_hours_are_summed():
    e = engine({"sauna": "Sauna"})
    e.state.refs["sauna"] = TrackerRef(dt(2026, 11, 23, 10), (5.0,))
    e.state.refs["pv"] = TrackerRef(dt(2026, 11, 23, 10), (50.0,))
    e.on_tick(dt(2026, 11, 23, 11, 0, 10), (1.0, 0.0), 52.0, {"sauna": 6.5}, LIM, T)
    m = e.state.months["2026-11"]
    assert m.consumers["sauna"] == pytest.approx(1.5) and m.pv == pytest.approx(2.0)
    assert e.state.days["2026-11-23"].consumers["sauna"] == pytest.approx(1.5)


def test_on_tick_trims_days_weather_and_marks():
    e = engine()
    now = dt(2026, 11, 23, 10, 0, 10)
    e.state.days = {"2026-09-01": Counters(), "2026-10-09": Counters(), "2026-10-10": Counters(), "2026-11-22": Counters()}
    e.state.weather = {"2024-01-01": {"temp": 1.0}, "2025-10-20": {"temp": 1.0}, "2026-11-22": {"temp": 1.0}}
    old = (now - timedelta(hours=60)).replace(minute=0, second=0).isoformat()
    e.state.months["2026-11"] = Counters(missing_marks=[f"sauna|{old}"], missing_hours={"sauna": 1})
    e.on_tick(now, (1.0, 0.0), None, {}, LIM, T)
    assert "2026-09-01" not in e.state.days and "2026-10-09" not in e.state.days
    assert "2026-10-10" in e.state.days and "2026-11-22" in e.state.days
    assert "2024-01-01" not in e.state.weather and "2025-10-20" in e.state.weather
    assert all(not mk.startswith("sauna|") for mk in e.state.months["2026-11"].missing_marks)
    assert e.state.months["2026-11"].missing_hours["sauna"] == 1


def test_missing_not_counted_without_started_tracker():
    e = engine({"sauna": "Sauna"})
    e.on_tick(dt(2026, 12, 1, 0, 30), (1.0, 0.0), None, {"sauna": None}, LIM, T)
    assert e.state.months == {} and e.state.days == {}
    assert e.catch_up(dt(2026, 12, 1, 2), T, False, LIM) == [] and e.state.history == []


def test_missing_not_counted_for_already_accounted_hour():
    e = engine()
    e.state.refs["fox"] = TrackerRef(dt(2026, 11, 23, 9), (0.0, 0.0))
    e.on_tick(dt(2026, 11, 23, 10, 0, 10), (1.0, 0.0), None, {}, LIM, T)
    e.on_tick(dt(2026, 11, 23, 10, 30), None, None, {}, LIM, T)
    assert e.state.months["2026-11"].missing_hours.get("fox", 0) == 0


def test_close_requires_earlier_months_closed():
    e = engine()
    e.state.pending["2026-11"] = pending("2026-11")
    e.state.pending["2026-12"] = pending("2026-12")
    with pytest.raises(ValueError):
        e.close_month("2026-12", dt(2027, 1, 1, 1), LIM)
    assert "2026-12" in e.state.pending and e.state.history == []
    e.state.months["2026-10"] = Counters()
    e.state.pending.pop("2026-11")
    with pytest.raises(ValueError):
        e.close_month("2026-12", dt(2027, 1, 1, 1), LIM)


def test_apply_correction_changes_inflows_without_log_entry():
    e = engine()
    e._apply_correction(D("10"), "2026-12")
    e._apply_correction(D("-4"), "2026-12")
    assert e.storage_kwh() == D("6") and e.state.corrections == []
    with pytest.raises(ValueError):
        e._apply_correction(D("-7"), "2026-12")
    assert e.storage_kwh() == D("6")
