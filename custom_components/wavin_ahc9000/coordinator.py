from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import timedelta
import json
import logging
from typing import Any, Callable

from aioesphomeapi import (
    APIClient,
    ConnectionClosedEvent,
    TextSensorInfo,
    TextSensorState,
    UserService,
)
from aioesphomeapi.core import ConnectionNotEstablishedAPIError

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo, EVENT_DEVICE_REGISTRY_UPDATED
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.event import async_track_state_change_event

from .const import (
    CONF_EXTERNAL_SENSORS,
    CONF_HOST,
    CONF_NOISE_PSK,
    CONF_PORT,
    DOMAIN,
    is_topology_name,
)

_LOGGER = logging.getLogger(__name__)

EntityFactory = Callable[["WavinCoordinator", dict[str, Any]], list[Entity]]
ControllerFactory = Callable[["WavinCoordinator", "WavinController"], list[Entity]]


@dataclass
class WavinController:
    """One AHC/Sentio controller behind the ESPHome device."""

    address: int
    topology_key: int
    model: str = "ahc9000"
    service_prefix: str = "wavin"
    zones: dict[int, dict[str, Any]] = field(default_factory=dict)
    rssi: int | None = None
    sync_enabled: bool = False
    vacation: bool = False
    master_zone: int | None = None
    master_name: str = ""
    device_id: str | None = None


class WavinCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """Own the ESPHome native API connection and the zone topology."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=timedelta(seconds=30))
        self.entry = entry
        self.client = APIClient(
            entry.data[CONF_HOST],
            entry.data[CONF_PORT],
            None,
            noise_psk=entry.data.get(CONF_NOISE_PSK) or None,
        )
        self.mac: str = ""
        self.device_name: str = ""
        self.sw_version: str | None = None
        self.parent_device_id: str | None = None
        self.services: dict[str, UserService] = {}
        self.controllers: dict[int, WavinController] = {}
        self.external_sensors: dict[str, str] = dict(
            entry.options.get(CONF_EXTERNAL_SENSORS, {})
        )
        self._external_unsubs: list[Any] = []
        self._external_service_warned = False
        self.rssi: int | None = None
        self.zones: dict[str, dict[str, Any]] = {}
        self._topology_keys: set[int] = set()
        self._platforms: dict[str, tuple[Callable, EntityFactory]] = {}
        self._platform_entities: dict[str, dict[str, list[Entity]]] = {}
        self._controller_platforms: dict[str, tuple[Callable, ControllerFactory]] = {}
        self._controller_entities: dict[str, dict[int, list[Entity]]] = {}
        self._reconnect_task: asyncio.Task | None = None
        self._shutdown = False
        self._remove_connection_closed = self.client.add_connection_closed_callback(
            self._on_connection_closed
        )
        self._remove_device_listener = hass.bus.async_listen(
            EVENT_DEVICE_REGISTRY_UPDATED, self._on_device_registry_updated
        )

    async def async_setup(self) -> None:
        await self._async_connect_and_discover()
        self._setup_external_sensor_subscriptions()

    async def _async_connect_and_discover(self) -> None:
        await self.client.connect(on_stop=self._on_stop, login=True)
        device_info = await self.client.device_info()
        if device_info is None:
            raise RuntimeError("ESPHome device information is unavailable")
        self.mac = device_info.mac_address
        self.device_name = device_info.name
        self.sw_version = device_info.project_version or device_info.esphome_version

        entities, services = await self.client.list_entities_services()
        self.services = {service.name: service for service in services}

        self._topology_keys = {
            info.key
            for info in entities
            if isinstance(info, TextSensorInfo) and is_topology_name(info.name)
        }
        if not self._topology_keys:
            raise RuntimeError("No Wavin topology text sensor found")

        self.client.subscribe_states(self._on_state)

        device_registry = dr.async_get(self.hass)
        parent = device_registry.async_get_or_create(
            config_entry_id=self.entry.entry_id,
            identifiers={(DOMAIN, self.mac)},
            name=self.device_name,
            manufacturer="MoreGeekN",
            model="Wavin Floor Heating",
            sw_version=self.sw_version,
        )
        self.parent_device_id = parent.id

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        if not self.client.is_connected:
            try:
                await self._async_connect_and_discover()
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("Reconnect failed: %s", err)
        return self.zones

    async def async_shutdown(self) -> None:
        self._shutdown = True
        self._remove_connection_closed()
        self._remove_device_listener()
        for unsub in self._external_unsubs:
            unsub()
        self._external_unsubs.clear()
        await self._release_external_sensors()
        if self._reconnect_task is not None:
            self._reconnect_task.cancel()
            self._reconnect_task = None
        await self.client.disconnect()

    @callback
    def _on_device_registry_updated(self, event) -> None:
        if event.data.get("action") != "update":
            return
        changes = event.data.get("changes") or {}
        if "name" not in changes and "name_by_user" not in changes:
            return
        self.async_set_updated_data(self.zones)

    @callback
    def _on_connection_closed(self, event: ConnectionClosedEvent) -> None:
        if self._shutdown:
            return
        _LOGGER.warning("ESPHome connection closed (%s); scheduling reconnect", event)
        if self._reconnect_task is None or self._reconnect_task.done():
            self._reconnect_task = self.hass.async_create_task(self._async_reconnect())

    async def _on_stop(self, expected: bool) -> None:
        if self._shutdown:
            return
        _LOGGER.warning("ESPHome connection stopped; reconnecting")
        self._reconnect_task = self.hass.async_create_task(self._async_reconnect())

    async def _async_reconnect(self) -> None:
        while not self._shutdown:
            try:
                await asyncio.sleep(3)
                await self._async_connect_and_discover()
                _LOGGER.info("Reconnected to Wavin ESPHome device")
                return
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("Reconnect failed: %s", err)
                await asyncio.sleep(5)

    @callback
    def _on_state(self, state: TextSensorState) -> None:
        if state.key not in self._topology_keys:
            return
        if getattr(state, "missing_state", False):
            return
        payload = getattr(state, "state", None)
        if not payload:
            return
        try:
            parsed = json.loads(payload)
        except (TypeError, ValueError):
            _LOGGER.warning("Invalid Wavin topology JSON: %s", payload)
            return

        address = int(parsed.get("address") or 1)
        model = parsed.get("model")
        if not model:
            model = "sentio" if any(
                isinstance(zone, dict) and ("humidity" in zone or "vacation" in parsed)
                for zone in parsed.get("zones", [])
            ) else "ahc9000"
        service_prefix = parsed.get("service_prefix")
        if not service_prefix:
            service_prefix = "sentio" if model == "sentio" else (
                "wavin" if address == 1 else f"wavin{address}"
            )

        controller = self.controllers.get(address)
        if controller is None:
            controller = WavinController(address=address, topology_key=state.key)
            self.controllers[address] = controller
        elif controller.topology_key != state.key:
            _LOGGER.warning(
                "Controller address %s changed topology sensor key %s -> %s",
                address,
                controller.topology_key,
                state.key,
            )
        controller.topology_key = state.key
        controller.model = model
        controller.service_prefix = service_prefix
        controller.rssi = parsed.get("rssi")
        self.rssi = controller.rssi
        controller.sync_enabled = bool(parsed.get("sync"))
        controller.vacation = bool(parsed.get("vacation"))
        controller.master_zone = parsed.get("master_zone") or None
        controller.master_name = parsed.get("master_name") or ""
        controller.zones = {
            int(zone["id"]): zone
            for zone in parsed.get("zones", [])
            if isinstance(zone, dict) and "id" in zone
        }

        self._rebuild_zones()
        self._ensure_controller_devices()
        self._ensure_zone_devices()
        self._remove_stale_floor_entities()
        self.async_set_updated_data(self.zones)
        self._sync_all_platforms()
        self._sync_all_controller_platforms()
        self._push_all_external_temperatures()

    def _rebuild_zones(self) -> None:
        zones: dict[str, dict[str, Any]] = {}
        for address, controller in self.controllers.items():
            for zone_id, raw_zone in controller.zones.items():
                zone = dict(raw_zone)
                zone_key = f"{address}:{zone_id}"
                zone["_controller"] = controller
                zone["_zone_key"] = zone_key
                zone["_address"] = address
                zones[zone_key] = zone
        self.zones = zones

    def _single_controller_legacy(self) -> bool:
        return len(self.controllers) == 1 and 1 in self.controllers

    def zone_unique_id(self, controller: WavinController, zone_id: int, key: str) -> str:
        if self._single_controller_legacy():
            return f"{self.mac}_zone_{zone_id}_{key}"
        return f"{self.mac}_{controller.address}_zone_{zone_id}_{key}"

    def sync_unique_id(self, controller: WavinController) -> str:
        if self._single_controller_legacy():
            return f"{self.mac}_sync"
        return f"{self.mac}_{controller.address}_sync"

    def _zone_identifier(self, controller: WavinController, zone_id: int) -> str:
        if self._single_controller_legacy():
            return f"{self.mac}_zone_{zone_id}"
        return f"{self.mac}_{controller.address}_zone_{zone_id}"

    def _zone_name(self, controller: WavinController, zone: dict[str, Any]) -> str:
        zone_id = int(zone["id"])
        name = zone.get("name") or f"{'Room' if controller.model == 'sentio' else 'Zone'} {zone_id}"
        if self._single_controller_legacy():
            return name
        return f"{name} (AHC {controller.address})"

    def _controller_name(self, controller: WavinController) -> str:
        return f"AHC {controller.address}"

    def _remove_stale_floor_entities(self) -> None:
        """Remove old floor entities for zones without a floor probe."""
        registry = er.async_get(self.hass)
        for zone in self.zones.values():
            if "floor_sensor" not in zone or zone.get("floor_sensor"):
                continue
            controller = zone["_controller"]
            zone_id = int(zone["id"])
            for domain, key in (
                ("sensor", "floor_temperature"),
                ("climate", "floor_limits"),
                ("switch", "floor_control"),
            ):
                unique_id = self.zone_unique_id(controller, zone_id, key)
                entity_id = registry.async_get_entity_id(domain, DOMAIN, unique_id)
                if entity_id:
                    registry.async_remove(entity_id)

    def _ensure_controller_devices(self) -> None:
        if self._single_controller_legacy():
            controller = self.controllers[1]
            controller.device_id = self.parent_device_id
            return
        device_registry = dr.async_get(self.hass)
        for controller in self.controllers.values():
            device = device_registry.async_get_or_create(
                config_entry_id=self.entry.entry_id,
                identifiers={(DOMAIN, f"{self.mac}_controller_{controller.address}")},
                name=self._controller_name(controller),
                manufacturer="MoreGeekN",
                model="Wavin Floor Heating Controller",
                via_device_id=self.parent_device_id,
            )
            controller.device_id = device.id

    def _ensure_zone_devices(self) -> None:
        device_registry = dr.async_get(self.hass)
        for zone in self.zones.values():
            controller = zone["_controller"]
            device_registry.async_get_or_create(
                config_entry_id=self.entry.entry_id,
                identifiers={(DOMAIN, self._zone_identifier(controller, int(zone["id"])))},
                name=self._zone_name(controller, zone),
                manufacturer="MoreGeekN",
                model="Wavin Floor Heating Zone",
                via_device_id=controller.device_id or self.parent_device_id,
            )

    def _master_display_name(self, controller: WavinController) -> str:
        if controller.master_zone:
            device = dr.async_get(self.hass).async_get_device(
                identifiers={(DOMAIN, self._zone_identifier(controller, controller.master_zone))}
            )
            if device is not None:
                name = device.name_by_user or device.name
                if name:
                    return name
        return controller.master_name

    def sync_label(self, controller: WavinController) -> str:
        name = self._master_display_name(controller).strip()
        if name.lower().startswith("zone "):
            name = "zone " + name[5:]
        if name and not self._single_controller_legacy():
            name = f"{name} (AHC {controller.address})"
        return f"Sync all zones to {name}" if name else "Sync all zones"

    @property
    def service_prefix(self) -> str:
        if len(self.controllers) == 1:
            return next(iter(self.controllers.values())).service_prefix
        return "wavin"

    def parent_device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.mac)},
            name=self.device_name,
            manufacturer="MoreGeekN",
            model="Wavin Floor Heating",
            sw_version=self.sw_version,
        )

    def controller_device_info(self, controller: WavinController) -> DeviceInfo:
        if self._single_controller_legacy() or controller.device_id is None:
            return self.parent_device_info()
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self.mac}_controller_{controller.address}")},
            name=self._controller_name(controller),
            manufacturer="MoreGeekN",
            model="Wavin Floor Heating Controller",
            via_device_id=self.parent_device_id,
        )

    def zone_device_info(self, controller: WavinController, zone: dict[str, Any]) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._zone_identifier(controller, int(zone["id"])))},
            name=self._zone_name(controller, zone),
            manufacturer="MoreGeekN",
            model="Wavin Floor Heating Zone",
            via_device_id=controller.device_id or self.parent_device_id,
        )

    def register_platform(
        self, key: str, add_entities: Callable, factory: EntityFactory
    ) -> None:
        self._platforms[key] = (add_entities, factory)
        self._sync_platform(key)

    def register_controller_platform(
        self, key: str, add_entities: Callable, factory: ControllerFactory
    ) -> None:
        self._controller_platforms[key] = (add_entities, factory)
        self._sync_controller_platform(key)

    def _sync_all_platforms(self) -> None:
        for key in self._platforms:
            self._sync_platform(key)

    def _sync_all_controller_platforms(self) -> None:
        for key in self._controller_platforms:
            self._sync_controller_platform(key)

    @callback
    def _sync_platform(self, key: str) -> None:
        add_entities, factory = self._platforms[key]
        existing = self._platform_entities.setdefault(key, {})
        wanted: dict[str, list[Entity]] = {}

        for zone_key, zone in self.zones.items():
            entities = factory(self, zone)
            if entities:
                wanted[zone_key] = entities

        for zone_key, entities in wanted.items():
            if zone_key not in existing:
                add_entities(entities)
                existing[zone_key] = entities

        for zone_key in list(existing):
            if zone_key not in wanted:
                for entity in existing.pop(zone_key):
                    self.hass.async_create_task(entity.async_remove())

    @callback
    def _sync_controller_platform(self, key: str) -> None:
        add_entities, factory = self._controller_platforms[key]
        existing = self._controller_entities.setdefault(key, {})
        wanted: dict[int, list[Entity]] = {}

        for address, controller in self.controllers.items():
            entities = factory(self, controller)
            if entities:
                wanted[address] = entities

        for address, entities in wanted.items():
            if address not in existing:
                add_entities(entities)
                existing[address] = entities

        for address in list(existing):
            if address not in wanted:
                for entity in existing.pop(address):
                    self.hass.async_create_task(entity.async_remove())


    def _controller_for_external_key(self, key: str) -> tuple[WavinController, int] | None:
        try:
            address_text, zone_text = key.split(":", 1)
            address = int(address_text)
            zone_id = int(zone_text)
        except (TypeError, ValueError):
            return None
        controller = self.controllers.get(address)
        if controller is None:
            return None
        return controller, zone_id

    def external_sensor_for(self, controller: WavinController, zone_id: int) -> str | None:
        return self.external_sensors.get(f"{controller.address}:{zone_id}")

    def _setup_external_sensor_subscriptions(self) -> None:
        for unsub in self._external_unsubs:
            unsub()
        self._external_unsubs.clear()
        entity_ids = {entity_id for entity_id in self.external_sensors.values() if entity_id}
        for entity_id in entity_ids:
            self._external_unsubs.append(
                async_track_state_change_event(
                    self.hass, [entity_id], self._on_external_sensor_event
                )
            )
        self._push_all_external_temperatures()

    @callback
    def _on_external_sensor_event(self, event) -> None:
        entity_id = event.data.get("entity_id")
        new_state = event.data.get("new_state")
        for key, mapped_entity_id in self.external_sensors.items():
            if mapped_entity_id == entity_id:
                self.hass.async_create_task(
                    self._push_external_temperature(key, new_state)
                )

    def _push_all_external_temperatures(self) -> None:
        for key, entity_id in self.external_sensors.items():
            state = self.hass.states.get(entity_id)
            self.hass.async_create_task(self._push_external_temperature(key, state))

    async def _push_external_temperature(self, key: str, state) -> None:
        parsed = self._controller_for_external_key(key)
        if parsed is None:
            return
        controller, zone_id = parsed
        prefix = controller.service_prefix
        if state is None or state.state in ("unknown", "unavailable"):
            service_name = f"{prefix}_zone_push_unavailable"
            data = {"zone": zone_id}
        else:
            try:
                temperature = float(state.state)
            except (TypeError, ValueError):
                service_name = f"{prefix}_zone_push_unavailable"
                data = {"zone": zone_id}
            else:
                service_name = f"{prefix}_zone_push_temperature"
                data = {"zone": zone_id, "temperature": temperature}
        if service_name not in self.services:
            if not self._external_service_warned:
                self._external_service_warned = True
                _LOGGER.warning(
                    "External room sensor requires firmware service %s; update the ESPHome firmware",
                    service_name,
                )
            return
        try:
            await self.call_service(service_name, data)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("External room sensor push failed: %s", err)

    async def _release_external_sensors(self) -> None:
        if not self.external_sensors:
            return
        tasks = []
        for key in self.external_sensors:
            parsed = self._controller_for_external_key(key)
            if parsed is None:
                continue
            controller, zone_id = parsed
            service_name = f"{controller.service_prefix}_zone_push_unavailable"
            if service_name in self.services:
                tasks.append(self.call_service(service_name, {"zone": zone_id}))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def call_service(self, name: str, data: dict[str, Any]) -> None:
        service = self.services.get(name)
        if service is None:
            _LOGGER.warning("ESPHome service %s is not available", name)
            return
        try:
            await self.client.execute_service(service, data)
        except ConnectionNotEstablishedAPIError:
            _LOGGER.warning("ESPHome connection lost during service call; reconnecting")
            await self._async_connect_and_discover()
            service = self.services.get(name)
            if service is not None:
                await self.client.execute_service(service, data)
