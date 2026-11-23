from __future__ import annotations

from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.components.climate.const import (
    ATTR_TARGET_TEMP_HIGH,
    ATTR_TARGET_TEMP_LOW,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import WavinCoordinator
from .entity import WavinZoneEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: WavinCoordinator = hass.data[DOMAIN][entry.entry_id]
    coordinator.register_platform("climate", async_add_entities, _zone_climates)


def _zone_climates(coordinator: WavinCoordinator, zone: dict[str, Any]):
    entities = [WavinClimateEntity(coordinator, zone)]
    if zone.get("floor_sensor"):
        entities.append(WavinFloorLimitsClimate(coordinator, zone))
    return entities


class WavinClimateEntity(WavinZoneEntity, ClimateEntity):
    _attr_name = None
    _attr_native_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = 0.5
    _attr_min_temp = 5.0
    _attr_max_temp = 35.0
    _attr_hvac_modes = [HVACMode.HEAT, HVACMode.OFF]
    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE
    _entity_key = "climate"

    @property
    def current_temperature(self) -> float | None:
        value = self.zone.get("current")
        return float(value) if value is not None else None

    @property
    def target_temperature(self) -> float | None:
        value = self.zone.get("target")
        return float(value) if value is not None else None

    @property
    def hvac_mode(self) -> HVACMode:
        return HVACMode.OFF if self.zone.get("off") else HVACMode.HEAT

    @property
    def hvac_action(self) -> HVACAction:
        if self.zone.get("off"):
            return HVACAction.OFF
        return (
            HVACAction.HEATING
            if self.zone.get("action") == "heating"
            else HVACAction.IDLE
        )

    async def async_set_temperature(self, **kwargs: Any) -> None:
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return
        await self.coordinator.call_service(
            f"{self._controller.service_prefix}_zone_set_target",
            {"zone": self._zone_id, "temperature": float(temperature)},
        )

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        await self.coordinator.call_service(
            f"{self._controller.service_prefix}_zone_set_mode",
            {"zone": self._zone_id, "turn_off": hvac_mode == HVACMode.OFF},
        )


class WavinFloorLimitsClimate(WavinZoneEntity, ClimateEntity):
    """The two-point floor temperature range exposed by AHC IR thermostats."""

    _attr_name = "Floor limits"
    _attr_native_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = 0.5
    _attr_min_temp = 5.0
    _attr_max_temp = 35.0
    _attr_hvac_modes = [HVACMode.HEAT_COOL]
    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE_RANGE
    _entity_key = "floor_limits"

    @property
    def current_temperature(self) -> float | None:
        value = self.zone.get("floor")
        return float(value) if value is not None else None

    @property
    def target_temperature_low(self) -> float | None:
        value = self.zone.get("floor_min")
        return float(value) if value is not None else None

    @property
    def target_temperature_high(self) -> float | None:
        value = self.zone.get("floor_max")
        return float(value) if value is not None else None

    @property
    def hvac_mode(self) -> HVACMode:
        return HVACMode.HEAT_COOL

    @property
    def hvac_action(self) -> HVACAction:
        current = self.current_temperature
        low = self.target_temperature_low
        if current is not None and low is not None and current < low:
            return HVACAction.HEATING
        return HVACAction.IDLE

    async def async_set_temperature(self, **kwargs: Any) -> None:
        low = kwargs.get(ATTR_TARGET_TEMP_LOW)
        high = kwargs.get(ATTR_TARGET_TEMP_HIGH)
        if low is not None:
            await self.coordinator.call_service(
                f"{self._controller.service_prefix}_zone_set_floor_min",
                {"zone": self._zone_id, "temperature": float(low)},
            )
        if high is not None:
            await self.coordinator.call_service(
                f"{self._controller.service_prefix}_zone_set_floor_max",
                {"zone": self._zone_id, "temperature": float(high)},
            )

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        # The floor-limits entity is always a heat/cool range; it has no mode
        # command of its own. Floor sensing is controlled by the separate
        # Floor control switch.
        return
