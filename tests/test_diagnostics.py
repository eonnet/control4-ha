"""Offline checks for privacy-preserving Home Assistant diagnostics."""

from __future__ import annotations

import importlib
from types import ModuleType, SimpleNamespace
import sys
import unittest
from unittest.mock import patch


def _redact(value, keys):
    if isinstance(value, dict):
        return {
            key: "**REDACTED**" if key in keys else _redact(item, keys)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item, keys) for item in value]
    return value


def ha_stub_modules():
    names = (
        "homeassistant",
        "homeassistant.components",
        "homeassistant.components.diagnostics",
        "homeassistant.config_entries",
        "homeassistant.const",
        "homeassistant.core",
        "homeassistant.helpers",
        "homeassistant.helpers.device_registry",
        "homeassistant.helpers.entity_registry",
    )
    modules = {name: ModuleType(name) for name in names}
    for name in (
        "homeassistant",
        "homeassistant.components",
        "homeassistant.helpers",
    ):
        modules[name].__path__ = []
    modules["homeassistant.components.diagnostics"].async_redact_data = _redact
    modules["homeassistant.config_entries"].ConfigEntry = type(
        "ConfigEntry", (), {}
    )
    modules["homeassistant.const"].CONF_HOST = "host"
    modules["homeassistant.const"].CONF_PASSWORD = "password"
    modules["homeassistant.const"].CONF_USERNAME = "username"
    modules["homeassistant.core"].HomeAssistant = type(
        "HomeAssistant", (), {}
    )
    modules["homeassistant.helpers.device_registry"].DeviceEntry = type(
        "DeviceEntry", (), {}
    )
    registry = modules["homeassistant.helpers.entity_registry"]
    registry.async_get = lambda hass: hass.entity_registry
    registry.async_entries_for_config_entry = (
        lambda entity_registry, entry_id: [
            entity
            for entity in entity_registry
            if entity.config_entry_id == entry_id
        ]
    )
    modules["homeassistant.helpers"].entity_registry = registry
    return modules


class FakeInventory:
    def __init__(self, items):
        self.items = items

    def proxy(self, device_id):
        return self.items.get(device_id, {}).get("proxy")


class FakeStateCache:
    revision = 17

    def __init__(self, states):
        self.states = states

    def snapshot(self, device_id):
        return dict(self.states.get(device_id, {}))


class FakeCommands:
    def __init__(self):
        self._metadata = {
            1: {"ON": {}, "OFF": {}},
            22: {
                "SET_VOLUME_LEVEL": {},
                "MUTE_ON": {},
                "MUTE_OFF": {},
                "ROOM_OFF": {},
                "SELECT_AUDIO_DEVICE": {},
            },
            23: {"SET_VOLUME_LEVEL": {}, "SELECT_AUDIO_DEVICE": {}},
        }

    def supports_room_volume(self, room_id):
        return room_id in {22, 23}

    def supports_room_mute(self, room_id):
        return room_id == 22

    def supports_room_off(self, room_id):
        return room_id == 22

    def supports_room_source(self, room_id, experience):
        return experience == "listen" or (room_id == 22 and experience == "watch")


class DiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.patcher = patch.dict(sys.modules, ha_stub_modules())
        cls.patcher.start()
        cls.diagnostics = importlib.import_module(
            "custom_components.control4_advanced.diagnostics"
        )

    @classmethod
    def tearDownClass(cls):
        sys.modules.pop("custom_components.control4_advanced.diagnostics", None)
        cls.patcher.stop()

    def setUp(self):
        self.items = {
            1: {
                "id": 1,
                "name": "Private Light Name",
                "roomName": "Private Room",
                "typeName": "device",
                "proxy": "light_v2",
                "manufacturer": "Control4",
                "model": "Dimmer",
                "capabilities": {"on_off": True, "dimmer": True},
            },
            22: {
                "id": 22,
                "name": "Private Kitchen",
                "roomName": "Private Kitchen",
                "typeName": "room",
                "proxy": "roomdevice",
            },
            23: {
                "id": 23,
                "name": "Private Bedroom",
                "roomName": "Private Bedroom",
                "typeName": "room",
                "proxy": "roomdevice",
            },
            91: {
                "id": 91,
                "name": "Private Account",
                "typeName": "device",
                "proxy": "media_service",
            },
        }
        states = {
            1: {"is_on": True, "brightness_percent": 42},
            22: {
                "is_on": True,
                "volume_percent": 21,
                "is_muted": False,
                "selected_source_id": 91,
                "media_title": "Private Song Title",
                "media_artist": "Private Artist",
                "media_image_url": "https://private.example/token",
            },
            23: {"is_on": False},
        }
        transport = SimpleNamespace(
            connected=True,
            reconciliation_seconds=900,
            tracked_ids={1, 22, 23},
            inventory=FakeInventory(self.items),
            state=FakeStateCache(states),
            rest=SimpleNamespace(host="private-director.local"),
        )
        playlist = SimpleNamespace(
            playlist_id="01234567-89ab-cdef-0123-456789abcdef"
        )
        favorite = SimpleNamespace(item_id="Station/s25028")
        source_list = (
            SimpleNamespace(device_id=91, experience="listen"),
            SimpleNamespace(device_id=92, experience="watch"),
        )
        session_coordinator = SimpleNamespace(
            ready=True,
            sessions=(SimpleNamespace(room_ids=(22, 23)),),
        )
        self.runtime = SimpleNamespace(
            transport=transport,
            commands=FakeCommands(),
            media_coordinators={22: object(), 23: object()},
            media_sources={22: source_list, 23: source_list[:1]},
            tidal_catalogs={
                22: (
                    SimpleNamespace(
                        source_id=91,
                        protocol_id=90,
                        playlists=(playlist,),
                    ),
                ),
                23: (
                    SimpleNamespace(
                        source_id=91,
                        protocol_id=90,
                        playlists=(playlist,),
                    ),
                ),
            },
            tunein_catalogs={
                22: (
                    SimpleNamespace(
                        source_id=93,
                        protocol_id=94,
                        favorites=(favorite,),
                    ),
                ),
            },
            media_session_coordinator=session_coordinator,
            media_entity_ids={22: "media_player.kitchen", 23: "media_player.bedroom"},
        )
        self.entry = SimpleNamespace(
            entry_id="entry-1",
            version=1,
            state=SimpleNamespace(value="loaded"),
            data={
                "host": "private-director.local",
                "username": "private@example.com",
                "password": "super-secret-password",
            },
            options={
                "reconciliation_seconds": 900,
                "tidal_playlists": (
                    "Private Account | Private Playlist | "
                    "01234567-89ab-cdef-0123-456789abcdef"
                ),
                "tunein_favorites": (
                    "Private TuneIn | Private Station | Station/s25028"
                ),
            },
            runtime_data=self.runtime,
        )
        entity_entries = [
            SimpleNamespace(
                config_entry_id="entry-1",
                entity_id="light.private_light",
                disabled_by=None,
            ),
            SimpleNamespace(
                config_entry_id="entry-1",
                entity_id="media_player.private_kitchen",
                disabled_by="integration",
            ),
        ]
        ha_states = {
            "light.private_light": SimpleNamespace(state="on"),
            "media_player.private_kitchen": SimpleNamespace(state="unavailable"),
        }
        preset_store = SimpleNamespace(presets=[{
            "name": "Private Preset",
            "members": [{"entity_id": "media_player.private_kitchen"}],
            "media": {"title": "Private Playlist"},
        }])
        self.hass = SimpleNamespace(
            data={"control4_advanced_group_preset_store": preset_store},
            entity_registry=entity_entries,
            states=SimpleNamespace(get=ha_states.get),
        )

    async def test_config_entry_diagnostics_are_useful_and_redacted(self):
        result = await self.diagnostics.async_get_config_entry_diagnostics(
            self.hass, self.entry
        )

        self.assertEqual(result["config_entry"]["state"], "loaded")
        self.assertEqual(
            result["config_entry"]["data"],
            {
                "host": "**REDACTED**",
                "username": "**REDACTED**",
                "password": "**REDACTED**",
            },
        )
        self.assertEqual(result["transport"]["tracked_count"], 3)
        self.assertEqual(result["transport"]["synchronized_count"], 3)
        self.assertEqual(result["commands"]["advertised_command_count"], 9)
        self.assertEqual(result["entities"]["by_domain"], {
            "light": 1,
            "media_player": 1,
        })
        self.assertEqual(
            result["entities"]["unavailable_by_domain"],
            {"media_player": 1},
        )
        self.assertEqual(result["media"]["room_count"], 2)
        self.assertEqual(
            result["media"]["source_associations"],
            {"listen": 2, "watch": 1},
        )
        self.assertEqual(result["media"]["tidal"]["playlist_count"], 1)
        self.assertEqual(result["media"]["tunein"]["favorite_count"], 1)
        self.assertEqual(result["media"]["group_presets"]["count"], 1)
        self.assertEqual(result["options"]["tidal_playlists"]["count"], 1)
        self.assertEqual(result["options"]["tunein_favorites"]["count"], 1)

        output = repr(result)
        for private_value in (
            "private-director.local",
            "private@example.com",
            "super-secret-password",
            "Private Light Name",
            "Private Kitchen",
            "Private Bedroom",
            "Private Account",
            "Private Playlist",
            "Private Song Title",
            "Private Artist",
            "private.example",
            "Station/s25028",
            "01234567-89ab-cdef-0123-456789abcdef",
        ):
            self.assertNotIn(private_value, output)

    async def test_device_diagnostics_exclude_identity_and_raw_media(self):
        device = SimpleNamespace(identifiers={
            ("control4_advanced", "private-director.local_22")
        })

        result = await self.diagnostics.async_get_device_diagnostics(
            self.hass, self.entry, device
        )

        self.assertTrue(result["device_available"])
        self.assertEqual(result["inventory"]["proxy"], "roomdevice")
        self.assertEqual(
            result["advertised_commands"],
            [
                "MUTE_OFF",
                "MUTE_ON",
                "ROOM_OFF",
                "SELECT_AUDIO_DEVICE",
                "SET_VOLUME_LEVEL",
            ],
        )
        self.assertEqual(
            result["state"]["safe_values"],
            {"is_muted": False, "is_on": True, "volume_percent": 21},
        )
        self.assertIn("media_title", result["state"]["fields"])
        output = repr(result)
        self.assertNotIn("private-director.local", output)
        self.assertNotIn("Private Kitchen", output)
        self.assertNotIn("Private Song Title", output)
        self.assertNotIn("https://private.example/token", output)

    async def test_diagnostics_handle_unloaded_and_unknown_devices(self):
        unloaded = SimpleNamespace(
            entry_id="entry-1",
            version=1,
            state="not_loaded",
            data=self.entry.data,
            options={},
            runtime_data=None,
        )
        result = await self.diagnostics.async_get_config_entry_diagnostics(
            self.hass, unloaded
        )
        self.assertFalse(result["runtime_loaded"])
        self.assertNotIn("transport", result)

        device = SimpleNamespace(identifiers={
            ("control4_advanced", "private-director.local_999")
        })
        result = await self.diagnostics.async_get_device_diagnostics(
            self.hass, self.entry, device
        )
        self.assertEqual(
            result,
            {"runtime_loaded": True, "device_available": False},
        )
