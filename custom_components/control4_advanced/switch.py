"""Push-driven radiant-floor relay switches, disabled until explicitly enabled in HA."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import Control4Runtime
from .entity import Control4Entity
from .selection import supported_relay_ids


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    runtime: Control4Runtime = entry.runtime_data
    items = runtime.transport.inventory.items
    async_add_entities([
        Control4RadiantFloorSwitch(runtime, items[device_id])
        for device_id in supported_relay_ids(
            items,
            runtime.transport.tracked_ids,
            runtime.commands.supports_parameterless,
            lambda item_id: isinstance(
                runtime.transport.state.snapshot(item_id).get("is_on"), bool
            ),
        )
    ])


class Control4RadiantFloorSwitch(Control4Entity, SwitchEntity):
    """Use verified relay feedback; never optimistically flip HA state."""

    # This proxy controls heating loads. Let the user enable each entity deliberately.
    _attr_entity_registry_enabled_default = False

    @property
    def is_on(self) -> bool | None:
        return self.state_data.get("is_on")

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.runtime.commands.relay_close(self.device_id)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.runtime.commands.relay_open(self.device_id)
