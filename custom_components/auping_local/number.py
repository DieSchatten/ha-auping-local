"""Kalibrierwerte als Konfigurations-Entitäten (Leerweg unten, Vollhub-Laufzeit)."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import apply_mattress
from .const import MAX_GAP_MS, MAX_TRAVEL_MS, MIN_TRAVEL_MS, MOTOR_NAMES
from .entity import AupingEntity

KEYS = {
    "gap": ("Leerweg unten", 0, MAX_GAP_MS),
    "up": ("Laufzeit hoch", MIN_TRAVEL_MS, MAX_TRAVEL_MS),
    "down": ("Laufzeit runter", MIN_TRAVEL_MS, MAX_TRAVEL_MS),
}


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    ctl = entry.runtime_data.controller
    # Entitäten "Anlauf hoch/runter" aus Version 0.3 entfernen
    registry = er.async_get(hass)
    for reg_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        if reg_entry.domain == "number" and reg_entry.unique_id.endswith(("_lag_up", "_lag_down")):
            registry.async_remove(reg_entry.entity_id)
    entities: list[NumberEntity] = [
        AupingCalibrationNumber(entry, side, motor, key)
        for side in ctl.sides
        for motor in ctl.motors
        for key in KEYS
    ]
    if len(ctl.sides) == 2:
        entities.append(MaxDiffNumber(entry))
    async_add_entities(entities)


class AupingCalibrationNumber(AupingEntity, NumberEntity):
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_native_step = 0.01

    def __init__(self, entry, side: str, motor: str, key: str) -> None:
        super().__init__(entry, f"calib_{side}_{motor}_{key}")
        label, lo, hi = KEYS[key]
        self._side, self._motor, self._key = side, motor, key
        self._label = label
        self._attr_native_min_value = lo / 1000
        self._attr_native_max_value = hi / 1000
        self._attr_icon = "mdi:timer-cog-outline" if key == "gap" else "mdi:timer-outline"
        # Laufzeiten sind nur für die Prozentanzeige wichtig – standardmäßig ausgeblendet.
        self._attr_entity_registry_enabled_default = key == "gap"

    @property
    def name(self) -> str:
        return f"{self._label} {MOTOR_NAMES[self._motor]} {self._data.names[self._side]}"

    def _state_key(self) -> object:
        return self.native_value

    @property
    def native_value(self) -> float:
        return round(self.controller.calibration[self._side][self._motor][self._key] / 1000, 3)

    async def async_set_native_value(self, value: float) -> None:
        self.controller.set_calibration(self._side, self._motor, self._key, value * 1000)


class MaxDiffNumber(AupingEntity, NumberEntity):
    """Wie weit die Seiten bei einer durchgehenden Matratze auseinander dürfen."""

    _attr_name = "Max. Unterschied der Seiten"
    _attr_icon = "mdi:arrow-expand-vertical"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_native_min_value = 1
    _attr_native_max_value = 50
    _attr_native_step = 1

    def __init__(self, entry) -> None:
        super().__init__(entry, "max_side_diff")

    def _state_key(self) -> object:
        return self.native_value

    @property
    def native_value(self) -> float:
        return round(self.controller.max_diff * 100)

    async def async_set_native_value(self, value: float) -> None:
        apply_mattress(self.hass, self._entry, max_diff=value)
