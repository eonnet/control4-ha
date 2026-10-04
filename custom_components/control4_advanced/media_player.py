"""Room volume, mute, and validated source selection."""

from __future__ import annotations

import asyncio
import math

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
from .transport.commands import UnsupportedCommand
from .transport.sources import RoomSource


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
    """Expose only commands supported by this room's Director metadata."""

    @property
    def supported_features(self) -> MediaPlayerEntityFeature:
        features = MediaPlayerEntityFeature(0)
        if self._volume_ready() and self.runtime.commands.supports_room_volume(self.device_id):
            features |= MediaPlayerEntityFeature.VOLUME_SET
        if self._volume_ready() and self.runtime.commands.supports_room_mute(self.device_id):
            features |= MediaPlayerEntityFeature.VOLUME_MUTE
        if self._sources():
            features |= MediaPlayerEntityFeature.SELECT_SOURCE
        return features

    def _sources(self) -> tuple[RoomSource, ...]:
        discovered = self.runtime.media_sources.get(self.device_id, ())
        return tuple(
            source for source in discovered
            if self.runtime.commands.supports_room_source(self.device_id, source.experience)
        )

    def _volume_ready(self) -> bool:
        bound_id = self.state_data.get("volume_device_id")
        bound = (
            self.runtime.transport.inventory.items.get(bound_id)
            if type(bound_id) is int and bound_id > 0
            else None
        )
        volume = self.state_data.get("volume_percent")
        return (
            bound is not None
            and bound.get("proxy") == "aswitch"
            and type(volume) in (int, float)
            and 0 <= volume <= 100
            and isinstance(self.state_data.get("is_muted"), bool)
        )

    async def async_set_volume_level(self, volume: float) -> None:
        if not isinstance(volume, (int, float)) or isinstance(volume, bool):
            raise UnsupportedCommand("Home Assistant room volume must be numeric")
        if not math.isfinite(volume) or not 0 <= volume <= 1:
            raise UnsupportedCommand("Home Assistant room volume must be between 0 and 1")
        await self.runtime.commands.room_volume(self.device_id, math.floor(volume * 100 + 0.5))

    async def async_mute_volume(self, mute: bool) -> None:
        await self.runtime.commands.room_mute(self.device_id, mute)

    @property
    def source_list(self) -> list[str]:
        return [source.label for source in self._sources()]

    @property
    def source(self) -> str | None:
        selected_id = self.state_data.get("selected_source_id")
        if type(selected_id) is not int or selected_id <= 0:
            return None
        return next(
            (source.label for source in self._sources() if source.device_id == selected_id),
            None,
        )

    async def async_select_source(self, source: str) -> None:
        selected = next((item for item in self._sources() if item.label == source), None)
        if selected is None:
            raise UnsupportedCommand("source is not advertised for this room")
        await self.runtime.commands.room_source(self.device_id, selected)
        # A command response is not state feedback. Only a REST read may
        # confirm the selected source; retry briefly for Director's settle.
        for delay in (0, 0.25, 0.5, 1.0, 1.0):
            if delay:
                await asyncio.sleep(delay)
            await self.runtime.transport.sync_device(self.device_id)
            if self.state_data.get("selected_source_id") == selected.device_id:
                break

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
        return (
            super().available
            and self.state is not None
            and (self._volume_ready() or bool(self._sources()))
        )
