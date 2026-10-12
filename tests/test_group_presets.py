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
        "media": None,
    }


def media(title="Klassik Radio"):
    return {
        "content_type": "channel",
        "content_id": "control4_advanced:tunein:22:station:92:Station/s25028",
        "title": title,
    }


class GroupPresetStorageTests(unittest.IsolatedAsyncioTestCase):
    async def test_load_keeps_only_valid_unique_presets(self):
        valid = preset()
        legacy = deepcopy(valid)
        legacy.pop("media")
        duplicate_name = preset("other", "whole house")
        invalid = {**preset("invalid", "Invalid"), "leader_room_id": 999}
        storage = GroupPresetStorage(
            FakeStore({"presets": [legacy, duplicate_name, invalid, "bad"]})
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
            media=media(),
        )
        updated_members = [
            member("media_player.kitchen_new", 22, 0.4, True),
        ]
        updated = await storage.async_save_preset(
            name=" whole house ",
            leader_entity_id="media_player.kitchen_new",
            leader_room_id=22,
            members=updated_members,
            media=media("Rock Classics"),
        )

        self.assertEqual(updated["id"], created["id"])
        self.assertEqual(len(storage.presets), 1)
        self.assertEqual(storage.presets[0]["members"], updated_members)
        self.assertEqual(storage.presets[0]["media"], media("Rock Classics"))
        self.assertEqual(store.saved[-1], {"presets": storage.presets})

    async def test_update_by_id_can_rename_and_preserves_order(self):
        store = FakeStore({"presets": [preset("first", "First"), preset("second", "Second")]})
        storage = GroupPresetStorage(store)
        await storage.async_load()

        updated = await storage.async_save_preset(
            preset_id="first",
            name="Renamed",
            leader_entity_id="media_player.kitchen",
            leader_room_id=22,
            members=preset()["members"],
            media=media(),
        )
        self.assertEqual(updated["id"], "first")
        self.assertEqual([item["id"] for item in storage.presets], ["first", "second"])
        self.assertEqual(storage.presets[0]["name"], "Renamed")

        reordered = await storage.async_reorder_presets(["second", "first"])
        self.assertEqual([item["id"] for item in reordered], ["second", "first"])
        self.assertEqual(store.saved[-1], {"presets": reordered})
        with self.assertRaisesRegex(ValueError, "every preset exactly once"):
            await storage.async_reorder_presets(["first", "first"])

    async def test_invalid_or_conflicting_update_is_rejected(self):
        storage = GroupPresetStorage(
            FakeStore({"presets": [preset("first", "First"), preset("second", "Second")]})
        )
        await storage.async_load()

        with self.assertRaisesRegex(ValueError, "already exists"):
            await storage.async_save_preset(
                preset_id="first",
                name="Second",
                leader_entity_id="media_player.kitchen",
                leader_room_id=22,
                members=preset()["members"],
            )
        with self.assertRaisesRegex(ValueError, "not found"):
            await storage.async_save_preset(
                preset_id="missing",
                name="Missing",
                leader_entity_id="media_player.kitchen",
                leader_room_id=22,
                members=preset()["members"],
            )

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
        with self.assertRaisesRegex(ValueError, "Invalid group preset"):
            await storage.async_save_preset(
                name="Bad media",
                leader_entity_id="media_player.kitchen",
                leader_room_id=22,
                members=[member("media_player.kitchen", 22)],
                media={"content_type": "channel", "content_id": "missing title"},
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

        self.assertEqual(len(registered), 4)
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
                "media": media(),
            },
        )
        saved_id = connection.results[-1][1]["preset"]["id"]
        await handlers["control4_advanced/group_presets/list"](
            hass, connection, {"id": 2}
        )
        self.assertEqual(
            connection.results[-1][1]["presets"][0]["name"], "Downstairs"
        )
        await handlers["control4_advanced/group_presets/reorder"](
            hass, connection, {"id": 3, "preset_ids": [saved_id]}
        )
        self.assertEqual(
            connection.results[-1][1]["presets"][0]["media"], media()
        )
        await handlers["control4_advanced/group_presets/delete"](
            hass, connection, {"id": 4, "preset_id": saved_id}
        )
        self.assertEqual(connection.results[-1], (4, {"deleted": saved_id}))
        self.assertEqual(connection.errors, [])


if __name__ == "__main__":
    unittest.main()
