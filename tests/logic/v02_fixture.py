"""Store payload in the v0.2 shape, built only from numbers used in the existing tests."""
from custom_components.prad.logic.tariff import DEFAULT_TARIFF

V02_SETTINGS = {
    "energia_droga": "0.627", "energia_tania": "0.418", "dystrybucja_droga": "0.3298", "dystrybucja_tania": "0.0512",
    "oplata_jakosciowa": "0.0332", "oplata_oze": "0.0073", "oplata_kogeneracyjna": "0.003",
    "oplata_sieciowa_stala": "10.86", "oplata_abonamentowa": "4.56", "oplata_mocowa": "24.05",
    "oplata_handlowa": "0", "oplata_przejsciowa": "0", "vat": "23", "wspolczynnik_opustu": "0.8",
    "wersja_cennika": "2026-09 faktura", "bezpiecznik_fox": "30", "bezpiecznik_pv": "12",
    "bezpiecznik_odbiorniki": "5", "baza_stopniodni": "18", "magazyn_przed_przeniesieniem": False,
    "porownanie_a": None, "porownanie_b": None,
}

def _counters(**kw):
    base = {"import_peak": 0.0, "import_offpeak": 0.0, "export_peak": 0.0, "export_offpeak": 0.0, "pv": 0.0,
            "consumers": {}, "missing_hours": {}, "missing_marks": []}
    return {**base, **kw}

def v02_september_row() -> dict:
    return {
        "month": "2026-09", "pobor_droga": "195", "pobor_tania": "280", "oddanie_droga": "279", "oddanie_tania": "312",
        "magazyn_wplyw": "0", "magazyn_uzyte": "0", "magazyn_wygasle": "0", "magazyn_stan": "0",
        "do_zaplaty_droga": "0", "do_zaplaty_tania": "2", "koszt_droga": "2.48", "koszt_tania": "4.78",
        "oplaty_stale": "48.55", "koszt_netto": "45.37", "koszt_razem": "55.81",
        "zuzycie_domu": 500.0, "produkcja_pv": 600.0, "autokonsumpcja": 9.0, "reszta": 400.0,
        "temp_srednia": None, "stopniodni": None, "naslonecznienie_mj": None, "wersja_cennika": "2026-09 faktura",
        "consumers": {}, "consumers_missing": {}, "invoice": "55.81", "csv_written": True,
        "tariff": DEFAULT_TARIFF.to_dict(), "storage_before_cross": False,
    }

def v02_payload() -> dict:
    state = {
        "schema": 1,
        "refs": {"fox": {"time": "2026-11-23T10:00:00+01:00", "values": [1500.0, 900.0]},
                 "pv": {"time": "2026-11-23T10:00:00+01:00", "values": [7000.0]}},
        "months": {"2026-11": _counters(import_peak=12.5, pv=3.0)},
        "days": {"2026-11-23": _counters(import_peak=1.5)},
        "pending": {"2026-10": {"month": "2026-10",
                                "counters": _counters(import_peak=370.0, import_offpeak=431.0, export_peak=209.0, export_offpeak=245.0),
                                "tariff": DEFAULT_TARIFF.to_dict(), "storage_before_cross": False}},
        "inflows": [{"month": "2026-11", "zone": "droga", "amount": "5", "valid_until": "2027-11"}],
        "corrections": [{"time": "2026-11-02T10:00:00+01:00", "month": "2026-11", "amount": "5"}],
        "history": [v02_september_row()],
        "last_closed": "2026-09", "storage_start": "2026-11", "weather": {},
        "last_tick": "2026-11-23T10:00:00+01:00", "rebuild_base": None,
    }
    return {"state": state, "settings": dict(V02_SETTINGS)}
