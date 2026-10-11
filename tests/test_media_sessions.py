"""Checks for native Control4 media-session parsing and polling."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock

from custom_components.control4_advanced.transport.rest import DirectorRestError
from custom_components.control4_advanced.transport.sessions import (
    MediaSession,
    MediaSessionCoordinator,
    parse_media_sessions,
)


class MediaSessionTests(unittest.IsolatedAsyncioTestCase):
    def test_parser_retains_only_exposed_rooms_and_preserves_leader_order(self):
        payload = [{
            "sessionid": 1088,
            "deviceid": 509,
            "roomids": [22, 97, 999],
            "mediainfo": {"private": "ignored"},
        }]
        self.assertEqual(
            parse_media_sessions(payload, {22, 23, 97}),
            (MediaSession(1088, 509, (22, 97)),),
        )

    def test_parser_rejects_ambiguous_or_malformed_membership(self):
        bad_payloads = (
            {},
            [None],
            [{"sessionid": 1, "deviceid": 2, "roomids": []}],
            [{"sessionid": True, "deviceid": 2, "roomids": [22]}],
            [
                {"sessionid": 1, "deviceid": 2, "roomids": [22]},
                {"sessionid": 2, "deviceid": 3, "roomids": [22]},
            ],
        )
        for payload in bad_payloads:
            with self.subTest(payload=payload), self.assertRaises(DirectorRestError):
                parse_media_sessions(payload, {22, 23})

    async def test_coordinator_refreshes_and_notifies_only_on_change(self):
        rest = type("Rest", (), {})()
        rest.get_media_sessions = AsyncMock(side_effect=[
            [{"sessionid": 8, "deviceid": 509, "roomids": [22]}],
            [{"sessionid": 8, "deviceid": 509, "roomids": [22]}],
            [{"sessionid": 8, "deviceid": 509, "roomids": [22, 23]}],
        ])
        coordinator = MediaSessionCoordinator(rest, {22, 23}, interval=60)
        writes = []
        coordinator.subscribe(lambda: writes.append(True))
        await coordinator.start()
        self.assertTrue(coordinator.ready)
        self.assertEqual(coordinator.group_room_ids(22), (22,))
        self.assertEqual(len(writes), 1)
        await coordinator.refresh()
        self.assertEqual(len(writes), 1)
        await coordinator.refresh()
        self.assertEqual(coordinator.group_room_ids(23), (22, 23))
        self.assertTrue(coordinator.same_group(22, [23]))
        self.assertEqual(len(writes), 2)
        await coordinator.stop()


if __name__ == "__main__":
    unittest.main()
