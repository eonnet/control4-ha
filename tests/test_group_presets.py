"""Tests for persistent Control4 media-group presets."""

from __future__ import annotations

from copy import deepcopy
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

from custom_components.control4_advanced.group_presets import (
    GroupPresetStorage,
    async_register_group_presets,
)


class FakeStore:
    def __init__(self, data=None):
        self.data = deepcopy(data)
        self.saved = []

    async def async_load(self):
        return deepcopy(self.data)

    async def async_save(self, data):
        self.data = deepcopy(data)
        self.saved.append(deepcopy(data))


def member(
    entity_id: str,
    room_id: int,
    volume_level: float | None = 0.25,
    is_volume_muted: bool | None = False,
):
    return {
        "entity_id": entity_id,
        "room_id": room_id,
        "volume_level": volume_level,
        "is_volume_muted": is_volume_muted,
    }


def preset(preset_id="whole", name="Whole House"):
    return {
        "id": preset_id,
        "name": name,
        "leader_entity_id": "media_player.kitchen",
        "leader_room_id": 22,
        "members": [
            member("media_player.kitchen", 22, 0.2),
            member("media_player.living", 23, 0.35, True),
        ],
    }


class GroupPresetStorageTests(unittest.IsolatedAsyncioTestCase):
    async def test_load_keeps_only_valid_unique_presets(self):
        valid = preset()
        duplicate_name = preset("other", "whole house")
        invalid = {**preset("invalid", "Invalid"), "leader_room_id": 999}
        storage = GroupPresetStorage(
            FakeStore({"presets": [valid, duplicate_name, invalid, "bad"]})
        )

        await storage.async_load()

        self.assertEqual(storage.presets, [valid])

    async def test_save_creates_and_same_name_replaces_preset(self):
        store = FakeStore()
        storage = GroupPresetStorage(store)
        await storage.async_load()

        created = await storage.async_save_preset(
            name="Whole House",
            leader_entity_id="media_player.kitchen",
            leader_room_id=22,
            members=preset()["members"],
        )
        updated_members = [
            member("media_player.kitchen_new", 22, 0.4, True),
        ]
        updated = await storage.async_save_preset(
            name=" whole house ",
            leader_entity_id="media_player.kitchen_new",
            leader_room_id=22,
            members=updated_members,
        )

        self.assertEqual(updated["id"], created["id"])
        self.assertEqual(len(storage.presets), 1)
        self.assertEqual(storage.presets[0]["members"], updated_members)
        self.assertEqual(store.saved[-1], {"presets": storage.presets})

    async def test_presets_property_is_detached_and_delete_is_persistent(self):
        store = FakeStore({"presets": [preset()]})
        storage = GroupPresetStorage(store)
        await storage.async_load()
        detached = storage.presets
        detached[0]["name"] = "Changed outside"

        self.assertEqual(storage.presets[0]["name"], "Whole House")
        self.assertFalse(await storage.async_delete_preset("missing"))
        self.assertTrue(await storage.async_delete_preset("whole"))
        self.assertEqual(storage.presets, [])
        self.assertEqual(store.saved[-1], {"presets": []})

    async def test_invalid_member_data_is_rejected(self):
        storage = GroupPresetStorage(FakeStore())
        await storage.async_load()

        with self.assertRaisesRegex(ValueError, "Invalid group preset"):
            await storage.async_save_preset(
                name="Bad",
                leader_entity_id="media_player.kitchen",
                leader_room_id=22,
                members=[member("media_player.kitchen", 22, 1.5)],
            )

    async def test_websocket_commands_register_once_and_round_trip(self):
        voluptuous = ModuleType("voluptuous")
        voluptuous.Required = lambda key: key
        voluptuous.Optional = lambda key: key
        voluptuous.All = lambda *validators: validators
        voluptuous.Any = lambda *validators: validators
        voluptuous.Coerce = lambda value_type: value_type
        voluptuous.Range = lambda **limits: limits
        voluptuous.Length = lambda **limits: limits

        registered = []
        websocket_api = ModuleType("homeassistant.components.websocket_api")
        websocket_api.ERR_INVALID_FORMAT = "invalid_format"
        websocket_api.ERR_NOT_FOUND = "not_found"

        def websocket_command(schema):
            def decorate(handler):
                handler._ws_command = schema["type"]
                return handler

            return decorate

        websocket_api.websocket_command = websocket_command
        websocket_api.async_response = lambda handler: handler
        websocket_api.async_register_command = (
            lambda hass, handler: registered.append(handler)
        )

        components = ModuleType("homeassistant.components")
        components.__path__ = []
        components.websocket_api = websocket_api
        helpers = ModuleType("homeassistant.helpers")
        helpers.__path__ = []
        config_validation = ModuleType("homeassistant.helpers.config_validation")
        config_validation.entity_id = str
        config_validation.string = str
        storage_module = ModuleType("homeassistant.helpers.storage")

        class HomeAssistantStore(FakeStore):
            def __init__(self, hass, version, key):
                super().__init__()

        storage_module.Store = HomeAssistantStore
        homeassistant = ModuleType("homeassistant")
        homeassistant.__path__ = []
        hass = SimpleNamespace(data={})

        class Connection:
            def __init__(self):
                self.results = []
                self.errors = []

            def send_result(self, message_id, result):
                self.results.append((message_id, result))

            def send_error(self, message_id, code, message):
                self.errors.append((message_id, code, message))

        with patch.dict(
            sys.modules,
            {
                "voluptuous": voluptuous,
                "homeassistant": homeassistant,
                "homeassistant.components": components,
                "homeassistant.components.websocket_api": websocket_api,
                "homeassistant.helpers": helpers,
                "homeassistant.helpers.config_validation": config_validation,
                "homeassistant.helpers.storage": storage_module,
            },
        ):
            manager = await async_register_group_presets(hass)
            self.assertIs(await async_register_group_presets(hass), manager)

        self.assertEqual(len(registered), 3)
        handlers = {handler._ws_command: handler for handler in registered}
        connection = Connection()
        await handlers["control4_advanced/group_presets/save"](
            hass,
            connection,
            {
                "id": 1,
                "name": "Downstairs",
                "leader_entity_id": "media_player.kitchen",
                "leader_room_id": 22,
                "members": [member("media_player.kitchen", 22)],
            },
        )
        saved_id = connection.results[-1][1]["preset"]["id"]
        await handlers["control4_advanced/group_presets/list"](
            hass, connection, {"id": 2}
        )
        self.assertEqual(
            connection.results[-1][1]["presets"][0]["name"], "Downstairs"
        )
        await handlers["control4_advanced/group_presets/delete"](
            hass, connection, {"id": 3, "preset_id": saved_id}
        )
        self.assertEqual(connection.results[-1], (3, {"deleted": saved_id}))
        self.assertEqual(connection.errors, [])


if __name__ == "__main__":
    unittest.main()
