from __future__ import annotations

import unittest

from control4_transport import DeviceCommandClient, DirectorRestError, UnsupportedCommand
from custom_components.control4_advanced.transport.favorites import TuneInFavorite
from custom_components.control4_advanced.transport.sources import RoomSource
from custom_components.control4_advanced.transport.playlists import TidalPlaylist


class FakeRest:
    def __init__(self) -> None:
        self.sent = []

    async def get_commands(self, device_id: int):
        if device_id == 91:
            return [{
                "command": "Play Item", "deviceId": 91, "params": [
                    {"name": "Item", "type": "CUSTOM_SELECT:GetItemList"},
                    {"name": "Room", "type": "DEVICE_SELECTOR",
                     "items": ["roomdevice.c4i"], "multiselect": True},
                ],
            }]
        if device_id == 93:
            return [{
                "command": "SelectPlaylist", "deviceId": 93, "params": [
                    {"name": "Playlist", "type": "CUSTOM_SELECT:SelectPlaylistName", "search": ""},
                    {"name": "Room", "type": "DEVICE_SELECTOR",
                     "items": ["roomdevice.c4i"], "multiselect": True},
                    {"name": "Shuffle", "type": "LIST", "items": ["Off", "On"]},
                ],
            }]
        if device_id == 22:
            return [
                {"command": "ROOM_OFF", "deviceId": 22, "params": []},
                {"command": "PLAY", "deviceId": 22, "params": []},
                {"command": "PAUSE", "deviceId": 22, "params": []},
                {"command": "STOP", "deviceId": 22, "params": []},
                {"command": "SKIP_REV", "deviceId": 22, "params": []},
                {"command": "SKIP_FWD", "deviceId": 22, "params": []},
                {"command": "SET_VOLUME_LEVEL", "deviceId": 22, "params": [
                    {"name": "LEVEL", "type": "RANGE", "valueType": "INTEGER",
                     "low": 0, "high": 100},
                ]},
                {"command": "MUTE_ON", "deviceId": 22},
                {"command": "MUTE_OFF", "deviceId": 22, "params": []},
                {"command": "SELECT_VIDEO_DEVICE", "deviceId": 22, "params": [
                    {"name": "deviceid", "valueType": "INTEGER"},
                    {"name": "deselect", "valueType": "BOOL"},
                ]},
                {"command": "SELECT_AUDIO_DEVICE", "deviceId": 22, "params": [
                    {"name": "deviceid", "valueType": "INTEGER"},
                    {"name": "deselect", "valueType": "BOOL"},
                ]},
            ]
        if device_id == 431:
            return [
                {"command": "OPEN", "deviceId": 431},
                {"command": "CLOSE", "deviceId": 431},
                {"command": "TOGGLE", "deviceId": 431},
            ]
        if device_id == 579:
            return [
                {"command": "OPEN", "deviceId": 579},
                {"command": "CLOSE", "deviceId": 579, "params": []},
                {"command": "TOGGLE", "deviceId": 579},
                # Synthetic negative case; not a Director command for ID 579.
                {"command": "PARAMETERIZED_TEST", "deviceId": 579, "params": [
                    {"name": "MODE", "valueType": "STRING"},
                ]},
            ]
        if device_id == 2726:
            return [
                {"command": "ON", "deviceId": 2726},
                {"command": "OFF", "deviceId": 2726},
                {"command": "SET_LEVEL", "deviceId": 2726, "params": [
                    {"name": "LEVEL", "type": "RANGE", "low": 0, "high": 100, "valueType": "INTEGER"}
                ]},
                {"command": "SET_BRIGHTNESS_PERCENT", "deviceId": 2725},
            ]
        if device_id == 2811:
            return [
                {"command": "SET_MODE_HOLD", "deviceId": 2811, "params": [
                    {"name": "MODE", "values": [{"value": value} for value in
                     ("Off", "2 Hours", "Until Next", "Permanent")]},
                ]},
                {"command": "SET_MODE_FAN", "deviceId": 2811, "params": [
                    {"name": "MODE", "values": [{"value": value} for value in
                     ("Auto", "Low", "Medium", "High")]},
                ]},
            ]
        if device_id == 404:
            return [
                {"command": "SET_SETPOINT_SINGLE", "deviceId": 404, "params": [
                    {"name": "CELSIUS", "low": 8, "high": 30,
                     "resolution": 1, "valueType": "STRING"},
                ]},
                {"command": "SET_SETPOINT_HEAT", "deviceId": 404, "params": [
                    {"name": "CELSIUS", "low": 4, "high": 31,
                     "resolution": 1, "valueType": "STRING"},
                ]},
            ]
        return [
            {"command": "SET_MODE_HVAC", "deviceId": 2646, "params": [
                {"name": "MODE", "values": [{"value": value} for value in ("Off", "Heat", "Cool", "Auto")]}
            ]},
            {"command": "SET_SETPOINT_HEAT", "deviceId": 2646, "params": [
                {"name": "CELSIUS", "low": 4, "high": 31, "resolution": 1, "valueType": "STRING"}
            ]},
            {"command": "SET_MODE_HOLD", "deviceId": 2646, "params": [
                {"name": "MODE", "values": [{"value": value} for value in
                 ("Off", "2 Hours", "Until Next", "Permanent")]},
            ]},
            {"command": "SET_PRESET", "deviceId": 2646, "params": [
                {"name": "NAME", "values": [{"value": value} for value in
                 ("Morning", "Home", "Away", "Morning Auto", "Cool", "Heat", "Off")]},
            ]},
            {"command": "SET_MODE_FAN", "deviceId": 2646, "params": [
                {"name": "MODE", "values": [{"value": value} for value in
                 ("Auto", "Low", "Medium", "High")]},
            ]},
            {"command": "SET_MODE_HUMIDITY", "deviceId": 2646, "params": [
                {"name": "MODE", "values": [{"value": value} for value in
                 ("Off", "Humidify", "Dehumidify", "Auto")]},
            ]},
            {"command": "INC_SETPOINT_HUMIDIFY", "deviceId": 2646, "params": []},
            {"command": "DEC_SETPOINT_HUMIDIFY", "deviceId": 2646, "params": []},
            {"command": "INC_SETPOINT_DEHUMIDIFY", "deviceId": 2646, "params": []},
            {"command": "DEC_SETPOINT_DEHUMIDIFY", "deviceId": 2646, "params": []},
        ]

    async def post_json(self, path, body):
        self.sent.append((path, body))
        return {"ok": True}


class CommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.rest = FakeRest()
        self.commands = DeviceCommandClient(self.rest)
        await self.commands.refresh([22, 91, 93, 2726, 2646, 2811, 579, 431, 404])

    async def test_room_volume_and_mute_use_advertised_commands(self) -> None:
        self.assertTrue(self.commands.supports_room_volume(22))
        self.assertTrue(self.commands.supports_room_mute(22))
        await self.commands.room_volume(22, 41)
        await self.commands.room_mute(22, True)
        await self.commands.room_mute(22, False)
        self.assertEqual(self.rest.sent, [
            ("/api/v1/items/22/commands", {
                "command": "SET_VOLUME_LEVEL", "params": {"LEVEL": 41},
            }),
            ("/api/v1/items/22/commands", {"command": "MUTE_ON"}),
            ("/api/v1/items/22/commands", {"command": "MUTE_OFF"}),
        ])

    async def test_humidity_mode_and_steps_require_exact_advertised_commands(self) -> None:
        await self.commands.humidity_mode(2646, "Auto")
        await self.commands.humidity_step(2646, "HUMIDIFY", increase=True)
        await self.commands.humidity_step(2646, "DEHUMIDIFY", increase=False)
        self.assertEqual(self.rest.sent, [
            ("/api/v1/items/2646/commands", {
                "command": "SET_MODE_HUMIDITY", "params": {"MODE": "Auto"},
            }),
            ("/api/v1/items/2646/commands", {
                "command": "INC_SETPOINT_HUMIDIFY",
            }),
            ("/api/v1/items/2646/commands", {
                "command": "DEC_SETPOINT_DEHUMIDIFY",
            }),
        ])
        self.rest.sent.clear()
        with self.assertRaises(UnsupportedCommand):
            await self.commands.humidity_mode(2646, "Dry")
        with self.assertRaises(UnsupportedCommand):
            await self.commands.humidity_step(2646, "OTHER", increase=True)
        with self.assertRaises(UnsupportedCommand):
            await self.commands.humidity_step(2811, "HUMIDIFY", increase=True)
        self.assertFalse(self.rest.sent)

    async def test_room_off_requires_parameterless_metadata(self) -> None:
        self.assertTrue(self.commands.supports_room_off(22))
        await self.commands.room_off(22)
        self.assertEqual(self.rest.sent, [
            ("/api/v1/items/22/commands", {"command": "ROOM_OFF"}),
        ])
        self.rest.sent.clear()
        self.commands._metadata[22]["ROOM_OFF"]["params"] = [
            {"name": "UNEXPECTED", "valueType": "BOOL"},
        ]
        self.assertFalse(self.commands.supports_room_off(22))
        with self.assertRaises(UnsupportedCommand):
            await self.commands.room_off(22)
        self.assertFalse(self.rest.sent)

    async def test_room_transport_is_limited_to_advertised_parameterless_commands(self) -> None:
        commands = ("SKIP_REV", "PLAY", "PAUSE", "STOP", "SKIP_FWD")
        for command in commands:
            self.assertTrue(self.commands.supports_room_transport(22, command))
            await self.commands.room_transport(22, command)
        self.assertEqual(self.rest.sent, [
            ("/api/v1/items/22/commands", {
                "async": True, "command": command, "tParams": {},
            })
            for command in commands
        ])
        self.rest.sent.clear()
        for command in ("ROOM_OFF", "TOGGLE", "PLAY_PAUSE"):
            self.assertFalse(self.commands.supports_room_transport(22, command))
            with self.assertRaises(UnsupportedCommand):
                await self.commands.room_transport(22, command)
        self.commands._metadata[22]["PAUSE"]["params"] = [{"name": "UNEXPECTED"}]
        self.assertFalse(self.commands.supports_room_transport(22, "PAUSE"))
        with self.assertRaises(UnsupportedCommand):
            await self.commands.room_transport(22, "PAUSE")
        self.assertFalse(self.rest.sent)

    async def test_unsupported_room_volume_or_mute_never_posts(self) -> None:
        for value in (-1, 101, 50.5, True, float("nan")):
            with self.assertRaises(UnsupportedCommand):
                await self.commands.room_volume(22, value)
        with self.assertRaises(UnsupportedCommand):
            await self.commands.room_mute(22, 1)
        self.assertFalse(self.rest.sent)
        self.commands._metadata[22]["SET_VOLUME_LEVEL"]["params"][0]["high"] = 90
        self.assertFalse(self.commands.supports_room_volume(22))
        with self.assertRaises(UnsupportedCommand):
            await self.commands.room_volume(22, 40)
        self.commands._metadata[22].pop("MUTE_OFF")
        self.assertFalse(self.commands.supports_room_mute(22))
        with self.assertRaises(UnsupportedCommand):
            await self.commands.room_mute(22, True)
        self.assertFalse(self.rest.sent)

    async def test_room_sources_use_documented_deviceid_only(self) -> None:
        watch = RoomSource(346, "watch", "Plex", "RF_MINI_APP", "Watch: Plex")
        listen = RoomSource(623, "listen", "Wiim", "AUDIO_SELECTION", "Listen: Wiim")
        self.assertTrue(self.commands.supports_room_source(22, "watch"))
        self.assertTrue(self.commands.supports_room_source(22, "listen"))
        self.commands.register_room_sources(22, (watch, listen))
        await self.commands.room_source(22, watch)
        await self.commands.room_source(22, listen)
        self.assertEqual(self.rest.sent, [
            ("/api/v1/items/22/commands", {
                "command": "SELECT_VIDEO_DEVICE", "params": {"deviceid": 346},
            }),
            ("/api/v1/items/22/commands", {
                "command": "SELECT_AUDIO_DEVICE", "params": {"deviceid": 623},
            }),
        ])

    async def test_native_media_grouping_uses_registered_digital_media_target(self) -> None:
        self.commands.register_media_grouping(100002, (22, 23, 37))
        await self.commands.add_rooms_to_session(22, (23, 37, 23))
        self.assertEqual(self.rest.sent, [
            ("/api/v1/items/100002/commands", {
                "command": "ADD_ROOMS_TO_SESSION",
                "params": {"ROOM_ID": 22, "ROOM_ID_LIST": "23,37"},
            }),
        ])

    async def test_invalid_media_group_never_posts(self) -> None:
        invalid_calls = (
            self.commands.add_rooms_to_session(22, (23,)),
            self.commands.add_rooms_to_session(22, ()),
        )
        for operation in invalid_calls:
            with self.assertRaises(UnsupportedCommand):
                await operation
        self.commands.register_media_grouping(100002, (22, 23))
        for leader, members in ((22, (22,)), (22, (999,)), (999, (23,))):
            with self.assertRaises(UnsupportedCommand):
                await self.commands.add_rooms_to_session(leader, members)
        self.assertFalse(self.rest.sent)

    async def test_tidal_playlist_is_allowlisted_and_shuffle_is_off(self) -> None:
        playlist = TidalPlaylist(
            "TIDAL", "Evening Mix", "01234567-89ab-cdef-0123-456789abcdef"
        )
        self.assertTrue(self.commands.supports_tidal_playlist(93))
        self.commands.register_tidal_playlists(93, 22, (playlist,))
        await self.commands.tidal_playlist(93, 22, playlist)
        self.assertEqual(self.rest.sent, [
            ("/api/v1/items/93/commands", {
                "command": "SelectPlaylist",
                "params": {
                    "Playlist": "01234567-89ab-cdef-0123-456789abcdef",
                    "Room": "22",
                    "Shuffle": "Off",
                },
            }),
        ])

        self.rest.sent.clear()
        with self.assertRaises(UnsupportedCommand):
            await self.commands.tidal_playlist(
                93,
                22,
                TidalPlaylist(
                    "TIDAL", "Invented", "11111111-2222-4333-8444-555555555555"
                ),
            )
        self.assertFalse(self.rest.sent)

    async def test_tunein_favorite_is_allowlisted_for_room(self) -> None:
        favorite = TuneInFavorite(
            "TuneIn", "Klassik Radio", "Station/s25028"
        )
        self.assertTrue(self.commands.supports_tunein_favorite(91))
        self.commands.register_tunein_favorites(91, 22, (favorite,))
        await self.commands.tunein_favorite(91, 22, favorite)
        self.assertEqual(self.rest.sent, [
            ("/api/v1/items/91/commands", {
                "command": "Play Item",
                "params": {"Item": "Station/s25028", "Room": "22"},
            }),
        ])

        self.rest.sent.clear()
        with self.assertRaises(UnsupportedCommand):
            await self.commands.tunein_favorite(
                91,
                22,
                TuneInFavorite("TuneIn", "Invented", "Station/s99999"),
            )
        self.assertFalse(self.rest.sent)

    async def test_invalid_source_or_metadata_never_posts(self) -> None:
        invalid = RoomSource(0, "watch", "None", "HDMI", "Watch: None")
        with self.assertRaises(UnsupportedCommand):
            await self.commands.room_source(22, invalid)
        with self.assertRaises(UnsupportedCommand):
            await self.commands.room_source(
                22, RoomSource(9999, "watch", "Invented", "HDMI", "Watch: Invented")
            )
        self.commands.register_room_sources(
            22, (RoomSource(346, "watch", "Plex", "RF_MINI_APP", "Watch: Plex"),)
        )
        self.commands._metadata[22]["SELECT_VIDEO_DEVICE"]["params"][1]["valueType"] = "STRING"
        self.assertFalse(self.commands.supports_room_source(22, "watch"))
        with self.assertRaises(UnsupportedCommand):
            await self.commands.room_source(
                22, RoomSource(346, "watch", "Plex", "RF_MINI_APP", "Watch: Plex")
            )
        self.assertFalse(self.commands.supports_room_source(22, "unknown"))
        self.assertFalse(self.rest.sent)

    async def test_light_body_uses_live_parameter_name(self) -> None:
        await self.commands.light_level(2726, 41)
        self.assertEqual(self.rest.sent, [
            ("/api/v1/items/2726/commands", {"command": "SET_LEVEL", "params": {"LEVEL": 41}})
        ])

    async def test_radiant_floor_relay_uses_advertised_open_close_commands(self) -> None:
        self.assertTrue(self.commands.supports_parameterless(579, "OPEN"))
        self.assertTrue(self.commands.supports_parameterless(579, "CLOSE"))
        self.assertFalse(self.commands.supports_parameterless(579, "PARAMETERIZED_TEST"))
        self.assertFalse(self.commands.supports_parameterless(579, "MISSING"))
        await self.commands.relay_open(579)
        await self.commands.relay_close(579)
        self.assertEqual(self.rest.sent, [
            ("/api/v1/items/579/commands", {"command": "OPEN"}),
            ("/api/v1/items/579/commands", {"command": "CLOSE"}),
        ])

    async def test_regular_relay_uses_advertised_open_close_commands(self) -> None:
        self.assertTrue(self.commands.supports_parameterless(431, "OPEN"))
        self.assertTrue(self.commands.supports_parameterless(431, "CLOSE"))
        await self.commands.relay_open(431)
        await self.commands.relay_close(431)
        self.assertEqual(self.rest.sent, [
            ("/api/v1/items/431/commands", {"command": "OPEN"}),
            ("/api/v1/items/431/commands", {"command": "CLOSE"}),
        ])

    async def test_one_metadata_failure_does_not_hide_other_devices(self) -> None:
        class FailingRest(FakeRest):
            async def get_commands(self, device_id: int):
                if device_id == 431:
                    raise DirectorRestError("simulated metadata failure")
                return await super().get_commands(device_id)

        commands = DeviceCommandClient(FailingRest())
        with self.assertLogs("custom_components.control4_advanced.transport.commands", level="WARNING") as logs:
            await commands.refresh([431, 579])
        self.assertNotIn("simulated metadata failure", " ".join(logs.output))
        self.assertFalse(commands.supports(431, "OPEN"))
        self.assertTrue(commands.supports(579, "OPEN"))
        with self.assertLogs("custom_components.control4_advanced.transport.commands", level="WARNING"):
            with self.assertRaises(DirectorRestError):
                await commands.refresh([431])

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

    async def test_hold_and_preset_use_only_device_specific_choices(self) -> None:
        await self.commands.hold_mode(2646, "until next")
        await self.commands.preset(2646, "morning auto")
        await self.commands.hold_mode(2811, "Permanent")
        self.assertEqual([body for _, body in self.rest.sent], [
            {"command": "SET_MODE_HOLD", "params": {"MODE": "Until Next"}},
            {"command": "SET_PRESET", "params": {"NAME": "Morning Auto"}},
            {"command": "SET_MODE_HOLD", "params": {"MODE": "Permanent"}},
        ])
        for operation in (
            self.commands.hold_mode(2646, "unsupported"),
            self.commands.preset(2646, "unsupported"),
            self.commands.preset(2811, "Away"),
        ):
            with self.assertRaises(UnsupportedCommand):
                await operation
        self.assertEqual(len(self.rest.sent), 3)

    async def test_fan_mode_uses_only_device_specific_choices(self) -> None:
        await self.commands.fan_mode(2646, "medium")
        await self.commands.fan_mode(2811, "auto")
        self.assertEqual(self.rest.sent, [
            ("/api/v1/items/2646/commands", {
                "command": "SET_MODE_FAN", "params": {"MODE": "Medium"},
            }),
            ("/api/v1/items/2811/commands", {
                "command": "SET_MODE_FAN", "params": {"MODE": "Auto"},
            }),
        ])
        for operation in (
            self.commands.fan_mode(2646, "Turbo"),
            self.commands.fan_mode(2811, "On"),
        ):
            with self.assertRaises(UnsupportedCommand):
                await operation
        self.assertEqual(len(self.rest.sent), 2)

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

    async def test_gree_single_setpoint_uses_advertised_range_and_wire_format(self) -> None:
        await self.commands.setpoint(404, "SINGLE", 23, "CELSIUS")
        self.assertEqual(self.rest.sent, [
            ("/api/v1/items/404/commands", {
                "command": "SET_SETPOINT_SINGLE", "params": {"CELSIUS": "23"},
            }),
        ])
        for invalid in (7, 31, 22.5, float("nan")):
            with self.assertRaises(UnsupportedCommand):
                await self.commands.setpoint(404, "SINGLE", invalid, "CELSIUS")
        self.assertEqual(len(self.rest.sent), 1)


if __name__ == "__main__":
    unittest.main()
