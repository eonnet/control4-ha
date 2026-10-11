"""Offline Home Assistant-shape checks for contact binary sensors."""

from __future__ import annotations

import importlib
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch


class FakeEntity:
    async def async_added_to_hass(self):
        pass

    async def async_will_remove_from_hass(self):
        pass


class FakeBinarySensorDeviceClass:
    DOOR = "door"
    MOTION = "motion"
    WINDOW = "window"


def ha_stub_modules():
    names = (
        "homeassistant", "homeassistant.components",
        "homeassistant.components.binary_sensor", "homeassistant.config_entries",
        "homeassistant.core", "homeassistant.helpers",
        "homeassistant.helpers.device_registry", "homeassistant.helpers.entity",
        "homeassistant.helpers.entity_platform",
    )
    modules = {name: ModuleType(name) for name in names}
    for name in ("homeassistant", "homeassistant.components", "homeassistant.helpers"):
        modules[name].__path__ = []
    modules["homeassistant.components.binary_sensor"].BinarySensorDeviceClass = (
        FakeBinarySensorDeviceClass
    )
    modules["homeassistant.components.binary_sensor"].BinarySensorEntity = type(
        "BinarySensorEntity", (), {}
    )
    modules["homeassistant.config_entries"].ConfigEntry = type("ConfigEntry", (), {})
    modules["homeassistant.core"].HomeAssistant = type("HomeAssistant", (), {})
    modules["homeassistant.helpers.device_registry"].DeviceInfo = dict
    modules["homeassistant.helpers.entity"].Entity = FakeEntity
    modules["homeassistant.helpers.entity_platform"].AddConfigEntryEntitiesCallback = object
    return modules


class BinarySensorTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.patcher = patch.dict(sys.modules, ha_stub_modules())
        cls.patcher.start()
        cls.binary_sensor = importlib.import_module(
            "custom_components.control4_advanced.binary_sensor"
        )

    @classmethod
    def tearDownClass(cls):
        for name in (
            "custom_components.control4_advanced.binary_sensor",
            "custom_components.control4_advanced.entity",
        ):
            sys.modules.pop(name, None)
        cls.patcher.stop()

    async def test_setup_assigns_each_observed_contact_device_class(self):
        items = {
            335: {"id": 335, "name": "Main Door", "typeName": "device",
                  "proxy": "contactsingle_doorcontactsensor_c4"},
            337: {"id": 337, "name": "Living Window", "typeName": "device",
                  "proxy": "contactsingle_windowcontactsensor_c4"},
            378: {"id": 378, "name": "Kid PIR", "typeName": "device",
                  "proxy": "contactsingle_motionsensor_c4"},
        }
        transport = SimpleNamespace(
            inventory=SimpleNamespace(items=items), tracked_ids=set(items),
            rest=SimpleNamespace(host="192.168.0.24"),
        )
        entities = []
        await self.binary_sensor.async_setup_entry(
            None, SimpleNamespace(runtime_data=SimpleNamespace(transport=transport)),
            entities.extend,
        )
        self.assertEqual(
            {entity.item["name"]: entity.device_class for entity in entities},
            {"Main Door": "door", "Living Window": "window", "Kid PIR": "motion"},
        )


if __name__ == "__main__":
    unittest.main()
