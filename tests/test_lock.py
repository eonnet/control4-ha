"""Offline Home Assistant-shape checks for native Control4 locks."""

from __future__ import annotations

import importlib
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch


class FakeEntity:
    async def async_added_to_hass(self):
        pass

    async def async_will_remove_from_hass(self):
        pass


def ha_stub_modules():
    names = (
        "homeassistant", "homeassistant.components",
        "homeassistant.components.lock", "homeassistant.config_entries",
        "homeassistant.core", "homeassistant.exceptions", "homeassistant.helpers",
        "homeassistant.helpers.device_registry", "homeassistant.helpers.entity",
        "homeassistant.helpers.entity_platform",
    )
    modules = {name: ModuleType(name) for name in names}
    for name in ("homeassistant", "homeassistant.components", "homeassistant.helpers"):
        modules[name].__path__ = []
    modules["homeassistant.components.lock"].LockEntity = type(
        "LockEntity", (), {}
    )
    modules["homeassistant.config_entries"].ConfigEntry = type(
        "ConfigEntry", (), {}
    )
    modules["homeassistant.core"].HomeAssistant = type("HomeAssistant", (), {})
    modules["homeassistant.exceptions"].HomeAssistantError = type(
        "HomeAssistantError", (Exception,), {}
    )
    modules["homeassistant.helpers.device_registry"].DeviceInfo = dict
    modules["homeassistant.helpers.entity"].Entity = FakeEntity
    modules[
        "homeassistant.helpers.entity_platform"
    ].AddConfigEntryEntitiesCallback = object
    return modules


class FakeCommands:
    def __init__(self) -> None:
        self.calls = []
        self.supported = {3403: {"LOCK", "UNLOCK"}, 3404: {"LOCK"}}

    def supports_lock(self, device_id):
        return {"LOCK", "UNLOCK"} <= self.supported.get(device_id, set())

    async def lock_device(self, device_id):
        self.calls.append(("lock", device_id))

    async def unlock_device(self, device_id):
        self.calls.append(("unlock", device_id))


class LockTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.patcher = patch.dict(sys.modules, ha_stub_modules())
        cls.patcher.start()
        cls.lock = importlib.import_module("custom_components.control4_advanced.lock")

    @classmethod
    def tearDownClass(cls):
        for name in (
            "custom_components.control4_advanced.lock",
            "custom_components.control4_advanced.entity",
        ):
            sys.modules.pop(name, None)
        cls.patcher.stop()

    async def test_native_child_is_grouped_and_uses_director_feedback(self):
        items = {
            3402: {
                "id": 3402, "name": "Z-Wave Lock", "typeName": "device",
                "proxy": "lock", "roomName": "Living",
            },
            3403: {
                "id": 3403, "name": "Z-Wave Lock", "typeName": "device",
                "proxy": "lock", "roomName": "Living", "parentId": 3402,
            },
            3404: {
                "id": 3404, "name": "Incomplete", "typeName": "device",
                "proxy": "lock", "roomName": "Living",
            },
        }
        states = {
            3402: {},
            3403: {},
            3404: {"lock_status": "locked"},
        }
        commands = FakeCommands()
        transport = SimpleNamespace(
            inventory=SimpleNamespace(items=items),
            tracked_ids=set(items),
            rest=SimpleNamespace(host="192.168.0.24"),
            state=SimpleNamespace(
                snapshot=lambda device_id: dict(states.get(device_id, {}))
            ),
            sync_device=AsyncMock(),
            connected=True,
        )
        entities = []
        await self.lock.async_setup_entry(
            None,
            SimpleNamespace(
                runtime_data=SimpleNamespace(
                    transport=transport, commands=commands
                )
            ),
            entities.extend,
        )

        self.assertEqual(len(entities), 1)
        entity = entities[0]
        self.assertEqual(entity.device_id, 3403)
        self.assertEqual(entity.registry_device_id, 3402)
        self.assertEqual(
            entity.device_info["identifiers"],
            {("control4_advanced", "192.168.0.24_3402")},
        )
        self.assertFalse(entity.available)
        with self.assertRaises(self.lock.HomeAssistantError):
            await entity.async_unlock()
        self.assertEqual(commands.calls, [])

        states[3403]["lock_status"] = "unlocked"
        self.assertFalse(entity.is_locked)
        self.assertTrue(entity.available)

        states[3403]["lock_status"] = "locking"
        self.assertTrue(entity.is_locking)
        self.assertIsNone(entity.is_locked)
        states[3403]["lock_status"] = "unlocking"
        self.assertTrue(entity.is_unlocking)
        states[3403]["lock_status"] = "jammed"
        self.assertTrue(entity.is_jammed)
        states[3403]["lock_status"] = "unlocked"

        reports = iter(("unlocked", "locked", "unlocked"))

        async def sync_device(device_id):
            states[device_id]["lock_status"] = next(reports)
            return True

        transport.sync_device.side_effect = sync_device
        with patch.object(self.lock, "_CONFIRMATION_DELAYS", (0, 0)):
            await entity.async_lock()
            self.assertTrue(entity.is_locked)
            await entity.async_unlock()
            self.assertFalse(entity.is_locked)
        self.assertEqual(transport.sync_device.await_count, 3)
        self.assertEqual(commands.calls, [("lock", 3403), ("unlock", 3403)])

        transport.sync_device.side_effect = None
        with (
            patch.object(self.lock, "_CONFIRMATION_DELAYS", (0,)),
            self.assertRaisesRegex(self.lock.HomeAssistantError, "did not confirm"),
        ):
            await entity.async_lock()
        self.assertFalse(entity.is_locked)
        self.assertEqual(commands.calls.count(("lock", 3403)), 2)

        transport.sync_device.return_value = False
        with (
            patch.object(self.lock, "_CONFIRMATION_DELAYS", (0,)),
            self.assertRaisesRegex(self.lock.HomeAssistantError, "did not confirm"),
        ):
            await entity.async_unlock()


if __name__ == "__main__":
    unittest.main()
