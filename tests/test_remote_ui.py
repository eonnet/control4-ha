"""Offline Home Assistant shape checks for Control4 room remotes."""

from __future__ import annotations

from enum import IntFlag
import importlib
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from custom_components.control4_advanced.transport.commands import UnsupportedCommand
from custom_components.control4_advanced.transport.sources import RoomSource


class FakeEntity:
    @property
    def unique_id(self):
        return self._attr_unique_id

    @property
    def name(self):
        return self._attr_name

    async def async_added_to_hass(self):
        pass

    async def async_will_remove_from_hass(self):
        pass

    def async_write_ha_state(self):
        self.writes = getattr(self, "writes", 0) + 1


class FakeRemoteEntity(FakeEntity):
    pass


class FakeRemoteFeature(IntFlag):
    LEARN_COMMAND = 1
    DELETE_COMMAND = 2
    ACTIVITY = 4


def ha_stub_modules():
    names = (
        "homeassistant", "homeassistant.components",
        "homeassistant.components.remote", "homeassistant.config_entries",
        "homeassistant.core", "homeassistant.helpers",
        "homeassistant.helpers.device_registry", "homeassistant.helpers.entity",
        "homeassistant.helpers.entity_platform",
    )
    modules = {name: ModuleType(name) for name in names}
    for name in ("homeassistant", "homeassistant.components", "homeassistant.helpers"):
        modules[name].__path__ = []
    remote = modules["homeassistant.components.remote"]
    remote.ATTR_DELAY_SECS = "delay_secs"
    remote.ATTR_HOLD_SECS = "hold_secs"
    remote.ATTR_NUM_REPEATS = "num_repeats"
    remote.DEFAULT_DELAY_SECS = 0.4
    remote.DEFAULT_HOLD_SECS = 0
    remote.DEFAULT_NUM_REPEATS = 1
    remote.RemoteEntity = FakeRemoteEntity
    remote.RemoteEntityFeature = FakeRemoteFeature
    modules["homeassistant.config_entries"].ConfigEntry = type("ConfigEntry", (), {})
    modules["homeassistant.core"].HomeAssistant = type("HomeAssistant", (), {})
    modules["homeassistant.helpers.device_registry"].DeviceInfo = dict
    modules["homeassistant.helpers.entity"].Entity = FakeEntity
    modules["homeassistant.helpers.entity_platform"].AddConfigEntryEntitiesCallback = object
    return modules


class FakeCommands:
    def __init__(self):
        self.room_remote = AsyncMock()
        self.room_source = AsyncMock()
        self.room_off = AsyncMock()
        self.remote_commands = (
            "CONTROL4", "UP", "ENTER", "PULSE_VOL_UP", "NUMBER_1",
        )
        self.hold_commands = {"START_UP", "STOP_UP"}

    def room_remote_commands(self, room_id):
        return self.remote_commands if room_id == 22 else ()

    def supports_room_remote(self, room_id, command):
        return room_id == 22 and (
            command in self.remote_commands or command in self.hold_commands
        )

    def supports_room_source(self, room_id, experience):
        return room_id == 22 and experience == "watch"

    def supports_room_off(self, room_id):
        return room_id == 22


class RemoteUITests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.patcher = patch.dict(sys.modules, ha_stub_modules())
        cls.patcher.start()
        cls.remote = importlib.import_module("custom_components.control4_advanced.remote")

    @classmethod
    def tearDownClass(cls):
        for name in (
            "custom_components.control4_advanced.remote",
            "custom_components.control4_advanced.entity",
        ):
            sys.modules.pop(name, None)
        cls.patcher.stop()

    async def asyncSetUp(self):
        self.state = {
            22: {"is_on": True, "selected_source_id": 346},
        }
        self.items = {
            22: {"id": 22, "name": "Living", "proxy": "roomdevice",
                 "typeName": "room", "roomName": "Living"},
        }
        self.commands = FakeCommands()

        def snapshot(device_id):
            return self.state.get(device_id, {}).copy()

        async def sync_room_source(room_id):
            self.state[room_id]["is_on"] = True

        async def sync_device(room_id):
            self.state[room_id]["is_on"] = False

        self.transport = SimpleNamespace(
            rest=SimpleNamespace(host="192.168.0.24"),
            inventory=SimpleNamespace(items=self.items),
            state=SimpleNamespace(snapshot=snapshot),
            events=SimpleNamespace(subscribe=lambda callback: lambda: None),
            subscribe_connection=lambda callback: lambda: None,
            connected=True,
            sync_room_source=AsyncMock(side_effect=sync_room_source),
            sync_device=AsyncMock(side_effect=sync_device),
        )
        self.sources = (
            RoomSource(346, "watch", "Netflix", "RF_MINI_APP", "Watch: Netflix"),
            RoomSource(623, "listen", "TIDAL", "AUDIO_SELECTION", "Listen: TIDAL"),
        )
        self.runtime = SimpleNamespace(
            transport=self.transport,
            commands=self.commands,
            media_coordinators={22: object()},
            media_sources={22: self.sources},
        )
        self.entity = self.remote.Control4RoomRemote(self.runtime, self.items[22])

    async def test_setup_and_state_expose_only_validated_room_remote(self):
        entities = []
        entry = SimpleNamespace(runtime_data=self.runtime)
        await self.remote.async_setup_entry(None, entry, entities.extend)
        self.assertEqual(len(entities), 1)
        entity = entities[0]
        self.assertEqual(entity.unique_id, "192.168.0.24_22_remote")
        self.assertEqual(entity.name, "Living Remote")
        self.assertTrue(entity.is_on)
        self.assertEqual(entity.current_activity, "Watch: Netflix")
        self.assertEqual(entity.activity_list, ["Watch: Netflix"])
        self.assertEqual(entity.supported_features, FakeRemoteFeature.ACTIVITY)
        self.assertEqual(entity.extra_state_attributes, {
            "control4_room_id": 22,
            "control4_commands": list(self.commands.remote_commands),
        })

        self.commands.remote_commands = ()
        entities.clear()
        await self.remote.async_setup_entry(None, entry, entities.extend)
        self.assertFalse(entities)

    async def test_send_command_repeats_and_hold_pairs_are_validated(self):
        await self.entity.async_send_command(["up", "ENTER"], delay_secs=0)
        self.assertEqual(
            [call.args[1] for call in self.commands.room_remote.await_args_list],
            ["UP", "ENTER"],
        )
        self.commands.room_remote.reset_mock()
        with patch.object(self.remote.asyncio, "sleep", AsyncMock()) as sleep:
            await self.entity.async_send_command(["UP"], hold_secs=1.5)
        self.assertEqual(
            [call.args[1] for call in self.commands.room_remote.await_args_list],
            ["START_UP", "STOP_UP"],
        )
        sleep.assert_awaited_once_with(1.5)

        self.commands.room_remote.reset_mock()
        self.state[22]["is_on"] = False
        with self.assertRaises(UnsupportedCommand):
            await self.entity.async_send_command(["UP"])
        self.commands.room_remote.assert_not_awaited()

    async def test_activity_and_off_use_only_room_catalog_and_confirm_state(self):
        await self.entity.async_turn_on(activity="Netflix")
        self.commands.room_source.assert_awaited_once_with(22, self.sources[0])
        self.transport.sync_room_source.assert_awaited()

        with self.assertRaises(UnsupportedCommand):
            await self.entity.async_turn_on(activity="Invented")

        await self.entity.async_turn_off()
        self.commands.room_off.assert_awaited_once_with(22)
        self.transport.sync_device.assert_awaited()
        self.assertFalse(self.entity.is_on)


if __name__ == "__main__":
    unittest.main()
