from __future__ import annotations

import unittest

from control4_transport import DeviceCommandClient, UnsupportedCommand


class FakeRest:
    def __init__(self) -> None:
        self.sent = []

    async def get_commands(self, device_id: int):
        if device_id == 2726:
            return [
                {"command": "ON", "deviceId": 2726},
                {"command": "OFF", "deviceId": 2726},
                {"command": "SET_LEVEL", "deviceId": 2726, "params": [
                    {"name": "LEVEL", "type": "RANGE", "low": 0, "high": 100, "valueType": "INTEGER"}
                ]},
                {"command": "SET_BRIGHTNESS_PERCENT", "deviceId": 2725},
            ]
        return [
            {"command": "SET_MODE_HVAC", "deviceId": 2646, "params": [
                {"name": "MODE", "values": [{"value": value} for value in ("Off", "Heat", "Cool", "Auto")]}
            ]},
            {"command": "SET_SETPOINT_HEAT", "deviceId": 2646, "params": [
                {"name": "CELSIUS", "low": 4, "high": 31, "resolution": 1, "valueType": "STRING"}
            ]},
        ]

    async def post_json(self, path, body):
        self.sent.append((path, body))
        return {"ok": True}


class CommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.rest = FakeRest()
        self.commands = DeviceCommandClient(self.rest)
        await self.commands.refresh([2726, 2646])

    async def test_light_body_uses_live_parameter_name(self) -> None:
        await self.commands.light_level(2726, 41)
        self.assertEqual(self.rest.sent, [
            ("/api/v1/items/2726/commands", {"command": "SET_LEVEL", "params": {"LEVEL": 41}})
        ])

    async def test_parent_driver_commands_are_not_exposed(self) -> None:
        self.assertFalse(self.commands.supports(2726, "SET_BRIGHTNESS_PERCENT"))
        with self.assertRaises(UnsupportedCommand):
            await self.commands.send(2726, "SET_BRIGHTNESS_PERCENT")
        self.assertFalse(self.rest.sent)

    async def test_mode_uses_advertised_wire_value(self) -> None:
        await self.commands.hvac_mode(2646, "heat")
        self.assertEqual(self.rest.sent[-1][1]["params"], {"MODE": "Heat"})
        with self.assertRaises(UnsupportedCommand):
            await self.commands.hvac_mode(2646, "Dry")
        self.assertEqual(len(self.rest.sent), 1)

    async def test_invalid_light_level_never_posts(self) -> None:
        for invalid in (-1, 101, 3.5, True):
            with self.assertRaises(UnsupportedCommand):
                await self.commands.light_level(2726, invalid)
        self.assertFalse(self.rest.sent)

    async def test_setpoint_range_resolution_and_string_value(self) -> None:
        await self.commands.setpoint(2646, "HEAT", 19, "CELSIUS")
        self.assertEqual(self.rest.sent[-1][1]["params"], {"CELSIUS": "19"})
        for invalid in (3, 32, 19.5, float("nan")):
            with self.assertRaises(UnsupportedCommand):
                await self.commands.setpoint(2646, "HEAT", invalid, "CELSIUS")
        self.assertEqual(len(self.rest.sent), 1)


if __name__ == "__main__":
    unittest.main()
