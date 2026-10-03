from __future__ import annotations

import unittest

from custom_components.control4_advanced.selection import is_candidate_item


class SelectionTests(unittest.TestCase):
    def test_light_requires_power_capability(self) -> None:
        item = {"id": 2726, "typeName": "device", "proxy": "light_v2", "capabilities": {"on_off": True, "dimmer": True}}
        self.assertTrue(is_candidate_item(item))
        self.assertFalse(is_candidate_item({**item, "capabilities": {"on_off": True}}))

    def test_thermostat_requires_heating_or_cooling(self) -> None:
        item = {"id": 2646, "typeName": "device", "proxy": "thermostatV2", "capabilities": {"can_heat": True}}
        self.assertTrue(is_candidate_item(item))
        self.assertFalse(is_candidate_item({**item, "capabilities": {"has_temperature": True}}))


if __name__ == "__main__":
    unittest.main()
