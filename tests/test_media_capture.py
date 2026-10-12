"""Tests for guided TIDAL playlist and TuneIn favorite capture."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
import unittest

from custom_components.control4_advanced.transport.client import EventDispatcher
from custom_components.control4_advanced.transport.events import NormalizedEvent
from custom_components.control4_advanced.transport.media_capture import (
    MediaCaptureTarget,
    MediaCaptureTimeout,
    capture_media_items,
    captured_media_items,
    media_capture_targets,
    merge_tidal_playlists,
    merge_tunein_favorites,
)
from custom_components.control4_advanced.transport.sources import RoomSource


PLAYLIST_ONE = "11111111-2222-4333-8444-555555555555"
PLAYLIST_TWO = "66666666-7777-4888-9999-aaaaaaaaaaaa"


def _raw(items):
    return {
        "evtName": "OnDataToUI",
        "iddevice": 1417,
        "data": {"RESPONSE": {"DATA": {"List": {"item": items}}}},
    }


def _event(raw, device_id=1417):
    return NormalizedEvent(
        device_id=device_id,
        source="websocket",
        event_type="OnDataToUI",
        changes={},
        received_at="2026-10-12T00:00:00+00:00",
        authoritative=False,
        raw=raw,
    )


class FakeTransport:
    def __init__(self) -> None:
        self.connected = True
        self.tracked_ids = {22}
        self.events = EventDispatcher()
        self.inventory = SimpleNamespace(items={1417: {"proxy": "media_service"}})
        self.tracked_calls = []
        self.untracked_calls = []

    async def track_device(self, device_id, *, sync=True):
        self.tracked_calls.append((device_id, sync))
        self.tracked_ids.add(device_id)

    async def untrack_device(self, device_id):
        self.untracked_calls.append(device_id)
        self.tracked_ids.discard(device_id)


class MediaCaptureParsingTests(unittest.TestCase):
    def test_discovers_unique_visible_media_service_sources(self) -> None:
        rooms = {
            22: (
                RoomSource(1417, "listen", "Miki", "AUDIO_SELECTION", "Listen: Miki"),
                RoomSource(92, "listen", "TuneIn", "AUDIO_SELECTION", "Listen: TuneIn"),
            ),
            23: (
                RoomSource(1417, "listen", "Miki", "AUDIO_SELECTION", "Listen: Miki"),
            ),
        }
        inventory = {
            1417: {
                "proxy": "media_service",
                "protocolFilename": "tidal.c4z",
                "name": "Miki",
            },
            92: {
                "proxy": "media_service",
                "protocolFilename": "tunein-premium.c4z",
                "name": "TuneIn Premium",
            },
        }

        self.assertEqual(
            media_capture_targets(rooms, inventory, "tidal"),
            (MediaCaptureTarget(1417, "Miki", "tidal"),),
        )
        self.assertEqual(
            media_capture_targets(rooms, inventory, "tunein"),
            (MediaCaptureTarget(92, "TuneIn Premium", "tunein"),),
        )

    def test_parses_only_valid_tidal_playlists(self) -> None:
        target = MediaCaptureTarget(1417, "Miki", "tidal")
        result = captured_media_items(
            _raw(
                [
                    {"itemType": "playlist", "id": PLAYLIST_ONE, "title": "Rock"},
                    {"itemType": "track", "id": PLAYLIST_TWO, "title": "Track"},
                    {"itemType": "playlist", "id": "not-a-uuid", "title": "Bad"},
                    {"itemType": "playlist", "id": PLAYLIST_TWO, "title": "Bad | label"},
                ]
            ),
            target,
        )
        self.assertEqual(
            [(item.source_name, item.name, item.item_id) for item in result],
            [("Miki", "Rock", PLAYLIST_ONE)],
        )

    def test_parses_only_valid_tunein_station_ids(self) -> None:
        target = MediaCaptureTarget(92, "TuneIn Premium", "tunein")
        result = captured_media_items(
            _raw(
                [
                    {"id": "Station/x123", "title": "Wrong"},
                    {"id": "Station/s24939", "title": "BBC Radio 1"},
                    {"id": "Station/s0", "title": "Invalid"},
                ]
            ),
            target,
        )
        self.assertEqual(
            [(item.name, item.item_id) for item in result],
            [("BBC Radio 1", "Station/s24939")],
        )

    def test_merge_updates_collisions_and_preserves_other_sources(self) -> None:
        target = MediaCaptureTarget(1417, "Miki", "tidal")
        captured = captured_media_items(
            _raw(
                [
                    {"itemType": "playlist", "id": PLAYLIST_ONE, "title": "Rock renamed"},
                    {"itemType": "playlist", "id": PLAYLIST_TWO, "title": "New"},
                ]
            ),
            target,
        )
        merged = merge_tidal_playlists(
            f"Miki | Rock | {PLAYLIST_ONE}\nOther | Keep | 11111111-1111-1111-1111-111111111111",
            captured,
        )
        self.assertEqual(
            merged.splitlines(),
            [
                "Other | Keep | 11111111-1111-1111-1111-111111111111",
                f"Miki | Rock renamed | {PLAYLIST_ONE}",
                f"Miki | New | {PLAYLIST_TWO}",
            ],
        )

    def test_merge_tunein_keeps_manual_entries(self) -> None:
        target = MediaCaptureTarget(92, "TuneIn", "tunein")
        captured = captured_media_items(
            _raw([{"id": "Station/s24939", "title": "BBC Radio 1"}]), target
        )
        self.assertEqual(
            merge_tunein_favorites(
                "Other | Jazz | Station/s123\nTuneIn | Old name | Station/s24939",
                captured,
            ).splitlines(),
            ["Other | Jazz | Station/s123", "TuneIn | BBC Radio 1 | Station/s24939"],
        )


class MediaCaptureWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_captures_latest_stable_list_and_cleans_up_tracking(self) -> None:
        transport = FakeTransport()
        target = MediaCaptureTarget(1417, "Miki", "tidal")
        task = asyncio.create_task(
            capture_media_items(
                transport, (target,), timeout=1, settle_seconds=0.04
            )
        )
        await asyncio.sleep(0)
        # A generic playlist page must not be accepted before the account's
        # observed "created playlists" navigation marker.
        await transport.events.publish(
            _event(_raw([{"itemType": "playlist", "id": PLAYLIST_ONE, "title": "First"}]))
        )
        await transport.events.publish(
            _event(
                _raw(
                    [
                        {
                            "itemType": "link",
                            "id": "users/123456789/playlists",
                            "title": "Playlists Created By Me",
                        }
                    ]
                )
            )
        )
        await asyncio.sleep(0.01)
        await transport.events.publish(
            _event(_raw([{"itemType": "playlist", "id": PLAYLIST_TWO, "title": "Final"}]))
        )

        result = await task

        self.assertEqual([(item.name, item.item_id) for item in result], [("Final", PLAYLIST_TWO)])
        self.assertEqual(transport.tracked_calls, [(1417, False)])
        self.assertEqual(transport.untracked_calls, [1417])
        self.assertNotIn(1417, transport.tracked_ids)

    async def test_timeout_without_matching_list_cleans_up(self) -> None:
        transport = FakeTransport()
        target = MediaCaptureTarget(1417, "Miki", "tidal")
        with self.assertRaises(MediaCaptureTimeout):
            await capture_media_items(
                transport, (target,), timeout=0.01, settle_seconds=0.005
            )
        self.assertEqual(transport.untracked_calls, [1417])


if __name__ == "__main__":
    unittest.main()
