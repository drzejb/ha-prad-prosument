"""CSV export of monthly history, consumers and invoices. Pure Python, no Home Assistant imports."""
from __future__ import annotations

import csv
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from .state import HistoryRow

MONTHS_HEADER: tuple[str, ...] = (
    "miesiac", "pobor_droga", "pobor_tania", "oddanie_droga", "oddanie_tania",
    "magazyn_wplyw", "magazyn_uzyte", "magazyn_wygasle", "magazyn_stan",
    "do_zaplaty_droga", "do_zaplaty_tania", "koszt_droga", "koszt_tania",
    "oplaty_stale", "koszt_netto", "koszt_razem", "zuzycie_domu", "produkcja_pv",
    "autokonsumpcja", "reszta", "temp_srednia", "stopniodni", "naslonecznienie_mj",
    "wersja_cennika",
)
CONSUMERS_HEADER: tuple[str, ...] = ("miesiac", "odbiornik", "kwh", "godziny_bez_danych")
INVOICES_HEADER: tuple[str, ...] = ("miesiac", "kwota", "data_wpisu")

_KWH = Decimal("0.001")
_PLN = Decimal("0.01")
_ONE = Decimal("0.1")
_FOX = "fox"  # consumers_missing also carries Fox's missing hours; not a consumer


def _fmt(value: Decimal | float | None, step: Decimal) -> str:
    if value is None:
        return ""
    d = value if isinstance(value, Decimal) else Decimal(str(value))
    return format(d.quantize(step, rounding=ROUND_HALF_UP), "f")


def month_row(row: HistoryRow) -> list[str]:
    return [
        row.month,
        _fmt(row.pobor_droga, _KWH), _fmt(row.pobor_tania, _KWH),
        _fmt(row.oddanie_droga, _KWH), _fmt(row.oddanie_tania, _KWH),
        _fmt(row.magazyn_wplyw, _KWH), _fmt(row.magazyn_uzyte, _KWH),
        _fmt(row.magazyn_wygasle, _KWH), _fmt(row.magazyn_stan, _KWH),
        _fmt(row.do_zaplaty_droga, _KWH), _fmt(row.do_zaplaty_tania, _KWH),
        _fmt(row.koszt_droga, _PLN), _fmt(row.koszt_tania, _PLN),
        _fmt(row.oplaty_stale, _PLN), _fmt(row.koszt_netto, _PLN), _fmt(row.koszt_razem, _PLN),
        _fmt(row.zuzycie_domu, _KWH), _fmt(row.produkcja_pv, _KWH),
        _fmt(row.autokonsumpcja, _KWH), _fmt(row.reszta, _KWH),
        _fmt(row.temp_srednia, _ONE), _fmt(row.stopniodni, _ONE), _fmt(row.naslonecznienie_mj, _ONE),
        row.wersja_cennika,
    ]


def _has_month(path: Path, month: str) -> bool:
    if not path.exists():
        return False
    with path.open(encoding="utf-8", newline="") as fh:
        reader = csv.reader(fh, delimiter=";")
        next(reader, None)  # header
        return any(line and line[0] == month for line in reader)


def _append(path: Path, header: tuple[str, ...], rows: list[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    new_file = not path.exists() or path.stat().st_size == 0
    with path.open("a", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, delimiter=";", lineterminator="\n")
        if new_file:
            writer.writerow(header)
        writer.writerows(rows)


def append_month_row(path: Path, row: HistoryRow) -> bool:
    if _has_month(path, row.month):
        return False
    _append(path, MONTHS_HEADER, [month_row(row)])
    return True


def append_consumer_rows(path: Path, row: HistoryRow) -> bool:
    if _has_month(path, row.month):
        return False
    slugs = sorted((set(row.consumers) | set(row.consumers_missing)) - {_FOX})
    rows = [
        [row.month, slug, _fmt(row.consumers.get(slug, 0.0), _KWH), str(row.consumers_missing.get(slug, 0))]
        for slug in slugs
    ]
    _append(path, CONSUMERS_HEADER, rows)
    return True


def append_invoice(path: Path, month: str, amount: Decimal, when: datetime) -> None:
    _append(path, INVOICES_HEADER, [[month, _fmt(amount, _PLN), when.isoformat()]])
