"""Offline checks for the read-only WebSocket probe's inventory output."""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import io
import json
import unittest
from unittest.mock import AsyncMock, patch

from tools.websocket_probe import Probe, fetch_device_metadata, fetch_inventory, safe_state_value


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


class ProbeInventoryTests(unittest.IsolatedAsyncioTestCase):
    def test_state_value_allowlist_rejects_arbitrary_strings_and_large_numbers(self) -> None:
        self.assertEqual(safe_state_value("CLOSED"), "CLOSED")
        self.assertEqual(safe_state_value("100"), "100")
        self.assertIsNone(safe_state_value("private-test-token"))
        self.assertIsNone(safe_state_value("123456789012345678"))

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
        self.assertEqual(metadata["state_values"], {"RelayState": 1, "LIGHT_STATE": "ON"})
        self.assertEqual([item["command"] for item in metadata["commands"]], ["CLOSE", "OPEN"])
        self.assertIn("Driver Secret", metadata["variable_names"])
        self.assertNotIn("never-print-this", output.getvalue())
        self.assertNotIn("private-test-token", output.getvalue())

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
