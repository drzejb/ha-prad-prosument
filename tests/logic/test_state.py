import json
from datetime import datetime
from decimal import Decimal

import pytest

from custom_components.prad.logic.settlement import Inflow
from custom_components.prad.logic.state import Counters, HistoryRow, PendingClose, State, to_decimal
from custom_components.prad.logic.tariff import DEFAULT_TARIFF
from custom_components.prad.logic.tracker import TrackerRef
from custom_components.prad.logic.zones import TZ, Zone

D = Decimal


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
        consumers={"sauna": 1.0}, consumers_missing={"sauna": 2}, invoice=D("447.37"), csv_written=True,
    )


def _full_state() -> State:
    t = datetime(2026, 11, 23, 10, tzinfo=TZ)
    return State(
        refs={"fox": TrackerRef(t, (1.5, 2.5)), "pv": TrackerRef(t, (100.0,))},
        months={"2026-11": Counters(import_peak=1.25, consumers={"sauna": 0.5}, missing_hours={"fox": 1},
                                    missing_marks=["fox|2026-11-23T09:00:00+01:00"])},
        days={"2026-11-23": Counters(pv=3.0)},
        pending={"2026-10": PendingClose("2026-10", Counters(export_offpeak=4.0), DEFAULT_TARIFF.to_dict(), True)},
        inflows=[Inflow("2026-11", Zone.OFFPEAK, D("12.5"), "2027-11")],
        corrections=[{"time": t.isoformat(), "month": "2026-11", "amount": "5"}],
        history=[_row()],
        last_closed="2026-10",
        storage_start="2026-11",
        weather={"2026-11-23": {"temp": 3.5, "radiation": None}},
        last_tick=t,
    )


def test_state_roundtrip():
    original = _full_state()
    restored = State.from_dict(json.loads(json.dumps(original.to_dict())))
    assert restored == original
    assert restored.history[0].invoice == D("447.37")
    assert restored.inflows[0].zone is Zone.OFFPEAK


def test_empty_state_roundtrip_defaults():
    restored = State.from_dict(json.loads(json.dumps(State().to_dict())))
    assert restored == State()
    assert restored.storage_start == "2026-11" and restored.last_closed is None and restored.last_tick is None


def test_unsupported_schema():
    with pytest.raises(ValueError, match="unsupported schema"):
        State.from_dict({"schema": 2})
