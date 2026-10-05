"""Support for Tuya Smart devices."""

import logging

from tuya_sharing import Manager

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_ENDPOINT,
    CONF_TERMINAL_ID,
    CONF_TOKEN_INFO,
    CONF_USER_CODE,
    DOMAIN,
    LOGGER,
    PLATFORMS,
    TUYA_CLIENT_ID,
)
from .coordinator import DeviceListener, TuyaConfigEntry
from .services import async_setup_services

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


class TuyaStreamView(HomeAssistantView):
    """View to provide dynamic RTSP stream URLs for Frigate and other NVRs."""

    url = "/api/tuya_stream/{device_or_name}"
    name = "api:tuya_stream"
    requires_auth = False

    def __init__(self, hass: HomeAssistant, manager: Manager):
        self.hass = hass
        self.manager = manager
        self.camera_map = {
            "phong_khach": "a312a25701d2944a5a1tmp",
            "camera_phong_khach": "a312a25701d2944a5a1tmp",
            "san_truoc": "a387213482be45bf5chn74",
            "camera_san": "a387213482be45bf5chn74",
            "camera_san_truoc": "a387213482be45bf5chn74",
            "san_sau": "a39926b17cbe580c63gsvs",
            "camera_san_sau": "a39926b17cbe580c63gsvs",
            "cong": "a3abd5b3ae7b66252bz0db",
            "camera_cong": "a3abd5b3ae7b66252bz0db",
            "bep": "a37456aa70d00b5e6abn2e",
            "camera_bep": "a37456aa70d00b5e6abn2e",
            "ban_cong": "a3f0486d0c47d41965sil7",
            "camera_ban_cong": "a3f0486d0c47d41965sil7",
        }

    async def get(self, request: web.Request, device_or_name: str) -> web.Response:
        dev_id = self.camera_map.get(device_or_name, device_or_name)
        try:
            url = await self.hass.async_add_executor_job(
                self.manager.get_device_stream_allocate,
                dev_id,
                "rtsp",
            )
            if url:
                return web.Response(text=url, content_type="text/plain")
            return web.Response(text="Error: Could not allocate stream", status=404)
        except Exception as e:
            LOGGER.error("Error allocating Tuya stream for %s: %s", device_or_name, e)
            return web.Response(text=f"Error: {e}", status=500)

# Suppress logs from the library, it logs unneeded on error
# logging.getLogger("tuya_sharing").setLevel(logging.CRITICAL)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Tuya Services."""
    await async_setup_services(hass)

    return True


async def async_setup_entry(hass: HomeAssistant, entry: TuyaConfigEntry) -> bool:
    """Async setup hass config entry."""
    listener = DeviceListener(hass, entry)
    await hass.async_add_executor_job(listener.initialize)

    # Connection is successful, store the listener in runtime_data
    entry.runtime_data = listener
    manager = listener.manager

    # Register dynamic stream API for Frigate / NVR
    hass.http.register_view(TuyaStreamView(hass, manager))

    # Cleanup device registry
    await cleanup_device_registry(hass, manager, entry)

    # Register known device IDs
    device_registry = dr.async_get(hass)
    for device in manager.device_map.values():
        LOGGER.debug(
            "Register device %s (online: %s): %s (function: %s, status range: %s)",
            device.id,
            device.online,
            device.status,
            device.function,
            device.status_range,
        )
        # Register quirk, and add device to the device registry
        listener.async_register_device(device_registry, device)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # If the device does not register any entities,
    # the device does not need to subscribe
    # So the subscription is here
    await hass.async_add_executor_job(manager.refresh_mq)
    listener.start_polling()
    return True


async def cleanup_device_registry(
    hass: HomeAssistant, device_manager: Manager, entry: TuyaConfigEntry
) -> None:
    """Unlink device registry entry if there are no remaining entities."""
    device_registry = dr.async_get(hass)
    for device_entry in dr.async_entries_for_config_entry(
        device_registry, entry.entry_id
    ):
        for item in device_entry.identifiers:
            if item[0] == DOMAIN and item[1] not in device_manager.device_map:
                device_registry.async_remove_device(device_entry.id)
                break


async def async_unload_entry(hass: HomeAssistant, entry: TuyaConfigEntry) -> bool:
    """Unloading the Tuya platforms."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        listener = entry.runtime_data
        manager = listener.manager
        if manager.mq is not None:
            manager.mq.stop()
        manager.remove_device_listener(listener)
    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: TuyaConfigEntry) -> None:
    """Remove a config entry.

    This will revoke the credentials from Tuya.
    """
    manager = Manager(
        TUYA_CLIENT_ID,
        entry.data[CONF_USER_CODE],
        entry.data[CONF_TERMINAL_ID],
        entry.data[CONF_ENDPOINT],
        entry.data[CONF_TOKEN_INFO],
    )
    await hass.async_add_executor_job(manager.unload)
