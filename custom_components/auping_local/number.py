"""Kalibrierwerte als Konfigurations-Entitäten (Anlaufverzögerung, Vollhub-Laufzeit)."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import MAX_LAG_MS, MAX_TRAVEL_MS, MIN_TRAVEL_MS, MOTOR_NAMES
from .entity import AupingEntity

KEYS = {
    "lag_up": ("Anlauf hoch", 0, MAX_LAG_MS),
    "lag_down": ("Anlauf runter", 0, MAX_LAG_MS),
    "up": ("Laufzeit hoch", MIN_TRAVEL_MS, MAX_TRAVEL_MS),
    "down": ("Laufzeit runter", MIN_TRAVEL_MS, MAX_TRAVEL_MS),
}


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    ctl = entry.runtime_data.controller
    names = entry.runtime_data.names
    async_add_entities(
        AupingCalibrationNumber(entry, side, motor, key, names[side])
        for side in ctl.sides
        for motor in ctl.motors
        for key in KEYS
    )


class AupingCalibrationNumber(AupingEntity, NumberEntity):
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_native_step = 0.01

    def __init__(self, entry, side: str, motor: str, key: str, side_name: str) -> None:
        super().__init__(entry, f"calib_{side}_{motor}_{key}")
        label, lo, hi = KEYS[key]
        self._side, self._motor, self._key = side, motor, key
        self._attr_name = f"{label} {MOTOR_NAMES[motor]} {side_name}"
        self._attr_native_min_value = lo / 1000
        self._attr_native_max_value = hi / 1000
        self._attr_icon = "mdi:timer-cog-outline" if key.startswith("lag") else "mdi:timer-outline"
        # Laufzeiten sind nur für die Prozentanzeige wichtig – standardmäßig ausgeblendet.
        self._attr_entity_registry_enabled_default = key.startswith("lag")

    def _state_key(self) -> object:
        return self.native_value

    @property
    def native_value(self) -> float:
        return round(self.controller.calibration[self._side][self._motor][self._key] / 1000, 3)

    async def async_set_native_value(self, value: float) -> None:
        self.controller.set_calibration(self._side, self._motor, self._key, value * 1000)
