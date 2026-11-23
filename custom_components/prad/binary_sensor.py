"""Peak-zone binary sensor."""
from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.util import dt as dt_util

from .const import NAME_PREFIX
from .coordinator import PradCoordinator
from .entity import PradEntity
from .logic.zones import Zone, next_zone_change, zone_at


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    async_add_entities([PradPeakZone(entry.runtime_data)])


class PradPeakZone(PradEntity, BinarySensorEntity):
    """On during the peak ("droga") zone; recomputed from the clock every full hour."""

    _attr_name = f"{NAME_PREFIX}strefa droga"

    def __init__(self, coordinator: PradCoordinator) -> None:
        super().__init__(coordinator, "binary_sensor", "strefa_droga")

    @property
    def is_on(self) -> bool:
        now = dt_util.now()
        return zone_at(now, self.coordinator.pricing().schedule_at(now)) is Zone.PEAK

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        change = next_zone_change(dt_util.now(), self.coordinator.pricing().schedule_for)
        return {"nastepna_zmiana": None if change is None else change.isoformat()}
