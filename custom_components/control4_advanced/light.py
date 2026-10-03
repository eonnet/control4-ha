"""Push-driven Control4 light_v2 entities."""

from __future__ import annotations

from typing import Any

from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
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
        Control4Light(runtime, items[device_id])
        for device_id in sorted(runtime.transport.tracked_ids)
        if items[device_id].get("proxy") == "light_v2"
        and bool(runtime.transport.state.snapshot(device_id))
        and runtime.commands.supports(device_id, "ON")
        and runtime.commands.supports(device_id, "OFF")
    ])


class Control4Light(Control4Entity, LightEntity):
    """A simple light with only power and observed brightness support."""

    @property
    def _can_dim(self) -> bool:
        capabilities = self.item.get("capabilities") or {}
        return bool(capabilities.get("dimmer")) and self.runtime.commands.supports(
            self.device_id, "SET_LEVEL"
        )

    @property
    def supported_color_modes(self) -> set[ColorMode]:
        return {ColorMode.BRIGHTNESS if self._can_dim else ColorMode.ONOFF}

    @property
    def color_mode(self) -> ColorMode:
        return ColorMode.BRIGHTNESS if self._can_dim else ColorMode.ONOFF

    @property
    def is_on(self) -> bool | None:
        return self.state_data.get("is_on")

    @property
    def brightness(self) -> int | None:
        if not self._can_dim or not self.is_on:
            return None
        percent = self.state_data.get("brightness_percent")
        return max(1, min(255, round(percent * 255 / 100))) if percent is not None else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        requested = kwargs.get(ATTR_BRIGHTNESS)
        if requested is not None and self._can_dim:
            if requested == 0:
                await self.runtime.commands.light_off(self.device_id)
            else:
                await self.runtime.commands.light_level(
                    self.device_id, max(1, min(100, round(requested * 100 / 255)))
                )
        else:
            await self.runtime.commands.light_on(self.device_id)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.runtime.commands.light_off(self.device_id)
