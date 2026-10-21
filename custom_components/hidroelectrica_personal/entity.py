"""Entitate de bază."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTRIBUTION, DOMAIN, MANUFACTURER
from .coordinator import HidroCoordinator


class HidroEntity(CoordinatorEntity[HidroCoordinator]):
    _attr_has_entity_name = True
    _attr_attribution = ATTRIBUTION

    def __init__(self, coordinator: HidroCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._key = key
        self._attr_translation_key = key
        self._attr_unique_id = f"{DOMAIN}_{coordinator.uan}_{key}"
        meter = (coordinator.data or {}).get("meter") or {}
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.uan)},
            name=f"Hidroelectrica {coordinator.uan}",
            manufacturer=MANUFACTURER,
            model="Loc de consum energie electrică",
            serial_number=str(meter.get("MeterNumber") or "") or None,
            entry_type=DeviceEntryType.SERVICE,
            configuration_url="https://ihidro.ro",
        )

    @property
    def data(self) -> dict:
        return self.coordinator.data or {}
