"""Câmp pentru indexul de trimis la autocitire."""

from __future__ import annotations

from homeassistant.components.number import NumberMode, RestoreNumber
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import HidroConfigEntry, HidroCoordinator
from .entity import HidroEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: HidroConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    # La prosumatori citirea o face distribuitorul, deci nu oferim autocitire.
    async_add_entities(
        HidroReadingNumber(coord)
        for coord in entry.runtime_data.coordinators.values()
        if not (coord.data or {}).get("is_prosumer")
    )


class HidroReadingNumber(HidroEntity, RestoreNumber):
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 0
    _attr_native_max_value = 99_999_999
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_icon = "mdi:counter"

    def __init__(self, coordinator: HidroCoordinator) -> None:
        super().__init__(coordinator, "reading_to_submit")
        self._value: float | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last and last.native_value is not None:
            self._value = last.native_value

    @property
    def native_value(self) -> float | None:
        if self._value is not None:
            return self._value
        return self.data.get("consumption_index")

    async def async_set_native_value(self, value: float) -> None:
        self._value = float(int(value))
        self.async_write_ha_state()
