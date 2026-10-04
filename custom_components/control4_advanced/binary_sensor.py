"""Push-driven window and motion sensors with verified Control4 feedback."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import Control4Runtime
from .entity import Control4Entity
from .selection import supported_binary_sensor_ids
from .transport.proxies import WINDOW_CONTACT_PROXY


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    runtime: Control4Runtime = entry.runtime_data
    items = runtime.transport.inventory.items
    async_add_entities([
        Control4ContactBinarySensor(runtime, items[device_id])
        for device_id in supported_binary_sensor_ids(items, runtime.transport.tracked_ids)
    ])


class Control4ContactBinarySensor(Control4Entity, BinarySensorEntity):
    """Use initial REST state, then only settled WebSocket contact feedback."""

    @property
    def device_class(self) -> BinarySensorDeviceClass:
        if self.item.get("proxy") == WINDOW_CONTACT_PROXY:
            return BinarySensorDeviceClass.WINDOW
        return BinarySensorDeviceClass.MOTION

    @property
    def is_on(self) -> bool | None:
        value = self.state_data.get("is_on")
        return value if isinstance(value, bool) else None

    @property
    def available(self) -> bool:
        return super().available and self.is_on is not None
