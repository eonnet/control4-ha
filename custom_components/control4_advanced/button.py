"""Explicit room playback actions without inferred playback state."""

from __future__ import annotations

from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import Control4Runtime
from .entity import Control4Entity
from .transport.commands import UnsupportedCommand
from .transport.events import normalize_rest_variables


ROOM_PLAYBACK_BUTTONS = (
    ("PLAY", "Play", "mdi:play"),
    ("PAUSE", "Pause", "mdi:pause"),
    ("STOP", "Stop", "mdi:stop"),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    runtime: Control4Runtime = entry.runtime_data
    items = runtime.transport.inventory.items
    async_add_entities([
        Control4RoomPlaybackButton(runtime, items[room_id], command, label, icon)
        for room_id in sorted(runtime.media_coordinators)
        if room_id in items
        and items[room_id].get("typeName") == "room"
        and items[room_id].get("proxy") == "roomdevice"
        for command, label, icon in ROOM_PLAYBACK_BUTTONS
        if runtime.commands.supports_room_transport(room_id, command)
    ])


class Control4RoomPlaybackButton(Control4Entity, ButtonEntity):
    """Send one advertised command; never infer playing/paused from its response."""

    _attr_has_entity_name = True

    def __init__(
        self, runtime: Control4Runtime, item: dict[str, Any],
        command: str, label: str, icon: str,
    ) -> None:
        super().__init__(runtime, item)
        self.command = command
        self._attr_unique_id = f"{runtime.transport.rest.host}_{self.device_id}_media_{command.lower()}"
        self._attr_name = label
        self._attr_icon = icon

    @property
    def available(self) -> bool:
        return (
            super().available
            and self.state_data.get("is_on") is True
            and self.runtime.commands.supports_room_transport(self.device_id, self.command)
        )

    async def async_press(self) -> None:
        if not self.available:
            raise UnsupportedCommand("room playback command is unavailable while the room is off")
        # The room may have powered off since its last reconciled snapshot.
        # A failed or inconclusive read must not send a transport command.
        variables = await self.runtime.transport.rest.get_variables(self.device_id)
        live_state = normalize_rest_variables(self.device_id, "roomdevice", variables).changes
        if live_state.get("is_on") is not True or not self.runtime.transport.connected:
            raise UnsupportedCommand("room playback command requires a live powered-on room")
        await self.runtime.commands.room_transport(self.device_id, self.command)
