"""Expose Control4 Advanced Lighting scenes in Home Assistant."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.components.scene import Scene
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import Control4Runtime
from .entity import Control4Entity
from .transport.commands import UnsupportedCommand
from .transport.lighting_scenes import ADVANCED_LIGHTING_PROXY, LightingScene
from .transport.rest import DirectorRestError


_LOGGER = logging.getLogger(__name__)
PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    runtime: Control4Runtime = entry.runtime_data
    agents = [
        (item_id, item)
        for item_id, item in runtime.transport.inventory.items.items()
        if item.get("typeName") == "agent"
        and item.get("proxy") == ADVANCED_LIGHTING_PROXY
    ]
    if len(agents) != 1:
        return

    agent_id, agent = agents[0]
    try:
        async with asyncio.timeout(10):
            await runtime.commands.refresh((agent_id,))
            if not runtime.commands.supports_lighting_scenes(agent_id):
                return
            scenes = await runtime.transport.rest.get_lighting_scenes()
    except (DirectorRestError, TimeoutError) as exc:
        _LOGGER.warning("Control4 lighting scene discovery failed: %s", type(exc).__name__)
        return

    runtime.commands.register_lighting_scenes(agent_id, scenes)
    async_add_entities(
        [Control4LightingScene(runtime, agent, scene) for scene in scenes]
    )


class Control4LightingScene(Control4Entity, Scene):
    """Activate an existing scene on the Advanced Lighting agent."""

    def __init__(
        self, runtime: Control4Runtime, agent: dict[str, Any], scene: LightingScene,
    ) -> None:
        super().__init__(runtime, agent)
        self._scene_id = scene.scene_id
        self._attr_unique_id = f"{self._attr_unique_id}_lighting_scene_{scene.scene_id}"
        self._attr_name = scene.name

    @property
    def available(self) -> bool:
        return self.runtime.transport.connected

    async def async_activate(self, **kwargs: Any) -> None:
        if not self.available:
            raise HomeAssistantError("Control4 Director is not connected")
        try:
            await self.runtime.commands.activate_lighting_scene(
                self.device_id, self._scene_id
            )
        except (DirectorRestError, UnsupportedCommand) as exc:
            raise HomeAssistantError("Control4 could not activate the lighting scene") from exc
