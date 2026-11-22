"""Base entity for the Prad integration."""
from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import PradCoordinator


class PradEntity(CoordinatorEntity[PradCoordinator]):
    """Entity bound to the Prad coordinator."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: PradCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{DOMAIN}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, "prad")},
            name="Prąd",
        )
