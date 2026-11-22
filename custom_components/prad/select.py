"""Month comparison selects (options come from the closed-month history)."""
from __future__ import annotations

from typing import Any

from homeassistant.components.select import SelectEntity

from .const import NAME_PREFIX
from .coordinator import PradCoordinator
from .entity import PradEntity


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    coordinator: PradCoordinator = entry.runtime_data
    async_add_entities(
        [
            PradCompareSelect(coordinator, "porownanie_a", "porównanie – miesiąc A"),
            PradCompareSelect(coordinator, "porownanie_b", "porównanie – miesiąc B"),
            PradInvoiceMonthSelect(coordinator),
        ]
    )


class PradCompareSelect(PradEntity, SelectEntity):
    """Pick one month of the history; the choice is kept in the settings."""

    _unrecorded_attributes = frozenset({"wiersz"})

    def __init__(self, coordinator: PradCoordinator, key: str, name: str) -> None:
        super().__init__(coordinator, "select", key)
        self._key = key
        self._attr_name = f"{NAME_PREFIX}{name}"

    @property
    def options(self) -> list[str]:
        months = {row.month for row in self.coordinator.engine.state.history}
        return sorted(months, reverse=True)

    @property
    def current_option(self) -> str | None:
        options = self.options
        if not options:
            return None
        chosen = self.coordinator.settings.get(self._key)
        if isinstance(chosen, str) and chosen in options:
            return chosen
        if self._key == "porownanie_a":
            return options[1] if len(options) > 1 else options[0]
        return options[0]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        current = self.current_option
        for row in self.coordinator.engine.state.history:
            if row.month == current:
                return {"wiersz": row.to_dict()}
        return {"wiersz": None}

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_set_setting(self._key, option)


class PradInvoiceMonthSelect(PradEntity, SelectEntity):
    """Invoice form: the month the typed amount is saved for (draft kept in memory)."""

    _attr_icon = "mdi:calendar-month"

    def __init__(self, coordinator: PradCoordinator) -> None:
        super().__init__(coordinator, "select", "faktura_miesiac")
        self._attr_name = f"{NAME_PREFIX}faktura – miesiąc"

    @property
    def options(self) -> list[str]:
        return self.coordinator.history_months()

    @property
    def current_option(self) -> str | None:
        return self.coordinator.invoice_form_month()

    async def async_select_option(self, option: str) -> None:
        self.coordinator.select_invoice_month(option)
