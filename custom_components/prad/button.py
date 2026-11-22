"""Invoice form button: saves the amount for the chosen month."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.exceptions import ServiceValidationError

from .const import NAME_PREFIX
from .coordinator import PradCoordinator
from .entity import PradEntity
from .services import async_store_invoice


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    coordinator: PradCoordinator = entry.runtime_data
    async_add_entities([PradSaveInvoiceButton(coordinator)])


class PradSaveInvoiceButton(PradEntity, ButtonEntity):
    """Save `number.prad_faktura_kwota` as the invoice of `select.prad_faktura_miesiac`."""

    _attr_icon = "mdi:content-save"

    def __init__(self, coordinator: PradCoordinator) -> None:
        super().__init__(coordinator, "button", "faktura_zapisz")
        self._attr_name = f"{NAME_PREFIX}zapisz fakturę"

    async def async_press(self) -> None:
        coordinator = self.coordinator
        month = coordinator.invoice_form_month()
        if month is None:
            raise ServiceValidationError("Brak zamkniętych miesięcy – nie ma do czego wpisać faktury")
        amount = coordinator.invoice_form_amount()
        if amount is None:
            raise ServiceValidationError("Wpisz kwotę faktury")
        await async_store_invoice(coordinator, month, amount)
        # Saved: the form moves on to the next month without an invoice.
        coordinator.invoice_month = None
        coordinator.invoice_amount = None
        coordinator.async_update_listeners()
