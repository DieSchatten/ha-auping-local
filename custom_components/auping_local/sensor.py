"""Diagnose: Firmware und IP-Adresse der Box."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import AupingEntity


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    async_add_entities(
        [
            AupingConfigSensor(entry, "firmware", "Firmware", "mdi:chip"),
            AupingConfigSensor(entry, "ip", "IP-Adresse", "mdi:ip-network"),
        ]
    )


class AupingConfigSensor(AupingEntity, SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry, field: str, name: str, icon: str) -> None:
        super().__init__(entry, f"sensor_{field}")
        self._field = field
        self._attr_name = name
        self._attr_icon = icon

    @property
    def available(self) -> bool:
        return self._data.coordinator.last_update_success

    @property
    def native_value(self) -> str | None:
        return (self._data.coordinator.data or {}).get(self._field)
