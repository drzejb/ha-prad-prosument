import json
from datetime import date, datetime
from decimal import Decimal

import pytest

from custom_components.prad.logic.settlement import Inflow
from custom_components.prad.logic.segments import freeze_segments
from custom_components.prad.logic.state import (
    Counters, HistoryRow, PendingClose, PriceSegment, RatioSegment, State, to_decimal,
)
from custom_components.prad.logic.tariff import DEFAULT_PRICING
from custom_components.prad.logic.tracker import TrackerRef
from custom_components.prad.logic.zones import TZ, Zone

D = Decimal
P, O = Zone.PEAK, Zone.OFFPEAK


def test_to_decimal_rounds_to_three_places():
    assert to_decimal(1.23456) == D("1.235")
    assert to_decimal(0.1 + 0.2) == D("0.3")


def test_counters_derived_values():
    c = Counters(import_peak=3.0, import_offpeak=1.0, export_peak=1.0, export_offpeak=0.5, pv=10.0,
                 consumers={"sauna": 2.0, "bojler": 1.5})
    assert c.house() == pytest.approx(12.5)  # 10 + 4 - 1.5
    assert c.self_consumption() == pytest.approx(8.5)  # 10 - 1.5
    assert c.rest() == pytest.approx(9.0)  # 12.5 - 3.5


def test_counters_never_negative():
    c = Counters(import_peak=0.0, export_peak=5.0, pv=1.0, consumers={"x": 3.0})
    assert c.house() == 0.0 and c.self_consumption() == 0.0 and c.rest() == 0.0


def _row() -> HistoryRow:
    return HistoryRow(
        month="2026-10", pobor_droga=D("370"), pobor_tania=D("431"), oddanie_droga=D("209"), oddanie_tania=D("245"),
        magazyn_wplyw=D("0"), magazyn_uzyte=D("0"), magazyn_wygasle=D("0"), magazyn_stan=D("12.5"),
        do_zaplaty_droga=D("203"), do_zaplaty_tania=D("235"), koszt_droga=D("300.10"), koszt_tania=D("100.20"),
        oplaty_stale=D("48.55"), koszt_netto=D("365.00"), koszt_razem=D("448.85"),
        zuzycie_domu=1.5, produkcja_pv=2.5, autokonsumpcja=1.0, reszta=0.5,
        temp_srednia=9.5, stopniodni=250.0, naslonecznienie_mj=None, wersja_cennika="v1",
        consumers={"sauna": 1.0}, consumers_missing={"sauna": 2}, invoice=D("123.45"), csv_written=True,
        storage_before_cross=True,
        segmenty=[PriceSegment(date(2026, 10, 1), date(2026, 10, 31), DEFAULT_PRICING.prices[0], {P: D("370"), O: D("431")}, {P: D("209"), O: D("245")}, {P: D("203"), O: D("235")})],
        segmenty_opustu=[RatioSegment(date(2026, 10, 1), date(2026, 10, 31), D("0.8"))],
    )


def _full_state() -> State:
    t = datetime(2026, 11, 23, 10, tzinfo=TZ)
    c = Counters(export_offpeak=4.0)
    return State(
        refs={"fox": TrackerRef(t, (1.5, 2.5)), "pv": TrackerRef(t, (100.0,))},
        months={"2026-11": Counters(import_peak=1.25, consumers={"sauna": 0.5}, missing_hours={"fox": 1},
                                    missing_marks=["fox|2026-11-23T09:00:00+01:00"])},
        days={"2026-11-23": Counters(pv=3.0)},
        pending={"2026-10": PendingClose("2026-10", c, *freeze_segments("2026-10", c, {}, DEFAULT_PRICING), True)},
        inflows=[Inflow("2026-11", Zone.OFFPEAK, D("12.5"), "2027-11")],
        corrections=[{"time": t.isoformat(), "month": "2026-11", "amount": "5"}],
        history=[_row()],
        last_closed="2026-10",
        storage_start="2026-11",
        weather={"2026-11-23": {"temp": 3.5, "radiation": None}},
        last_tick=t,
        zrodla={"fox": "dwa|sensor.test_import|sensor.test_export"},
        zmiana_licznika="2026-11-20",
        rebuild_base={"od": "2026-11-23", "inflows": [{"month": "2026-10", "zone": "droga", "amount": "2", "valid_until": "2027-10"}]},
    )


def test_state_roundtrip():
    original = _full_state()
    restored = State.from_dict(json.loads(json.dumps(original.to_dict())))
    assert restored == original
    assert restored.history[0].invoice == D("123.45")
    assert restored.inflows[0].zone is Zone.OFFPEAK


def test_empty_state_roundtrip_defaults():
    restored = State.from_dict(json.loads(json.dumps(State().to_dict())))
    assert restored == State()
    assert restored.storage_start == "2026-11" and restored.last_closed is None and restored.last_tick is None


def test_unsupported_schema():
    with pytest.raises(ValueError, match="unsupported schema"):
        State.from_dict({"schema": 1})


def test_state_without_rebuild_base_loads_as_none():
    data = State().to_dict()
    del data["rebuild_base"]
    assert State.from_dict(data).rebuild_base is None


def test_history_row_without_frozen_inputs_loads_as_none():
    data = _row().to_dict()
    del data["segmenty"]
    del data["segmenty_opustu"]
    del data["storage_before_cross"]
    row = HistoryRow.from_dict(data)
    assert row.segmenty is None and row.segmenty_opustu is None and row.storage_before_cross is None


def test_history_row_frozen_inputs_roundtrip():
    restored = HistoryRow.from_dict(json.loads(json.dumps(_row().to_dict())))
    assert restored.segmenty == _row().segmenty and restored.storage_before_cross is True


def test_pv_brak_makes_derived_values_none():
    c = Counters(import_peak=3.0, pv=0.0, consumers={"x": 1.0}, pv_brak=True)
    assert c.house() is None and c.self_consumption() is None and c.rest() is None and c.pv_value() is None
    assert Counters.from_dict(json.loads(json.dumps(c.to_dict()))).pv_brak is True
    assert Counters.from_dict({}).pv_brak is False and Counters(pv=2.0).pv_value() == 2.0


def test_state_defaults_for_new_fields():
    data = State().to_dict()
    del data["zrodla"]
    del data["zmiana_licznika"]
    s = State.from_dict(data)
    assert s.zrodla == {} and s.zmiana_licznika is None and s.schema == 2
