"""Read-only room media status from Director REST, refreshed by bound-device push."""

from __future__ import annotations

from homeassistant.components.media_player import (
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import Control4Runtime
from .entity import Control4Entity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    runtime: Control4Runtime = entry.runtime_data
    items = runtime.transport.inventory.items
    async_add_entities([
        Control4RoomMediaPlayer(runtime, items[room_id])
        for room_id in sorted(runtime.media_coordinators)
    ])


class Control4RoomMediaPlayer(Control4Entity, MediaPlayerEntity):
    """Report the room's power, volume, and mute state without media writes."""

    _attr_supported_features = MediaPlayerEntityFeature(0)

    @property
    def state(self) -> MediaPlayerState | None:
        powered = self.state_data.get("is_on")
        if isinstance(powered, bool):
            return MediaPlayerState.ON if powered else MediaPlayerState.OFF
        return None

    @property
    def volume_level(self) -> float | None:
        percent = self.state_data.get("volume_percent")
        if type(percent) in (int, float) and 0 <= percent <= 100:
            return percent / 100
        return None

    @property
    def is_volume_muted(self) -> bool | None:
        muted = self.state_data.get("is_muted")
        return muted if isinstance(muted, bool) else None

    @property
    def available(self) -> bool:
        bound_id = self.state_data.get("volume_device_id")
        bound = (
            self.runtime.transport.inventory.items.get(bound_id)
            if type(bound_id) is int and bound_id > 0
            else None
        )
        return (
            super().available
            and self.state is not None
            and self.volume_level is not None
            and self.is_volume_muted is not None
            and bound is not None
            and bound.get("proxy") == "aswitch"
        )
