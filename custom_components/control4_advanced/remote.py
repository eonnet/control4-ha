"""Control4 room virtual remote entities."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from typing import Any

from homeassistant.components.remote import (
    ATTR_DELAY_SECS,
    ATTR_HOLD_SECS,
    ATTR_NUM_REPEATS,
    DEFAULT_DELAY_SECS,
    DEFAULT_HOLD_SECS,
    DEFAULT_NUM_REPEATS,
    RemoteEntity,
    RemoteEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import Control4Runtime
from .entity import Control4Entity
from .transport.commands import UnsupportedCommand
from .transport.sources import RoomSource


_HOLD_COMMANDS = {
    "UP": ("START_UP", "STOP_UP"),
    "DOWN": ("START_DOWN", "STOP_DOWN"),
    "LEFT": ("START_LEFT", "STOP_LEFT"),
    "RIGHT": ("START_RIGHT", "STOP_RIGHT"),
    "PULSE_VOL_UP": ("START_VOL_UP", "STOP_VOL_UP"),
    "PULSE_VOL_DOWN": ("START_VOL_DOWN", "STOP_VOL_DOWN"),
    "PULSE_CH_UP": ("START_CH_UP", "STOP_CH_UP"),
    "PULSE_CH_DOWN": ("START_CH_DOWN", "STOP_CH_DOWN"),
    "SCAN_REV": ("START_SCAN_REV", "STOP_SCAN_REV"),
    "SCAN_FWD": ("START_SCAN_FWD", "STOP_SCAN_FWD"),
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add one virtual remote for every discovered Control4 media room."""
    runtime: Control4Runtime = entry.runtime_data
    items = runtime.transport.inventory.items
    async_add_entities([
        Control4RoomRemote(runtime, items[room_id])
        for room_id in sorted(runtime.media_coordinators)
        if runtime.commands.room_remote_commands(room_id)
    ])


class Control4RoomRemote(Control4Entity, RemoteEntity):
    """Route Home Assistant remote commands through a Control4 roomdevice."""

    def __init__(self, runtime: Control4Runtime, item: dict[str, Any]) -> None:
        super().__init__(runtime, item)
        self._attr_unique_id = (
            f"{runtime.transport.rest.host}_{self.device_id}_remote"
        )
        self._attr_name = f"{self._attr_name} Remote"

    @property
    def supported_features(self) -> RemoteEntityFeature:
        return (
            RemoteEntityFeature.ACTIVITY
            if self._watch_sources()
            else RemoteEntityFeature(0)
        )

    @property
    def is_on(self) -> bool | None:
        value = self.state_data.get("is_on")
        return value if isinstance(value, bool) else None

    @property
    def available(self) -> bool:
        # A room can be off or have no current state snapshot while its Watch
        # activities and virtual-remote commands remain usable. Activity
        # selection can power it on; individual key presses retain their live
        # powered-room guard in ``async_send_command``.
        return (
            self.runtime.transport.connected
            and bool(self.runtime.commands.room_remote_commands(self.device_id))
        )

    @property
    def activity_list(self) -> list[str] | None:
        sources = self._watch_sources()
        return [source.label for source in sources] if sources else None

    @property
    def current_activity(self) -> str | None:
        selected_id = self.state_data.get("selected_source_id")
        if type(selected_id) is not int or selected_id <= 0:
            return None
        selected = next(
            (
                source for source in self.runtime.media_sources.get(
                    self.device_id, ()
                )
                if source.device_id == selected_id
            ),
            None,
        )
        if selected is None:
            return None
        if selected.experience == "listen":
            playing_id = self.state_data.get("playing_audio_source_id")
            if type(playing_id) is int and playing_id > 0:
                selected = next(
                    (
                        source
                        for source in self.runtime.media_sources.get(
                            self.device_id, ()
                        )
                        if source.device_id == playing_id
                    ),
                    selected,
                )
        return selected.label

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "control4_room_id": self.device_id,
            "control4_commands": list(
                self.runtime.commands.room_remote_commands(self.device_id)
            ),
        }

    def _watch_sources(self) -> tuple[RoomSource, ...]:
        if not self.runtime.commands.supports_room_source(self.device_id, "watch"):
            return ()
        return tuple(
            source
            for source in self.runtime.media_sources.get(self.device_id, ())
            if source.experience == "watch"
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Select a validated Watch activity, powering the room as Control4 does."""
        activity = kwargs.get("activity")
        if not isinstance(activity, str) or not activity:
            if self.is_on:
                return
            raise UnsupportedCommand(
                "select a Control4 Watch activity to turn on this room"
            )
        source = next(
            (
                item for item in self._watch_sources()
                if item.label.casefold() == activity.casefold()
                or item.name.casefold() == activity.casefold()
            ),
            None,
        )
        if source is None:
            raise UnsupportedCommand("activity is not in this room's Watch catalog")
        await self.runtime.commands.room_source(self.device_id, source)
        for delay in (0, 0.25, 0.5, 1.0):
            if delay:
                await asyncio.sleep(delay)
            await self.runtime.transport.sync_room_source(self.device_id)
            if (
                self.is_on
                and self.state_data.get("selected_source_id") == source.device_id
            ):
                return

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the active Control4 room off."""
        del kwargs
        if not self.is_on or not self.runtime.commands.supports_room_off(
            self.device_id
        ):
            raise UnsupportedCommand("room off is unavailable for this room state")
        await self.runtime.commands.room_off(self.device_id)
        for delay in (0, 0.25, 0.5, 1.0):
            if delay:
                await asyncio.sleep(delay)
            await self.runtime.transport.sync_device(self.device_id)
            if self.is_on is False:
                return

    async def async_send_command(
        self, command: Iterable[str], **kwargs: Any
    ) -> None:
        """Send an ordered sequence through the active room's virtual remote."""
        if not self.is_on or not self.runtime.transport.connected:
            raise UnsupportedCommand(
                "Control4 remote commands require a connected, powered-on room"
            )
        repeats = kwargs.get(ATTR_NUM_REPEATS, DEFAULT_NUM_REPEATS)
        delay = kwargs.get(ATTR_DELAY_SECS, DEFAULT_DELAY_SECS)
        hold = kwargs.get(ATTR_HOLD_SECS, DEFAULT_HOLD_SECS)
        if type(repeats) is not int or repeats < 1:
            raise UnsupportedCommand("remote repeat count must be a positive integer")
        if isinstance(delay, bool) or not isinstance(delay, (int, float)) or delay < 0:
            raise UnsupportedCommand("remote delay must be a non-negative number")
        if isinstance(hold, bool) or not isinstance(hold, (int, float)) or hold < 0:
            raise UnsupportedCommand("remote hold must be a non-negative number")
        requested = (command,) if isinstance(command, str) else tuple(command)
        if not requested or not all(isinstance(item, str) for item in requested):
            raise UnsupportedCommand("remote command list must contain command names")
        for repeat_index in range(repeats):
            for index, single_command in enumerate(requested):
                name = single_command.strip().upper()
                hold_pair = _HOLD_COMMANDS.get(name) if hold > 0 else None
                if hold_pair and all(
                    self.runtime.commands.supports_room_remote(
                        self.device_id, hold_command
                    )
                    for hold_command in hold_pair
                ):
                    await self.runtime.commands.room_remote(
                        self.device_id, hold_pair[0]
                    )
                    await asyncio.sleep(hold)
                    await self.runtime.commands.room_remote(
                        self.device_id, hold_pair[1]
                    )
                else:
                    await self.runtime.commands.room_remote(self.device_id, name)
                if delay and (
                    index < len(requested) - 1 or repeat_index < repeats - 1
                ):
                    await asyncio.sleep(delay)
