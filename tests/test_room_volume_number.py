"""Offline HA-shape checks for independent room-volume sliders."""

from __future__ import annotations

from enum import StrEnum
import importlib
import math
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from custom_components.control4_advanced.transport.commands import UnsupportedCommand


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
        pass


class FakeNumberEntity(FakeEntity):
    pass


class FakeNumberMode(StrEnum):
    SLIDER = "slider"


def ha_stub_modules():
    names = (
        "homeassistant", "homeassistant.components", "homeassistant.components.number",
        "homeassistant.config_entries", "homeassistant.const", "homeassistant.core",
        "homeassistant.helpers", "homeassistant.helpers.device_registry",
        "homeassistant.helpers.entity", "homeassistant.helpers.entity_platform",
    )
    modules = {name: ModuleType(name) for name in names}
    for name in ("homeassistant", "homeassistant.components", "homeassistant.helpers"):
        modules[name].__path__ = []
    modules["homeassistant.components.number"].NumberEntity = FakeNumberEntity
    modules["homeassistant.components.number"].NumberMode = FakeNumberMode
    modules["homeassistant.config_entries"].ConfigEntry = type("ConfigEntry", (), {})
    modules["homeassistant.const"].PERCENTAGE = "%"
    modules["homeassistant.core"].HomeAssistant = type("HomeAssistant", (), {})
    modules["homeassistant.helpers.device_registry"].DeviceInfo = dict
    modules["homeassistant.helpers.entity"].Entity = FakeEntity
    modules[
        "homeassistant.helpers.entity_platform"
    ].AddConfigEntryEntitiesCallback = object
    return modules


class RoomVolumeNumberTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.patcher = patch.dict(sys.modules, ha_stub_modules())
        cls.patcher.start()
        cls.number = importlib.import_module("custom_components.control4_advanced.number")

    @classmethod
    def tearDownClass(cls):
        for name in (
            "custom_components.control4_advanced.number",
            "custom_components.control4_advanced.entity",
        ):
            sys.modules.pop(name, None)
        cls.patcher.stop()

    async def asyncSetUp(self):
        self.state = {
            22: {
                "is_on": True,
                "volume_percent": 41.0,
                "is_muted": False,
                "volume_device_id": 623,
            },
            23: {
                "is_on": True,
                "volume_percent": 30.0,
                "is_muted": False,
                "volume_device_id": 624,
            },
        }
        self.items = {
            22: {
                "id": 22, "name": "Living", "typeName": "room",
                "proxy": "roomdevice", "roomName": "Living",
            },
            23: {
                "id": 23, "name": "Bedroom", "typeName": "room",
                "proxy": "roomdevice", "roomName": "Bedroom",
            },
            623: {"id": 623, "proxy": "aswitch"},
            624: {"id": 624, "proxy": "aswitch"},
        }
        self.commands = SimpleNamespace(
            supports_room_volume=lambda room_id: room_id == 22,
            room_volume=AsyncMock(),
        )
        transport = SimpleNamespace(
            rest=SimpleNamespace(host="192.168.0.24"),
            inventory=SimpleNamespace(items=self.items),
            state=SimpleNamespace(
                snapshot=lambda room_id: self.state.get(room_id, {}).copy()
            ),
            events=SimpleNamespace(subscribe=lambda callback: lambda: None),
            subscribe_connection=lambda callback: lambda: None,
            connected=True,
        )
        self.runtime = SimpleNamespace(
            transport=transport,
            commands=self.commands,
            media_coordinators={22: object(), 23: object()},
        )

    async def test_setup_adds_only_validated_room_volume_sliders(self):
        entities = []
        await self.number.async_setup_entry(
            None, SimpleNamespace(runtime_data=self.runtime), entities.extend
        )
        self.assertEqual(len(entities), 1)
        entity = entities[0]
        self.assertEqual(entity.name, "Volume")
        self.assertEqual(entity.unique_id, "192.168.0.24_22_room_volume")
        self.assertEqual(entity.native_value, 41.0)
        self.assertEqual(entity._attr_native_unit_of_measurement, "%")
        self.assertEqual(entity._attr_mode, FakeNumberMode.SLIDER)
        self.assertEqual(entity.device_info["suggested_area"], "Living")
        self.assertTrue(entity.available)

    async def test_slider_controls_only_its_room_without_optimistic_state(self):
        entity = self.number.Control4RoomVolumeNumber(
            self.runtime, self.items[22]
        )
        await entity.async_set_native_value(42.5)
        self.commands.room_volume.assert_awaited_once_with(22, 43)
        self.assertEqual(entity.native_value, 41.0)
        self.assertEqual(self.state[23]["volume_percent"], 30.0)

    async def test_unavailable_or_invalid_slider_never_sends(self):
        entity = self.number.Control4RoomVolumeNumber(
            self.runtime, self.items[22]
        )
        for value in (-1, 101, math.nan, True):
            with self.subTest(value=value), self.assertRaises(UnsupportedCommand):
                await entity.async_set_native_value(value)
        self.state[22]["is_on"] = False
        self.assertFalse(entity.available)
        with self.assertRaises(UnsupportedCommand):
            await entity.async_set_native_value(40)
        self.commands.room_volume.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
