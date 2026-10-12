from __future__ import annotations

from base64 import b64encode
import unittest

from control4_transport.client import DeviceStateCache
from control4_transport.events import normalize_rest_variables, normalize_websocket_event


class EventNormalizationTests(unittest.TestCase):
    def test_wiim_output_status_push_is_device_scoped_and_bounded(self) -> None:
        event = normalize_websocket_event({
            "evtName": "OnDataToUI", "iddevice": 623,
            "data": {"OutputStatus": {
                "OutputID": 4000, "VolumeLevel": 41, "MuteFlag": False,
                "InputID": 1751, "Title": "private-title",
            }},
        }, "aswitch")
        self.assertEqual(event.changes, {
            "output_id": 4000, "output_volume_percent": 41.0,
            "output_is_muted": False,
        })
        self.assertTrue(event.authoritative)
        self.assertEqual(event.device_id, 623)
        self.assertEqual(event.raw["data"]["OutputStatus"]["InputID"], 1751)
        for status in (
            {"OutputID": True, "VolumeLevel": 41, "MuteFlag": False},
            {"OutputID": 4000, "VolumeLevel": 101, "MuteFlag": "false"},
            {"OutputID": 4000, "VolumeLevel": "private-test-token"},
        ):
            with self.subTest(status=status):
                invalid = normalize_websocket_event({
                    "evtName": "OnDataToUI", "iddevice": 623,
                    "data": {"OutputStatus": status},
                }, "aswitch")
                self.assertEqual(invalid.changes, {})
                self.assertFalse(invalid.authoritative)

    def test_wiim_mute_burst_preserves_each_observed_flag_for_settling_layer(self) -> None:
        observed = []
        for muted, level in ((False, 0), (True, 0), (True, 41), (False, 41)):
            event = normalize_websocket_event({
                "evtName": "OnDataToUI", "iddevice": 623,
                "data": {"OutputStatus": {
                    "OutputID": 4000, "MuteFlag": muted, "VolumeLevel": level,
                }},
            }, "aswitch")
            self.assertTrue(event.authoritative)
            observed.append((event.changes["output_is_muted"], event.changes["output_volume_percent"]))
        self.assertEqual(observed, [(False, 0.0), (True, 0.0), (True, 41.0), (False, 41.0)])

    def test_room_rest_snapshot_does_not_invent_room_push(self) -> None:
        snapshot = normalize_rest_variables(22, "roomdevice", [
            {"varName": "POWER_STATE", "value": 1},
            {"varName": "CURRENT_VOLUME", "value": 41},
            {"varName": "IS_MUTED", "value": 0},
            {"varName": "CURRENT_VOLUME_DEVICE_ID", "value": 623},
            {"varName": "CURRENT_SELECTED_DEVICE", "value": 346},
            {"varName": "PLAYING_AUDIO_DEVICE", "value": "1366"},
            {"varName": "CURRENT MEDIA INFO", "value": "private-title"},
        ])
        self.assertEqual(snapshot.changes, {
            "is_on": True, "volume_percent": 41.0,
            "is_muted": False, "volume_device_id": 623,
            "selected_source_id": 346,
            "playing_audio_source_id": 1366,
        })
        self.assertTrue(snapshot.authoritative)
        no_push = normalize_websocket_event({
            "evtName": "OnDataToUI", "iddevice": 22,
            "data": {"volume_level": 41},
        }, "roomdevice")
        self.assertEqual(no_push.changes, {})
        self.assertFalse(no_push.authoritative)

    def test_room_selected_source_rest_validation(self) -> None:
        for value in (0, "1273"):
            event = normalize_rest_variables(22, "roomdevice", [
                {"varName": "CURRENT_SELECTED_DEVICE", "value": value},
            ])
            self.assertEqual(event.changes, {"selected_source_id": int(value)})
        for value in (-1, True, "1.5", "999999999999", 4294966299):
            event = normalize_rest_variables(22, "roomdevice", [
                {"varName": "CURRENT_SELECTED_DEVICE", "value": value},
            ])
            self.assertEqual(event.changes, {})

    def test_room_playing_audio_source_rest_validation(self) -> None:
        for value in (0, "1366"):
            event = normalize_rest_variables(22, "roomdevice", [
                {"varName": "PLAYING_AUDIO_DEVICE", "value": value},
            ])
            self.assertEqual(
                event.changes, {"playing_audio_source_id": int(value)}
            )
        for value in (-1, True, "1.5", "999999999999", 4294966299):
            event = normalize_rest_variables(22, "roomdevice", [
                {"varName": "PLAYING_AUDIO_DEVICE", "value": value},
            ])
            self.assertEqual(event.changes, {})

    def test_room_current_media_info_is_bounded_and_clears_when_off(self) -> None:
        artwork_url = "http://resources.tidal.com/images/album.jpg"
        playing = normalize_rest_variables(22, "roomdevice", [
            {"varName": "POWER_STATE", "value": 1},
            {"varName": "CURRENT MEDIA INFO", "value": {"mediainfo": {
                "title": "  Tender Surrender  ",
                "artist": "Steve Vai",
                "album": "The Infinite Steve Vai: An Anthology",
                "img": b64encode(artwork_url.encode()).decode(),
                "stationid": "private-token",
            }}},
        ])
        self.assertEqual(playing.changes, {
            "is_on": True,
            "media_title": "Tender Surrender",
            "media_artist": "Steve Vai",
            "media_album_name": "The Infinite Steve Vai: An Anthology",
            "media_image_url": artwork_url,
        })

        raw_artwork = normalize_rest_variables(22, "roomdevice", [{
            "varName": "CURRENT MEDIA INFO",
            "value": {"mediainfo": {"img": "https://media.example.test/art.jpg"}},
        }])
        self.assertEqual(
            raw_artwork.changes["media_image_url"],
            "https://media.example.test/art.jpg",
        )

        malformed = normalize_rest_variables(22, "roomdevice", [
            {"varName": "CURRENT MEDIA INFO", "value": "private-title"},
        ])
        self.assertEqual(malformed.changes, {})

        bounded = normalize_rest_variables(22, "roomdevice", [
            {"varName": "CURRENT MEDIA INFO", "value": {"mediainfo": {
                "title": "x" * 513, "artist": "bad\nlabel", "album": 123,
            }}},
        ])
        self.assertEqual(bounded.changes, {
            "media_title": None, "media_artist": None, "media_album_name": None,
            "media_image_url": None,
        })

        for image_url in (
            "/art.jpg",
            "data:image/png;base64,private",
            "file:///private/art.jpg",
            "https://user:password@private.invalid/art",
            "https://private.invalid:invalid/art",
            "https://private.invalid/" + "x" * 2049,
            b64encode(b"file:///private/art.jpg").decode(),
            b64encode(b"https://user:password@private.invalid/art").decode(),
        ):
            with self.subTest(image_url=image_url[:80]):
                invalid_artwork = normalize_rest_variables(22, "roomdevice", [{
                    "varName": "CURRENT MEDIA INFO",
                    "value": {"mediainfo": {"img": image_url}},
                }])
                self.assertEqual(invalid_artwork.changes, {
                    "media_title": None,
                    "media_artist": None,
                    "media_album_name": None,
                    "media_image_url": None,
                })

        stopped = normalize_rest_variables(22, "roomdevice", [
            {"varName": "POWER_STATE", "value": 0},
            {"varName": "CURRENT MEDIA INFO", "value": ""},
        ])
        self.assertEqual(stopped.changes, {
            "is_on": False,
            "media_title": None, "media_artist": None, "media_album_name": None,
            "media_image_url": None,
        })

    def test_programming_mirror_is_not_room_volume_and_binding_can_change(self) -> None:
        room = normalize_rest_variables(22, "roomdevice", [
            {"varName": "CURRENT_VOLUME", "value": 41},
            {"varName": "CURRENT_VOLUME_DEVICE_ID", "value": 623},
        ])
        cache = DeviceStateCache()
        cache.apply(room)

        sony_mirror = normalize_websocket_event({
            "evtName": "OnDataToUI", "iddevice": 1227,
            "data": {"volume_level": 0},
        }, "tv")
        self.assertEqual(sony_mirror.changes, {})
        self.assertFalse(sony_mirror.authoritative)
        cache.apply(sony_mirror)
        self.assertEqual(cache.snapshot(22), {"volume_percent": 41.0, "volume_device_id": 623})

        cache.apply(normalize_rest_variables(22, "roomdevice", [
            {"varName": "CURRENT_VOLUME_DEVICE_ID", "value": 999},
        ]))
        self.assertEqual(cache.snapshot(22)["volume_device_id"], 999)

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

    def test_gree_single_setpoint_profile_requires_explicit_zero_separate_fields(self) -> None:
        variables = [
            {"varName": "SCALE", "value": "CELSIUS"},
            {"varName": "HVAC_MODE", "value": "COOL"},
            {"varName": "SINGLE_SETPOINT_C", "value": 22},
            {"varName": "HEAT_SETPOINT_C", "value": 0},
            {"varName": "COOL_SETPOINT_C", "value": 0},
        ]
        snapshot = normalize_rest_variables(404, "thermostatV2", variables)
        self.assertEqual(snapshot.changes["setpoint_profile"], "SINGLE")
        self.assertEqual(snapshot.changes["target_temperature_c"], 22.0)
        cache = DeviceStateCache()
        cache.apply(snapshot)
        for value, mode in ((21, "COOL"), (20, "HEAT")):
            cache.apply(normalize_websocket_event(
                {"evtName": "OnDataToUI", "iddevice": 404,
                 "data": {"setpoint_single_c": value, "hvac_mode": mode}},
                "thermostatV2",
            ))
            self.assertEqual(cache.snapshot(404)["target_temperature_c"], value)
            self.assertEqual(cache.snapshot(404)["setpoint_profile"], "SINGLE")

        missing_cool = normalize_rest_variables(404, "thermostatV2", variables[:-1])
        self.assertNotIn("setpoint_profile", missing_cool.changes)
        separate = normalize_rest_variables(2646, "thermostatV2", [
            {"varName": "SINGLE_SETPOINT_C", "value": 22},
            {"varName": "HEAT_SETPOINT_C", "value": 20},
            {"varName": "COOL_SETPOINT_C", "value": 24},
        ])
        self.assertNotIn("setpoint_profile", separate.changes)

    def test_thermostat_rest_hold_and_preset_use_observed_variables(self) -> None:
        living = normalize_rest_variables(2646, "thermostatV2", [
            {"varName": "HOLD_MODE", "value": "Off"},
            {"varName": "HOLD_MODES_LIST", "value": "Off,2 Hours,Until Next,Permanent"},
            {"varName": "PRESET", "value": "Morning Auto"},
        ])
        self.assertEqual(living.changes, {
            "hold_mode": "Off", "preset_mode": "Morning Auto",
        })
        kid = normalize_rest_variables(2811, "thermostatV2", [
            {"varName": "HOLD_MODE", "value": "Until Next"},
            {"varName": "V1 HOLDMODE", "value": "TO_NEXT"},
            {"varName": "PRESET", "value": "-"},
        ])
        self.assertEqual(kid.changes, {"hold_mode": "Until Next"})

    def test_thermostat_hold_and_preset_push_ignore_legacy_hint(self) -> None:
        events = [
            ({"hold_mode": "Off"}, {"hold_mode": "Off"}),
            ({"settings": {"holdmode": "OFF"}}, {}),
            ({"holdmode": "TO_NEXT"}, {}),
            ({"hold_mode": "Until Next"}, {"hold_mode": "Until Next"}),
            ({"preset": "Morning Auto"}, {"preset_mode": "Morning Auto"}),
            ({"preset": "Away"}, {"preset_mode": "Away"}),
        ]
        cache = DeviceStateCache()
        for data, expected in events:
            with self.subTest(data=data):
                event = normalize_websocket_event(
                    {"evtName": "OnDataToUI", "iddevice": 2646, "data": data},
                    "thermostatV2",
                )
                self.assertEqual(event.changes, expected)
                self.assertEqual(event.authoritative, bool(expected))
                cache.apply(event)
        self.assertEqual(cache.snapshot(2646), {
            "hold_mode": "Until Next", "preset_mode": "Away",
        })

    def test_thermostat_fan_mode_uses_direct_label_and_rest_variable(self) -> None:
        rest = normalize_rest_variables(2646, "thermostatV2", [
            {"varName": "FAN_MODE", "value": "Low"},
            {"varName": "FAN_MODES_LIST", "value": "Auto,Low,Medium,High"},
            {"varName": "V1 FANMODE", "value": "LOW"},
            {"varName": "V1 FANMODES", "value": "On,Auto"},
        ])
        self.assertEqual(rest.changes, {"fan_mode": "Low"})
        for data, expected in (
            ({"settings": {"fanmode": "AUTO"}}, {}),
            ({"fanmode": "MEDIUM"}, {}),
            ({"fan_mode": "Medium"}, {"fan_mode": "Medium"}),
            ({"fan_mode": "-"}, {}),
        ):
            with self.subTest(data=data):
                event = normalize_websocket_event(
                    {"evtName": "OnDataToUI", "iddevice": 2646, "data": data},
                    "thermostatV2",
                )
                self.assertEqual(event.changes, expected)
                self.assertEqual(event.authoritative, bool(expected))

    def test_thermostat_humidity_rest_and_push_use_observed_fields(self) -> None:
        rest = normalize_rest_variables(2646, "thermostatV2", [
            {"varName": "HUMIDITY", "value": 43},
            {"varName": "HUMIDITY_MODE", "value": "Auto"},
            {"varName": "HUMIDITY_STATE", "value": "Humidifying"},
            {"varName": "HUMIDIFY_SETPOINT", "value": 35},
            {"varName": "DEHUMIDIFY_SETPOINT", "value": 55},
        ])
        self.assertEqual(rest.changes, {
            "current_humidity": 43.0,
            "humidity_mode": "Auto",
            "humidity_state": "Humidifying",
            "humidify_setpoint": 35.0,
            "dehumidify_setpoint": 55.0,
        })
        push = normalize_websocket_event(
            {
                "evtName": "OnDataToUI",
                "iddevice": 2646,
                "data": {
                    "humidity": 44,
                    "humidity_mode": "Dehumidify",
                    "humidity_state": "Dehumidifying",
                    "setpoint_humidify": 36,
                    "setpoint_dehumidify": 54,
                },
            },
            "thermostatV2",
        )
        self.assertEqual(push.changes, {
            "current_humidity": 44.0,
            "humidity_mode": "Dehumidify",
            "humidity_state": "Dehumidifying",
            "humidify_setpoint": 36.0,
            "dehumidify_setpoint": 54.0,
        })
        invalid = normalize_websocket_event(
            {
                "evtName": "OnDataToUI",
                "iddevice": 2646,
                "data": {"humidity": 101, "setpoint_humidify": -1},
            },
            "thermostatV2",
        )
        self.assertEqual(invalid.changes, {})

    def test_kid_ac_fan_change_trace_merges_partial_push(self) -> None:
        cache = DeviceStateCache()
        for value in ("Medium", "High", "Medium", "Auto", "Low"):
            event = normalize_websocket_event(
                {"evtName": "OnDataToUI", "iddevice": 2811, "data": {"fan_mode": value}},
                "thermostatV2",
            )
            self.assertEqual(event.changes, {"fan_mode": value})
            self.assertTrue(event.authoritative)
            cache.apply(event)
            self.assertEqual(cache.snapshot(2811)["fan_mode"], value)
        self.assertEqual(cache.snapshot(2811), {"fan_mode": "Low"})

    def test_gree_direct_fan_and_empty_preset_clears_active_preset(self) -> None:
        cache = DeviceStateCache()
        for data, expected in (
            ({"preset": "Night Heat"}, {"preset_mode": "Night Heat"}),
            ({"fan_mode": "LOW"}, {"fan_mode": "LOW"}),
            ({"fanmode": "MEDIUM"}, {}),
            ({"fan_mode": "MEDIUM"}, {"fan_mode": "MEDIUM"}),
            ({"preset": ""}, {"preset_mode": None}),
        ):
            with self.subTest(data=data):
                event = normalize_websocket_event(
                    {"evtName": "OnDataToUI", "iddevice": 404, "data": data},
                    "thermostatV2",
                )
                self.assertEqual(event.changes, expected)
                self.assertEqual(event.authoritative, bool(expected))
                cache.apply(event)
        self.assertEqual(cache.snapshot(404), {"preset_mode": None, "fan_mode": "MEDIUM"})

    def test_rest_on_off_only_light_state_is_readable(self) -> None:
        off = normalize_rest_variables(978, "light_v2", [{"varName": "LIGHT_STATE", "value": 0}])
        on = normalize_rest_variables(719, "light_v2", [{"varName": "LIGHT_STATE", "value": 1}])
        self.assertEqual(off.changes, {"is_on": False})
        self.assertEqual(on.changes, {"is_on": True})

    def test_on_off_only_ceiling_light_has_settled_push_feedback(self) -> None:
        for level, expected in ((100, True), (0, False)):
            with self.subTest(level=level):
                event = normalize_websocket_event(
                    {"evtName": "OnDataToUI", "iddevice": 1150, "data": {"LIGHT_LEVEL": level}},
                    "light_v2",
                )
                self.assertEqual(event.changes, {"brightness_percent": float(level), "is_on": expected})
                self.assertTrue(event.authoritative)

    def test_radiant_floor_relay_uses_verified_settled_feedback(self) -> None:
        proxy = "relaysingle_radiantfloor_c4"
        early = normalize_websocket_event(
            {"evtName": "OnDataToUI", "iddevice": 579, "data": {"state": False}}, proxy
        )
        self.assertEqual(early.changes, {})
        self.assertFalse(early.authoritative)
        for current, expected in (("OPENED", False), ("CLOSED", True)):
            with self.subTest(current=current):
                event = normalize_websocket_event(
                    {"evtName": "OnDataToUI", "iddevice": 579, "data": {
                        "relay_state": {
                            "current_state": current, "feedback_bound": True,
                            "is_verified": True,
                        }
                    }}, proxy,
                )
                self.assertEqual(event.changes, {"is_on": expected})
                self.assertTrue(event.authoritative)
        unverified = normalize_websocket_event(
            {"evtName": "OnDataToUI", "iddevice": 579, "data": {
                "relay_state": {"current_state": "CLOSED", "feedback_bound": True, "is_verified": False}
            }}, proxy,
        )
        self.assertEqual(unverified.changes, {})
        self.assertFalse(unverified.authoritative)

        on = normalize_rest_variables(579, proxy, [{"varName": "RelayState", "value": 1}])
        off = normalize_rest_variables(579, proxy, [{"varName": "RelayState", "value": "0"}])
        unknown = normalize_rest_variables(579, proxy, [{"varName": "RelayState", "value": 2}])
        self.assertEqual(on.changes, {"is_on": True})
        self.assertEqual(off.changes, {"is_on": False})
        self.assertEqual(unknown.changes, {})

    def test_sony_plug_regular_relay_has_the_same_verified_mapping(self) -> None:
        proxy = "relaysingle_relay_c4"
        for current, expected in (("OPENED", False), ("CLOSED", True)):
            with self.subTest(current=current):
                early = normalize_websocket_event(
                    {"evtName": "OnDataToUI", "iddevice": 431, "data": {"state": expected}}, proxy
                )
                self.assertEqual(early.changes, {})
                self.assertFalse(early.authoritative)
                verified = normalize_websocket_event(
                    {"evtName": "OnDataToUI", "iddevice": 431, "data": {
                        "relay_state": {
                            "current_state": current, "feedback_bound": True, "is_verified": True,
                        }
                    }}, proxy,
                )
                self.assertEqual(verified.device_id, 431)
                self.assertEqual(verified.changes, {"is_on": expected})
                self.assertTrue(verified.authoritative)
        self.assertEqual(
            normalize_rest_variables(431, proxy, [{"varName": "RelayState", "value": 1}]).changes,
            {"is_on": True},
        )

    def test_contact_sensors_use_user_confirmed_triggered_mapping(self) -> None:
        for device_id, proxy in (
            (335, "contactsingle_doorcontactsensor_c4"),
            (377, "contactsingle_windowcontactsensor_c4"),
            (378, "contactsingle_motionsensor_c4"),
        ):
            with self.subTest(device_id=device_id):
                for current in ("OPENED", "CLOSED"):
                    for early in (
                        {"Action": {"Name": "state", "Value": {"state": current == "CLOSED"}}},
                        {"state": current == "CLOSED"},
                    ):
                        event = normalize_websocket_event(
                            {"evtName": "OnDataToUI", "iddevice": device_id, "data": early}, proxy
                        )
                        self.assertEqual(event.changes, {})
                        self.assertFalse(event.authoritative)
                    verified = normalize_websocket_event(
                        {"evtName": "OnDataToUI", "iddevice": device_id, "data": {
                            "contact_state": {
                                "current_state": current,
                                "feedback_bound": True,
                                "is_verified": True,
                            }
                        }}, proxy,
                    )
                    self.assertEqual(verified.changes, {
                        "director_contact_state": current,
                        "is_on": current == "OPENED",
                    })
                    self.assertTrue(verified.authoritative)
                unverified = normalize_websocket_event(
                    {"evtName": "OnDataToUI", "iddevice": device_id, "data": {
                        "contact_state": {
                            "current_state": "CLOSED", "feedback_bound": True,
                            "is_verified": False,
                        }
                    }}, proxy,
                )
                self.assertEqual(unverified.changes, {})
                self.assertFalse(unverified.authoritative)
                for value, label in ((0, "OPENED"), (1, "CLOSED")):
                    rest = normalize_rest_variables(
                        device_id, proxy, [{"varName": "ContactState", "value": value}]
                    )
                    self.assertEqual(rest.changes, {
                        "director_contact_state": label,
                        "is_on": value == 0,
                    })
                invalid = normalize_rest_variables(
                    device_id, proxy, [{"varName": "ContactState", "value": 2}]
                )
                self.assertEqual(invalid.changes, {})

    def test_uibutton_driver_information_is_not_switch_state(self) -> None:
        event = normalize_websocket_event(
            {
                "evtName": "OnDataToUI",
                "iddevice": 319,
                "data": {
                    "devicecommand": {
                        "command": "UPDATE_PROPERTY",
                        "params": {"param": [
                            {"name": "Name", "value": {"static": "Driver Information"}},
                            {"name": "Value", "value": {"static": "Device switched on externally"}},
                        ]},
                    }
                },
            },
            "uibutton",
        )
        self.assertIsNotNone(event)
        self.assertEqual(event.device_id, 319)
        self.assertEqual(event.changes, {})
        self.assertFalse(event.authoritative)

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
