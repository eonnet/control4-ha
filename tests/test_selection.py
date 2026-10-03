from __future__ import annotations

import unittest

from custom_components.control4_advanced.selection import is_candidate_item, supported_light_ids


class SelectionTests(unittest.TestCase):
    def test_light_requires_power_capability(self) -> None:
        item = {"id": 2726, "typeName": "device", "proxy": "light_v2", "capabilities": {"on_off": True, "dimmer": True}}
        self.assertTrue(is_candidate_item(item))
        self.assertFalse(is_candidate_item({**item, "capabilities": {"on_off": True}}))

    def test_thermostat_requires_heating_or_cooling(self) -> None:
        item = {"id": 2646, "typeName": "device", "proxy": "thermostatV2", "capabilities": {"can_heat": True}}
        self.assertTrue(is_candidate_item(item))
        self.assertFalse(is_candidate_item({**item, "capabilities": {"has_temperature": True}}))

    def test_supported_lights_do_not_depend_on_initial_state(self) -> None:
        items = {
            1: {"id": 1, "typeName": "device", "proxy": "light_v2", "capabilities": {"on_off": True, "dimmer": True}},
            2: {"id": 2, "typeName": "device", "proxy": "light_v2", "capabilities": {"on_off": True}},
            3: {"id": 3, "typeName": "device", "proxy": "light_v2", "capabilities": {"on_off": True, "dimmer": True}},
        }
        commands = {1: {"ON", "OFF"}, 2: {"ON", "OFF"}, 3: {"ON"}}
        self.assertEqual(
            supported_light_ids(items, items, lambda device_id, command: command in commands[device_id]),
            [1],
        )


if __name__ == "__main__":
    unittest.main()
