"""Base entity for the Prad integration."""
from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import PradCoordinator


class PradEntity(CoordinatorEntity[PradCoordinator]):
    """Entity bound to the Prad coordinator."""

    # Explicit entity_id and Polish display names: no device-name prefix.
    _attr_has_entity_name = False

    def __init__(self, coordinator: PradCoordinator, platform: str, key: str) -> None:
        """Create the entity.

        `platform` is the entity domain (e.g. "sensor"), `key` the tail of the entity_id
        without the "prad_" prefix (e.g. "mc_koszt"). Sets entity_id = f"{platform}.prad_{key}"
        and unique_id = f"prad_{key}".
        """
        super().__init__(coordinator)
        self.entity_id = f"{platform}.{DOMAIN}_{key}"
        self._attr_unique_id = f"{DOMAIN}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, "prad")},
            name="Prąd",
        )
