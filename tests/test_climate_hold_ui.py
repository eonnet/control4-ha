"""Offline HA-shape checks for thermostat presets and hold selects."""

from __future__ import annotations

from enum import IntFlag, StrEnum
import importlib
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

from custom_components.control4_advanced.transport.playlists import (
    RoomTidalCatalog,
    TidalPlaylist,
)
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
        pass


class FakeSelectEntity(FakeEntity):
    @property
    def options(self):
        return self._attr_options


class FakeClimateEntity(FakeEntity):
    pass


class FakeFeatures(IntFlag):
    TARGET_TEMPERATURE = 1
    PRESET_MODE = 2
    FAN_MODE = 4


class FakeHVACMode(StrEnum):
    OFF = "off"
    HEAT = "heat"
    COOL = "cool"
    AUTO = "auto"


class FakeCommands:
    def __init__(self):
        self.sent = []
        self.choices_by_device = {
            2646: {
                ("SET_MODE_HVAC", "MODE"): ["Off", "Heat", "Cool", "Auto"],
                ("SET_PRESET", "NAME"): ["Morning", "Away", "Off"],
                ("SET_MODE_HOLD", "MODE"): ["Off", "2 Hours", "Until Next", "Permanent"],
                ("SET_MODE_FAN", "MODE"): ["Auto", "Low", "Medium", "High"],
            },
            2811: {
                ("SET_MODE_HVAC", "MODE"): ["Off", "Heat", "Cool", "Auto"],
                ("SET_MODE_HOLD", "MODE"): ["Off", "2 Hours", "Until Next", "Permanent"],
                ("SET_MODE_FAN", "MODE"): ["Auto", "Low", "Medium", "High"],
            },
        }

    def supports(self, device_id, command):
        if command == "SET_SETPOINT_SINGLE" and device_id == 404:
            return True
        if command == "SET_SETPOINT_HEAT":
            return device_id in self.choices_by_device
        return any(key[0] == command for key in self.choices_by_device[device_id])

    def choices(self, device_id, command, parameter):
        return self.choices_by_device[device_id][command, parameter]

    async def preset(self, device_id, option):
        self.sent.append(("preset", device_id, option))

    async def hold_mode(self, device_id, option):
        self.sent.append(("hold", device_id, option))

    async def tidal_playlist(self, protocol_id, room_id, playlist):
        self.sent.append(("tidal", protocol_id, room_id, playlist.playlist_id))

    async def room_source(self, room_id, source):
        self.sent.append(("source", room_id, source.device_id))

    async def fan_mode(self, device_id, option):
        self.sent.append(("fan", device_id, option))

    async def humidity_mode(self, device_id, option):
        self.sent.append(("humidity_mode", device_id, option))

    def range(self, device_id, command, parameter):
        if (device_id, command, parameter) == (404, "SET_SETPOINT_SINGLE", "CELSIUS"):
            return (8.0, 30.0)
        return (4.0, 31.0)

    async def setpoint(self, device_id, kind, temperature, scale):
        self.sent.append(("setpoint", device_id, kind, temperature, scale))


def ha_stub_modules():
    names = (
        "homeassistant", "homeassistant.components", "homeassistant.components.climate",
        "homeassistant.components.climate.const", "homeassistant.components.select",
        "homeassistant.config_entries", "homeassistant.const", "homeassistant.core",
        "homeassistant.exceptions", "homeassistant.helpers",
        "homeassistant.helpers.device_registry", "homeassistant.helpers.entity",
        "homeassistant.helpers.entity_platform",
    )
    modules = {name: ModuleType(name) for name in names}
    for name in ("homeassistant", "homeassistant.components", "homeassistant.helpers"):
        modules[name].__path__ = []
    modules["homeassistant.components.climate"].__path__ = []
    modules["homeassistant.components.climate"].ClimateEntity = FakeClimateEntity
    modules["homeassistant.components.climate"].ClimateEntityFeature = FakeFeatures
    modules["homeassistant.components.climate"].HVACMode = FakeHVACMode
    modules["homeassistant.components.climate.const"].ATTR_HVAC_MODE = "hvac_mode"
    modules["homeassistant.components.select"].SelectEntity = FakeSelectEntity
    modules["homeassistant.config_entries"].ConfigEntry = type("ConfigEntry", (), {})
    modules["homeassistant.const"].ATTR_TEMPERATURE = "temperature"
    modules["homeassistant.const"].UnitOfTemperature = SimpleNamespace(CELSIUS="°C")
    modules["homeassistant.core"].HomeAssistant = type("HomeAssistant", (), {})
    modules["homeassistant.exceptions"].HomeAssistantError = type(
        "HomeAssistantError", (Exception,), {}
    )
    modules["homeassistant.helpers.device_registry"].DeviceInfo = dict
    modules["homeassistant.helpers.entity"].Entity = FakeEntity
    modules["homeassistant.helpers.entity_platform"].AddConfigEntryEntitiesCallback = object
    return modules


class ClimateHoldUITests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.patcher = patch.dict(sys.modules, ha_stub_modules())
        cls.patcher.start()
        cls.climate = importlib.import_module("custom_components.control4_advanced.climate")
        cls.select = importlib.import_module("custom_components.control4_advanced.select")
        cls.error = sys.modules["homeassistant.exceptions"].HomeAssistantError

    @classmethod
    def tearDownClass(cls):
        for name in (
            "custom_components.control4_advanced.climate",
            "custom_components.control4_advanced.select",
            "custom_components.control4_advanced.entity",
        ):
            sys.modules.pop(name, None)
        cls.patcher.stop()

    async def asyncSetUp(self):
        self.states = {
            2646: {"scale": "CELSIUS", "hold_mode": "Off", "preset_mode": "Off", "fan_mode": "Auto"},
            2811: {"scale": "CELSIUS", "hold_mode": "Until Next", "fan_mode": "Low"},
        }
        self.items = {
            device_id: {"id": device_id, "name": name, "proxy": "thermostatV2"}
            for device_id, name in ((2646, "Living AC"), (2811, "Kid AC"))
        }
        self.commands = FakeCommands()
        async def sync_device(device_id):
            self.synced_device_ids.append(device_id)

        self.synced_device_ids = []
        transport = SimpleNamespace(
            rest=SimpleNamespace(host="192.168.0.24"),
            inventory=SimpleNamespace(items=self.items),
            tracked_ids=set(self.items),
            state=SimpleNamespace(snapshot=lambda device_id: self.states[device_id]),
            connected=True,
            sync_device=sync_device,
        )
        self.runtime = SimpleNamespace(
            transport=transport, commands=self.commands, tidal_catalogs={},
            tunein_catalogs={},
        )
        self.entry = SimpleNamespace(runtime_data=self.runtime)

    async def test_only_living_has_native_preset_but_both_have_hold_select(self):
        climates = []
        selects = []
        await self.climate.async_setup_entry(None, self.entry, climates.extend)
        await self.select.async_setup_entry(None, self.entry, selects.extend)
        self.assertEqual([entity.device_id for entity in climates], [2646, 2811])
        self.assertEqual([entity.device_id for entity in selects], [2646, 2811])
        living, kid = climates
        self.assertEqual(living.preset_modes, ["Morning", "Away", "Off"])
        self.assertEqual(living.preset_mode, "Off")
        self.assertTrue(living._attr_supported_features & FakeFeatures.PRESET_MODE)
        self.assertEqual(kid.preset_modes, [])
        self.assertFalse(kid._attr_supported_features & FakeFeatures.PRESET_MODE)
        self.assertEqual(kid.extra_state_attributes, {"control4_hold_mode": "Until Next"})
        self.assertEqual(living.fan_modes, ["Auto", "Low", "Medium", "High"])
        self.assertEqual(living.fan_mode, "Auto")
        self.assertTrue(living._attr_supported_features & FakeFeatures.FAN_MODE)
        self.assertEqual(kid.fan_modes, ["Auto", "Low", "Medium", "High"])
        self.assertEqual(kid.fan_mode, "Low")
        self.assertTrue(kid._attr_supported_features & FakeFeatures.FAN_MODE)
        self.assertEqual(selects[0].device_info["identifiers"], living.device_info["identifiers"])
        self.assertEqual(selects[0].unique_id, "192.168.0.24_2646_hold_mode")
        self.assertEqual(selects[0].device_info["name"], "Living AC")

        self.states[2646].update({"preset_mode": "Away", "hold_mode": "Permanent", "fan_mode": "Medium"})
        self.assertEqual(living.preset_mode, "Away")
        self.assertEqual(living.fan_mode, "Medium")
        self.assertEqual(living.extra_state_attributes, {"control4_hold_mode": "Permanent"})
        self.assertEqual(selects[0].current_option, "Permanent")

    async def test_ui_methods_reject_unknown_options_before_command(self):
        living = self.climate.Control4Climate(self.runtime, self.items[2646])
        kid = self.climate.Control4Climate(self.runtime, self.items[2811])
        hold = self.select.Control4HoldSelect(
            self.runtime, self.items[2646], ["Off", "2 Hours", "Until Next", "Permanent"]
        )
        with self.assertRaises(self.error):
            await living.async_set_preset_mode("Unknown")
        with self.assertRaises(self.error):
            await living.async_set_fan_mode("Turbo")
        with self.assertRaises(self.error):
            await kid.async_set_fan_mode("On")
        with self.assertRaises(self.error):
            await hold.async_select_option("Unknown")
        self.assertEqual(self.commands.sent, [])
        await living.async_set_preset_mode("Away")
        await living.async_set_fan_mode("Medium")
        await kid.async_set_fan_mode("Auto")
        await hold.async_select_option("Permanent")
        self.assertEqual(self.commands.sent, [
            ("preset", 2646, "Away"), ("fan", 2646, "Medium"),
            ("fan", 2811, "Auto"),
            ("hold", 2646, "Permanent"),
        ])

    async def test_humidity_is_on_climate_and_mode_select_uses_same_device(self):
        self.commands.choices_by_device[2646][
            ("SET_MODE_HUMIDITY", "MODE")
        ] = ["Off", "Humidify", "Dehumidify", "Auto"]
        self.states[2646].update({
            "current_humidity": 43.0,
            "humidity_mode": "Off",
            "humidity_state": "Off",
            "humidify_setpoint": 35.0,
            "dehumidify_setpoint": 55.0,
        })
        living = self.climate.Control4Climate(self.runtime, self.items[2646])
        self.assertEqual(living.current_humidity, 43.0)
        self.assertEqual(living.extra_state_attributes, {
            "control4_hold_mode": "Off",
            "control4_humidity_mode": "Off",
            "control4_humidity_state": "Off",
            "control4_humidify_setpoint": 35.0,
            "control4_dehumidify_setpoint": 55.0,
        })

        selects = []
        await self.select.async_setup_entry(None, self.entry, selects.extend)
        humidity = next(
            entity for entity in selects
            if isinstance(entity, self.select.Control4HumidityModeSelect)
        )
        self.assertEqual(humidity.options, ["Off", "Humidify", "Dehumidify", "Auto"])
        self.assertEqual(humidity.current_option, "Off")
        self.assertEqual(humidity.device_info["identifiers"], living.device_info["identifiers"])
        self.assertEqual(
            humidity.unique_id, "192.168.0.24_2646_humidity_mode"
        )
        with self.assertRaises(self.error):
            await humidity.async_select_option("Dry")
        await humidity.async_select_option("Auto")
        self.assertEqual(self.commands.sent, [("humidity_mode", 2646, "Auto")])
        self.assertEqual(self.synced_device_ids, [2646])

    async def test_tidal_account_and_playlist_selects_are_linked_to_room(self):
        self.items[22] = {
            "id": 22,
            "name": "Living",
            "proxy": "roomdevice",
            "typeName": "room",
            "roomName": "Living",
        }
        self.states[22] = {"is_on": True, "selected_source_id": 94}
        tidal_playlist = TidalPlaylist(
            "TIDAL", "Evening Mix", "01234567-89ab-cdef-0123-456789abcdef"
        )
        juliane_playlist = TidalPlaylist(
            "Juliane", "Morning Mix", "11234567-89ab-cdef-0123-456789abcdef"
        )
        self.runtime.tidal_catalogs = {
            22: (
                RoomTidalCatalog(22, 94, 93, "TIDAL", (tidal_playlist,)),
                RoomTidalCatalog(22, 1366, 1365, "Juliane", (juliane_playlist,)),
            )
        }
        self.runtime.media_sources = {
            22: (
                RoomSource(94, "listen", "TIDAL", "AUDIO_SELECTION", "Listen: TIDAL"),
                RoomSource(
                    1366, "listen", "Juliane", "AUDIO_SELECTION", "Listen: Juliane"
                ),
            )
        }

        async def sync_device(device_id):
            if self.commands.sent[-1] == ("source", 22, 1366):
                self.states[device_id]["selected_source_id"] = 1366

        self.runtime.transport.sync_device = sync_device
        selects = []
        await self.select.async_setup_entry(None, self.entry, selects.extend)
        account = next(entity for entity in selects if isinstance(
            entity, self.select.Control4TidalAccountSelect
        ))
        tidal = next(entity for entity in selects if isinstance(
            entity, self.select.Control4TidalPlaylistSelect
        ))
        self.assertEqual(account.name, "Living TIDAL account")
        self.assertEqual(account.unique_id, "192.168.0.24_22_tidal_account")
        self.assertEqual(account.options, ["TIDAL", "Juliane"])
        self.assertEqual(account.current_option, "TIDAL")
        self.assertEqual(tidal.name, "Living TIDAL playlist")
        self.assertEqual(tidal.unique_id, "192.168.0.24_22_tidal_playlist")
        self.assertEqual(tidal.options, ["Evening Mix"])
        self.assertIsNone(tidal.current_option)
        self.assertEqual(
            tidal.device_info["identifiers"],
            {("control4_advanced", "192.168.0.24_22")},
        )
        await account.async_select_option("Juliane")
        self.assertEqual(account.current_option, "Juliane")
        self.assertEqual(tidal.options, ["Morning Mix"])
        await tidal.async_select_option("Morning Mix")
        self.assertEqual(self.commands.sent, [
            ("source", 22, 1366),
            ("tidal", 1365, 22, "11234567-89ab-cdef-0123-456789abcdef"),
        ])
        with self.assertRaises(self.error):
            await account.async_select_option("Unknown")
        with self.assertRaises(self.error):
            await tidal.async_select_option("Unknown")

    async def test_tidal_account_select_exists_without_playlists(self):
        self.items[22] = {
            "id": 22,
            "name": "Living",
            "proxy": "roomdevice",
            "typeName": "room",
            "roomName": "Living",
        }
        self.states[22] = {"is_on": True, "selected_source_id": 94}
        self.runtime.tidal_catalogs = {
            22: (RoomTidalCatalog(22, 94, 93, "Miki", ()),)
        }
        self.runtime.media_sources = {
            22: (
                RoomSource(94, "listen", "Miki", "AUDIO_SELECTION", "Listen: Miki"),
            )
        }
        selects = []
        await self.select.async_setup_entry(None, self.entry, selects.extend)
        accounts = [
            entity
            for entity in selects
            if isinstance(entity, self.select.Control4TidalAccountSelect)
        ]
        playlists = [
            entity
            for entity in selects
            if isinstance(entity, self.select.Control4TidalPlaylistSelect)
        ]
        self.assertEqual(accounts[0].options, ["Miki"])
        self.assertEqual(playlists, [])

    async def test_advertised_choices_allow_unknown_initial_preset_and_fan(self):
        self.states[2646].pop("preset_mode")
        self.states[2646].pop("fan_mode")
        self.states[2811].pop("hold_mode")
        self.states[2811].pop("fan_mode")
        climates = []
        selects = []
        await self.climate.async_setup_entry(None, self.entry, climates.extend)
        await self.select.async_setup_entry(None, self.entry, selects.extend)
        self.assertEqual(climates[0].preset_modes, ["Morning", "Away", "Off"])
        self.assertIsNone(climates[0].preset_mode)
        self.assertTrue(climates[0]._attr_supported_features & FakeFeatures.PRESET_MODE)
        self.assertEqual(climates[0].fan_modes, ["Auto", "Low", "Medium", "High"])
        self.assertIsNone(climates[0].fan_mode)
        self.assertTrue(climates[0]._attr_supported_features & FakeFeatures.FAN_MODE)
        self.assertEqual(climates[1].preset_modes, [])
        self.assertIsNone(climates[1].fan_mode)
        self.assertTrue(climates[1]._attr_supported_features & FakeFeatures.FAN_MODE)
        self.assertEqual([entity.device_id for entity in selects], [2646])

    async def test_gree_choices_enable_fan_and_preset_without_initial_values(self):
        self.items[404] = {"id": 404, "name": "GREE", "proxy": "thermostatV2"}
        self.runtime.transport.tracked_ids.add(404)
        self.states[404] = {"scale": "CELSIUS", "hvac_mode": "HEAT"}
        self.commands.choices_by_device[404] = {
            ("SET_MODE_HVAC", "MODE"): ["Off", "Heat", "Cool", "Auto"],
            ("SET_MODE_FAN", "MODE"): ["LOW", "MEDIUM", "HIGH", "AUTO", "STRONG"],
            ("SET_PRESET", "NAME"): [
                "Off", "Heat Auto", "Cool Auto", "Night Heat", "Away Cool", "Away Heat", "Home"
            ],
        }
        climates = []
        selects = []
        await self.climate.async_setup_entry(None, self.entry, climates.extend)
        await self.select.async_setup_entry(None, self.entry, selects.extend)
        gree = next(entity for entity in climates if entity.device_id == 404)
        self.assertEqual(gree.fan_modes, ["LOW", "MEDIUM", "HIGH", "AUTO", "STRONG"])
        self.assertEqual(gree.preset_modes, [
            "Off", "Heat Auto", "Cool Auto", "Night Heat", "Away Cool", "Away Heat", "Home"
        ])
        self.assertIsNone(gree.fan_mode)
        self.assertIsNone(gree.preset_mode)
        self.assertTrue(gree._attr_supported_features & FakeFeatures.FAN_MODE)
        self.assertTrue(gree._attr_supported_features & FakeFeatures.PRESET_MODE)
        self.assertNotIn(404, [entity.device_id for entity in selects])
        self.states[404].update({"fan_mode": "LOW", "preset_mode": "Night Heat"})
        self.assertEqual(gree.fan_mode, "LOW")
        self.assertEqual(gree.preset_mode, "Night Heat")
        self.states[404]["preset_mode"] = None
        self.assertIsNone(gree.preset_mode)
        await gree.async_set_fan_mode("STRONG")
        await gree.async_set_preset_mode("Home")
        self.assertEqual(self.commands.sent, [("fan", 404, "STRONG"), ("preset", 404, "Home")])

    async def test_gree_single_setpoint_is_used_in_heat_and_cool(self):
        self.items[404] = {"id": 404, "name": "GREE", "proxy": "thermostatV2"}
        self.runtime.transport.tracked_ids.add(404)
        self.commands.choices_by_device[404] = {
            ("SET_MODE_HVAC", "MODE"): ["Off", "Heat", "Cool", "Auto"],
        }
        self.states[404] = {
            "scale": "CELSIUS", "hvac_mode": "COOL", "setpoint_profile": "SINGLE",
            "target_temperature_c": 22.0, "heat_setpoint_c": 0.0,
            "cool_setpoint_c": 32.0,
        }
        gree = self.climate.Control4Climate(self.runtime, self.items[404])
        self.assertEqual(gree.target_temperature, 22.0)
        self.assertEqual((gree.min_temp, gree.max_temp), (8.0, 30.0))
        self.states[404].update({"target_temperature_c": 21.0})
        self.assertEqual(gree.target_temperature, 21.0)
        await gree.async_set_temperature(temperature=23)
        self.states[404].update({"hvac_mode": "HEAT", "target_temperature_c": 20.0})
        self.assertEqual(gree.target_temperature, 20.0)
        await gree.async_set_temperature(temperature=24)
        self.assertEqual(self.commands.sent, [
            ("setpoint", 404, "SINGLE", 23, "CELSIUS"),
            ("setpoint", 404, "SINGLE", 24, "CELSIUS"),
        ])

    async def test_other_thermostat_keeps_heat_setpoint_routing(self):
        self.states[2646].update({
            "hvac_mode": "HEAT", "heat_setpoint_c": 25.0,
            "target_temperature_c": 22.0,
        })
        living = self.climate.Control4Climate(self.runtime, self.items[2646])
        self.assertEqual(living.target_temperature, 25.0)
        await living.async_set_temperature(temperature=26)
        self.assertEqual(self.commands.sent, [
            ("setpoint", 2646, "HEAT", 26, "CELSIUS"),
        ])


if __name__ == "__main__":
    unittest.main()
