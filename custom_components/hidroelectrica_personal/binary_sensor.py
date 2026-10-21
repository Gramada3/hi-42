"""Senzori binari Hidroelectrica (personal)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import parsers as p
from .coordinator import HidroConfigEntry, HidroCoordinator
from .entity import HidroEntity


def _balance(d: dict) -> float | None:
    return (d.get("bill") or {}).get("balance")


def _overdue(d: dict) -> bool:
    bal, due = _balance(d), (d.get("bill") or {}).get("due_date")
    return bool(bal and bal > 0 and due and date.today() > due)


@dataclass(frozen=True, kw_only=True)
class HidroBinaryDescription(BinarySensorEntityDescription):
    value_fn: Callable[[dict], bool | None]
    attrs_fn: Callable[[dict], dict] | None = None


BINARY_SENSORS: tuple[HidroBinaryDescription, ...] = (
    HidroBinaryDescription(
        key="balance_due",
        icon="mdi:cash-alert",
        value_fn=lambda d: None if _balance(d) is None else _balance(d) > 0,
        attrs_fn=lambda d: {
            "sold": _balance(d),
            "scadenta": p.iso((d.get("bill") or {}).get("due_date")),
        },
    ),
    HidroBinaryDescription(
        key="overdue",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=_overdue,
        attrs_fn=lambda d: {
            "suma_restanta": _balance(d) if _overdue(d) else 0.0,
            "zile_intarziere": (date.today() - d["bill"]["due_date"]).days if _overdue(d) else 0,
        },
    ),
    HidroBinaryDescription(
        key="reading_window",
        icon="mdi:clipboard-text-clock",
        value_fn=lambda d: (d.get("window") or {}).get("is_open"),
        attrs_fn=lambda d: {
            "inceput": p.iso((d.get("window") or {}).get("start")),
            "sfarsit": p.iso((d.get("window") or {}).get("end")),
        },
    ),
    HidroBinaryDescription(
        key="prosumer",
        icon="mdi:solar-panel",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.get("is_prosumer"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: HidroConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities(
        HidroBinarySensor(coord, desc)
        for coord in entry.runtime_data.coordinators.values()
        for desc in BINARY_SENSORS
    )


class HidroBinarySensor(HidroEntity, BinarySensorEntity):
    entity_description: HidroBinaryDescription

    def __init__(self, coordinator: HidroCoordinator, description: HidroBinaryDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.value_fn(self.data)

    @property
    def extra_state_attributes(self) -> dict | None:
        fn = self.entity_description.attrs_fn
        return fn(self.data) if fn and self.data else None
