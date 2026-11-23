"""Tariff version text setting."""
from __future__ import annotations

from homeassistant.components.text import TextEntity, TextMode
from homeassistant.exceptions import ServiceValidationError

from .const import NAME_PREFIX
from .coordinator import PradCoordinator
from .entity import PradEntity
from .logic.tariff import PriceListError


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    async_add_entities([PradTariffVersion(entry.runtime_data)])


class PradTariffVersion(PradEntity, TextEntity):
    """Label stored with every closed month (`wersja_cennika`)."""

    _attr_name = f"{NAME_PREFIX}wersja cennika"
    _attr_mode = TextMode.TEXT
    _attr_native_min = 0
    _attr_native_max = 100

    def __init__(self, coordinator: PradCoordinator) -> None:
        super().__init__(coordinator, "text", "wersja_cennika")

    @property
    def native_value(self) -> str | None:
        value = self.coordinator.setting_value("wersja_cennika")
        return None if value is None else str(value)

    async def async_set_value(self, value: str) -> None:
        try:
            await self.coordinator.async_set_setting("wersja_cennika", value)
        except (ValueError, PriceListError) as err:
            raise ServiceValidationError(str(err)) from err
