from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal

import pytest

from custom_components.prad.logic.engine import Engine, Limits
from custom_components.prad.logic.migrate import migrate_v1_to_v2
from custom_components.prad.logic.settlement import Inflow
from custom_components.prad.logic.state import State
from custom_components.prad.logic.tariff import (
    EXAMPLE_PRICE_NAME, OpustEntry, PriceVersion, Pricing, example_price,
)
from custom_components.prad.logic.zones import TAURON_G12W, TZ, Zone
from v02_fixture import v02_payload

D = Decimal
P, O = Zone.PEAK, Zone.OFFPEAK
TODAY = date(2026, 11, 23)

def test_settings_become_one_price_version_and_one_ratio():
    old = v02_payload()
    new = migrate_v1_to_v2(old, TODAY)
    assert old == v02_payload()                                   # wejście nietknięte
    s = new["settings"]
    assert [v["od"] for v in s["cenniki"]] == ["2026-09-01"] and s["opust"] == [{"od": "2026-09-01", "wspolczynnik": "0.8"}]
    v = PriceVersion.from_dict(s["cenniki"][0])
    assert v.nazwa == "2026-09 faktura" and v.taryfa == "G12w" and v.strefy == TAURON_G12W
    assert replace(v, od=date(2026, 1, 1), nazwa=EXAMPLE_PRICE_NAME) == example_price(date(2026, 1, 1))
    assert "energia_droga" not in s and "wspolczynnik_opustu" not in s and "wersja_cennika" not in s
    assert s["bezpiecznik_fox"] == "30" and s["magazyn_przed_przeniesieniem"] is False

def test_history_storage_and_invoices_survive():
    state = State.from_dict(migrate_v1_to_v2(v02_payload(), TODAY)["state"])
    row = state.history[0]
    assert (row.koszt_razem, row.koszt_netto, row.invoice) == (D("55.81"), D("45.37"), D("55.81"))
    seg = row.segmenty[0]
    assert (seg.od, seg.do) == (date(2026, 9, 1), date(2026, 9, 30)) and seg.cennik.nazwa == "2026-09 faktura"
    assert seg.pobor == {P: D("195"), O: D("280")} and seg.oddanie == {P: D("279"), O: D("312")} and seg.do_zaplaty == {P: D("0"), O: D("2")}
    assert row.segmenty_opustu[0].wspolczynnik == D("0.8") and row.storage_before_cross is False
    assert state.inflows == [Inflow("2026-11", Zone.PEAK, D("5"), "2027-11")] and len(state.corrections) == 1
    assert state.months["2026-11"].import_peak == 12.5 and state.months["2026-11"].pv_brak is False
    assert state.refs["fox"].values == (1500.0, 900.0) and state.zrodla == {} and state.zmiana_licznika is None

def test_pending_month_closes_with_the_same_amount_as_before():
    payload = migrate_v1_to_v2(v02_payload(), TODAY)
    e = Engine(State.from_dict(payload["state"]), {})
    p = e.state.pending["2026-10"]
    assert p.segmenty_cen[0].pobor == {P: D("370"), O: D("431")} and p.segmenty_opustu[0].oddanie == {P: D("209"), O: D("245")}
    row = e.close_month("2026-10", datetime(2026, 11, 23, 12, tzinfo=TZ), Limits())
    assert (row.do_zaplaty_droga, row.do_zaplaty_tania) == (D("203"), D("235"))
    assert (row.koszt_netto, row.koszt_razem) == (D("366.75"), D("451.10"))
    assert row.magazyn_stan == D("5") and e.state.inflows == [Inflow("2026-11", Zone.PEAK, D("5"), "2027-11")]
    pricing = Pricing(tuple(PriceVersion.from_dict(v) for v in payload["settings"]["cenniki"]),
                      tuple(OpustEntry.from_dict(o) for o in payload["settings"]["opust"]))
    assert e.current_snapshot(datetime(2026, 11, 23, 12, tzinfo=TZ), pricing, False).totals.gross > 0

def test_row_without_frozen_tariff_uses_current_settings():
    old = v02_payload()
    del old["state"]["history"][0]["tariff"]
    old["settings"]["wspolczynnik_opustu"] = "0.7"
    row = State.from_dict(migrate_v1_to_v2(old, TODAY)["state"]).history[0]
    assert row.segmenty[0].cennik.energy_peak == D("0.627") and row.segmenty_opustu[0].wspolczynnik == D("0.7")
    assert row.segmenty[0].cennik.od == date(2026, 9, 1)

def test_incomplete_frozen_tariff_is_filled_from_settings():
    old = v02_payload()
    old["settings"]["oplata_oze"] = "0.0099"
    old["settings"]["wspolczynnik_opustu"] = "0.7"
    for tariff in (old["state"]["history"][0]["tariff"], old["state"]["pending"]["2026-10"]["tariff"]):
        del tariff["oze"]; del tariff["credit_ratio"]; del tariff["version"]
        tariff["trade"] = None
    del old["settings"]["oplata_handlowa"]             # brak także w ustawieniach → tabela domyślnych (0)
    state = State.from_dict(migrate_v1_to_v2(old, TODAY)["state"])
    for cennik, ratio in ((state.history[0].segmenty[0].cennik, state.history[0].segmenty_opustu[0].wspolczynnik),
                          (state.pending["2026-10"].segmenty_cen[0].cennik, state.pending["2026-10"].segmenty_opustu[0].wspolczynnik)):
        assert cennik.oze == D("0.0099") and cennik.trade == D("0") and cennik.nazwa == "2026-09 faktura"
        assert cennik.energy_peak == D("0.627") and ratio == D("0.7")

def test_missing_settings_key_uses_defaults():
    old = v02_payload()
    del old["settings"]
    new = migrate_v1_to_v2(old, TODAY)
    assert new["settings"]["cenniki"][0]["energy_peak"] == "0.627" and new["settings"]["opust"][0]["wspolczynnik"] == "0.8"
    old["settings"] = None
    assert migrate_v1_to_v2(old, TODAY)["settings"]["cenniki"][0]["nazwa"] == "2026-09 faktura"

def test_empty_state_starts_on_first_day_of_current_month():
    old = {"state": {"schema": 1, "storage_start": "2026-11"}, "settings": {}}
    new = migrate_v1_to_v2(old, TODAY)
    assert new["settings"]["cenniki"][0]["od"] == "2026-11-01" and new["settings"]["opust"][0]["wspolczynnik"] == "0.8"
    assert State.from_dict(new["state"]).schema == 2

def test_migration_is_idempotent():
    once = migrate_v1_to_v2(v02_payload(), TODAY)
    assert migrate_v1_to_v2(once, TODAY) == once

def test_broken_history_row_raises_and_leaves_input():
    old = v02_payload()
    del old["state"]["history"][0]["pobor_droga"]
    with pytest.raises(KeyError):
        migrate_v1_to_v2(old, TODAY)
