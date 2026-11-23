from __future__ import annotations

import asyncio
import logging

import voluptuous as vol
from aioesphomeapi import APIClient, TextSensorInfo

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .const import CONF_NOISE_PSK, DEFAULT_PORT, DOMAIN, is_topology_name

_LOGGER = logging.getLogger(__name__)


def _normalize_mac(mac: str) -> str:
    """Normalize a MAC address for use as a config entry unique ID."""
    return mac.replace(":", "").replace("-", "").upper()


class WavinAHC9000ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Configure a Wavin AHC9000 ESPHome device."""

    VERSION = 1

    def __init__(self) -> None:
        self._host: str = ""
        self._port: int = DEFAULT_PORT
        self._name: str = ""
        self._noise_required = False

    async def _async_probe(self, host: str, port: int, noise_psk: str | None):
        client = APIClient(host, port, None, noise_psk=noise_psk or None)
        try:
            async with asyncio.timeout(20):
                await client.connect(login=True)
                entities, _services = await client.list_entities_services()
                device_info = await client.device_info()
                if device_info is None:
                    raise RuntimeError("ESPHome device information is unavailable")
                topology_found = any(
                    isinstance(info, TextSensorInfo)
                    and is_topology_name(info.name)
                    for info in entities
                )
                return (
                    _normalize_mac(device_info.mac_address),
                    device_info.name,
                    topology_found,
                )
        finally:
            await client.disconnect()

    async def async_step_zeroconf(
        self, discovery_info: ZeroconfServiceInfo
    ) -> FlowResult:
        """Handle automatic discovery of an ESPHome device."""
        mac = discovery_info.properties.get("mac")
        if not mac:
            return self.async_abort(reason="mdns_missing_mac")

        normalized_mac = _normalize_mac(mac)
        await self.async_set_unique_id(normalized_mac)
        self._abort_if_unique_id_configured()

        hostname = discovery_info.hostname.removesuffix(".local.")
        self._host = discovery_info.host
        self._port = discovery_info.port or DEFAULT_PORT
        self._name = discovery_info.properties.get("friendly_name", hostname)
        self._noise_required = bool(discovery_info.properties.get("api_encryption"))

        if not self._noise_required:
            try:
                mac, name, topology_found = await self._async_probe(
                    self._host, self._port, None
                )
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unable to probe ESPHome device at %s", self._host)
                return self.async_abort(reason="cannot_connect")
            if not topology_found:
                return self.async_abort(reason="not_wavin")
            await self.async_set_unique_id(mac)
            self._abort_if_unique_id_configured()
            self._name = name or self._name

        return await self.async_step_zeroconf_confirm()

    async def async_step_zeroconf_confirm(
        self, user_input=None
    ) -> FlowResult:
        """Confirm a discovered Wavin device."""
        errors: dict[str, str] = {}
        if user_input is not None:
            noise_psk = user_input.get(CONF_NOISE_PSK, "").strip() or None
            if self._noise_required:
                try:
                    mac, name, topology_found = await self._async_probe(
                        self._host, self._port, noise_psk
                    )
                except Exception:  # noqa: BLE001
                    _LOGGER.exception(
                        "Unable to connect to ESPHome device at %s", self._host
                    )
                    errors["base"] = "cannot_connect"
                else:
                    if not topology_found:
                        errors["base"] = "no_topology"
                    else:
                        await self.async_set_unique_id(mac)
                        self._abort_if_unique_id_configured()
                        self._name = name or self._name
                        return self.async_create_entry(
                            title=self._name,
                            data={
                                CONF_HOST: self._host,
                                CONF_PORT: self._port,
                                CONF_NOISE_PSK: noise_psk,
                            },
                        )
            else:
                return self.async_create_entry(
                    title=self._name,
                    data={
                        CONF_HOST: self._host,
                        CONF_PORT: self._port,
                        CONF_NOISE_PSK: None,
                    },
                )

        data_schema = (
            vol.Schema({vol.Optional(CONF_NOISE_PSK, default=""): str})
            if self._noise_required
            else vol.Schema({})
        )
        return self.async_show_form(
            step_id="zeroconf_confirm",
            description_placeholders={"name": self._name},
            data_schema=data_schema,
            errors=errors,
        )

    async def async_step_user(self, user_input=None) -> FlowResult:
        """Fallback manual setup when discovery is not available."""
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST]
            port = user_input[CONF_PORT]
            noise_psk = user_input.get(CONF_NOISE_PSK, "").strip() or None
            try:
                mac, name, topology_found = await self._async_probe(host, port, noise_psk)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unable to connect to ESPHome device at %s:%s", host, port)
                errors["base"] = "cannot_connect"
            else:
                if not topology_found:
                    errors["base"] = "no_topology"
                else:
                    await self.async_set_unique_id(mac)
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(
                        title=name or f"Wavin floor heating ({mac})",
                        data={
                            CONF_HOST: host,
                            CONF_PORT: port,
                            CONF_NOISE_PSK: noise_psk,
                        },
                    )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST): str,
                    vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
                    vol.Optional(CONF_NOISE_PSK, default=""): str,
                }
            ),
            errors=errors,
        )
