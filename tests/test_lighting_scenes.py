"""Scene discovery, Home Assistant setup, and activation without a live write."""

from __future__ import annotations

import importlib
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from custom_components.control4_advanced.transport.commands import (
    DeviceCommandClient,
    UnsupportedCommand,
)
from custom_components.control4_advanced.transport.lighting_scenes import (
    LIGHTING_SCENES_PATH,
    LightingScene,
)
from custom_components.control4_advanced.transport.rest import DirectorRestClient


def _scene_test_modules():
    names = (
        "homeassistant", "homeassistant.components", "homeassistant.components.scene",
        "homeassistant.config_entries", "homeassistant.core", "homeassistant.exceptions",
        "homeassistant.helpers", "homeassistant.helpers.device_registry",
        "homeassistant.helpers.entity", "homeassistant.helpers.entity_platform",
    )
    modules = {name: ModuleType(name) for name in names}
    for name in ("homeassistant", "homeassistant.components", "homeassistant.helpers"):
        modules[name].__path__ = []
    modules["homeassistant.components.scene"].Scene = type("Scene", (), {})
    modules["homeassistant.config_entries"].ConfigEntry = object
    modules["homeassistant.core"].HomeAssistant = object
    modules["homeassistant.exceptions"].HomeAssistantError = type(
        "HomeAssistantError", (Exception,), {}
    )
    modules["homeassistant.helpers.device_registry"].DeviceInfo = dict
    modules["homeassistant.helpers.entity"].Entity = type("Entity", (), {})
    modules["homeassistant.helpers.entity_platform"].AddConfigEntryEntitiesCallback = object
    return modules


class LightingSceneTests(unittest.IsolatedAsyncioTestCase):
    async def test_setup_imports_scenes_and_activates_only_catalog_ids(self):
        agent = {
            "id": 23, "name": "Advanced Lighting", "typeName": "agent",
            "proxy": "control4_agent_adv_lighting",
        }
        metadata = [{
            "command": "ACTIVATE_SCENE",
            "deviceId": 23,
            "params": [{
                "name": "SCENE_ID",
                "type": "LIST",
                "valueType": "INTEGER",
                "valueSrc": {"method": "GET", "path": LIGHTING_SCENES_PATH},
                "valueField": "scene_id",
            }],
        }]
        catalog = [
            {"scene_id": 0, "name": "Full lighting"},
            {"scene_id": 7, "name": "  Evening  "},
            {"scene_id": 7, "name": "Evening"},
            {"scene_id": 8, "name": "Ambiguous"},
            {"scene_id": 8, "name": "Conflicting name"},
            {"scene_id": True, "name": "Invalid selector"},
            {"scene_id": 9, "name": ""},
        ]
        rest = DirectorRestClient("example.test", None, None)
        commands = DeviceCommandClient(rest)
        transport = SimpleNamespace(
            inventory=SimpleNamespace(items={23: agent}), rest=rest, connected=True,
        )
        entry = SimpleNamespace(
            runtime_data=SimpleNamespace(transport=transport, commands=commands)
        )
        entities = []

        with (
            patch.dict(sys.modules, _scene_test_modules()),
            patch.object(rest, "get_commands", AsyncMock(return_value=metadata)) as get_commands,
            patch.object(rest, "request_json", AsyncMock(return_value=catalog)) as request,
            patch.object(rest, "post_json", AsyncMock()) as post,
        ):
            try:
                scene = importlib.import_module("custom_components.control4_advanced.scene")
                await scene.async_setup_entry(None, entry, entities.extend)
                get_commands.assert_awaited_once_with(23)
                request.assert_awaited_once_with("GET", LIGHTING_SCENES_PATH)
                self.assertEqual([e._attr_name for e in entities], ["Full lighting", "Evening"])
                self.assertEqual([e._attr_unique_id for e in entities], [
                    "example.test_23_lighting_scene_0",
                    "example.test_23_lighting_scene_7",
                ])
                self.assertTrue(all(e.available for e in entities))
                post.assert_not_awaited()

                await entities[0].async_activate()
                post.assert_awaited_once_with(
                    "/api/v1/items/23/commands",
                    {"command": "ACTIVATE_SCENE", "params": {"SCENE_ID": 0}},
                )
                for invalid in (8, 999, True, "7"):
                    with self.assertRaises(UnsupportedCommand):
                        await commands.activate_lighting_scene(23, invalid)
                transport.connected = False
                with self.assertRaises(scene.HomeAssistantError):
                    await entities[1].async_activate()
                self.assertEqual(post.await_count, 1)
            finally:
                sys.modules.pop("custom_components.control4_advanced.scene", None)
                sys.modules.pop("custom_components.control4_advanced.entity", None)

    async def test_catalog_ids_cannot_bypass_mismatched_command_metadata(self):
        rest = SimpleNamespace(
            get_commands=AsyncMock(return_value=[{
                "command": "ACTIVATE_SCENE", "deviceId": 23,
                "params": [{
                    "name": "SCENE_ID", "type": "LIST", "valueType": "INTEGER",
                    "valueSrc": {"method": "GET", "path": "/api/v1/experiences"},
                    "valueField": "scene_id",
                }],
            }]),
            post_json=AsyncMock(),
        )
        commands = DeviceCommandClient(rest)
        await commands.refresh([23])
        commands.register_lighting_scenes(23, [LightingScene(7, "Evening")])
        with self.assertRaises(UnsupportedCommand):
            await commands.activate_lighting_scene(23, 7)
        rest.post_json.assert_not_awaited()
