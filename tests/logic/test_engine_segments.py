import json
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal as D

import pytest

from custom_components.prad.logic.amounts import compute_amounts
from custom_components.prad.logic.engine import Engine, Limits
from custom_components.prad.logic.segments import freeze_segments
from custom_components.prad.logic.state import Counters, PendingClose, State
from custom_components.prad.logic.tariff import EXAMPLE_PRICE_NAME, OpustEntry, Pricing, example_price
from custom_components.prad.logic.tracker import TrackerRef
from custom_components.prad.logic.zones import G11, TZ, Zone

P, O = Zone.PEAK, Zone.OFFPEAK
LIM = Limits()
A = example_price(date(2026, 1, 1))
B = replace(A, od=date(2026, 11, 15), nazwa="B", energy_peak=D("0.70"), energy_offpeak=D("0.45"),
            oze=D("0.008"), fixed_network=D("12.00"))
PR = Pricing((A, B), (OpustEntry(date(2026, 1, 1), D("0.8")),))


def dt(y, m, d, h=0, mi=0, s=0):
    return datetime(y, m, d, h, mi, s, tzinfo=TZ)


def nov_days():
    return {f"2026-11-{d:02d}": Counters(import_peak=10.0 if d <= 14 else 6.3, import_offpeak=5.0,
                                         export_peak=2.0 if d <= 14 else 0.0,
                                         export_offpeak=1.0 if d <= 14 else 0.0) for d in range(1, 31)}


def _november(pricing):
    e = Engine(State(), {})
    e.state.days = nov_days()
    e.state.months["2026-11"] = Counters(import_peak=240.8, import_offpeak=150.0, export_peak=28.0, export_offpeak=14.0)
    e.state.refs["fox"] = TrackerRef(dt(2026, 12, 1), (0.0, 0.0))
    return e.catch_up(dt(2026, 12, 1, 1), pricing, False, LIM)[0], e


def test_price_change_on_15_november_closes_in_two_segments():
    row, e = _november(PR)
    assert (row.pobor_droga, row.pobor_tania, row.do_zaplaty_droga, row.do_zaplaty_tania) == (D("241"), D("150"), D("219"), D("139"))
    assert (row.koszt_netto, row.koszt_razem) == (D("339.96"), D("418.17"))
    assert (row.koszt_droga, row.koszt_tania, row.oplaty_stale) == (D("278.06"), D("90.81"), D("49.30"))
    assert row.wersja_cennika == f"{EXAMPLE_PRICE_NAME} + B"
    s1, s2 = row.segmenty
    assert (s1.od, s1.do, s2.od, s2.do) == (date(2026, 11, 1), date(2026, 11, 14), date(2026, 11, 15), date(2026, 11, 30))
    assert s1.pobor == {P: D("140"), O: D("70")} and s2.pobor == {P: D("101"), O: D("80")}
    assert s1.do_zaplaty == {P: D("127"), O: D("65")} and s2.do_zaplaty == {P: D("92"), O: D("74")}
    assert s1.oddanie == {P: D("28"), O: D("14")} and s2.oddanie == {P: D("0"), O: D("0")}
    assert s1.cennik == A and s2.cennik == B and len(row.segmenty_opustu) == 1 and row.segmenty_opustu[0].oddanie is None
    assert State.from_dict(json.loads(json.dumps(e.state.to_dict()))) == e.state


def test_ratio_change_only_does_not_split_money_lines():
    pricing = Pricing((A,), (OpustEntry(date(2026, 1, 1), D("0.8")), OpustEntry(date(2026, 11, 15), D("0.7"))))
    e = Engine(State(), {})
    e.state.days = {k: replace(c, export_peak=2.0 if int(k[-2:]) <= 14 else 1.55, export_offpeak=1.0 if int(k[-2:]) <= 14 else 0.5)
                    for k, c in nov_days().items()}
    e.state.months["2026-11"] = Counters(import_peak=240.8, import_offpeak=150.0, export_peak=52.8, export_offpeak=22.0)
    e.state.refs["fox"] = TrackerRef(dt(2026, 12, 1), (0.0, 0.0))
    row = e.catch_up(dt(2026, 12, 1, 1), pricing, False, LIM)[0]
    # export 53 / 22; credit: 28 * 0.8 + 25 * 0.7 = 39.9 -> 40; 14 * 0.8 + 8 * 0.7 = 16.8 -> 17
    assert (row.oddanie_droga, row.oddanie_tania, row.do_zaplaty_droga, row.do_zaplaty_tania) == (D("53"), D("22"), D("201"), D("133"))
    assert row.koszt_razem == D("380.46") and row.koszt_netto == D("309.31") and row.oplaty_stale == D("48.55")
    assert row.koszt_razem == compute_amounts({P: D("241"), O: D("150")}, {P: D("201"), O: D("133")}, A).gross
    assert len(row.segmenty) == 1 and [s.wspolczynnik for s in row.segmenty_opustu] == [D("0.8"), D("0.7")]


def test_frozen_month_ignores_later_price_edit():
    e = Engine(State(), {})
    e.state.days = nov_days()
    e.state.months["2026-11"] = Counters(import_peak=240.8, import_offpeak=150.0, export_peak=28.0, export_offpeak=14.0)
    e.freeze_month("2026-11", PR, False)
    edited = Pricing((replace(A, energy_peak=D("9")), replace(B, energy_peak=D("9"))), PR.opust)
    assert e.catch_up(dt(2026, 12, 1, 1), edited, False, LIM)[0].koszt_razem == D("418.17")


def test_current_cost_with_two_segments_is_fractional_and_fixed_fees_cover_whole_month():
    e = Engine(State(), {})
    e.state.days = {k: c for k, c in nov_days().items() if k <= "2026-11-19"}
    e.state.months["2026-11"] = Counters(import_peak=171.5, import_offpeak=95.0, export_peak=28.0, export_offpeak=14.0)
    snap = e.current_snapshot(dt(2026, 11, 20), PR, False)
    assert snap.settlement.billable == {P: D("149.1"), O: D("83.8")}
    assert (snap.totals.net, snap.totals.gross, snap.totals.fixed_gross) == (D("235.26"), D("289.39"), D("49.30"))
    assert (snap.totals.peak_gross, snap.totals.offpeak_gross) == (D("186.20"), D("53.89"))


def test_backdated_price_edit_reprices_open_month_at_once():
    e = Engine(State(), {})
    e.state.days = {k: c for k, c in nov_days().items() if k <= "2026-11-19"}
    e.state.months["2026-11"] = Counters(import_peak=171.5, import_offpeak=95.0, export_peak=28.0, export_offpeak=14.0)
    only_a = Pricing((A,), PR.opust)
    assert e.current_snapshot(dt(2026, 11, 20), only_a, False).totals.gross != D("289.39")
    assert e.current_snapshot(dt(2026, 11, 20), PR, False).totals.gross == D("289.39")


def test_forecast_uses_announced_future_price():
    e = Engine(State(), {})
    e.state.days = {f"2026-11-{d:02d}": Counters(import_peak=10.0) for d in range(1, 10)}
    e.state.months["2026-11"] = Counters(import_peak=90.0)
    snap = e.forecast_snapshot(dt(2026, 11, 10), PR, False)
    assert snap.counters.import_peak == pytest.approx(300.0)             # 90 + 10 * 21 days
    assert (snap.totals.net, snap.totals.gross) == (D("351.96"), D("432.91"))    # 140 kWh at A, 160 kWh at B
    assert e.forecast_snapshot(dt(2026, 11, 10), Pricing((A,), PR.opust), False).totals.gross == D("417.66")


def test_forecast_g11_puts_both_zone_means_into_peak():
    g11 = replace(A, od=date(2026, 12, 1), nazwa="G11", taryfa="G11", strefy=G11)
    e = Engine(State(), {})
    e.state.days = {f"2026-11-{d:02d}": Counters(import_peak=4.0, import_offpeak=6.0) for d in range(24, 31)}
    snap = e.forecast_snapshot(dt(2026, 12, 1), Pricing((A, g11), PR.opust), False)
    assert snap.counters.import_peak == pytest.approx(310.0) and snap.counters.import_offpeak == pytest.approx(0.0)


def test_hour_zone_follows_version_of_the_day():
    g11 = replace(A, od=date(2026, 12, 1), nazwa="G11", taryfa="G11", strefy=G11)
    e = Engine(State(), {})
    e.state.refs["fox"] = TrackerRef(dt(2026, 11, 30, 22), (0.0, 0.0))
    e.on_tick(dt(2026, 12, 1, 1, 0, 10), (3.0, 0.0), None, {}, LIM, Pricing((A, g11), PR.opust))
    assert e.state.months["2026-11"].import_offpeak == pytest.approx(2.0)     # 22:00 and 23:00 still G12w
    assert e.state.months["2026-12"].import_peak == pytest.approx(1.0)        # 00:00 already G11


def test_month_pricing_override_is_used_for_zone_and_freeze():
    e = Engine(State(), {})
    e.month_pricing = {"2026-11": Pricing((replace(A, nazwa="G11", taryfa="G11", strefy=G11),), PR.opust)}
    e.state.refs["fox"] = TrackerRef(dt(2026, 11, 23, 23), (0.0, 0.0))
    e.on_tick(dt(2026, 11, 24, 0, 0, 10), (1.0, 0.0), None, {}, LIM, PR)
    assert e.state.months["2026-11"].import_peak == pytest.approx(1.0)
    e.freeze_month("2026-11", PR, False)
    assert e.state.pending["2026-11"].segmenty_cen[0].cennik.taryfa == "G11"


def test_net_mode_routes_signed_delta_and_uses_both_limits():
    e = Engine(State(), {}, grid_mode="netto")
    e.state.refs["fox"] = TrackerRef(dt(2026, 11, 23, 10), (100.0,))
    assert e.on_tick(dt(2026, 11, 23, 11, 0, 10), (101.5,), None, {}, LIM, PR) == []
    assert e.on_tick(dt(2026, 11, 23, 12, 0, 10), (99.0,), None, {}, LIM, PR) == []
    m = e.state.months["2026-11"]
    assert m.import_peak == pytest.approx(1.5) and m.export_peak == pytest.approx(2.5)
    assert e.on_tick(dt(2026, 11, 23, 13, 0, 10), (86.0,), None, {}, LIM, PR) == ["fox: over_limit, new reference"]   # -13 < -12
    assert e.on_tick(dt(2026, 11, 23, 14, 0, 10), (117.0,), None, {}, LIM, PR) == ["fox: over_limit, new reference"]  # +31 > 30
    assert m.export_peak == pytest.approx(2.5) and e.state.refs["fox"].values == (117.0,)


def test_without_pv_hours_are_flagged_and_row_has_none():
    e = Engine(State(), {"sauna": "Sauna"}, has_pv=False)
    e.state.refs["fox"] = TrackerRef(dt(2026, 11, 30, 22), (0.0, 0.0))
    e.state.refs["sauna"] = TrackerRef(dt(2026, 11, 30, 22), (0.0,))
    e.on_tick(dt(2026, 12, 1, 0, 0, 10), (2.0, 0.0), 55.0, {"sauna": 1.0}, LIM, PR)
    assert e.state.months["2026-11"].pv_brak is True and e.state.days["2026-11-30"].pv_brak is True
    assert "pv" not in e.state.refs and e.state.months["2026-11"].pv == 0.0
    row = e.catch_up(dt(2026, 12, 1, 1), PR, False, LIM)[0]
    assert row.zuzycie_domu is None and row.produkcja_pv is None and row.autokonsumpcja is None and row.reszta is None
    assert row.consumers == {"sauna": pytest.approx(1.0)} and row.pobor_tania == D("2")


def test_sync_sources_first_time_keeps_references():
    e = Engine(State(), {})
    e.state.refs = {"fox": TrackerRef(dt(2026, 11, 23, 10), (1.0, 2.0)), "pv": TrackerRef(dt(2026, 11, 23, 10), (3.0,))}
    sig = {"fox": "dwa|sensor.test_import|sensor.test_export", "pv": "sensor.test_pv"}
    assert e.sync_sources(sig, date(2026, 11, 23)) is True
    assert set(e.state.refs) == {"fox", "pv"} and e.state.zrodla == sig and e.state.zmiana_licznika is None
    assert e.sync_sources(sig, date(2026, 11, 24)) is False


def test_sync_sources_changed_meter_drops_reference_and_sets_date():
    e = Engine(State(zrodla={"fox": "dwa|sensor.test_import|sensor.test_export", "sauna": "sensor.test_a"}), {"sauna": "Sauna"})
    e.state.refs = {"fox": TrackerRef(dt(2026, 11, 23, 10), (1.0, 2.0)), "sauna": TrackerRef(dt(2026, 11, 23, 10), (3.0,))}
    e.state.months["2026-11"] = Counters(import_peak=5.0)
    assert e.sync_sources({"fox": "netto|sensor.test_net", "sauna": "sensor.test_b"}, date(2026, 11, 23)) is True
    assert e.state.refs == {} and e.state.zmiana_licznika == "2026-11-23" and e.state.months["2026-11"].import_peak == 5.0
    e.state.zrodla["sauna"] = "sensor.test_c"
    e.state.zmiana_licznika = None
    e.sync_sources({"fox": "netto|sensor.test_net", "sauna": "sensor.test_b"}, date(2026, 11, 24))
    assert e.state.zmiana_licznika is None                                # a consumer alone does not set the date


def test_sync_sources_removed_source_drops_reference_and_date_never_goes_back():
    e = Engine(State(zrodla={"fox": "a", "pv": "sensor.test_pv"}, zmiana_licznika="2026-12-01"), {})
    e.state.refs = {"pv": TrackerRef(dt(2026, 11, 23, 10), (3.0,)), "stary": TrackerRef(dt(2026, 11, 1), (1.0,))}
    assert e.sync_sources({"fox": "b"}, date(2026, 11, 23)) is True
    assert e.state.refs == {} and e.state.zrodla == {"fox": "b"} and e.state.zmiana_licznika == "2026-12-01"


def test_sync_sources_adding_or_removing_pv_sets_meter_change_date():
    e = Engine(State(zrodla={"fox": "a"}), {})
    assert e.sync_sources({"fox": "a", "pv": "sensor.test_pv"}, date(2026, 11, 23)) is True        # PV added
    assert e.state.zmiana_licznika == "2026-11-23" and e.state.zrodla["pv"] == "sensor.test_pv"
    e.state.refs["pv"] = TrackerRef(dt(2026, 11, 24, 10), (3.0,))
    assert e.sync_sources({"fox": "a"}, date(2026, 11, 25)) is True                                  # PV removed
    assert e.state.zmiana_licznika == "2026-11-25" and "pv" not in e.state.refs and "pv" not in e.state.zrodla
    e2 = Engine(State(zrodla={"fox": "a", "sauna": "sensor.test_a"}), {"sauna": "Sauna"})
    e2.sync_sources({"fox": "a"}, date(2026, 11, 25))                                                # consumer removed
    assert e2.state.zmiana_licznika is None


def test_counted_range():
    e = Engine(State(), {})
    assert e.counted_range() is None
    e.state.months["2026-11"] = Counters()
    e.state.pending["2026-10"] = PendingClose("2026-10", Counters(), *freeze_segments("2026-10", Counters(), {}, PR), False)
    e.state.last_tick = dt(2026, 11, 23, 0)
    assert e.counted_range() == (date(2026, 10, 1), date(2026, 11, 22))     # last counted hour started 22.11 at 23:00
