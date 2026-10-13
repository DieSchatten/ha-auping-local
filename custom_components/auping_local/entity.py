"""Gemeinsame Basis für alle Entitäten."""

from __future__ import annotations

import time

from homeassistant.core import callback
from homeassistant.helpers.entity import Entity

from .controller import BedController

WRITE_INTERVAL_S = 1.0  # während der Fahrt höchstens einmal pro Sekunde in HA schreiben


class AupingEntity(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry, key: str) -> None:
        self._entry = entry
        self._data = entry.runtime_data
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = self._data.device_info
        self._last_write = 0.0
        self._last_key: object = None

    @property
    def controller(self) -> BedController:
        return self._data.controller

    @property
    def available(self) -> bool:
        return self.controller.available and self._data.coordinator.last_update_success

    def _state_key(self) -> object:
        """Werte, deren Änderung sofort geschrieben werden soll."""
        return None

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.controller.add_listener(self._on_controller_update))
        self.async_on_remove(self._data.coordinator.async_add_listener(self.async_write_ha_state))

    @callback
    def _on_controller_update(self) -> None:
        key = (self.available, tuple(self._data.names.values()), self._state_key())
        now = time.monotonic()
        if key != self._last_key or (self._is_changing() and now - self._last_write >= WRITE_INTERVAL_S):
            self._last_key = key
            self._last_write = now
            self.async_write_ha_state()

    def _is_changing(self) -> bool:
        return False
