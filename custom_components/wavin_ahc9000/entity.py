from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import WavinCoordinator


class WavinParentEntity(CoordinatorEntity[WavinCoordinator]):
    """Base class for an entity belonging to the parent ESPHome device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: WavinCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.mac}_{self._entity_key}"

    @property
    def device_info(self) -> DeviceInfo:
        return self.coordinator.parent_device_info()


class WavinZoneEntity(CoordinatorEntity[WavinCoordinator]):
    """Base class for an entity belonging to one Wavin zone."""

    _attr_has_entity_name = True
    _entity_key = "zone"

    def __init__(self, coordinator: WavinCoordinator, zone: dict[str, Any]) -> None:
        super().__init__(coordinator)
        self._controller = zone["_controller"]
        self._zone_id = int(zone["id"])
        self._zone_key = zone["_zone_key"]
        self._attr_unique_id = coordinator.zone_unique_id(
            self._controller, self._zone_id, self._entity_key
        )

    @property
    def controller(self):
        return self._controller

    @property
    def zone(self) -> dict[str, Any]:
        return self.coordinator.zones.get(self._zone_key, {})

    @property
    def available(self) -> bool:
        return super().available and bool(self.zone)

    @property
    def device_info(self) -> DeviceInfo:
        return self.coordinator.zone_device_info(self._controller, self.zone)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attributes = {
            "channels": self.zone.get("channels"),
            "zone_id": self._zone_id,
        }
        external_sensor = self.coordinator.external_sensor_for(
            self._controller, self._zone_id
        )
        if external_sensor:
            attributes["external_sensor"] = external_sensor
            attributes["external_active"] = bool(self.zone.get("external_active"))
            attributes["external_failsafe"] = bool(self.zone.get("external_failsafe"))
        return attributes
