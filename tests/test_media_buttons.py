"""Offline Home Assistant-shape checks for stateless room transport buttons."""

from __future__ import annotations

import importlib
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


def ha_stub_modules():
    names = (
        "homeassistant", "homeassistant.components", "homeassistant.components.button",
        "homeassistant.config_entries", "homeassistant.core", "homeassistant.helpers",
        "homeassistant.helpers.device_registry", "homeassistant.helpers.entity",
        "homeassistant.helpers.entity_platform",
    )
    modules = {name: ModuleType(name) for name in names}
    for name in ("homeassistant", "homeassistant.components", "homeassistant.helpers"):
        modules[name].__path__ = []
    modules["homeassistant.components.button"].ButtonEntity = type("ButtonEntity", (FakeEntity,), {})
    modules["homeassistant.config_entries"].ConfigEntry = type("ConfigEntry", (), {})
    modules["homeassistant.core"].HomeAssistant = type("HomeAssistant", (), {})
    modules["homeassistant.helpers.device_registry"].DeviceInfo = dict
    modules["homeassistant.helpers.entity"].Entity = FakeEntity
    modules["homeassistant.helpers.entity_platform"].AddConfigEntryEntitiesCallback = object
    return modules


class MediaButtonTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.patcher = patch.dict(sys.modules, ha_stub_modules())
        cls.patcher.start()
        cls.button = importlib.import_module("custom_components.control4_advanced.button")

    @classmethod
    def tearDownClass(cls):
        for name in (
            "custom_components.control4_advanced.button",
            "custom_components.control4_advanced.entity",
        ):
            sys.modules.pop(name, None)
        cls.patcher.stop()

    async def asyncSetUp(self):
        self.state = {
            22: {"is_on": True},
            2646: {
                "current_humidity": 43.0,
                "humidify_setpoint": 35.0,
                "dehumidify_setpoint": 55.0,
            },
        }
        self.items = {
            22: {"id": 22, "name": "Living", "typeName": "room", "proxy": "roomdevice", "roomName": "Living"},
            23: {"id": 23, "name": "Bedroom", "typeName": "room", "proxy": "roomdevice"},
            2646: {"id": 2646, "name": "Living AC", "typeName": "device", "proxy": "thermostatV2"},
        }
        self.commands = SimpleNamespace(
            supports_room_transport=lambda room_id, command: room_id == 22 and command in {
                "SKIP_REV", "PLAY", "PAUSE", "STOP", "SKIP_FWD",
            },
            room_transport=AsyncMock(),
            supports_parameterless=lambda device_id, command: False,
            humidity_step=AsyncMock(),
        )
        self.live_variables = [{"varName": "POWER_STATE", "value": 1}]
        self.get_variables = AsyncMock(side_effect=lambda room_id: self.live_variables)
        self.transport = SimpleNamespace(
            rest=SimpleNamespace(host="192.168.0.24", get_variables=self.get_variables),
            inventory=SimpleNamespace(items=self.items),
            state=SimpleNamespace(snapshot=lambda room_id: self.state.get(room_id, {}).copy()),
            events=SimpleNamespace(subscribe=lambda callback: lambda: None),
            subscribe_connection=lambda callback: lambda: None,
            connected=True,
            tracked_ids=set(self.items),
            sync_device=AsyncMock(),
        )
        self.runtime = SimpleNamespace(
            transport=self.transport, commands=self.commands,
            media_coordinators={22: object(), 23: object()},
        )

    async def test_only_advertised_room_commands_create_device_buttons(self):
        entities = []
        await self.button.async_setup_entry(None, SimpleNamespace(runtime_data=self.runtime), entities.extend)
        self.assertEqual(
            [entity.name for entity in entities],
            ["Previous", "Play", "Pause", "Stop", "Next"],
        )
        self.assertEqual([entity.unique_id for entity in entities], [
            "192.168.0.24_22_media_skip_rev",
            "192.168.0.24_22_media_play", "192.168.0.24_22_media_pause",
            "192.168.0.24_22_media_stop",
            "192.168.0.24_22_media_skip_fwd",
        ])
        for entity in entities:
            self.assertTrue(entity.available)
            self.assertEqual(entity.device_info["identifiers"], {
                ("control4_advanced", "192.168.0.24_22")
            })
            self.assertEqual(entity.device_info["suggested_area"], "Living")
            self.assertTrue(entity._attr_has_entity_name)
            self.assertFalse(entity._attr_should_poll)

    async def test_press_does_not_change_power_or_claim_playback_state(self):
        button = self.button.Control4RoomPlaybackButton(
            self.runtime, self.items[22], "PAUSE", "Pause", "mdi:pause"
        )
        await button.async_press()
        self.get_variables.assert_awaited_once_with(22)
        self.commands.room_transport.assert_awaited_once_with(22, "PAUSE")
        self.assertEqual(self.state[22], {"is_on": True})

    async def test_humidity_step_buttons_share_thermostat_device_and_resync(self):
        supported = {
            "INC_SETPOINT_HUMIDIFY", "DEC_SETPOINT_HUMIDIFY",
            "INC_SETPOINT_DEHUMIDIFY", "DEC_SETPOINT_DEHUMIDIFY",
        }
        self.commands.supports_parameterless = (
            lambda device_id, command: device_id == 2646 and command in supported
        )
        entities = []
        await self.button.async_setup_entry(
            None, SimpleNamespace(runtime_data=self.runtime), entities.extend
        )
        humidity = [
            entity for entity in entities
            if isinstance(entity, self.button.Control4HumidityStepButton)
        ]
        self.assertEqual([entity.name for entity in humidity], [
            "Humidifier −1%", "Humidifier +1%",
            "Dehumidifier −1%", "Dehumidifier +1%",
        ])
        self.assertEqual([entity.unique_id for entity in humidity], [
            "192.168.0.24_2646_humidify_decrease",
            "192.168.0.24_2646_humidify_increase",
            "192.168.0.24_2646_dehumidify_decrease",
            "192.168.0.24_2646_dehumidify_increase",
        ])
        for entity in humidity:
            self.assertEqual(entity.device_info["identifiers"], {
                ("control4_advanced", "192.168.0.24_2646")
            })
            self.assertTrue(entity.available)
        self.assertEqual(
            humidity[0].extra_state_attributes, {"current_setpoint": 35.0}
        )
        self.assertEqual(
            humidity[2].extra_state_attributes, {"current_setpoint": 55.0}
        )
        self.state[2646].update({
            "humidify_setpoint": 0.0, "dehumidify_setpoint": 100.0,
        })
        self.assertFalse(humidity[0].available)
        self.assertTrue(humidity[1].available)
        self.assertTrue(humidity[2].available)
        self.assertFalse(humidity[3].available)
        self.state[2646].update({
            "humidify_setpoint": 35.0, "dehumidify_setpoint": 55.0,
        })
        await humidity[1].async_press()
        self.commands.humidity_step.assert_awaited_once_with(
            2646, "HUMIDIFY", increase=True
        )
        self.transport.sync_device.assert_awaited_once_with(2646)

    async def test_stale_on_snapshot_cannot_send_if_live_room_is_off(self):
        button = self.button.Control4RoomPlaybackButton(
            self.runtime, self.items[22], "PLAY", "Play", "mdi:play"
        )
        self.assertTrue(button.available)
        self.live_variables = [{"varName": "POWER_STATE", "value": 0}]
        with self.assertRaises(UnsupportedCommand):
            await button.async_press()
        self.commands.room_transport.assert_not_awaited()

        self.live_variables = []
        with self.assertRaises(UnsupportedCommand):
            await button.async_press()
        self.commands.room_transport.assert_not_awaited()

        self.get_variables.side_effect = RuntimeError("read failed")
        with self.assertRaises(RuntimeError):
            await button.async_press()
        self.commands.room_transport.assert_not_awaited()

    async def test_offline_or_powered_off_button_never_sends_command(self):
        button = self.button.Control4RoomPlaybackButton(
            self.runtime, self.items[22], "PLAY", "Play", "mdi:play"
        )
        for connected, is_on in ((True, False), (False, True)):
            self.transport.connected = connected
            self.state[22]["is_on"] = is_on
            self.assertFalse(button.available)
            with self.assertRaises(UnsupportedCommand):
                await button.async_press()
        self.commands.room_transport.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
