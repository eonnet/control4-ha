"""Independent Control4 room-volume sliders for grouped playback."""

from __future__ import annotations

import math
from typing import Any

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import Control4Runtime
from .entity import Control4Entity
from .transport.commands import UnsupportedCommand


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add one independent volume slider for each supported media room."""
    runtime: Control4Runtime = entry.runtime_data
    items = runtime.transport.inventory.items
    async_add_entities([
        Control4RoomVolumeNumber(runtime, items[room_id])
        for room_id in sorted(runtime.media_coordinators)
        if room_id in items
        and items[room_id].get("typeName") == "room"
        and items[room_id].get("proxy") == "roomdevice"
        and runtime.commands.supports_room_volume(room_id)
    ])


class Control4RoomVolumeNumber(Control4Entity, NumberEntity):
    """Set one room's volume without changing the other group members."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:volume-high"
    _attr_mode = NumberMode.SLIDER
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(self, runtime: Control4Runtime, item: dict[str, Any]) -> None:
        super().__init__(runtime, item)
        self._attr_unique_id = (
            f"{runtime.transport.rest.host}_{self.device_id}_room_volume"
        )
        self._attr_name = "Volume"

    def _volume_ready(self) -> bool:
        state = self.state_data
        bound_id = state.get("volume_device_id")
        bound = (
            self.runtime.transport.inventory.items.get(bound_id)
            if type(bound_id) is int and bound_id > 0
            else None
        )
        volume = state.get("volume_percent")
        return (
            state.get("is_on") is True
            and bound is not None
            and bound.get("proxy") == "aswitch"
            and type(volume) in (int, float)
            and 0 <= volume <= 100
        )

    @property
    def native_value(self) -> float | None:
        """Return the independently reported Director room volume."""
        value = self.state_data.get("volume_percent")
        return (
            float(value)
            if type(value) in (int, float) and 0 <= value <= 100
            else None
        )

    @property
    def available(self) -> bool:
        return (
            super().available
            and self._volume_ready()
            and self.runtime.commands.supports_room_volume(self.device_id)
        )

    async def async_set_native_value(self, value: float) -> None:
        """Set only this room's volume and wait for Director feedback."""
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or not 0 <= value <= 100
        ):
            raise UnsupportedCommand("room volume must be between 0 and 100")
        if not self.available:
            raise UnsupportedCommand("room volume is unavailable")
        await self.runtime.commands.room_volume(
            self.device_id, math.floor(value + 0.5)
        )
