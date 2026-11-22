"""Storage-before-cross-zone switch."""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity

from .const import NAME_PREFIX
from .coordinator import PradCoordinator
from .entity import PradEntity


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    async_add_entities([PradStorageBeforeCross(entry.runtime_data)])


class PradStorageBeforeCross(PradEntity, SwitchEntity):
    """Use the storage before moving energy between zones (default off)."""

    _attr_name = f"{NAME_PREFIX}magazyn przed przeniesieniem"

    def __init__(self, coordinator: PradCoordinator) -> None:
        super().__init__(coordinator, "switch", "magazyn_przed_przeniesieniem")

    @property
    def is_on(self) -> bool:
        return self.coordinator.storage_before_cross()

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_setting("magazyn_przed_przeniesieniem", True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_setting("magazyn_przed_przeniesieniem", False)
