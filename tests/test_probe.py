"""Offline checks for the read-only WebSocket probe's inventory output."""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import io
import json
import unittest
from unittest.mock import AsyncMock, patch
from xml.etree import ElementTree

from tools.websocket_probe import (
    Probe,
    build_tidal_tabs_soap,
    fetch_contact_state_value,
    fetch_device_metadata,
    fetch_inventory,
    media_event_projection,
    media_output_status_shape,
    media_output_status_values,
    media_room_metadata_summary,
    request_tidal_tabs,
    safe_state_value,
    safe_thermostat_label,
    tidal_browse_projection,
    TIDAL_PROBE_SEQUENCE,
)


class FakeResponse:
    status = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def text(self):
        return json.dumps([
            {"id": 319, "name": "Vent", "typeName": "device", "proxy": "uibutton"},
            {"id": 3177, "name": "Other light", "typeName": "device", "proxy": "light_v2"},
            {"id": 500, "name": "Test relay", "typeName": "device", "proxy": "relaysingle_relay_c4", "roomName": "Test room"},
            {"id": 501, "name": "Gate", "typeName": "device", "proxy": "relaysingle_electronicgate_c4"},
            {"id": 600, "name": "iKid Heating Panel", "typeName": "device", "proxy": "switch"},
            {"id": 601, "name": "Kid Ceiling", "typeName": "device", "proxy": "light_v2", "capabilities": {"on_off": True, "dimmer": False}},
        ])


class FakeSession:
    def get(self, url, *, headers):
        self.headers = headers
        return FakeResponse()


class FakePostResponse:
    status = 202

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def read(self):
        return b"private-response-body"


class FakePostSession:
    def __init__(self):
        self.calls = []

    def post(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return FakePostResponse()


class FakePayloadResponse(FakeResponse):
    def __init__(self, payload):
        self.payload = payload

    async def text(self):
        return json.dumps(self.payload)


class FakeMetadataSession:
    def get(self, url, *, headers):
        if url.endswith("/variables"):
            return FakePayloadResponse([
                {"varName": "RelayState", "value": 1},
                {"varName": "ContactState", "value": 0},
                {"varName": "Driver Secret", "value": "never-print-this"},
                {"varName": "LIGHT_STATE", "value": "ON"},
            ])
        if url.endswith("/commands"):
            return FakePayloadResponse([
                {"deviceId": 500, "command": "CLOSE", "params": []},
                {"deviceId": 500, "command": "OPEN", "params": []},
                {"deviceId": 999, "command": "OTHER_DEVICE", "params": []},
            ])
        raise AssertionError("unexpected read-only endpoint")


class FakeContactSession:
    def __init__(self):
        self.calls = []
        self.value = 0

    def get(self, url, *, headers):
        self.calls.append((url, headers))
        if not url.endswith("/377/variables"):
            raise AssertionError("unexpected read-only endpoint")
        return FakePayloadResponse([
            {"varName": "ContactState", "value": self.value},
            {"varName": "Driver Secret", "value": "never-print-this"},
        ])


class FakeThermostatSession:
    def get(self, url, *, headers):
        if url.endswith("/variables"):
            return FakePayloadResponse([
                {"varName": "HOLD_MODE", "value": "Permanent"},
                {"varName": "HOLD_MODES_LIST", "value": "Off, Temporary, Permanent"},
                {"varName": "PRESET_NAME", "value": "Night"},
                {"varName": "PRESET_SECRET", "value": "private-test-token"},
                {"varName": "FAN_MODE", "value": "Low"},
                {"varName": "FAN_MODES_LIST", "value": "Auto,Low,Medium,High"},
                {"varName": "V1 FANMODES", "value": "On,Auto"},
                {"varName": "FAN_STATE", "value": "ON"},
                {"varName": "FAN_SECRET", "value": "never-print-this"},
                {"varName": "SINGLE_SETPOINT_C", "value": 26},
                {"varName": "HEAT_SETPOINT_C", "value": 23},
                {"varName": "COOL_SETPOINT_C", "value": "private-test-token"},
                {"varName": "SETPOINT_SECRET", "value": 24},
                {"varName": "HUMIDITY", "value": 47},
                {"varName": "HUMIDITY_MODE", "value": "Auto"},
                {"varName": "HUMIDITY_STATE", "value": "Idle"},
                {"varName": "HUMIDIFY_SETPOINT", "value": 35},
                {"varName": "DEHUMIDIFY_SETPOINT", "value": 55},
                {"varName": "HUMIDITY_SETPOINTS_DEADBAND", "value": 10},
                {"varName": "OTHER", "value": "never-print-this"},
            ])
        if url.endswith("/commands"):
            return FakePayloadResponse([
                {"deviceId": 2646, "command": "SET_MODE_HOLD", "params": [
                    {"name": "MODE", "values": [
                        {"value": "Off"}, {"value": "Permanent"},
                        {"value": "Bearer private-test-token"},
                    ]},
                ]},
                {"deviceId": 2646, "command": "SET_PRESET", "params": [
                    {"name": "NAME", "values": [{"value": "Night"}, {"value": "Away"}]},
                ]},
                {"deviceId": 2646, "command": "SET_MODE_FAN", "params": [
                    {"name": "MODE", "values": [
                        {"value": "Auto"}, {"value": "Low"},
                        {"value": "Medium"}, {"value": "High"},
                        {"value": "Bearer private-test-token"},
                    ]},
                ]},
                {"deviceId": 2646, "command": "SET_SETPOINT_SINGLE", "params": [
                    {"name": "CELSIUS", "low": 16, "high": 30,
                     "resolution": 1, "valueType": "INTEGER", "secret": "never-print-this"},
                ]},
                {"deviceId": 2646, "command": "SET_SETPOINT_HEAT", "params": [
                    {"name": "CELSIUS", "low": 6, "high": 1000,
                     "resolution": 0.5, "valueType": "FLOAT"},
                ]},
                {"deviceId": 2646, "command": "SET_MODE_HUMIDITY", "params": [
                    {"name": "MODE", "values": [
                        {"value": "Off"}, {"value": "Auto"},
                        {"value": "Bearer private-test-token"},
                    ]},
                ]},
                {"deviceId": 2646, "command": "INC_SETPOINT_HUMIDIFY", "params": []},
                {"deviceId": 2646, "command": "DEC_SETPOINT_HUMIDIFY", "params": []},
                {"deviceId": 2646, "command": "INC_SETPOINT_DEHUMIDIFY", "params": []},
                {"deviceId": 2646, "command": "DEC_SETPOINT_DEHUMIDIFY", "params": []},
                {"deviceId": 2811, "command": "SET_PRESET", "params": [
                    {"name": "NAME", "values": [{"value": "Other device"}]},
                ]},
            ])
        raise AssertionError("unexpected read-only endpoint")


class FakeMediaRoomSession:
    def get(self, url, *, headers):
        if url.endswith("/variables"):
            return FakePayloadResponse([
                {"varName": "POWER_STATE", "value": 1},
                {"varName": "IS_MUTED", "value": False},
                {"varName": "CURRENT_VOLUME", "value": "42"},
                {"varName": "CURRENT_AUDIO_DEVICE", "value": 624},
                {"varName": "CURRENT_VOLUME_DEVICE_ID", "value": 623},
                {"varName": "CURRENT_VIDEO_DEVICE", "value": 99999},
                {"varName": "CURRENT MEDIA INFO", "value": "private-title"},
                {"varName": "PRIVATE_TOKEN", "value": "private-test-token"},
            ])
        if url.endswith("/commands"):
            return FakePayloadResponse([
                {"deviceId": 22, "command": "ROOM_OFF", "params": []},
                {"deviceId": 22, "command": "SET_VOLUME_LEVEL", "params": []},
                {"deviceId": 22, "command": "PLAY", "params": []},
                {"deviceId": 23, "command": "ROOM_OFF", "params": []},
            ])
        raise AssertionError("unexpected read-only endpoint")


class ProbeInventoryTests(unittest.IsolatedAsyncioTestCase):
    def test_tidal_soap_builds_bounded_send_to_device_async_message(self) -> None:
        message = build_tidal_tabs_soap(
            1417,
            96,
            "navigator-id",
        )
        self.assertTrue(message.endswith(b"\0"))
        root = ElementTree.fromstring(message[:-1])
        self.assertEqual(root.attrib["name"], "SendToDeviceAsync")
        self.assertEqual(root.find("./param[@name='iddevice']").text, "1417")
        self.assertEqual(root.findtext("./param/devicecommand/command"), "GetTabs")
        values = {
            param.findtext("name"): param.findtext("value/static", default="")
            for param in root.findall("./param/devicecommand/params/param")
        }
        self.assertEqual(values["ROOMID"], "96")
        self.assertEqual(values["NAVID"], "navigator-id")
        self.assertEqual(values["SEQ"], str(TIDAL_PROBE_SEQUENCE))

    def test_state_value_allowlist_rejects_arbitrary_strings_and_large_numbers(self) -> None:
        self.assertEqual(safe_state_value("CLOSED"), "CLOSED")
        self.assertEqual(safe_state_value("100"), "100")
        self.assertIsNone(safe_state_value("private-test-token"))
        self.assertIsNone(safe_state_value("123456789012345678"))

    def test_thermostat_label_rejects_credential_like_values(self) -> None:
        self.assertEqual(safe_thermostat_label("Permanent"), "Permanent")
        self.assertEqual(safe_thermostat_label(" Night "), "Night")
        self.assertIsNone(safe_thermostat_label("Bearer private-test-token"))
        self.assertIsNone(safe_thermostat_label("A" * 40))
        self.assertIsNone(safe_thermostat_label("https://example.invalid/secret"))
        self.assertIsNone(safe_thermostat_label("line\nbreak"))

    async def test_watched_item_is_reported_without_token(self) -> None:
        output = io.StringIO()
        session = FakeSession()
        with redirect_stdout(output):
            item_ids = await fetch_inventory(
                session, "127.0.0.1", "private-test-token", {319, 999}
            )
        inventory, record, relays = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(inventory["record_type"], "inventory")
        self.assertEqual(record["record_type"], "watched_inventory")
        self.assertEqual(relays["record_type"], "relay_inventory")
        self.assertEqual(item_ids, {319, 3177, 500, 501, 600, 601})
        self.assertEqual(
            record["watched_items"],
            [{"id": 319, "name": "Vent", "typeName": "device", "proxy": "uibutton"}],
        )
        self.assertEqual(record["missing_watch_ids"], [999])
        self.assertEqual(relays["candidate_count"], 1)
        self.assertEqual(relays["items"], [{
            "id": 500, "name": "Test relay", "typeName": "device",
            "proxy": "relaysingle_relay_c4", "roomName": "Test room",
        }])
        self.assertFalse(relays["truncated"])
        self.assertNotIn("private-test-token", output.getvalue())

    async def test_name_lookup_is_bounded_and_shows_only_selected_capabilities(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            await fetch_inventory(
                FakeSession(), "127.0.0.1", "private-test-token",
                name_terms=["iKid Heating Panel", "ceiling"],
            )
        records = [json.loads(line) for line in output.getvalue().splitlines()]
        matches = next(record for record in records if record["record_type"] == "matched_inventory")
        self.assertEqual(matches["match_count"], 2)
        self.assertEqual([item["id"] for item in matches["items"]], [600, 601])
        self.assertEqual(matches["items"][1]["capabilities"], {"on_off": True, "dimmer": False})
        self.assertNotIn("private-test-token", output.getvalue())

    async def test_metadata_view_is_read_only_and_omits_unrelated_values(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            await fetch_device_metadata(
                FakeMetadataSession(), "127.0.0.1", "private-test-token", {500, 999}, {500}
            )
        missing, metadata = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(missing["record_type"], "missing_metadata_ids")
        self.assertEqual(missing["ids"], [999])
        self.assertEqual(metadata["iddevice"], 500)
        self.assertEqual(metadata["state_values"], {
            "RelayState": 1, "ContactState": 0, "LIGHT_STATE": "ON",
        })
        self.assertEqual([item["command"] for item in metadata["commands"]], ["CLOSE", "OPEN"])
        self.assertIn("Driver Secret", metadata["variable_names"])
        self.assertNotIn("never-print-this", output.getvalue())
        self.assertNotIn("private-test-token", output.getvalue())

    async def test_thermostat_metadata_reports_only_bounded_hold_preset_fan_evidence(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            await fetch_device_metadata(
                FakeThermostatSession(), "127.0.0.1", "private-test-token", {2646}, {2646}
            )
        records = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual([record["record_type"] for record in records], [
            "device_metadata", "thermostat_metadata",
        ])
        details = records[1]
        self.assertEqual(details["state_values"], {
            "HOLD_MODE": "Permanent", "HOLD_MODES_LIST": "Off, Temporary, Permanent",
            "PRESET_NAME": "Night", "FAN_MODE": "Low",
            "FAN_MODES_LIST": "Auto,Low,Medium,High", "FAN_STATE": "ON",
            "V1 FANMODES": "On,Auto",
        })
        self.assertNotIn("PRESET_SECRET", details["candidate_variable_names"])
        self.assertNotIn("FAN_SECRET", details["candidate_variable_names"])
        self.assertEqual(details["commands"][0]["parameters"][0]["choices"], [
            "Off", "Permanent",
        ])
        self.assertTrue(details["commands"][0]["parameters"][0]["choices_truncated_or_redacted"])
        self.assertEqual(details["commands"][1]["parameters"][0]["choices"], ["Night", "Away"])
        self.assertEqual(details["commands"][2]["command"], "SET_MODE_FAN")
        self.assertEqual(details["commands"][2]["parameters"][0]["choices"], [
            "Auto", "Low", "Medium", "High",
        ])
        self.assertTrue(details["commands"][2]["parameters"][0]["choices_truncated_or_redacted"])
        self.assertEqual(details["setpoint_variable_names"], [
            "COOL_SETPOINT_C", "HEAT_SETPOINT_C", "SINGLE_SETPOINT_C",
        ])
        self.assertEqual(details["setpoint_state_values"], {
            "HEAT_SETPOINT_C": 23, "SINGLE_SETPOINT_C": 26,
        })
        self.assertEqual(details["setpoint_commands"], [
            {"command": "SET_SETPOINT_SINGLE", "parameters": [
                {"name": "CELSIUS", "low": 16, "high": 30,
                 "resolution": 1, "value_type": "INTEGER"},
            ]},
            {"command": "SET_SETPOINT_HEAT", "parameters": [
                {"name": "CELSIUS", "low": 6, "resolution": 0.5,
                 "value_type": "FLOAT"},
            ]},
        ])
        self.assertEqual(details["humidity_variable_names"], [
            "DEHUMIDIFY_SETPOINT", "HUMIDIFY_SETPOINT", "HUMIDITY",
            "HUMIDITY_MODE", "HUMIDITY_SETPOINTS_DEADBAND", "HUMIDITY_STATE",
        ])
        self.assertEqual(details["humidity_state_values"], {
            "HUMIDITY": 47, "HUMIDITY_MODE": "Auto", "HUMIDITY_STATE": "Idle",
            "HUMIDIFY_SETPOINT": 35, "DEHUMIDIFY_SETPOINT": 55,
            "HUMIDITY_SETPOINTS_DEADBAND": 10,
        })
        self.assertEqual(details["humidity_commands"], [
            {"command": "SET_MODE_HUMIDITY", "parameters": [{
                "name": "MODE", "choice_count": 3, "choices": ["Off", "Auto"],
                "choices_truncated_or_redacted": True,
            }]},
            {"command": "INC_SETPOINT_HUMIDIFY", "parameters": []},
            {"command": "DEC_SETPOINT_HUMIDIFY", "parameters": []},
            {"command": "INC_SETPOINT_DEHUMIDIFY", "parameters": []},
            {"command": "DEC_SETPOINT_DEHUMIDIFY", "parameters": []},
        ])
        self.assertNotIn("private-test-token", output.getvalue())
        self.assertNotIn("never-print-this", output.getvalue())

    async def test_contact_rest_sample_reads_only_allowlisted_value_after_verified_push(self) -> None:
        session = FakeContactSession()
        args = argparse.Namespace(
            host="127.0.0.1", watch_ids={377}, inspect_ids=set(), max_events=0,
            sample_contact_rest=True,
        )
        probe = Probe(args, session)
        probe._token = "private-test-token"
        output = io.StringIO()
        with redirect_stdout(output):
            await probe.on_event(377, {
                "evtName": "OnDataToUI", "iddevice": 377, "data": {"state": False},
            })
            self.assertEqual(session.calls, [])
            verified_open = {
                "evtName": "OnDataToUI", "iddevice": 377,
                "data": {"contact_state": {
                    "current_state": "OPENED", "feedback_bound": True, "is_verified": True,
                }},
            }
            await probe.on_event(377, verified_open)
            await probe.on_event(377, verified_open)
            self.assertEqual(len(session.calls), 1)
            session.value = 1
            await probe.on_event(377, {
                "evtName": "OnDataToUI", "iddevice": 377,
                "data": {"contact_state": {
                    "current_state": "CLOSED", "feedback_bound": True, "is_verified": True,
                }},
            })
        samples = [
            json.loads(line) for line in output.getvalue().splitlines()
            if '"record_type": "contact_rest_sample"' in line
        ]
        self.assertEqual([(item["director_contact_state"], item["contact_state_value"])
                          for item in samples], [("OPENED", 0), ("CLOSED", 1)])
        self.assertEqual(len(session.calls), 2)
        self.assertTrue(all(call[0].startswith("https://127.0.0.1/api/v1/items/377/") for call in session.calls))
        self.assertNotIn("private-test-token", output.getvalue())
        self.assertNotIn("never-print-this", output.getvalue())

    async def test_contact_state_helper_returns_only_simple_value(self) -> None:
        session = FakeContactSession()
        self.assertEqual(
            await fetch_contact_state_value(session, "127.0.0.1", "private-test-token", 377),
            0,
        )
        self.assertEqual(len(session.calls), 1)

    async def test_media_room_metadata_excludes_track_details_and_unknown_ids(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            await fetch_device_metadata(
                FakeMediaRoomSession(), "127.0.0.1", "private-test-token", {22}, {22, 623, 624}
            )
        records = [json.loads(line) for line in output.getvalue().splitlines()]
        details = next(record for record in records if record["record_type"] == "media_room_metadata")
        self.assertEqual(details["commands"], ["PLAY", "ROOM_OFF", "SET_VOLUME_LEVEL"])
        self.assertEqual(details["state_values"], {
            "POWER_STATE": 1, "IS_MUTED": False,
            "CURRENT_VOLUME": "42", "CURRENT_AUDIO_DEVICE": 624,
            "CURRENT_VOLUME_DEVICE_ID": 623,
        })
        self.assertNotIn("private-title", output.getvalue())
        self.assertNotIn("private-test-token", output.getvalue())

    def test_media_room_metadata_requires_room_command(self) -> None:
        self.assertIsNone(media_room_metadata_summary(
            [{"varName": "POWER_STATE", "value": 1}],
            [{"deviceId": 22, "command": "PLAY"}], 22, {22},
        ))

    async def test_media_safe_event_excludes_titles_urls_and_untrusted_header_fields(self) -> None:
        args = argparse.Namespace(
            host="127.0.0.1", watch_ids={22}, inspect_ids=set(),
            max_events=0, media_safe=True, sample_contact_rest=False,
        )
        probe = Probe(args, FakeSession())
        output = io.StringIO()
        with redirect_stdout(output):
            await probe.on_event(22, {
                "evtName": "Bearer private-test-token", "iddevice": 22,
                "time": "private-test-token",
                "data": {
                    "volume_level": 42, "is_muted": True,
                    "media_info": {"title": "private-title", "art_url": "https://private.invalid/token"},
                    "Bearer private-title": "private-test-token",
                    "access_token": "private-test-token",
                },
            })
        records = [json.loads(line) for line in output.getvalue().splitlines()]
        event = next(record for record in records if record["record_type"] == "event")
        self.assertEqual(event["iddevice"], 22)
        self.assertEqual(event["event_type"], "other")
        self.assertIsNone(event["director_time"])
        self.assertEqual(event["state_payload"], {"volume_level": 42, "is_muted": True})
        self.assertEqual(event["nested_keys"]["media_info"], ["art_url", "title"])
        self.assertNotIn("private-title", output.getvalue())
        self.assertNotIn("private-test-token", output.getvalue())
        self.assertNotIn("private.invalid", output.getvalue())

    def test_media_projection_bounded_and_scalar_only(self) -> None:
        projected = media_event_projection({
            "current_volume": 101, "power_state": "secret", "is_muted": 1,
            "media_info": {"title": "private-title", "art_url": "https://private.invalid"},
        })
        self.assertEqual(projected["state_payload"], {"is_muted": True})
        self.assertEqual(projected["nested_keys"], {"media_info": ["art_url", "title"]})

    def test_media_projection_only_exposes_allowlisted_protocol_labels(self) -> None:
        projected = media_event_projection({
            "devicecommand": {
                "command": "PAUSE",
                "params": {"title": "private-title", "access_token": "private-token"},
            },
        })
        self.assertEqual(projected["safe_protocol_label"], "PAUSE")
        self.assertNotIn("private-title", json.dumps(projected))
        self.assertNotIn("private-token", json.dumps(projected))

        for secret_label in ("Bearer private-token", "pause", "PAUSE private-token"):
            projected = media_event_projection({
                "devicecommand": {"command": secret_label},
                "EVENT": {"NAME": secret_label},
            })
            self.assertNotIn("safe_protocol_label", projected)
            self.assertNotIn("private-token", json.dumps(projected))

        self.assertEqual(
            media_event_projection({"EVENT": {"NAME": "PLAYING", "ARGS": "private-title"}})["safe_protocol_label"],
            "PLAYING",
        )

    def test_output_status_shape_never_exposes_media_text_or_credentials(self) -> None:
        raw_json = json.dumps({
            "volume": 44, "title": "private-title",
            "access_token": "private-test-token", "outputs": [{"level": 44}],
        })
        projected = media_event_projection({"OutputStatus": raw_json})
        self.assertEqual(projected["output_status_shape"], {
            "value_type": "str", "length": len(raw_json), "format": "json_like",
            "keys": ["outputs", "title", "volume"],
            "field_types": {"outputs": "list", "title": "str", "volume": "int"},
        })
        self.assertNotIn("private-title", json.dumps(projected))
        self.assertNotIn("private-test-token", json.dumps(projected))

        raw_xml = '<OutputStatus secret="private-test-token"><Volume>44</Volume><Title>private-title</Title></OutputStatus>'
        xml_shape = media_output_status_shape(raw_xml)
        self.assertEqual(xml_shape["xml_tags"], ["OutputStatus", "Title", "Volume"])
        self.assertEqual(xml_shape["xml_attributes"], [])
        self.assertNotIn("private-title", json.dumps(xml_shape))
        self.assertNotIn("private-test-token", json.dumps(xml_shape))
        self.assertEqual(media_output_status_shape("x" * 5000)["format"], "oversize")

    def test_output_status_values_only_allow_observed_bounded_fields(self) -> None:
        raw = {
            "OutputID": 1, "VolumeLevel": 43, "MuteFlag": False,
            "InputID": 1751, "Title": "private-title",
            "access_token": "private-test-token",
        }
        self.assertEqual(media_output_status_values(raw), {
            "OutputID": 1, "VolumeLevel": 43, "MuteFlag": False,
        })
        projected = media_event_projection({"OutputStatus": raw})
        self.assertEqual(projected["output_status_values"], {
            "OutputID": 1, "VolumeLevel": 43, "MuteFlag": False,
        })
        self.assertNotIn("private-title", json.dumps(projected))
        self.assertNotIn("private-test-token", json.dumps(projected))
        self.assertEqual(media_output_status_values({
            "OutputID": True, "VolumeLevel": 101, "MuteFlag": "false",
        }), {})
        self.assertEqual(media_output_status_values({"OutputID": 1227}), {"OutputID": 1227})
        self.assertEqual(media_output_status_values({"OutputID": 2147483648}), {})

    def test_tidal_browse_projection_preserves_contract_and_redacts_secrets(self) -> None:
        projected = tidal_browse_projection({
            "devicecommand": {
                "command": "DATA_RECEIVED",
                "params": [
                    {"name": "NAVID", "value": "browse-12"},
                    {"name": "access_token", "value": "private-token"},
                    {
                        "name": "DATA",
                        "value": (
                            '<List><item id="123" artwork="https://example.test/private.jpg">'
                            '<title>Evening Mix</title><creator>Example Artist</creator>'
                            '<password>private-password</password></item></List>'
                        ),
                    },
                ],
            },
        })
        rendered = json.dumps(projected)
        self.assertIn("DATA_RECEIVED", rendered)
        self.assertIn("browse-12", rendered)
        self.assertIn("Evening Mix", rendered)
        self.assertIn("Example Artist", rendered)
        self.assertIn('id=\\"123\\"', rendered)
        self.assertNotIn("private-token", rendered)
        self.assertNotIn("private-password", rendered)
        self.assertNotIn("example.test", rendered)
        self.assertIn("url-redacted", rendered)

    async def test_tidal_get_tabs_uses_only_read_only_proxy_command(self) -> None:
        session = FakePostSession()
        output = io.StringIO()
        with redirect_stdout(output):
            await request_tidal_tabs(
                session,
                "127.0.0.1",
                "private-token",
                1417,
                22,
                "assigned-navigator-id",
            )
        args, kwargs = session.calls[0]
        self.assertEqual(args, ("https://127.0.0.1/api/v1/items/1417/commands",))
        self.assertEqual(kwargs["json"]["command"], "GetTabs")
        self.assertEqual(kwargs["json"]["tParams"]["ROOMID"], 22)
        self.assertEqual(kwargs["json"]["tParams"]["NAVID"], "assigned-navigator-id")
        self.assertEqual(kwargs["json"]["tParams"]["SEQ"], TIDAL_PROBE_SEQUENCE)
        self.assertEqual(kwargs["json"]["tParams"]["ARGS"], "<args></args>")
        self.assertNotIn("private-token", output.getvalue())
        self.assertNotIn("private-response-body", output.getvalue())
        self.assertIn('"http_status": 202', output.getvalue())

    async def test_inventory_only_does_not_open_websocket(self) -> None:
        args = argparse.Namespace(
            host="127.0.0.1", token_python="unused", token_helper="unused",
            watch_ids=set(), find_name=[], inspect_ids=set(), inventory_only=True,
        )
        output = io.StringIO()
        with patch("tools.websocket_probe.verify_tcp_reachable", new_callable=AsyncMock), \
             patch("tools.websocket_probe.asyncio.to_thread", new_callable=AsyncMock, return_value="private-test-token"), \
             redirect_stdout(output):
            await Probe(args, FakeSession()).run()
        records = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertIn("REST_AUTH_OK", [r.get("milestone") for r in records])
        self.assertNotIn("WS_CONNECTING", [r.get("milestone") for r in records])
        self.assertNotIn("private-test-token", output.getvalue())

    async def test_inventory_only_fails_without_retrying_when_unreachable(self) -> None:
        args = argparse.Namespace(host="127.0.0.1", watch_ids=set(), find_name=[], inspect_ids=set(), inventory_only=True)
        output = io.StringIO()
        with patch("tools.websocket_probe.verify_tcp_reachable", new_callable=AsyncMock) as reach, \
             redirect_stdout(output):
            reach.side_effect = OSError("unreachable")
            with self.assertRaisesRegex(RuntimeError, "inventory-only probe failed"):
                await Probe(args, FakeSession()).run()
        self.assertEqual(reach.await_count, 1)
        self.assertIn('"record_type": "connection_error"', output.getvalue())


if __name__ == "__main__":
    unittest.main()
