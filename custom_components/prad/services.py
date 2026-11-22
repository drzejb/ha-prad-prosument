"""Services of the Prad integration."""
from __future__ import annotations

import math
from datetime import datetime, time
from decimal import Decimal
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import PradCoordinator
from .logic.zones import TZ

_MONTH = vol.Match(r"^\d{4}-(0[1-9]|1[0-2])$", msg="Miesiąc musi mieć format RRRR-MM")

SCHEMA_STORAGE = vol.Schema({vol.Required("ilosc"): vol.Coerce(float), vol.Required("miesiac"): _MONTH})
SCHEMA_INVOICE = vol.Schema(
    {vol.Required("miesiac"): _MONTH, vol.Required("kwota"): vol.All(vol.Coerce(float), vol.Range(min=0))}
)
SCHEMA_REBUILD = vol.Schema(
    {
        vol.Required("od"): cv.date,
        vol.Required("poczatek_magazynu"): _MONTH,
        vol.Optional("potwierdz", default=False): cv.boolean,
    }
)


def _coordinator(hass: HomeAssistant) -> PradCoordinator:
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is ConfigEntryState.LOADED:
            return entry.runtime_data
    raise ServiceValidationError("Integracja Prąd nie jest skonfigurowana")


def _decimal(value: float) -> Decimal:
    if not math.isfinite(value):
        raise ServiceValidationError("Liczba musi być skończona")
    return Decimal(str(value))


async def _storage_correction(call: ServiceCall) -> None:
    coordinator = _coordinator(call.hass)
    amount = _decimal(call.data["ilosc"])
    async with coordinator.lock:
        try:
            coordinator.engine.correct_storage(amount, call.data["miesiac"], dt_util.now())
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err
        await coordinator.async_save()
    coordinator.async_update_listeners()


async def _set_invoice(call: ServiceCall) -> None:
    coordinator = _coordinator(call.hass)
    await async_store_invoice(coordinator, call.data["miesiac"], _decimal(call.data["kwota"]))


async def async_store_invoice(coordinator: PradCoordinator, month: str, amount: Decimal) -> None:
    """Shared by the service and the invoice form button; errors as ServiceValidationError."""
    try:
        await coordinator.async_set_invoice(month, amount)
    except ValueError as err:
        raise ServiceValidationError(str(err)) from err
    except OSError as err:
        raise ServiceValidationError("Faktura zapisana, ale nie udało się dopisać do pliku CSV") from err


async def _rebuild(call: ServiceCall) -> dict[str, Any]:
    from .recorder_import import async_rebuild

    coordinator = _coordinator(call.hass)
    day = call.data["od"]
    if day > dt_util.now().astimezone(TZ).date():
        raise ServiceValidationError("Data początkowa z przyszłości")
    start = datetime.combine(day, time(0), tzinfo=TZ)
    async with coordinator.lock:
        st = coordinator.engine.state
        if (st.history or st.inflows or st.corrections) and not call.data["potwierdz"]:
            raise ServiceValidationError("Stan już istnieje (historia, magazyn lub korekty) – wywołaj z potwierdz: true")
        return await async_rebuild(call.hass, coordinator, start, call.data["poczatek_magazynu"])


def async_register_services(hass: HomeAssistant) -> None:
    hass.services.async_register(DOMAIN, "magazyn_korekta", _storage_correction, SCHEMA_STORAGE)
    hass.services.async_register(DOMAIN, "wpisz_fakture", _set_invoice, SCHEMA_INVOICE)
    hass.services.async_register(
        DOMAIN, "inicjuj", _rebuild, SCHEMA_REBUILD, supports_response=SupportsResponse.OPTIONAL
    )
