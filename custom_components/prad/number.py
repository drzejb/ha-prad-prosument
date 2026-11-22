"""Tariff and limit settings as number entities (the Store is the only source of truth)."""
from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.number import NumberEntity, NumberMode

from .const import NAME_PREFIX
from .coordinator import PradCoordinator
from .entity import PradEntity


@dataclass(frozen=True)
class NumberSpec:
    key: str
    name: str
    unit: str | None
    minimum: float
    maximum: float
    step: float


_PRICE = (0.0, 5.0, 0.00001)
_FEE_PER_KWH = (0.0, 1.0, 0.00001)
_FEE_FIXED = (0.0, 500.0, 0.01)

NUMBERS: tuple[NumberSpec, ...] = (
    NumberSpec("energia_droga", "cena energii (droga)", "zł/kWh", *_PRICE),
    NumberSpec("energia_tania", "cena energii (tania)", "zł/kWh", *_PRICE),
    NumberSpec("dystrybucja_droga", "dystrybucja zmienna (droga)", "zł/kWh", *_PRICE),
    NumberSpec("dystrybucja_tania", "dystrybucja zmienna (tania)", "zł/kWh", *_PRICE),
    NumberSpec("oplata_jakosciowa", "opłata jakościowa", "zł/kWh", *_FEE_PER_KWH),
    NumberSpec("oplata_oze", "opłata OZE", "zł/kWh", *_FEE_PER_KWH),
    NumberSpec("oplata_kogeneracyjna", "opłata kogeneracyjna", "zł/kWh", *_FEE_PER_KWH),
    NumberSpec("oplata_sieciowa_stala", "opłata sieciowa stała", "zł", *_FEE_FIXED),
    NumberSpec("oplata_abonamentowa", "opłata abonamentowa", "zł", *_FEE_FIXED),
    NumberSpec("oplata_mocowa", "opłata mocowa", "zł", *_FEE_FIXED),
    NumberSpec("oplata_handlowa", "opłata handlowa", "zł", *_FEE_FIXED),
    NumberSpec("oplata_przejsciowa", "opłata przejściowa", "zł", *_FEE_FIXED),
    NumberSpec("vat", "VAT", "%", 0.0, 100.0, 1.0),
    NumberSpec("wspolczynnik_opustu", "współczynnik opustu", None, 0.0, 1.0, 0.01),
    NumberSpec("bezpiecznik_fox", "bezpiecznik odczytu licznika Fox", "kWh", 1.0, 100.0, 1.0),
    NumberSpec("bezpiecznik_pv", "bezpiecznik odczytu produkcji PV", "kWh", 1.0, 100.0, 1.0),
    NumberSpec("bezpiecznik_odbiorniki", "bezpiecznik odczytu odbiorników", "kWh", 1.0, 100.0, 1.0),
    NumberSpec("baza_stopniodni", "baza stopniodni", "°C", 10.0, 25.0, 0.5),
)


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    coordinator: PradCoordinator = entry.runtime_data
    async_add_entities(PradNumber(coordinator, spec) for spec in NUMBERS)


class PradNumber(PradEntity, NumberEntity):
    """One tariff or limit setting."""

    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator: PradCoordinator, spec: NumberSpec) -> None:
        super().__init__(coordinator, "number", spec.key)
        self._key = spec.key
        self._attr_name = f"{NAME_PREFIX}{spec.name}"
        self._attr_native_unit_of_measurement = spec.unit
        self._attr_native_min_value = spec.minimum
        self._attr_native_max_value = spec.maximum
        self._attr_native_step = spec.step

    @property
    def native_value(self) -> float | None:
        value = self.coordinator.settings.get(self._key)
        return None if value is None else float(value)

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_setting(self._key, value)
