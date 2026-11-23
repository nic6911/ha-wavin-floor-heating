from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import WavinController, WavinCoordinator
from .entity import WavinZoneEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: WavinCoordinator = hass.data[DOMAIN][entry.entry_id]
    coordinator.register_controller_platform(
        "sync", async_add_entities, _controller_switches
    )
    coordinator.register_platform("switch", async_add_entities, _zone_switches)


class WavinChildLockSwitch(WavinZoneEntity, SwitchEntity):
    _attr_name = "Child lock"
    _entity_key = "child_lock"

    @property
    def is_on(self) -> bool:
        return bool(self.zone.get("child_lock"))

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.call_service(
            f"{self._controller.service_prefix}_zone_set_child_lock",
            {"zone": self._zone_id, "enabled": True},
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.call_service(
            f"{self._controller.service_prefix}_zone_set_child_lock",
            {"zone": self._zone_id, "enabled": False},
        )


def _zone_switches(coordinator: WavinCoordinator, zone: dict[str, Any]):
    entities = [WavinChildLockSwitch(coordinator, zone)]
    if zone.get("floor_sensor"):
        entities.append(WavinFloorControlSwitch(coordinator, zone))
    return entities


class WavinFloorControlSwitch(WavinZoneEntity, SwitchEntity):
    _attr_name = "Floor control"
    _entity_key = "floor_control"

    @property
    def is_on(self) -> bool:
        return bool(self.zone.get("floor_enabled"))

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.call_service(
            f"{self._controller.service_prefix}_zone_set_floor_enabled",
            {"zone": self._zone_id, "enabled": True},
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.call_service(
            f"{self._controller.service_prefix}_zone_set_floor_enabled",
            {"zone": self._zone_id, "enabled": False},
        )


def _controller_switches(
    coordinator: WavinCoordinator, controller: WavinController
):
    return [WavinModeSwitch(coordinator, controller)]


class WavinModeSwitch(CoordinatorEntity[WavinCoordinator], SwitchEntity):
    """Synchronise all room/zone setpoints for one controller."""

    _attr_has_entity_name = False

    def __init__(
        self, coordinator: WavinCoordinator, controller: WavinController
    ) -> None:
        super().__init__(coordinator)
        self._controller = controller
        self._attr_unique_id = coordinator.sync_unique_id(controller)

    @property
    def name(self) -> str:
        return self.coordinator.sync_label(self._controller)

    @property
    def device_info(self):
        return self.coordinator.controller_device_info(self._controller)

    @property
    def is_on(self) -> bool:
        return self._controller.sync_enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.call_service(
            f"{self._controller.service_prefix}_zone_set_sync", {"enabled": True}
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.call_service(
            f"{self._controller.service_prefix}_zone_set_sync", {"enabled": False}
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._update_registry_name()

    @callback
    def _handle_coordinator_update(self) -> None:
        super()._handle_coordinator_update()
        self._update_registry_name()

    @callback
    def _update_registry_name(self) -> None:
        if not self.entity_id:
            return
        er.async_get(self.hass).async_update_entity(
            self.entity_id, has_entity_name=False, name=self.name
        )
