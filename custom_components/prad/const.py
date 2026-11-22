"""Constants for the Prad integration."""
from __future__ import annotations

from decimal import Decimal

DOMAIN = "prad"
STORAGE_KEY = "prad"
BACKUP_KEY = "prad_backup"
STORAGE_VERSION = 1
STORAGE_MINOR = 1

CONF_IMPORT = "import_entity"
CONF_EXPORT = "export_entity"
CONF_PV = "pv_entity"
CONF_TEMP = "temperature_entity"
CONF_CONSUMERS = "consumers"  # list of {"name": str, "entity_id": str}

CSV_DIR = "prad"  # relative to hass.config.path()
CSV_MONTHS = "prad_miesiace.csv"
CSV_CONSUMERS = "prad_odbiorniki.csv"
CSV_INVOICES = "prad_faktury.csv"

PLATFORMS = ["sensor", "binary_sensor", "number", "text", "switch", "select"]

# Key = tail of the setting entity_id (entities themselves come in Task 10).
SETTING_DEFAULTS: dict[str, Decimal | str | bool | None] = {
    "energia_droga": Decimal("0.627"),
    "energia_tania": Decimal("0.418"),
    "dystrybucja_droga": Decimal("0.3298"),
    "dystrybucja_tania": Decimal("0.0512"),
    "oplata_jakosciowa": Decimal("0.0332"),
    "oplata_oze": Decimal("0.0073"),
    "oplata_kogeneracyjna": Decimal("0.003"),
    "oplata_sieciowa_stala": Decimal("10.86"),
    "oplata_abonamentowa": Decimal("4.56"),
    "oplata_mocowa": Decimal("24.05"),
    "oplata_handlowa": Decimal("0"),
    "oplata_przejsciowa": Decimal("0"),
    "vat": Decimal("23"),
    "wspolczynnik_opustu": Decimal("0.8"),
    "wersja_cennika": "2026-09 faktura",
    "bezpiecznik_fox": Decimal("30"),
    "bezpiecznik_pv": Decimal("12"),
    "bezpiecznik_odbiorniki": Decimal("5"),
    "baza_stopniodni": Decimal("18"),
    "magazyn_przed_przeniesieniem": False,
    "porownanie_a": None,
    "porownanie_b": None,
}

# Setting key -> Tariff field name.
TARIFF_FIELDS: dict[str, str] = {
    "energia_droga": "energy_peak",
    "energia_tania": "energy_offpeak",
    "dystrybucja_droga": "dist_peak",
    "dystrybucja_tania": "dist_offpeak",
    "oplata_jakosciowa": "quality",
    "oplata_oze": "oze",
    "oplata_kogeneracyjna": "cogeneration",
    "oplata_sieciowa_stala": "fixed_network",
    "oplata_abonamentowa": "subscription",
    "oplata_mocowa": "capacity",
    "oplata_handlowa": "trade",
    "oplata_przejsciowa": "transition",
    "vat": "vat_percent",
    "wspolczynnik_opustu": "credit_ratio",
    "wersja_cennika": "version",
}
