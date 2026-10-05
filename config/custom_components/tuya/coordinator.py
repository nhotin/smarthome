"""Support for Tuya Smart devices."""

from datetime import timedelta
from pathlib import Path
from typing import Any

import requests
from tuya_device_handlers import TUYA_QUIRKS_REGISTRY
from tuya_device_handlers.devices import register_tuya_quirks
from tuya_sharing import (
    CustomerDevice,
    Manager,
    SharingDeviceListener,
    SharingTokenListener,
)

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_send, dispatcher_send
from homeassistant.helpers.event import async_track_time_interval

from .const import (
    CONF_ENDPOINT,
    CONF_TERMINAL_ID,
    CONF_TOKEN_INFO,
    CONF_USER_CODE,
    DOMAIN,
    LOGGER,
    TUYA_CLIENT_ID,
    TUYA_DISCOVERY_NEW,
    TUYA_HA_SIGNAL_UPDATE_ENTITY,
)
from .util import get_device_info

type TuyaConfigEntry = ConfigEntry[DeviceListener]


class DeviceListener(SharingDeviceListener):
    """Device Update Listener."""

    manager: Manager

    def __init__(
        self,
        hass: HomeAssistant,
        entry: TuyaConfigEntry,
    ) -> None:
        """Init DeviceListener."""
        self.hass = hass
        self._entry = entry

    def initialize(self) -> None:
        """Initialize device listener.

        Needs to be called in executor as these make blocking calls:
        - `register_tuya_quirks`
        - `Manager` initialization
        - `manager.update_device_cache`
        """
        entry = self._entry
        hass = self.hass

        # Makes blocking call to load files from disk
        register_tuya_quirks(str(Path(hass.config.config_dir, "tuya_quirks")))

        token_listener = _TokenListener(hass, entry)

        # Makes blocking call to import_module
        # with args ('.system', 'urllib3.contrib.resolver')
        manager = Manager(
            TUYA_CLIENT_ID,
            entry.data[CONF_USER_CODE],
            entry.data[CONF_TERMINAL_ID],
            entry.data[CONF_ENDPOINT],
            entry.data[CONF_TOKEN_INFO],
            token_listener,
        )

        manager.add_device_listener(self)

        # Get all devices from Tuya, makes blocking web calls
        try:
            manager.update_device_cache()
        except requests.exceptions.ConnectionError as exc:
            msg = "Unable to connect to Tuya"
            raise ConfigEntryNotReady(msg) from exc
        except Exception as exc:
            # While in general, we should avoid catching broad exceptions,
            # we have no other way of detecting this case.
            if "sign invalid" in str(exc):
                msg = "Authentication failed. Please re-authenticate"
                raise ConfigEntryAuthFailed(msg) from exc
            raise

        self.manager = manager

    def update_device(
        self,
        device: CustomerDevice,
        updated_status_properties: list[str] | None = None,
        dp_timestamps: dict[str, int] | None = None,
    ) -> None:
        """Handle device update event."""
        LOGGER.debug(
            "Received update for device %s (online: %s): %s"
            " (updated properties: %s, dp_timestamps: %s)",
            device.id,
            device.online,
            device.status,
            updated_status_properties,
            dp_timestamps,
        )
        dispatcher_send(
            self.hass,
            f"{TUYA_HA_SIGNAL_UPDATE_ENTITY}_{device.id}",
            updated_status_properties,
            dp_timestamps,
        )

    def add_device(self, device: CustomerDevice) -> None:
        """Handle device added event."""
        LOGGER.debug(
            "Add device %s (online: %s): %s (function: %s, status range: %s)",
            device.id,
            device.online,
            device.status,
            device.function,
            device.status_range,
        )
        self.hass.add_job(self.async_add_device, device)

    @callback
    def async_add_device(self, device: CustomerDevice) -> None:
        """Add device to Home Assistant."""
        # Ensure the (stale) device isn't present in the device registry
        self.async_remove_device(device.id)

        # Register quirk, and add device to the device registry
        device_registry = dr.async_get(self.hass)
        self.async_register_device(device_registry, device)

        # Notify platforms of new device so entities can be created
        async_dispatcher_send(self.hass, TUYA_DISCOVERY_NEW, [device.id])

    @callback
    def async_register_device(
        self, device_registry: dr.DeviceRegistry, device: CustomerDevice
    ) -> None:
        """Register device with Home Assistant."""
        TUYA_QUIRKS_REGISTRY.initialise_device_quirk(device)

        device_registry.async_get_or_create(
            config_entry_id=self._entry.entry_id,
            **get_device_info(device, initial=True),
        )

    def remove_device(self, device_id: str) -> None:
        """Handle device removal event."""
        LOGGER.debug("Remove device: %s", device_id)
        self.hass.add_job(self.async_remove_device, device_id)

    @callback
    def async_remove_device(self, device_id: str) -> None:
        """Remove device from Home Assistant."""
        device_registry = dr.async_get(self.hass)
        device_entry = device_registry.async_get_device_by_identifier(
            (DOMAIN, device_id), self._entry.entry_id
        )
        if device_entry is not None:
            device_registry.async_remove_device(device_entry.id)

    def start_polling(self) -> None:
        """Start periodic polling to sync device status with Tuya Cloud."""
        async def _async_poll(_now: Any = None) -> None:
            await self.hass.async_add_executor_job(self._poll_devices)

        self._entry.async_on_unload(
            async_track_time_interval(self.hass, _async_poll, timedelta(seconds=20))
        )

    def _poll_devices(self) -> None:
        """Query devices status from Tuya Cloud and dispatch updates if changed."""
        try:
            for home in self.manager.user_homes:
                resp = self.manager.customer_api.get(
                    "/v1.0/m/life/ha/home/devices", {"homeId": home.id}
                )
                if not resp or not resp.get("success"):
                    continue
                for item in resp.get("result", []):
                    dev_id = item.get("id")
                    device = self.manager.device_map.get(dev_id)
                    if not device:
                        continue
                    updated_properties: list[str] = []
                    for s in item.get("status", []):
                        code = s.get("code")
                        val = s.get("value")
                        if code is not None and device.status.get(code) != val:
                            device.status[code] = val
                            updated_properties.append(code)
                    if updated_properties:
                        LOGGER.debug(
                            "Polled status update for %s (%s): %s",
                            device.name,
                            dev_id,
                            updated_properties,
                        )
                        dispatcher_send(
                            self.hass,
                            f"{TUYA_HA_SIGNAL_UPDATE_ENTITY}_{dev_id}",
                            updated_properties,
                            None,
                        )
        except Exception as exc:
            LOGGER.debug("Error while polling Tuya devices: %s", exc)


class _TokenListener(SharingTokenListener):
    """Token listener for upstream token updates."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: TuyaConfigEntry,
    ) -> None:
        """Init TokenListener."""
        self.hass = hass
        self.entry = entry

    def update_token(self, token_info: dict[str, Any]) -> None:
        """Update token info in config entry."""
        data = {
            **self.entry.data,
            CONF_TOKEN_INFO: {
                "t": token_info["t"],
                "uid": token_info["uid"],
                "expire_time": token_info["expire_time"],
                "access_token": token_info["access_token"],
                "refresh_token": token_info["refresh_token"],
            },
        }

        @callback
        def async_update_entry() -> None:
            """Update config entry."""
            self.hass.config_entries.async_update_entry(self.entry, data=data)

        self.hass.add_job(async_update_entry)
