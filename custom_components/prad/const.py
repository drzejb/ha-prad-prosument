"""Constants for the Prad integration."""
from __future__ import annotations

from decimal import Decimal

DOMAIN = "prad"
STORAGE_KEY = "prad"
BACKUP_KEY = "prad_backup"
STORAGE_VERSION = 1
STORAGE_MINOR = 2
BACKUP_V02_KEY = "prad_backup_v02"

CONF_IMPORT = "import_entity"
CONF_EXPORT = "export_entity"
CONF_PV = "pv_entity"
CONF_TEMP = "temperature_entity"
CONF_NET = "net_entity"
CONF_GRID_MODE = "tryb_sieci"
GRID_TWO = "dwa"
GRID_NET = "netto"
CONF_START = "start"  # entry.data: {"rozklad": str, "strefy": dict | None, "opust": str}; new installs only
CONF_CONSUMERS = "consumers"  # list of {"name": str, "entity_id": str}

RESERVED_SLUGS = frozenset({"fox", "pv"})  # engine tracker keys

CSV_DIR = "prad"  # relative to hass.config.path()
CSV_MONTHS = "prad_miesiace.csv"
CSV_CONSUMERS = "prad_odbiorniki.csv"
CSV_INVOICES = "prad_faktury.csv"

NAME_PREFIX = "Prąd – "  # display-name prefix of every entity

PLATFORMS = ["sensor", "binary_sensor", "number", "text", "switch", "select", "button"]

# Key = tail of the setting entity_id; only settings kept in `settings` (prices live in price versions).
SETTING_DEFAULTS: dict[str, Decimal | str | bool | None] = {
    "bezpiecznik_fox": Decimal("30"),
    "bezpiecznik_pv": Decimal("12"),
    "bezpiecznik_odbiorniki": Decimal("5"),
    "baza_stopniodni": Decimal("18"),
    "magazyn_przed_przeniesieniem": False,
    "porownanie_a": None,
    "porownanie_b": None,
}

# Setting entity key -> entity_id tail, where it differs from the key (unique_id keeps the key).
ENTITY_ID_KEYS: dict[str, str] = {"bezpiecznik_fox": "bezpiecznik_siec"}

# Setting key -> PriceVersion field (edits the version valid today).
PRICE_KEYS: dict[str, str] = {
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
}
KEY_PRICE_NAME = "wersja_cennika"
KEY_RATIO = "wspolczynnik_opustu"
NOTIFICATION_REBUILD = "prad_inicjuj"
