"""Verbindungsstatus zur Box."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import AupingEntity


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    async_add_entities([AupingConnectivity(entry)])


class AupingConnectivity(AupingEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_name = "Verbindung"

    def __init__(self, entry) -> None:
        super().__init__(entry, "connectivity")

    @property
    def available(self) -> bool:
        return True

    def _state_key(self) -> object:
        return self.is_on

    @property
    def is_on(self) -> bool:
        return self.controller.available and self._data.coordinator.last_update_success
