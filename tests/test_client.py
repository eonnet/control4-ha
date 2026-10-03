from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
import unittest

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
        self.items = [{"id": 2726, "name": "Lamp Table", "proxy": "light_v2"}]
        self.read_started = asyncio.Event()
        self.read_release: asyncio.Event | None = None
        self.read_error: Exception | None = None
        self.read_count = 0

    async def get_items(self):
        return list(self.items)

    async def get_variables(self, device_id: int):
        self.read_count += 1
        self.read_started.set()
        if self.read_release is not None:
            await self.read_release.wait()
        if self.read_error is not None:
            raise self.read_error
        return list(self.variables)


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
            if self.rest.read_count > first_reads:
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
