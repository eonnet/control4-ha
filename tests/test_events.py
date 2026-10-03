from __future__ import annotations

import unittest

from control4_transport.client import DeviceStateCache
from control4_transport.events import normalize_rest_variables, normalize_websocket_event


class EventNormalizationTests(unittest.TestCase):
    def test_light_settled_and_transition_are_distinct(self) -> None:
        settled = normalize_websocket_event(
            {"evtName": "OnDataToUI", "iddevice": 2726, "data": {"LIGHT_LEVEL": 41}},
            "light_v2",
        )
        self.assertIsNotNone(settled)
        self.assertEqual(settled.changes, {"brightness_percent": 41.0, "is_on": True})
        self.assertTrue(settled.authoritative)

        changing = normalize_websocket_event(
            {
                "evtName": "OnDataToUI",
                "iddevice": 2726,
                "data": {"light_brightness_changing": {"light_brightness_current": 0, "light_brightness_target": 41}},
            },
            "light_v2",
        )
        self.assertEqual(changing.changes, {"brightness_target_percent": 41.0})
        self.assertFalse(changing.authoritative)

        cache = DeviceStateCache()
        cache.apply(settled)
        cache.apply(changing)
        self.assertEqual(cache.snapshot(2726), {"brightness_percent": 41.0, "is_on": True})

    def test_thermostat_partial_fields_and_unknown_raw_values(self) -> None:
        event = normalize_websocket_event(
            {
                "evtName": "OnDataToUI",
                "iddevice": 2646,
                "data": {"setpoint_cool": 2921, "setpoint_cool_c": 19},
            },
            "thermostatV2",
        )
        self.assertEqual(event.changes, {"cool_setpoint_c": 19.0})
        self.assertEqual(event.raw["data"]["setpoint_cool"], 2921)
        self.assertTrue(event.authoritative)

        mode = normalize_websocket_event(
            {"evtName": "OnDataToUI", "iddevice": 2646, "data": {"hvac_mode": "Off"}},
            "thermostatV2",
        )
        self.assertEqual(mode.changes, {"hvac_mode": "OFF"})

    def test_rest_snapshot_uses_observed_variable_names_and_scale(self) -> None:
        variables = [
            {"varName": "SCALE", "value": "CELSIUS"},
            {"varName": "TEMPERATURE_C", "value": 24.9},
            {"varName": "HEAT_SETPOINT_C", "value": 19},
            {"varName": "COOL_SETPOINT_C", "value": 19},
            {"varName": "HVAC_MODE", "value": "Off"},
            {"varName": "IS_CONNECTED", "value": 1},
        ]
        event = normalize_rest_variables(2646, "thermostatV2", variables)
        self.assertEqual(
            event.changes,
            {
                "scale": "CELSIUS",
                "hvac_mode": "OFF",
                "current_temperature_c": 24.9,
                "heat_setpoint_c": 19.0,
                "cool_setpoint_c": 19.0,
                "is_connected": True,
            },
        )

        fahrenheit = normalize_rest_variables(
            2646,
            "thermostatV2",
            [
                {"varName": "SCALE", "value": "FAHRENHEIT"},
                {"varName": "DISPLAY_TEMPERATURE", "value": 76.8},
            ],
        )
        self.assertNotIn("current_temperature_c", fahrenheit.changes)

    def test_rest_on_off_only_light_state_is_readable_but_not_push_proven(self) -> None:
        off = normalize_rest_variables(978, "light_v2", [{"varName": "LIGHT_STATE", "value": 0}])
        on = normalize_rest_variables(719, "light_v2", [{"varName": "LIGHT_STATE", "value": 1}])
        self.assertEqual(off.changes, {"is_on": False})
        self.assertEqual(on.changes, {"is_on": True})

    def test_stale_rest_field_does_not_replace_newer_push_field(self) -> None:
        cache = DeviceStateCache()
        baseline = cache.revision
        push = normalize_websocket_event(
            {"evtName": "OnDataToUI", "iddevice": 2726, "data": {"LIGHT_LEVEL": 41}},
            "light_v2",
        )
        cache.apply(push)
        stale_rest = normalize_rest_variables(
            2726, "light_v2", [{"varName": "Brightness Percent", "value": 0}]
        )
        self.assertEqual(cache.apply(stale_rest, only_if_unchanged_since=baseline), {})
        self.assertEqual(cache.snapshot(2726)["brightness_percent"], 41.0)


if __name__ == "__main__":
    unittest.main()
