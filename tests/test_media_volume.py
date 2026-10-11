from __future__ import annotations

import asyncio
import unittest

from control4_transport import Control4Transport, RoomVolumeCoordinator
from test_client import FakeRest, FakeWebsocket


def room_variables(volume: int, muted: int, volume_device_id: int) -> list[dict]:
    return [
        {"varName": "CURRENT_VOLUME", "value": volume},
        {"varName": "IS_MUTED", "value": muted},
        {"varName": "CURRENT_VOLUME_DEVICE_ID", "value": volume_device_id},
    ]


class RoomVolumeCoordinatorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        FakeWebsocket.instances.clear()
        FakeWebsocket.delay_subscription = False
        FakeWebsocket.silent_disconnect = False
        self.rest = FakeRest()
        self.rest.items = [
            {"id": 22, "name": "Living", "proxy": "roomdevice"},
            {"id": 623, "name": "Wiim", "proxy": "aswitch"},
            {"id": 624, "name": "Another Volume Device", "proxy": "aswitch"},
            {"id": 1227, "name": "Sony", "proxy": "tv"},
        ]
        self.rest.variables_by_id = {
            22: room_variables(41, 0, 623),
            623: [], 624: [], 1227: [],
        }
        self.room_reads = lambda: sum(
            device_id == 22 for device_id in self.rest.read_ids
        )
        self.transport = Control4Transport(
            self.rest, [22, 1227], websocket_factory=FakeWebsocket
        )
        await self.transport.start()
        self.coordinator = RoomVolumeCoordinator(self.transport, 22)
        await self.coordinator.start()
        await asyncio.sleep(0)

    async def asyncTearDown(self) -> None:
        await self.coordinator.stop()
        await self.transport.stop()

    async def test_bound_device_push_triggers_immediate_room_read(self) -> None:
        self.assertIn(623, self.transport.tracked_ids)
        self.assertIn(623, FakeWebsocket.instances[-1].callbacks)
        before = self.room_reads()
        self.rest.variables_by_id[22] = room_variables(41, 1, 623)
        socket = FakeWebsocket.instances[-1]
        for muted, level in ((False, 0), (True, 0), (True, 41)):
            await socket.push(623, {"OutputStatus": {
                "OutputID": 4000, "VolumeLevel": level, "MuteFlag": muted,
            }})
        await asyncio.sleep(0.05)
        self.assertEqual(self.room_reads(), before + 1)
        self.assertEqual(self.transport.state.snapshot(22)["volume_percent"], 41.0)
        self.assertTrue(self.transport.state.snapshot(22)["is_muted"])

    async def test_push_during_in_flight_room_read_requests_follow_up(self) -> None:
        socket = FakeWebsocket.instances[-1]
        before = self.room_reads()
        self.rest.read_started.clear()
        self.rest.read_release = asyncio.Event()
        self.rest.variables_by_id[22] = room_variables(0, 1, 623)
        await socket.push(623, {"OutputStatus": {
            "OutputID": 4000, "VolumeLevel": 0, "MuteFlag": True,
        }})
        await asyncio.wait_for(self.rest.read_started.wait(), timeout=1)
        self.assertEqual(self.room_reads(), before + 1)

        self.rest.variables_by_id[22] = room_variables(40, 0, 623)
        await socket.push(623, {"OutputStatus": {
            "OutputID": 4000, "VolumeLevel": 40, "MuteFlag": False,
        }})
        self.rest.read_release.set()
        for _ in range(100):
            if self.room_reads() >= before + 2:
                break
            await asyncio.sleep(0.01)
        self.assertEqual(self.room_reads(), before + 2)
        self.assertEqual(self.transport.state.snapshot(22)["volume_percent"], 40.0)
        self.assertFalse(self.transport.state.snapshot(22)["is_muted"])

    async def test_sony_mirror_is_ignored_and_new_binding_is_followed(self) -> None:
        socket = FakeWebsocket.instances[-1]
        before = self.room_reads()
        await socket.push(1227, {"volume_level": 0})
        await asyncio.sleep(0.05)
        self.assertEqual(self.room_reads(), before)

        self.rest.variables_by_id[22] = room_variables(30, 0, 624)
        await self.transport.sync_device(22)
        await asyncio.sleep(0)
        self.assertIn(624, self.transport.tracked_ids)
        self.assertIn(624, socket.callbacks)
        before = self.room_reads()
        await socket.push(623, {"OutputStatus": {
            "OutputID": 4000, "VolumeLevel": 29, "MuteFlag": False,
        }})
        await asyncio.sleep(0.05)
        self.assertEqual(self.room_reads(), before)

        self.rest.variables_by_id[22] = room_variables(28, 0, 624)
        await socket.push(624, {"OutputStatus": {
            "OutputID": 7, "VolumeLevel": 28, "MuteFlag": False,
        }})
        await asyncio.sleep(0.05)
        self.assertEqual(self.room_reads(), before + 1)
        self.assertEqual(self.transport.state.snapshot(22)["volume_percent"], 28.0)

    async def test_non_aswitch_volume_binding_is_not_added_to_tracking(self) -> None:
        await self.coordinator.stop()
        await self.transport.stop()
        self.rest.variables_by_id[22] = room_variables(31, 0, 1227)
        self.transport = Control4Transport(
            self.rest, [22], websocket_factory=FakeWebsocket
        )
        await self.transport.start()
        self.coordinator = RoomVolumeCoordinator(self.transport, 22)
        await self.coordinator.start()
        self.assertNotIn(1227, self.transport.tracked_ids)

    async def test_stop_cancels_pending_refresh(self) -> None:
        before = self.room_reads()
        await FakeWebsocket.instances[-1].push(623, {"OutputStatus": {
            "OutputID": 4000, "VolumeLevel": 27, "MuteFlag": False,
        }})
        await self.coordinator.stop()
        await asyncio.sleep(0.05)
        self.assertEqual(self.room_reads(), before)

    async def test_reconnect_resync_follows_changed_room_binding(self) -> None:
        socket = FakeWebsocket.instances[-1]
        await socket.sio_disconnect()
        self.rest.variables_by_id[22] = room_variables(25, 0, 624)
        await socket.sio_connect("test-token")
        for _ in range(100):
            if 624 in self.transport.tracked_ids:
                break
            await asyncio.sleep(0.01)
        self.assertIn(624, self.transport.tracked_ids)
        self.assertEqual(self.transport.state.snapshot(22)["volume_device_id"], 624)
        self.assertIn(624, socket.callbacks)

    async def test_source_fallback_refreshes_only_selected_source(self) -> None:
        seen = []

        async def observe(event):
            seen.append(event)

        self.transport.events.subscribe(observe)
        self.rest.variables_by_id[22] = room_variables(99, 1, 623) + [
            {"varName": "CURRENT_SELECTED_DEVICE", "value": "342"},
        ]
        self.coordinator.enable_source_fallback(interval=0.02)
        for _ in range(100):
            if self.transport.state.snapshot(22).get("selected_source_id") == 342:
                break
            await asyncio.sleep(0.01)
        state = self.transport.state.snapshot(22)
        self.assertEqual(state["selected_source_id"], 342)
        self.assertEqual(state["volume_percent"], 41.0)
        self.assertFalse(state["is_muted"])
        source_events = [event for event in seen if event.device_id == 22]
        self.assertEqual(len(source_events), 1)
        self.assertEqual(source_events[0].changes, {"selected_source_id": 342})

        await asyncio.sleep(0.05)
        self.assertEqual(len([event for event in seen if event.device_id == 22]), 1)
        before = self.room_reads()
        await self.coordinator.stop()
        await asyncio.sleep(0.05)
        self.assertEqual(self.room_reads(), before)

    async def test_source_fallback_reconciles_current_media_labels(self) -> None:
        seen = []

        async def observe(event):
            seen.append(event)

        self.transport.events.subscribe(observe)
        self.rest.variables_by_id[22] = room_variables(41, 0, 623) + [{
            "varName": "CURRENT MEDIA INFO",
            "value": {"mediainfo": {
                "title": "Tender Surrender", "artist": "Steve Vai",
                "album": "Alien Love Secrets",
            }},
        }]
        await self.transport.sync_room_source(22)
        self.assertEqual(self.transport.state.snapshot(22), {
            "volume_percent": 41.0, "is_muted": False,
            "volume_device_id": 623,
            "media_title": "Tender Surrender", "media_artist": "Steve Vai",
            "media_album_name": "Alien Love Secrets",
        })
        self.assertEqual(seen[-1].changes, {
            "media_title": "Tender Surrender", "media_artist": "Steve Vai",
            "media_album_name": "Alien Love Secrets",
        })

        self.rest.variables_by_id[22] = room_variables(41, 0, 623) + [
            {"varName": "POWER_STATE", "value": 0},
            {"varName": "CURRENT MEDIA INFO", "value": ""},
        ]
        await self.transport.sync_room_source(22)
        self.assertEqual(seen[-1].changes, {
            "is_on": False,
            "media_title": None, "media_artist": None, "media_album_name": None,
        })

    async def test_room_fallback_reconciles_power_and_changed_binding(self) -> None:
        seen = []

        async def observe(event):
            seen.append(event)

        self.transport.events.subscribe(observe)
        self.rest.variables_by_id[22] = room_variables(99, 1, 623) + [
            {"varName": "POWER_STATE", "value": 1},
            {"varName": "CURRENT_SELECTED_DEVICE", "value": 342},
        ]
        await self.transport.sync_room_source(22)
        self.assertEqual(self.transport.state.snapshot(22)["is_on"], True)
        self.assertEqual(self.transport.state.snapshot(22)["selected_source_id"], 342)
        self.assertEqual(self.transport.state.snapshot(22)["volume_percent"], 41.0)
        self.assertFalse(self.transport.state.snapshot(22)["is_muted"])
        self.assertEqual(seen[-1].changes, {"selected_source_id": 342, "is_on": True})

        self.rest.variables_by_id[22] = room_variables(0, 1, 0) + [
            {"varName": "POWER_STATE", "value": 0},
            {"varName": "CURRENT_SELECTED_DEVICE", "value": 0},
        ]
        await self.transport.sync_room_source(22)
        self.assertIs(self.transport.state.snapshot(22)["is_on"], False)
        self.assertEqual(self.transport.state.snapshot(22)["selected_source_id"], 0)
        self.assertEqual(self.transport.state.snapshot(22)["volume_device_id"], 0)
        self.assertEqual(self.transport.state.snapshot(22)["volume_percent"], 0.0)
        self.assertTrue(self.transport.state.snapshot(22)["is_muted"])
        self.assertEqual(seen[-1].changes, {
            "selected_source_id": 0, "is_on": False, "volume_device_id": 0,
            "volume_percent": 0.0, "is_muted": True,
        })

        self.rest.variables_by_id[22] = room_variables(35, 0, 623) + [
            {"varName": "POWER_STATE", "value": 1},
            {"varName": "CURRENT_SELECTED_DEVICE", "value": 346},
        ]
        await self.transport.sync_room_source(22)
        self.assertIs(self.transport.state.snapshot(22)["is_on"], True)
        self.assertEqual(self.transport.state.snapshot(22)["selected_source_id"], 346)
        self.assertEqual(self.transport.state.snapshot(22)["volume_device_id"], 623)
        self.assertEqual(self.transport.state.snapshot(22)["volume_percent"], 35.0)
        self.assertFalse(self.transport.state.snapshot(22)["is_muted"])
        self.assertEqual(seen[-1].changes, {
            "selected_source_id": 346, "is_on": True, "volume_device_id": 623,
            "volume_percent": 35.0, "is_muted": False,
        })

        self.rest.variables_by_id[22] = room_variables(99, 1, 623) + [
            {"varName": "POWER_STATE", "value": 0},
        ]
        await self.transport.sync_room_source(22)
        self.assertIs(self.transport.state.snapshot(22)["is_on"], False)
        self.assertEqual(self.transport.state.snapshot(22)["selected_source_id"], 346)
        self.assertEqual(self.transport.state.snapshot(22)["volume_percent"], 35.0)
        self.assertFalse(self.transport.state.snapshot(22)["is_muted"])
        self.assertEqual(seen[-1].changes, {"is_on": False})


if __name__ == "__main__":
    unittest.main()
