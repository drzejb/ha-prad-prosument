from datetime import datetime, timedelta, timezone
from decimal import Decimal

from custom_components.prad.logic.csvio import (
    CONSUMERS_HEADER,
    INVOICES_HEADER,
    MONTHS_HEADER,
    append_consumer_rows,
    append_invoice,
    append_month_row,
    month_row,
)
from custom_components.prad.logic.state import HistoryRow

D = Decimal


def _row(month="2026-10", **kw) -> HistoryRow:
    base = dict(
        month=month, pobor_droga=D("194.2"), pobor_tania=D("431"), oddanie_droga=D("209"), oddanie_tania=D("245"),
        magazyn_wplyw=D("0"), magazyn_uzyte=D("0"), magazyn_wygasle=D("0"), magazyn_stan=D("12.5"),
        do_zaplaty_droga=D("203"), do_zaplaty_tania=D("235"), koszt_droga=D("30.1"), koszt_tania=D("10.2"),
        oplaty_stale=D("48.55"), koszt_netto=D("40.3"), koszt_razem=D("55.81"),
        zuzycie_domu=1.5, produkcja_pv=2.5, autokonsumpcja=1.0, reszta=0.5,
        temp_srednia=9.5, stopniodni=250.0, naslonecznienie_mj=None, wersja_cennika="v1",
    )
    base.update(kw)
    return HistoryRow(**base)


def _lines(path):
    return path.read_text(encoding="utf-8").splitlines()


def test_header_created_once(tmp_path):
    p = tmp_path / "sub" / "months.csv"
    assert append_month_row(p, _row("2026-09")) is True
    assert append_month_row(p, _row("2026-10")) is True
    lines = _lines(p)
    assert len(lines) == 3
    assert lines[0] == ";".join(MONTHS_HEADER)
    assert len(MONTHS_HEADER) == 24
    assert not p.read_bytes().startswith(b"\xef\xbb\xbf")


def test_append_month_row_is_idempotent(tmp_path):
    p = tmp_path / "months.csv"
    assert append_month_row(p, _row()) is True
    assert append_month_row(p, _row()) is False
    assert len(_lines(p)) == 2


def test_no_duplicate_of_older_month(tmp_path):
    p = tmp_path / "months.csv"
    assert append_month_row(p, _row("2026-09")) is True
    assert append_month_row(p, _row("2026-10")) is True
    assert append_month_row(p, _row("2026-09")) is False
    assert len(_lines(p)) == 3


def test_decimal_format():
    cells = dict(zip(MONTHS_HEADER, month_row(_row(koszt_razem=D("55.81"))), strict=True))
    assert cells["koszt_razem"] == "55.81"
    assert cells["pobor_droga"] == "194.200"
    assert cells["koszt_droga"] == "30.10"
    assert cells["oplaty_stale"] == "48.55"
    assert cells["zuzycie_domu"] == "1.500"
    assert cells["temp_srednia"] == "9.5"
    assert cells["stopniodni"] == "250.0"
    assert cells["naslonecznienie_mj"] == ""
    assert cells["wersja_cennika"] == "v1"


def test_rounding_half_up():
    cells = dict(zip(MONTHS_HEADER, month_row(_row(pobor_droga=D("1.0005"), zuzycie_domu=0.0005)), strict=True))
    assert cells["pobor_droga"] == "1.001"
    assert cells["zuzycie_domu"] == "0.001"


def test_consumers_long_format(tmp_path):
    p = tmp_path / "consumers.csv"
    assert append_consumer_rows(p, _row("2026-11", consumers={"sauna": 12.5})) is True
    lines = _lines(p)
    assert lines[0] == ";".join(CONSUMERS_HEADER)
    assert lines[1] == "2026-11;sauna;12.500;0"
    assert append_consumer_rows(p, _row("2026-11", consumers={"sauna": 12.5})) is False
    assert len(_lines(p)) == 2


def test_consumers_union_missing_and_fox_excluded(tmp_path):
    p = tmp_path / "consumers.csv"
    row = _row("2026-11", consumers={"sauna": 1.0}, consumers_missing={"bojler": 3, "fox": 5})
    append_consumer_rows(p, row)
    assert _lines(p)[1:] == ["2026-11;bojler;0.000;3", "2026-11;sauna;1.000;0"]


def test_invoice_append(tmp_path):
    p = tmp_path / "invoices.csv"
    when = datetime(2026, 11, 22, 10, 0, tzinfo=timezone(timedelta(hours=1)))
    append_invoice(p, "2026-09", D("55.81"), when)
    append_invoice(p, "2026-09", D("55.81"), when)
    lines = _lines(p)
    assert lines[0] == ";".join(INVOICES_HEADER)
    assert lines[1] == "2026-09;55.81;2026-11-22T10:00:00+01:00"
    assert len(lines) == 3


def test_none_energy_fields_are_empty():
    cells = dict(zip(MONTHS_HEADER, month_row(
        _row(zuzycie_domu=None, produkcja_pv=None, autokonsumpcja=None, reszta=None))))
    assert [cells[k] for k in ("zuzycie_domu", "produkcja_pv", "autokonsumpcja", "reszta")] == ["", "", "", ""]
