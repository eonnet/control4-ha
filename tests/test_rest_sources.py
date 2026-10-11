"""The source catalog must remain a read-only, shape-checked REST call."""

from __future__ import annotations

import unittest

from custom_components.control4_advanced.transport.rest import DirectorRestClient, DirectorRestError


class FakeRest(DirectorRestClient):
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    async def request_json(self, method, path, *, body=None):
        self.calls.append((method, path, body))
        return self.payload


class SourceRestTests(unittest.IsolatedAsyncioTestCase):
    async def test_ui_configuration_is_read_with_get(self):
        rest = FakeRest({"experiences": []})
        self.assertEqual(await rest.get_ui_configuration(), {"experiences": []})
        self.assertEqual(rest.calls, [("GET", "/api/v1/agents/ui_configuration", None)])

    async def test_malformed_ui_configuration_is_rejected_without_logging_body(self):
        for payload in (None, [], {}, {"experiences": {"private": "value"}}):
            with self.assertRaises(DirectorRestError) as raised:
                await FakeRest(payload).get_ui_configuration()
            self.assertNotIn("private", str(raised.exception))

    async def test_media_sessions_are_read_with_get_and_shape_checked(self):
        rest = FakeRest([{"sessionid": 8, "deviceid": 509, "roomids": [22]}])
        self.assertEqual((await rest.get_media_sessions())[0]["sessionid"], 8)
        self.assertEqual(rest.calls, [("GET", "/api/v1/media_sessions", None)])
        for payload in (None, {}, ["private"]):
            with self.assertRaises(DirectorRestError) as raised:
                await FakeRest(payload).get_media_sessions()
            self.assertNotIn("private", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
