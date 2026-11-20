"""Offline Home Assistant-shape checks for Control4 numeric sensors."""

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


class FakeSensorDeviceClass:
    BATTERY = "battery"
    HUMIDITY = "humidity"
    ILLUMINANCE = "illuminance"
    TEMPERATURE = "temperature"


class FakeSensorStateClass:
    MEASUREMENT = "measurement"


def ha_stub_modules():
    names = (
        "homeassistant", "homeassistant.components",
        "homeassistant.components.sensor", "homeassistant.config_entries",
        "homeassistant.const", "homeassistant.core", "homeassistant.helpers",
        "homeassistant.helpers.device_registry", "homeassistant.helpers.entity",
        "homeassistant.helpers.entity_platform",
    )
    modules = {name: ModuleType(name) for name in names}
    for name in ("homeassistant", "homeassistant.components", "homeassistant.helpers"):
        modules[name].__path__ = []
    sensor = modules["homeassistant.components.sensor"]
    sensor.SensorDeviceClass = FakeSensorDeviceClass
    sensor.SensorEntity = type("SensorEntity", (), {})
    sensor.SensorStateClass = FakeSensorStateClass
    modules["homeassistant.config_entries"].ConfigEntry = type("ConfigEntry", (), {})
    modules["homeassistant.const"].LIGHT_LUX = "lx"
    modules["homeassistant.const"].PERCENTAGE = "%"
    modules["homeassistant.const"].UnitOfTemperature = SimpleNamespace(CELSIUS="°C")
    modules["homeassistant.core"].HomeAssistant = type("HomeAssistant", (), {})
    modules["homeassistant.helpers.device_registry"].DeviceInfo = dict
    modules["homeassistant.helpers.entity"].Entity = FakeEntity
    modules[
        "homeassistant.helpers.entity_platform"
    ].AddConfigEntryEntitiesCallback = object
    return modules


class SensorTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.patcher = patch.dict(sys.modules, ha_stub_modules())
        cls.patcher.start()
        cls.sensor = importlib.import_module(
            "custom_components.control4_advanced.sensor"
        )

    @classmethod
    def tearDownClass(cls):
        for name in (
            "custom_components.control4_advanced.sensor",
            "custom_components.control4_advanced.entity",
        ):
            sys.modules.pop(name, None)
        cls.patcher.stop()

    async def test_setup_creates_only_director_advertised_metrics(self):
        items = {
            2144: {
                "id": 2144, "name": "Heiman HT", "typeName": "device",
                "proxy": "zwave-common-sensor", "roomName": "Bedroom",
            },
            335: {
                "id": 335, "name": "Main Door", "typeName": "device",
                "proxy": "contactsingle_doorcontactsensor_c4",
            },
        }
        states = {2144: {
            "is_connected": True,
            "zwave_sensor_battery": 82.0,
            "zwave_sensor_temperature_0": 23.5,
            "zwave_sensor_humidity_0": 47.0,
            "zwave_sensor_illumination_1": 125.0,
            "zwave_sensor_variables": (
                "Battery Level", "Local Temperature 0",
                "Humidity percentage 0", "Illumination 1",
            ),
        }}
        transport = SimpleNamespace(
            inventory=SimpleNamespace(items=items),
            tracked_ids=set(items),
            rest=SimpleNamespace(host="192.168.0.24"),
            state=SimpleNamespace(
                snapshot=lambda device_id: dict(states.get(device_id, {}))
            ),
            connected=True,
        )
        entities = []
        await self.sensor.async_setup_entry(
            None,
            SimpleNamespace(runtime_data=SimpleNamespace(transport=transport)),
            entities.extend,
        )

        self.assertEqual([entity._attr_name for entity in entities], [
            "Heiman HT Battery", "Heiman HT Temperature",
            "Heiman HT Humidity", "Heiman HT Illumination 1",
        ])
        self.assertEqual([entity._attr_unique_id for entity in entities], [
            "192.168.0.24_2144_battery", "192.168.0.24_2144_temperature_0",
            "192.168.0.24_2144_humidity_0", "192.168.0.24_2144_illumination_1",
        ])
        self.assertEqual([entity.native_value for entity in entities], [
            82.0, 23.5, 47.0, 125.0,
        ])
        self.assertEqual(
            [getattr(entity, "_attr_device_class", None) for entity in entities],
            ["battery", "temperature", "humidity", "illuminance"],
        )
        self.assertTrue(all(entity.available for entity in entities))

    async def test_sleeping_device_keeps_valid_value_available(self):
        item = {"id": 2140, "name": "Kid Window", "proxy": "zwave-common-sensor"}
        state = {
            "is_connected": False,
            "zwave_sensor_battery": 82.0,
            "zwave_sensor_variables": ("Battery Level",),
        }
        transport = SimpleNamespace(
            inventory=SimpleNamespace(items={2140: item}), tracked_ids={2140},
            rest=SimpleNamespace(host="192.168.0.24"),
            state=SimpleNamespace(snapshot=lambda _device_id: dict(state)),
            connected=True,
        )
        entities = []
        await self.sensor.async_setup_entry(
            None,
            SimpleNamespace(runtime_data=SimpleNamespace(transport=transport)),
            entities.extend,
        )
        self.assertEqual(len(entities), 1)
        self.assertEqual(entities[0].native_value, 82.0)
        self.assertTrue(entities[0].available)

        state["zwave_sensor_battery"] = None
        self.assertFalse(entities[0].available)

    async def test_security_sensor_wins_and_joins_matching_pir_device(self):
        items = {
            354: {
                "id": 354, "name": "Kitchen PIR", "typeName": "device",
                "proxy": "contactsingle_motionsensor_c4", "roomName": "Kitchen",
            },
            378: {
                "id": 378, "name": "Kid PIR", "typeName": "device",
                "proxy": "contactsingle_motionsensor_c4", "roomName": "Kid",
            },
            2090: {
                "id": 2090, "name": "Kitchen PIR", "typeName": "device",
                "proxy": "zwave-common-sensor", "roomName": "Kitchen",
            },
            3416: {
                "id": 3416, "name": "Kid PIR", "typeName": "device",
                "proxy": "zwave-security-sensor", "roomName": "Kid",
            },
            3423: {
                "id": 3423, "name": "Kitchen PIR", "typeName": "device",
                "proxy": "zwave-security-sensor", "roomName": "Kitchen",
            },
        }
        states = {
            # Even if the legacy common-proxy read fails during setup, its
            # established unique IDs remain stable while security data wins.
            2090: {},
            3416: {
                "zwave_sensor_battery": 90.0,
                "zwave_sensor_temperature": 25.1,
                "zwave_sensor_illumination": None,
                "zwave_sensor_variables": (
                    "BatteryPercent", "TemperatureC", "IlluminanceLux",
                ),
            },
            3423: {
                "zwave_sensor_battery": 100.0,
                "zwave_sensor_temperature": 26.7,
                "zwave_sensor_illumination": 50.0,
                "zwave_sensor_variables": (
                    "BatteryPercent", "TemperatureC", "IlluminanceLux",
                ),
            },
        }
        transport = SimpleNamespace(
            inventory=SimpleNamespace(items=items),
            tracked_ids=set(items),
            rest=SimpleNamespace(host="192.168.0.24"),
            state=SimpleNamespace(
                snapshot=lambda device_id: dict(states.get(device_id, {}))
            ),
            connected=True,
        )
        entities = []
        await self.sensor.async_setup_entry(
            None,
            SimpleNamespace(runtime_data=SimpleNamespace(transport=transport)),
            entities.extend,
        )

        by_unique_id = {entity._attr_unique_id: entity for entity in entities}
        self.assertEqual(set(by_unique_id), {
            "192.168.0.24_2090_battery",
            "192.168.0.24_2090_temperature_0",
            "192.168.0.24_2090_illumination_0",
            "192.168.0.24_3416_battery",
            "192.168.0.24_3416_temperature",
            "192.168.0.24_3416_illumination",
        })
        self.assertEqual(
            by_unique_id["192.168.0.24_2090_temperature_0"].device_id,
            3423,
        )
        self.assertEqual(
            by_unique_id["192.168.0.24_2090_temperature_0"].native_value,
            26.7,
        )
        self.assertEqual(
            by_unique_id["192.168.0.24_2090_illumination_0"].native_value,
            50.0,
        )
        self.assertEqual(
            by_unique_id["192.168.0.24_3416_temperature"].native_value,
            25.1,
        )
        self.assertFalse(
            by_unique_id["192.168.0.24_3416_illumination"].available
        )
        self.assertEqual(
            by_unique_id["192.168.0.24_2090_battery"].device_info["identifiers"],
            {("control4_advanced", "192.168.0.24_354")},
        )
        self.assertEqual(
            by_unique_id["192.168.0.24_3416_battery"].device_info["identifiers"],
            {("control4_advanced", "192.168.0.24_378")},
        )


if __name__ == "__main__":
    unittest.main()
