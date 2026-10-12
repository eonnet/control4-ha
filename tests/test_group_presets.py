"""Tests for persistent Control4 media-group presets."""

from __future__ import annotations

from copy import deepcopy
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

from custom_components.control4_advanced.group_presets import (
    GroupPresetActivationError,
    GroupPresetStorage,
    SERVICE_ACTIVATE_GROUP_PRESET,
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


class FakeState:
    def __init__(
        self,
        entity_id: str,
        room_id: int,
        *,
        supported_features: int = 12,
        group_members: list[str] | None = None,
    ):
        self.entity_id = entity_id
        self.attributes = {
            "control4_room_id": room_id,
            "supported_features": supported_features,
            "group_members": group_members or [entity_id],
        }


class FakeStates:
    def __init__(self, states: list[FakeState]):
        self._states = {state.entity_id: state for state in states}

    def get(self, entity_id):
        return self._states.get(entity_id)

    def async_all(self, domain=None):
        states = list(self._states.values())
        if domain is None:
            return states
        return [
            state for state in states if state.entity_id.startswith(f"{domain}.")
        ]


class FakeServices:
    def __init__(self):
        self.calls = []
        self.registered = {}

    async def async_call(
        self, domain, service, service_data, *, blocking, context, target
    ):
        self.calls.append(
            {
                "domain": domain,
                "service": service,
                "service_data": deepcopy(service_data),
                "blocking": blocking,
                "context": context,
                "target": deepcopy(target),
            }
        )

    def async_register(self, domain, service, handler, *, schema):
        self.registered[(domain, service)] = (handler, schema)


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

    async def test_activate_resolves_renamed_rooms_and_runs_backend_sequence(self):
        saved = preset()
        saved["media"] = media()
        storage = GroupPresetStorage(FakeStore({"presets": [saved]}))
        await storage.async_load()
        services = FakeServices()
        hass = SimpleNamespace(
            states=FakeStates(
                [
                    FakeState(
                        "media_player.kitchen_renamed",
                        22,
                        group_members=[
                            "media_player.kitchen_renamed",
                            "media_player.living",
                            "media_player.bedroom",
                        ],
                    ),
                    FakeState("media_player.living", 23, supported_features=4),
                    FakeState("media_player.bedroom", 24),
                ]
            ),
            services=services,
        )

        result = await storage.async_activate(
            hass, preset_id="whole", context="test-context"
        )

        self.assertEqual(result["leader_entity_id"], "media_player.kitchen_renamed")
        self.assertEqual(
            result["member_entity_ids"],
            ["media_player.kitchen_renamed", "media_player.living"],
        )
        self.assertEqual(
            [call["service"] for call in services.calls],
            [
                "play_media",
                "join",
                "unjoin",
                "volume_set",
                "volume_set",
                "volume_mute",
            ],
        )
        self.assertEqual(
            services.calls[0]["service_data"],
            {
                "media_content_type": "channel",
                "media_content_id": (
                    "control4_advanced:tunein:22:station:92:Station/s25028"
                ),
            },
        )
        self.assertEqual(
            services.calls[1]["service_data"],
            {"group_members": ["media_player.living"]},
        )
        self.assertEqual(
            services.calls[2]["target"],
            {"entity_id": ["media_player.bedroom"]},
        )
        self.assertTrue(all(call["blocking"] for call in services.calls))
        self.assertTrue(
            all(call["context"] == "test-context" for call in services.calls)
        )

    async def test_activate_validates_selector_and_missing_players(self):
        storage = GroupPresetStorage(FakeStore({"presets": [preset()]}))
        await storage.async_load()
        hass = SimpleNamespace(
            states=FakeStates([FakeState("media_player.kitchen", 22)]),
            services=FakeServices(),
        )

        with self.assertRaisesRegex(GroupPresetActivationError, "exactly one"):
            await storage.async_activate(hass)
        with self.assertRaisesRegex(GroupPresetActivationError, "exactly one"):
            await storage.async_activate(
                hass, preset_id="whole", name="Whole House"
            )
        with self.assertRaisesRegex(GroupPresetActivationError, "not found"):
            await storage.async_activate(hass, name="Missing")
        with self.assertRaisesRegex(GroupPresetActivationError, "unavailable"):
            await storage.async_activate(hass, name="whole house")

    async def test_websocket_commands_register_once_and_round_trip(self):
        voluptuous = ModuleType("voluptuous")
        voluptuous.Required = lambda key: key
        voluptuous.Optional = lambda key: key
        voluptuous.All = lambda *validators: validators
        voluptuous.Any = lambda *validators: validators
        voluptuous.Coerce = lambda value_type: value_type
        voluptuous.Range = lambda **limits: limits
        voluptuous.Length = lambda **limits: limits
        voluptuous.Schema = lambda schema: schema

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
        exceptions = ModuleType("homeassistant.exceptions")

        class HomeAssistantError(Exception):
            pass

        exceptions.HomeAssistantError = HomeAssistantError
        homeassistant = ModuleType("homeassistant")
        homeassistant.__path__ = []
        services = FakeServices()
        hass = SimpleNamespace(data={}, services=services)

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
                "homeassistant.exceptions": exceptions,
                "homeassistant.helpers": helpers,
                "homeassistant.helpers.config_validation": config_validation,
                "homeassistant.helpers.storage": storage_module,
            },
        ):
            manager = await async_register_group_presets(hass)
            self.assertIs(await async_register_group_presets(hass), manager)

        self.assertEqual(len(registered), 5)
        self.assertIn(
            ("control4_advanced", SERVICE_ACTIVATE_GROUP_PRESET),
            services.registered,
        )
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
