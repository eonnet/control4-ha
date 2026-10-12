from __future__ import annotations

import unittest
from types import SimpleNamespace

from custom_components.control4_advanced import _discover_media_room_ids
from custom_components.control4_advanced.selection import (
    command_metadata_ids, is_candidate_item, supported_binary_sensor_ids, supported_light_ids,
    registered_media_room_ids, supported_media_room_ids, supported_relay_ids,
)


class SelectionTests(unittest.TestCase):
    def test_light_requires_power_capability(self) -> None:
        item = {"id": 2726, "typeName": "device", "proxy": "light_v2", "capabilities": {"on_off": True, "dimmer": True}}
        self.assertTrue(is_candidate_item(item))
        self.assertTrue(is_candidate_item({**item, "capabilities": {"on_off": True, "dimmer": False}}))
        self.assertFalse(is_candidate_item({**item, "capabilities": {"on_off": True}}))

    def test_thermostat_requires_heating_or_cooling(self) -> None:
        item = {"id": 2646, "typeName": "device", "proxy": "thermostatV2", "capabilities": {"can_heat": True}}
        self.assertTrue(is_candidate_item(item))
        self.assertFalse(is_candidate_item({**item, "capabilities": {"has_temperature": True}}))

    def test_supported_lights_do_not_depend_on_initial_state(self) -> None:
        items = {
            1: {"id": 1, "typeName": "device", "proxy": "light_v2", "capabilities": {"on_off": True, "dimmer": True}},
            2: {"id": 2, "typeName": "device", "proxy": "light_v2", "capabilities": {"on_off": True, "dimmer": False}},
            3: {"id": 3, "typeName": "device", "proxy": "light_v2", "capabilities": {"on_off": True, "dimmer": True}},
            4: {"id": 4, "typeName": "device", "proxy": "light_v2", "capabilities": {"on_off": True}},
        }
        commands = {1: {"ON", "OFF"}, 2: {"ON", "OFF"}, 3: {"ON"}, 4: {"ON", "OFF"}}
        self.assertEqual(
            supported_light_ids(items, items, lambda device_id, command: command in commands[device_id]),
            [1, 2],
        )

    def test_only_observed_relays_with_commands_and_initial_state_are_switches(self) -> None:
        items = {
            579: {"id": 579, "typeName": "device", "proxy": "relaysingle_radiantfloor_c4", "capabilities": {}},
            450: {"id": 450, "typeName": "device", "proxy": "relaysingle_radiantfloor_c4", "capabilities": {}},
            431: {"id": 431, "typeName": "device", "proxy": "relaysingle_relay_c4", "capabilities": {}},
            362: {"id": 362, "typeName": "device", "proxy": "relaysingle_relay_c4", "capabilities": {}},
            900: {"id": 900, "typeName": "device", "proxy": "cardaccess_wirelessrelay", "capabilities": {}},
        }
        self.assertTrue(is_candidate_item(items[579]))
        self.assertTrue(is_candidate_item(items[431]))
        self.assertFalse(is_candidate_item(items[900]))
        self.assertFalse(is_candidate_item({**items[431], "proxy": ["relaysingle_relay_c4"]}))
        commands = {
            579: {"OPEN", "CLOSE"}, 450: {"OPEN", "CLOSE"},
            431: {"OPEN", "CLOSE"}, 362: {"OPEN"}, 900: {"OPEN", "CLOSE"},
        }
        self.assertEqual(
            supported_relay_ids(
                items, items,
                lambda device_id, command: command in commands[device_id],
                lambda device_id: device_id in {579, 431, 362, 900},
            ),
            [431, 579],
        )

    def test_only_observed_contact_proxies_are_binary_sensors(self) -> None:
        items = {
            335: {"id": 335, "typeName": "device", "proxy": "contactsingle_doorcontactsensor_c4"},
            377: {"id": 377, "typeName": "device", "proxy": "contactsingle_windowcontactsensor_c4"},
            378: {"id": 378, "typeName": "device", "proxy": "contactsingle_motionsensor_c4"},
            2140: {"id": 2140, "typeName": "device", "proxy": "zwave-common-sensor"},
            319: {"id": 319, "typeName": "device", "proxy": "uibutton"},
            431: {"id": 431, "typeName": "device", "proxy": "relaysingle_relay_c4"},
            400: {"id": 400, "typeName": "room", "proxy": "contactsingle_windowcontactsensor_c4"},
            401: {"id": 401, "typeName": "device", "proxy": ["contactsingle_windowcontactsensor_c4"]},
        }
        self.assertTrue(is_candidate_item(items[335]))
        self.assertTrue(is_candidate_item(items[377]))
        self.assertTrue(is_candidate_item(items[378]))
        self.assertFalse(is_candidate_item(items[2140]))
        self.assertFalse(is_candidate_item(items[400]))
        self.assertFalse(is_candidate_item(items[401]))
        self.assertEqual(supported_binary_sensor_ids(items, items), [335, 377, 378])
        self.assertEqual(command_metadata_ids(items, {335, 377, 378, 431}), [431])

    def test_media_rooms_require_complete_state_and_inventory_volume_binding(self) -> None:
        items = {
            22: {"id": 22, "typeName": "room", "proxy": "roomdevice"},
            23: {"id": 23, "typeName": "room", "proxy": "roomdevice"},
            24: {"id": 24, "typeName": "room", "proxy": "roomdevice"},
            25: {"id": 25, "typeName": "device", "proxy": "roomdevice"},
            623: {"id": 623, "typeName": "device", "proxy": "aswitch"},
            1227: {"id": 1227, "typeName": "device", "proxy": "tv"},
        }
        valid = {"is_on": True, "volume_percent": 41.0, "is_muted": False,
                 "volume_device_id": 623}
        self.assertEqual(supported_media_room_ids(items, {
            22: valid,
            23: {**valid, "volume_device_id": 1227},
            24: {**valid, "is_muted": None},
            25: valid,
        }), [22, 23])
        self.assertEqual(supported_media_room_ids(items, {22: {**valid, "volume_percent": True}}), [])

        off = {"is_on": False, "selected_source_id": 0, "volume_device_id": 0}
        self.assertEqual(supported_media_room_ids(items, {22: off}), [])
        self.assertEqual(supported_media_room_ids(items, {22: off}, {22}), [22])
        self.assertEqual(supported_media_room_ids(items, {
            22: {**off, "selected_source_id": 346},
        }, {22}), [])

    def test_only_owned_registered_media_rooms_can_survive_off_reload(self) -> None:
        items = {
            22: {"typeName": "room", "proxy": "roomdevice"},
            23: {"typeName": "room", "proxy": "roomdevice"},
            431: {"typeName": "device", "proxy": "relaysingle_relay_c4"},
        }
        def entry(entity_id, unique_id, config_entry_id="entry", platform="control4_advanced"):
            return SimpleNamespace(entity_id=entity_id, unique_id=unique_id,
                                   config_entry_id=config_entry_id, platform=platform)

        self.assertEqual(registered_media_room_ids([
            entry("media_player.living", "192.168.0.24_22"),
            entry("media_player.other_host", "192.168.0.25_23"),
            entry("media_player.other_entry", "192.168.0.24_23", config_entry_id="other"),
            entry("media_player.other_platform", "192.168.0.24_23", platform="other"),
            entry("switch.relay", "192.168.0.24_431"),
        ], config_entry_id="entry", host="192.168.0.24", items=items), {22})


class MediaRoomDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_discovery_reads_rooms_only_and_skips_failed_or_unsupported_rooms(self) -> None:
        items = {
            22: {"id": 22, "typeName": "room", "proxy": "roomdevice"},
            23: {"id": 23, "typeName": "room", "proxy": "roomdevice"},
            24: {"id": 24, "typeName": "room", "proxy": "roomdevice"},
            623: {"id": 623, "typeName": "device", "proxy": "aswitch"},
        }
        reads = []

        async def get_variables(item_id):
            reads.append(item_id)
            if item_id == 24:
                raise RuntimeError("unavailable")
            return [
                {"varName": "POWER_STATE", "value": 1},
                {"varName": "CURRENT_VOLUME", "value": 41},
                {"varName": "IS_MUTED", "value": 0},
                {"varName": "CURRENT_VOLUME_DEVICE_ID", "value": 623 if item_id == 22 else 0},
            ]

        transport = SimpleNamespace(
            inventory=SimpleNamespace(items=items),
            rest=SimpleNamespace(get_variables=get_variables),
        )
        with self.assertLogs("custom_components.control4_advanced", level="WARNING"):
            self.assertEqual(await _discover_media_room_ids(transport), [22])
        self.assertEqual(reads, [22, 23, 24])

    async def test_discovery_retains_registered_room_with_off_snapshot(self) -> None:
        items = {22: {"id": 22, "typeName": "room", "proxy": "roomdevice"}}

        async def get_variables(room_id):
            self.assertEqual(room_id, 22)
            return [
                {"varName": "POWER_STATE", "value": 0},
                {"varName": "CURRENT_SELECTED_DEVICE", "value": 0},
                {"varName": "CURRENT_VOLUME_DEVICE_ID", "value": 0},
            ]

        transport = SimpleNamespace(
            inventory=SimpleNamespace(items=items),
            rest=SimpleNamespace(get_variables=get_variables),
        )
        self.assertEqual(await _discover_media_room_ids(transport), [])
        self.assertEqual(await _discover_media_room_ids(transport, {22}), [22])


if __name__ == "__main__":
    unittest.main()
