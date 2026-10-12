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
    ("SKIP_REV", "Previous", "mdi:skip-previous"),
    ("PLAY", "Play", "mdi:play"),
    ("PAUSE", "Pause", "mdi:pause"),
    ("STOP", "Stop", "mdi:stop"),
    ("SKIP_FWD", "Next", "mdi:skip-next"),
)
THERMOSTAT_HUMIDITY_BUTTONS = (
    ("HUMIDIFY", False, "Humidifier −1%", "mdi:minus-circle-outline"),
    ("HUMIDIFY", True, "Humidifier +1%", "mdi:plus-circle-outline"),
    ("DEHUMIDIFY", False, "Dehumidifier −1%", "mdi:minus-circle-outline"),
    ("DEHUMIDIFY", True, "Dehumidifier +1%", "mdi:plus-circle-outline"),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    runtime: Control4Runtime = entry.runtime_data
    items = runtime.transport.inventory.items
    entities: list[ButtonEntity] = [
        Control4RoomPlaybackButton(runtime, items[room_id], command, label, icon)
        for room_id in sorted(runtime.media_coordinators)
        if room_id in items
        and items[room_id].get("typeName") == "room"
        and items[room_id].get("proxy") == "roomdevice"
        for command, label, icon in ROOM_PLAYBACK_BUTTONS
        if runtime.commands.supports_room_transport(room_id, command)
    ]
    entities.extend(
        Control4HumidityStepButton(
            runtime, items[device_id], kind, increase, label, icon
        )
        for device_id in sorted(runtime.transport.tracked_ids)
        if device_id in items and items[device_id].get("proxy") == "thermostatV2"
        for kind, increase, label, icon in THERMOSTAT_HUMIDITY_BUTTONS
        if runtime.commands.supports_parameterless(
            device_id, f"{'INC' if increase else 'DEC'}_SETPOINT_{kind}"
        )
    )
    async_add_entities(entities)


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


class Control4HumidityStepButton(Control4Entity, ButtonEntity):
    """Move one thermostat humidity setpoint by Director's one-percent step."""

    _attr_has_entity_name = True

    def __init__(
        self,
        runtime: Control4Runtime,
        item: dict[str, Any],
        kind: str,
        increase: bool,
        label: str,
        icon: str,
    ) -> None:
        super().__init__(runtime, item)
        self.kind = kind
        self.increase = increase
        direction = "increase" if increase else "decrease"
        self.command = f"{'INC' if increase else 'DEC'}_SETPOINT_{kind}"
        self._attr_unique_id = (
            f"{runtime.transport.rest.host}_{self.device_id}_"
            f"{kind.lower()}_{direction}"
        )
        self._attr_name = label
        self._attr_icon = icon

    @property
    def available(self) -> bool:
        if not super().available or not self.runtime.commands.supports_parameterless(
            self.device_id, self.command
        ):
            return False
        key = (
            "humidify_setpoint"
            if self.kind == "HUMIDIFY"
            else "dehumidify_setpoint"
        )
        value = self.state_data.get(key)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return False
        return value < 100 if self.increase else value > 0

    @property
    def extra_state_attributes(self) -> dict[str, float]:
        key = (
            "humidify_setpoint"
            if self.kind == "HUMIDIFY"
            else "dehumidify_setpoint"
        )
        value = self.state_data.get(key)
        return {"current_setpoint": value} if isinstance(value, (int, float)) else {}

    async def async_press(self) -> None:
        if not self.available:
            raise UnsupportedCommand("humidity setpoint adjustment is unavailable")
        await self.runtime.commands.humidity_step(
            self.device_id, self.kind, increase=self.increase
        )
        await self.runtime.transport.sync_device(self.device_id)
