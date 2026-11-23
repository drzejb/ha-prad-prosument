"""Store migration v0.2 -> v0.3 (schema 1 -> 2). Pure Python, works on dicts only."""
from __future__ import annotations

import calendar
import copy
from datetime import date
from typing import Any

from .state import to_decimal
from .zones import TAURON_G12W

DEFAULT_SETTINGS: dict[str, Any] = {
    "energia_droga": "0.627", "energia_tania": "0.418", "dystrybucja_droga": "0.3298",
    "dystrybucja_tania": "0.0512", "oplata_jakosciowa": "0.0332", "oplata_oze": "0.0073",
    "oplata_kogeneracyjna": "0.003", "oplata_sieciowa_stala": "10.86", "oplata_abonamentowa": "4.56",
    "oplata_mocowa": "24.05", "oplata_handlowa": "0", "oplata_przejsciowa": "0", "vat": "23",
    "wspolczynnik_opustu": "0.8", "wersja_cennika": "2026-09 faktura",
}

# old setting key -> price version field
SETTING_TO_FIELD: dict[str, str] = {
    "energia_droga": "energy_peak", "energia_tania": "energy_offpeak",
    "dystrybucja_droga": "dist_peak", "dystrybucja_tania": "dist_offpeak",
    "oplata_jakosciowa": "quality", "oplata_oze": "oze", "oplata_kogeneracyjna": "cogeneration",
    "oplata_sieciowa_stala": "fixed_network", "oplata_abonamentowa": "subscription",
    "oplata_mocowa": "capacity", "oplata_handlowa": "trade", "oplata_przejsciowa": "transition",
    "vat": "vat_percent",
}
_OLD_KEYS = (*DEFAULT_SETTINGS,)
_DEFAULT_NAME = "Cennik"


def _month_bounds(month: str) -> tuple[str, str]:
    year, mon = int(month[:4]), int(month[5:7])
    return date(year, mon, 1).isoformat(), date(year, mon, calendar.monthrange(year, mon)[1]).isoformat()


def _text(value: Any) -> str:
    return str(value)


def _version(od: str, name: Any, fields: dict[str, str]) -> dict[str, Any]:
    return {
        "od": od, "nazwa": _text(name) if name not in (None, "") else _DEFAULT_NAME,
        "taryfa": "G12w", "strefy": TAURON_G12W.to_dict(), **fields,
    }


def _price_from_tariff(tariff: dict[str, Any], settings: dict[str, Any]) -> tuple[dict[str, str], Any, Any]:
    """Frozen tariff of a row / pending month; missing or None fields come from the current settings."""
    fields: dict[str, str] = {}
    for key, field in SETTING_TO_FIELD.items():
        value = tariff.get(field)
        fields[field] = _text(settings[key] if value is None else value)
    ratio = tariff.get("credit_ratio")
    version = tariff.get("version")
    return (
        fields,
        settings["wspolczynnik_opustu"] if ratio is None else ratio,
        settings["wersja_cennika"] if version is None else version,
    )


def migrate_v1_to_v2(data: dict[str, Any], today: date) -> dict[str, Any]:
    new = copy.deepcopy(data)
    state: dict[str, Any] = new.setdefault("state", {})
    if state.get("schema", 1) >= 2:
        return new

    settings: dict[str, Any] = new.get("settings") or {}
    new["settings"] = settings
    effective = {key: (settings[key] if settings.get(key) is not None else default)
                 for key, default in DEFAULT_SETTINGS.items()}

    history: list[dict[str, Any]] = state.get("history") or []
    pending: dict[str, Any] = state.get("pending") or {}
    months = [row["month"] for row in history] + list(state.get("months") or {}) + list(pending)
    start = _month_bounds(min(months))[0] if months else today.replace(day=1).isoformat()

    price_fields = {field: _text(effective[key]) for key, field in SETTING_TO_FIELD.items()}
    base_ratio = _text(effective["wspolczynnik_opustu"])
    base_version = _version(start, effective["wersja_cennika"], price_fields)
    for key in _OLD_KEYS:
        settings.pop(key, None)
    settings["cenniki"] = [base_version]
    settings["opust"] = [{"od": start, "wspolczynnik": base_ratio}]

    def frozen(tariff: Any, first: str) -> tuple[dict[str, Any], str]:
        if tariff is None:
            return {**copy.deepcopy(base_version), "od": first}, base_ratio
        fields, ratio, name = _price_from_tariff(tariff, effective)
        return _version(first, name, fields), _text(ratio)

    for row in history:
        first, last = _month_bounds(row["month"])
        cennik, ratio = frozen(row.pop("tariff", None), first)
        row["segmenty"] = [{
            "od": first, "do": last, "cennik": cennik,
            "pobor": {"droga": row["pobor_droga"], "tania": row["pobor_tania"]},
            "oddanie": {"droga": row["oddanie_droga"], "tania": row["oddanie_tania"]},
            "do_zaplaty": {"droga": row["do_zaplaty_droga"], "tania": row["do_zaplaty_tania"]},
        }]
        row["segmenty_opustu"] = [{"od": first, "do": last, "wspolczynnik": ratio}]

    for month, item in pending.items():
        first, last = _month_bounds(month)
        cennik, ratio = frozen(item.pop("tariff", None), first)
        c = item["counters"]
        peak_out = {"droga": str(to_decimal(c["export_peak"])), "tania": str(to_decimal(c["export_offpeak"]))}
        item["segmenty_cen"] = [{
            "od": first, "do": last, "cennik": cennik,
            "pobor": {"droga": str(to_decimal(c["import_peak"])), "tania": str(to_decimal(c["import_offpeak"]))},
            "oddanie": dict(peak_out),
        }]
        item["segmenty_opustu"] = [{"od": first, "do": last, "wspolczynnik": ratio, "oddanie": peak_out}]

    state["schema"] = 2
    state["zrodla"] = {}
    state["zmiana_licznika"] = None
    return new
