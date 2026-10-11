"""Offline setup/unload check for the optional room-volume platform."""

from __future__ import annotations

from types import ModuleType, SimpleNamespace
import sys
import unittest
from unittest.mock import patch

import custom_components.control4_advanced as integration


class FakeSession:
    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True


class FakeRest:
    def __init__(self, host, provider, session):
        self.host = host
        self.items = [
            {"id": 22, "name": "Living", "typeName": "room", "proxy": "roomdevice"},
            {"id": 23, "name": "Bedroom", "typeName": "room", "proxy": "roomdevice"},
            {"id": 93, "name": "TIDAL", "typeName": "device", "proxy": "media_service",
             "filename": "tidal.c4z", "protocolId": 93},
            {"id": 94, "name": "TIDAL", "typeName": "device", "proxy": "media_service",
             "protocolId": 93, "protocolFilename": "tidal.c4z"},
            {"id": 623, "name": "Wiim", "typeName": "device", "proxy": "aswitch"},
            {"id": 346, "name": "Plex", "typeName": "device", "proxy": "media_player"},
            {"id": 100002, "name": "Digital Media", "typeName": "device",
             "proxy": "control4_digitalaudio", "filename": "control4_digitalaudio.c4i"},
            {"id": 2726, "name": "Lamp", "typeName": "device", "proxy": "light_v2",
             "capabilities": {"on_off": True, "dimmer": True}},
        ]

    async def get_items(self):
        return self.items

    async def get_variables(self, device_id):
        if device_id not in (22, 23):
            raise AssertionError("media discovery must only read rooms")
        if device_id == 23:
            return [
                {"varName": "POWER_STATE", "value": 0},
                {"varName": "CURRENT_SELECTED_DEVICE", "value": 0},
                {"varName": "CURRENT_VOLUME_DEVICE_ID", "value": 0},
            ]
        return [
            {"varName": "POWER_STATE", "value": 1},
            {"varName": "CURRENT_VOLUME", "value": 41},
            {"varName": "IS_MUTED", "value": 0},
            {"varName": "CURRENT_VOLUME_DEVICE_ID", "value": 623},
        ]

    async def get_ui_configuration(self):
        return {"experiences": [
            {"room_id": 22, "type": "watch", "sources": {
                "source": [{"id": 346, "type": "RF_MINI_APP"},
                           {"id": 4294966299, "type": "SPECIAL_AUDIO"}],
            }},
            {"room_id": 22, "type": "listen", "sources": {
                "source": [{"id": 94, "type": "DIGITAL_AUDIO_SERVER"}],
            }},
            {"room_id": 23, "type": "listen", "sources": {
                "source": [{"id": 94, "type": "DIGITAL_AUDIO_SERVER"}],
            }},
        ]}


class FakeTransport:
    instances = []

    def __init__(self, rest, tracked_ids, *, reconciliation_seconds):
        self.rest = rest
        self.inventory = SimpleNamespace(items={item["id"]: item for item in rest.items})
        self.tracked_ids = set(tracked_ids)
        self.started = False
        self.stopped = False
        self.instances.append(self)

    async def start(self, *, ready_timeout):
        self.started = True

    async def stop(self):
        self.stopped = True


class FakeCommands:
    def __init__(self, rest):
        self.metadata_ids = []
        self.room_sources = {}
        self.tidal_playlists = {}
        self.media_grouping = None

    async def refresh(self, device_ids):
        self.metadata_ids.append(list(device_ids))

    def register_room_sources(self, room_id, sources):
        self.room_sources[room_id] = tuple(sources)

    def supports_room_source(self, room_id, experience):
        return room_id in (22, 23) and experience in ("watch", "listen")

    def supports_tidal_playlist(self, protocol_id):
        return protocol_id == 93

    def register_tidal_playlists(self, protocol_id, room_id, playlists):
        self.tidal_playlists[protocol_id, room_id] = tuple(playlists)

    def register_media_grouping(self, digital_media_id, room_ids):
        self.media_grouping = (digital_media_id, tuple(room_ids))


class FakeCoordinator:
    instances = []

    def __init__(self, transport, room_id):
        self.room_id = room_id
        self.started = False
        self.stopped = False
        self.source_fallback_enabled = False
        self.instances.append(self)

    async def start(self):
        self.started = True

    async def stop(self):
        self.stopped = True

    def enable_source_fallback(self):
        self.source_fallback_enabled = True


class FakeSessionCoordinator:
    instances = []

    def __init__(self, rest, room_ids):
        self.room_ids = tuple(room_ids)
        self.started = False
        self.stopped = False
        self.instances.append(self)

    async def start(self):
        self.started = True

    async def stop(self):
        self.stopped = True


class MediaPlayerSetupTests(unittest.IsolatedAsyncioTestCase):
    async def test_setup_registers_room_and_unload_stops_coordinator(self):
        FakeTransport.instances.clear()
        FakeCoordinator.instances.clear()
        FakeSessionCoordinator.instances.clear()
        homeassistant = ModuleType("homeassistant")
        homeassistant.__path__ = []
        constants = ModuleType("homeassistant.const")
        constants.CONF_HOST = "host"
        constants.CONF_USERNAME = "username"
        constants.CONF_PASSWORD = "password"
        exceptions = ModuleType("homeassistant.exceptions")
        exceptions.ConfigEntryAuthFailed = type("ConfigEntryAuthFailed", (Exception,), {})
        exceptions.ConfigEntryNotReady = type("ConfigEntryNotReady", (Exception,), {})
        helpers = ModuleType("homeassistant.helpers")
        helpers.__path__ = []
        entity_registry = ModuleType("homeassistant.helpers.entity_registry")
        entity_registry.async_get = lambda hass: object()
        entity_registry.async_entries_for_config_entry = lambda registry, entry_id: []
        helpers.entity_registry = entity_registry
        session = FakeSession()
        forwarded = []

        async def forward(entry, platforms):
            forwarded.extend(platforms)

        async def unload(entry, platforms):
            return True

        hass = SimpleNamespace(config_entries=SimpleNamespace(
            async_forward_entry_setups=forward, async_unload_platforms=unload
        ))
        entry = SimpleNamespace(
            data={"host": "127.0.0.1", "username": "test", "password": "test"},
            options={"tidal_playlists":
                     "TIDAL | Evening Mix | 01234567-89ab-cdef-0123-456789abcdef"},
            entry_id="test-entry", runtime_data=None,
        )
        with (
            patch.dict(sys.modules, {"homeassistant": homeassistant,
                                     "homeassistant.const": constants,
                                     "homeassistant.exceptions": exceptions,
                                     "homeassistant.helpers": helpers,
                                     "homeassistant.helpers.entity_registry": entity_registry}),
            patch.object(integration.aiohttp, "TCPConnector", return_value=object()),
            patch.object(integration.aiohttp, "ClientSession", return_value=session),
            patch.object(integration, "DirectorRestClient", FakeRest),
            patch.object(integration, "Control4Transport", FakeTransport),
            patch.object(integration, "DeviceCommandClient", FakeCommands),
            patch.object(integration, "RoomVolumeCoordinator", FakeCoordinator),
            patch.object(integration, "MediaSessionCoordinator", FakeSessionCoordinator),
            patch.object(integration, "migrate_tidal_playlist_registry_entries"),
            patch.object(integration, "remove_stale_registry_entries"),
        ):
            self.assertTrue(await integration.async_setup_entry(hass, entry))
            self.assertIn("media_player", forwarded)
            self.assertIn("button", forwarded)
            self.assertEqual(FakeTransport.instances[0].tracked_ids, {2726})
            self.assertEqual(entry.runtime_data.commands.metadata_ids, [[2726], [22, 23], [93]])
            self.assertEqual(list(entry.runtime_data.media_coordinators), [22, 23])
            self.assertEqual(
                [(source.device_id, source.label) for source in entry.runtime_data.media_sources[22]],
                [(346, "Watch: Plex"), (94, "Listen: TIDAL")],
            )
            self.assertEqual(
                entry.runtime_data.commands.room_sources[22],
                entry.runtime_data.media_sources[22],
            )
            self.assertEqual(
                [(source.device_id, source.label)
                 for source in entry.runtime_data.media_sources[23]],
                [(94, "Listen: TIDAL")],
            )
            self.assertEqual(
                set(entry.runtime_data.commands.room_sources), {22, 23}
            )
            self.assertTrue(all(item.started for item in FakeCoordinator.instances))
            self.assertTrue(FakeSessionCoordinator.instances[0].started)
            self.assertEqual(
                entry.runtime_data.commands.media_grouping, (100002, (22, 23))
            )
            self.assertTrue(all(
                item.source_fallback_enabled for item in FakeCoordinator.instances
            ))
            catalog = entry.runtime_data.tidal_catalogs[22][0]
            self.assertEqual((catalog.source_id, catalog.protocol_id), (94, 93))
            self.assertEqual([item.name for item in catalog.playlists], ["Evening Mix"])
            self.assertEqual(
                list(entry.runtime_data.commands.tidal_playlists), [(93, 22), (93, 23)]
            )
            self.assertTrue(await integration.async_unload_entry(hass, entry))
        self.assertTrue(all(item.stopped for item in FakeCoordinator.instances))
        self.assertTrue(FakeSessionCoordinator.instances[0].stopped)
        self.assertTrue(FakeTransport.instances[0].stopped)
        self.assertTrue(session.closed)


if __name__ == "__main__":
    unittest.main()
