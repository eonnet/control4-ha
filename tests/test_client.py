from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from control4_transport.auth import DirectorToken
from control4_transport.client import Control4Transport


class FakeTokenProvider:
    async def get_token(self, *, force_refresh: bool = False) -> DirectorToken:
        return DirectorToken("test-token", time.monotonic() + 3600)


class FakeRest:
    def __init__(self) -> None:
        self.host = "127.0.0.1"
        self.session = object()
        self.token_provider = FakeTokenProvider()
        self.variables = [{"varName": "Brightness Percent", "value": 0}]
        self.variables_by_id: dict[int, list[dict]] = {}
        self.items = [{"id": 2726, "name": "Lamp Table", "proxy": "light_v2"}]
        self.read_started = asyncio.Event()
        self.read_release: asyncio.Event | None = None
        self.read_error: Exception | None = None
        self.read_count = 0
        self.read_ids: list[int] = []

    async def get_items(self):
        return list(self.items)

    async def get_variables(self, device_id: int):
        self.read_count += 1
        self.read_ids.append(device_id)
        self.read_started.set()
        if self.read_release is not None:
            await self.read_release.wait()
        if self.read_error is not None:
            raise self.read_error
        return list(self.variables_by_id.get(device_id, self.variables))


class FakeWebsocket:
    instances: list[FakeWebsocket] = []
    delay_subscription = False
    silent_disconnect = False

    def __init__(self, host, session, on_connect, on_disconnect) -> None:
        self.on_connect = on_connect
        self.on_disconnect = on_disconnect
        self.callbacks = {}
        self.connected = False
        self.namespace = SimpleNamespace(
            uri="/api/v1/items/datatoui", connected=False, subscription_id=None
        )
        self._sio = SimpleNamespace(
            connected=False, namespace_handlers={"/": self.namespace}
        )
        self.instances.append(self)

    def add_item_callback(self, item_id, callback) -> None:
        self.callbacks[item_id] = callback

    def remove_item_callback(self, item_id, callback=None) -> None:
        if callback is None or self.callbacks.get(item_id) == callback:
            self.callbacks.pop(item_id, None)

    async def sio_connect(self, token) -> None:
        self.connected = True
        self._sio.connected = True
        await self.on_connect()
        if not self.delay_subscription:
            self.finish_subscription()

    def finish_subscription(self) -> None:
        self.namespace.connected = True
        self.namespace.subscription_id = "fake-subscription"

    async def sio_disconnect(self) -> None:
        if self.connected:
            self.connected = False
            self._sio.connected = False
            self.namespace.connected = False
            self.namespace.subscription_id = None
            if not self.silent_disconnect:
                await self.on_disconnect()

    async def push(self, item_id: int, data: dict) -> None:
        await self.callbacks[item_id](
            item_id, {"evtName": "OnDataToUI", "iddevice": item_id, "data": data}
        )


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        FakeWebsocket.instances.clear()
        FakeWebsocket.delay_subscription = False
        FakeWebsocket.silent_disconnect = False
        self.rest = FakeRest()
        self.transport = Control4Transport(
            self.rest, [2726], websocket_factory=FakeWebsocket
        )

    async def asyncTearDown(self) -> None:
        await self.transport.stop()

    async def test_initial_snapshot_then_push_and_reconciliation(self) -> None:
        seen = []

        async def observe(event):
            seen.append(event)

        self.transport.events.subscribe(observe)
        await self.transport.start()
        self.assertEqual(self.transport.state.snapshot(2726)["brightness_percent"], 0.0)
        socket = FakeWebsocket.instances[-1]
        await socket.push(2726, {"LIGHT_LEVEL": 41})
        self.assertEqual(self.transport.state.snapshot(2726)["brightness_percent"], 41.0)
        self.assertEqual([event.source for event in seen], ["rest", "websocket"])

        self.rest.variables = [{"varName": "Brightness Percent", "value": 0}]
        await self.transport.sync_all(reason="reconciliation")
        self.assertEqual(self.transport.state.snapshot(2726)["brightness_percent"], 0.0)

    async def test_push_during_rest_read_wins(self) -> None:
        await self.transport.start()
        self.rest.read_started.clear()
        self.rest.read_release = asyncio.Event()
        sync = asyncio.create_task(self.transport.sync_all())
        await self.rest.read_started.wait()
        await FakeWebsocket.instances[-1].push(2726, {"LIGHT_LEVEL": 41})
        self.rest.read_release.set()
        await sync
        self.assertEqual(self.transport.state.snapshot(2726)["brightness_percent"], 41.0)

    async def test_targeted_read_cannot_overwrite_newer_push(self) -> None:
        await self.transport.start()
        self.rest.read_started.clear()
        self.rest.read_release = asyncio.Event()
        sync = asyncio.create_task(self.transport.sync_device(2726))
        await self.rest.read_started.wait()
        await FakeWebsocket.instances[-1].push(2726, {"LIGHT_LEVEL": 41})
        self.rest.read_release.set()
        await sync
        self.assertEqual(self.transport.state.snapshot(2726)["brightness_percent"], 41.0)

    async def test_temporary_tracking_can_skip_rest_and_be_removed(self) -> None:
        self.rest.items.append(
            {"id": 1417, "name": "TIDAL account", "proxy": "media_service"}
        )
        await self.transport.start()
        reads = self.rest.read_count
        socket = FakeWebsocket.instances[-1]

        await self.transport.track_device(1417, sync=False)

        self.assertIn(1417, self.transport.tracked_ids)
        self.assertIn(1417, socket.callbacks)
        self.assertEqual(self.rest.read_count, reads)

        await self.transport.untrack_device(1417)

        self.assertNotIn(1417, self.transport.tracked_ids)
        self.assertNotIn(1417, socket.callbacks)

    async def test_push_recovers_state_after_initial_rest_read_failure(self) -> None:
        self.rest.read_error = RuntimeError("temporary REST failure")
        with self.assertLogs(
            "custom_components.control4_advanced.transport.client", level="WARNING"
        ):
            await self.transport.start()
        self.assertTrue(self.transport.connected)
        self.assertEqual(self.transport.state.snapshot(2726), {})
        await FakeWebsocket.instances[-1].push(2726, {"LIGHT_LEVEL": 41})
        self.assertEqual(
            self.transport.state.snapshot(2726),
            {"brightness_percent": 41.0, "is_on": True},
        )

    async def test_common_zwave_sensor_push_triggers_rest_refresh(self) -> None:
        self.rest.items = [{
            "id": 2144, "name": "Heiman HT", "proxy": "zwave-common-sensor",
        }]
        self.rest.variables = [
            {"varName": "Device Status", "value": "ONLINE"},
            {"varName": "Local Temperature 0", "value": 22},
        ]
        self.transport = Control4Transport(
            self.rest, [2144], websocket_factory=FakeWebsocket
        )
        await self.transport.start()
        first_reads = self.rest.read_count
        self.rest.variables = [
            {"varName": "Device Status", "value": "ONLINE"},
            {"varName": "Local Temperature 0", "value": 23},
        ]

        await FakeWebsocket.instances[-1].push(2144, {"changed": True})

        self.assertEqual(self.rest.read_count, first_reads + 1)
        self.assertEqual(
            self.transport.state.snapshot(2144)["zwave_sensor_temperature_0"],
            23.0,
        )

    async def test_security_zwave_sensor_push_triggers_rest_refresh(self) -> None:
        self.rest.items = [{
            "id": 3416, "name": "Kid PIR", "proxy": "zwave-security-sensor",
        }]
        self.rest.variables = [
            {"varName": "BatteryPercent", "value": 90},
            {"varName": "TemperatureC", "value": 25.1},
        ]
        self.transport = Control4Transport(
            self.rest, [3416], websocket_factory=FakeWebsocket
        )
        await self.transport.start()
        first_reads = self.rest.read_count
        self.rest.variables = [
            {"varName": "BatteryPercent", "value": 89},
            {"varName": "TemperatureC", "value": 25.4},
        ]

        await FakeWebsocket.instances[-1].push(3416, {"changed": True})

        self.assertEqual(self.rest.read_count, first_reads + 1)
        self.assertEqual(
            self.transport.state.snapshot(3416)["zwave_sensor_temperature"],
            25.4,
        )

    async def test_zigbee_climate_refreshes_on_push_and_without_push(self) -> None:
        self.rest.items.extend([
            {"id": 1, "typeName": "device", "proxy": "keypad_proxy",
             "filename": "zigbee-climate-sensor.c4z"},
            {"id": 2, "typeName": "device", "proxy": "keypad_proxy"},
        ])
        self.rest.variables_by_id[1] = [
            {"varName": "TemperatureC", "value": 22},
            {"varName": "TemperatureValid", "value": 1},
        ]
        self.transport = Control4Transport(
            self.rest, [1, 2, 2726], websocket_factory=FakeWebsocket
        )

        with patch(
            "custom_components.control4_advanced.transport.client."
            "_ZIGBEE_CLIMATE_REFRESH_SECONDS", 0.02
        ):
            await self.transport.start()
            self.assertEqual(self.transport.state.snapshot(1)["zigbee_climate_temperature"], 22.0)
            self.rest.read_ids.clear()
            socket = FakeWebsocket.instances[-1]
            self.rest.variables_by_id[1][0]["value"] = 23
            await socket.push(2, {"changed": True})
            await socket.push(1, {"changed": True})
            self.assertEqual(self.rest.read_ids, [1])
            self.assertEqual(self.transport.state.snapshot(1)["zigbee_climate_temperature"], 23.0)

            self.rest.read_ids.clear()
            self.rest.variables_by_id[1][0]["value"] = 24
            self.rest.read_started.clear()
            await asyncio.wait_for(self.rest.read_started.wait(), timeout=2)
            self.assertEqual(self.rest.read_ids, [1])
            self.assertEqual(self.transport.state.snapshot(1)["zigbee_climate_temperature"], 24.0)

    async def test_native_lock_push_triggers_rest_refresh(self) -> None:
        self.rest.items = [{
            "id": 3403, "name": "Z-Wave Lock", "proxy": "lock",
        }]
        self.rest.variables = [{"varName": "LockStatus", "value": "unlocked"}]
        self.transport = Control4Transport(
            self.rest, [3403], websocket_factory=FakeWebsocket
        )
        await self.transport.start()
        first_reads = self.rest.read_count
        self.rest.variables = [{"varName": "LockStatus", "value": "locked"}]

        await FakeWebsocket.instances[-1].push(3403, {"changed": True})

        self.assertEqual(self.rest.read_count, first_reads + 1)
        self.assertEqual(
            self.transport.state.snapshot(3403)["lock_status"], "locked"
        )
        self.rest.variables = [{"varName": "LockStatus", "value": "unknown"}]
        await FakeWebsocket.instances[-1].push(3403, {"changed": True})
        self.assertIsNone(self.transport.state.snapshot(3403)["lock_status"])

    async def test_reconnect_reloads_rest_state(self) -> None:
        statuses = []
        self.transport.subscribe_connection(statuses.append)
        await self.transport.start()
        self.assertEqual(statuses, [True])
        first_reads = self.rest.read_count
        socket = FakeWebsocket.instances[-1]
        await socket.sio_disconnect()
        self.assertEqual(statuses[-1], False)
        self.rest.variables = [{"varName": "Brightness Percent", "value": 58}]
        await socket.sio_connect("test-token")
        for _ in range(100):
            if self.rest.read_count > first_reads and statuses[-1] is True:
                break
            await asyncio.sleep(0.01)
        self.assertGreater(self.rest.read_count, first_reads)
        self.assertEqual(self.transport.state.snapshot(2726)["brightness_percent"], 58.0)
        self.assertEqual(statuses[-1], True)

    async def test_ready_waits_for_director_subscription_id(self) -> None:
        FakeWebsocket.delay_subscription = True
        starting = asyncio.create_task(self.transport.start())
        for _ in range(100):
            if FakeWebsocket.instances:
                break
            await asyncio.sleep(0.01)
        self.assertTrue(FakeWebsocket.instances)
        self.assertFalse(starting.done())
        FakeWebsocket.instances[-1].finish_subscription()
        await starting
        self.assertTrue(self.transport.connected)

    async def test_silent_disconnect_rebuilds_websocket_and_resyncs(self) -> None:
        await self.transport.start()
        first = FakeWebsocket.instances[-1]
        first_reads = self.rest.read_count
        FakeWebsocket.silent_disconnect = True
        await first.sio_disconnect()
        self.assertFalse(self.transport.connected)
        for _ in range(250):
            if len(FakeWebsocket.instances) > 1 and self.rest.read_count > first_reads:
                break
            await asyncio.sleep(0.01)
        self.assertGreater(len(FakeWebsocket.instances), 1)
        self.assertGreater(self.rest.read_count, first_reads)

    async def test_misrouted_websocket_event_cannot_update_another_device(self) -> None:
        self.rest.items.append({"id": 2646, "name": "Second Lamp", "proxy": "light_v2"})
        self.transport = Control4Transport(
            self.rest, [2726, 2646], websocket_factory=FakeWebsocket
        )
        seen = []

        async def observe(event):
            seen.append(event)

        self.transport.events.subscribe(observe)
        await self.transport.start()
        seen.clear()
        socket = FakeWebsocket.instances[-1]
        with self.assertLogs(
            "custom_components.control4_advanced.transport.client", level="WARNING"
        ):
            await socket.callbacks[2726](
                2726, {"evtName": "OnDataToUI", "iddevice": 2646, "data": {"LIGHT_LEVEL": 41}}
            )
        self.assertEqual(self.transport.state.snapshot(2646)["brightness_percent"], 0.0)
        self.assertEqual(seen, [])
        await socket.push(2646, {"LIGHT_LEVEL": 41})
        self.assertEqual(self.transport.state.snapshot(2646)["brightness_percent"], 41.0)


if __name__ == "__main__":
    unittest.main()
