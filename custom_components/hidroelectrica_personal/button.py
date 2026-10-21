"""Butoane: trimitere autocitire și actualizare date."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .coordinator import HidroConfigEntry, HidroCoordinator
from .entity import HidroEntity
from .reading import async_submit_reading


async def async_setup_entry(
    hass: HomeAssistant, entry: HidroConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    entities: list[ButtonEntity] = []
    for coord in entry.runtime_data.coordinators.values():
        entities.append(HidroRefreshButton(coord))
        if not (coord.data or {}).get("is_prosumer"):
            entities.append(HidroSubmitButton(coord))
    async_add_entities(entities)


class HidroSubmitButton(HidroEntity, ButtonEntity):
    _attr_icon = "mdi:send-circle"

    def __init__(self, coordinator: HidroCoordinator) -> None:
        super().__init__(coordinator, "submit_reading")

    async def async_press(self) -> None:
        registry = er.async_get(self.hass)
        number_id = registry.async_get_entity_id(
            "number", DOMAIN, f"{DOMAIN}_{self.coordinator.uan}_reading_to_submit"
        )
        state = self.hass.states.get(number_id) if number_id else None
        try:
            value = float(state.state) if state else None
        except ValueError:
            value = None
        if value is None:
            raise ServiceValidationError("Completați întâi câmpul „Index de trimis”.")
        await async_submit_reading(self.coordinator, index=value)


class HidroRefreshButton(HidroEntity, ButtonEntity):
    _attr_icon = "mdi:refresh"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: HidroCoordinator) -> None:
        super().__init__(coordinator, "refresh")

    async def async_press(self) -> None:
        await self.coordinator.async_full_refresh()
