"""Push-first Control4 transport with targeted REST synchronization."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable
from copy import deepcopy
from dataclasses import replace
import logging
import time
from typing import Any

from pyControl4.websocket import C4Websocket

from .events import NormalizedEvent, normalize_rest_variables, normalize_websocket_event
from .rest import DirectorRestClient


_LOGGER = logging.getLogger(__name__)
EventCallback = Callable[[NormalizedEvent], Awaitable[None]]
ConnectionCallback = Callable[[bool], None]


class InventoryCache:
    def __init__(self, rest: DirectorRestClient) -> None:
        self.rest = rest
        self.items: dict[int, dict[str, Any]] = {}

    async def refresh(self) -> None:
        items = await self.rest.get_items()
        result: dict[int, dict[str, Any]] = {}
        for item in items:
            try:
                item_id = int(item["id"])
            except (KeyError, TypeError, ValueError):
                continue
            result[item_id] = item
        self.items = result

    def proxy(self, device_id: int) -> str | None:
        item = self.items.get(device_id)
        value = item.get("proxy") if item else None
        return value if isinstance(value, str) else None


class DeviceStateCache:
    """Merge partial updates and protect newer push fields from stale REST reads."""

    def __init__(self) -> None:
        self._states: dict[int, dict[str, Any]] = {}
        self._field_revisions: dict[tuple[int, str], int] = {}
        self._revision = 0

    @property
    def revision(self) -> int:
        return self._revision

    def snapshot(self, device_id: int) -> dict[str, Any]:
        return deepcopy(self._states.get(device_id, {}))

    def apply(
        self, event: NormalizedEvent, *, only_if_unchanged_since: int | None = None
    ) -> dict[str, Any]:
        if not event.authoritative:
            return {}
        accepted: dict[str, Any] = {}
        for key, value in event.changes.items():
            if (
                only_if_unchanged_since is not None
                and self._field_revisions.get((event.device_id, key), 0) > only_if_unchanged_since
            ):
                continue
            accepted[key] = value
        if accepted:
            self._revision += 1
            state = self._states.setdefault(event.device_id, {})
            state.update(accepted)
            for key in accepted:
                self._field_revisions[(event.device_id, key)] = self._revision
        return accepted


class EventDispatcher:
    def __init__(self) -> None:
        self._subscribers: set[EventCallback] = set()

    def subscribe(self, callback: EventCallback) -> Callable[[], None]:
        self._subscribers.add(callback)
        return lambda: self._subscribers.discard(callback)

    async def publish(self, event: NormalizedEvent) -> None:
        for callback in tuple(self._subscribers):
            try:
                await callback(event)
            except Exception as exc:
                # Callback exceptions must not break the Director subscription.
                _LOGGER.warning("event subscriber failed: %s", type(exc).__name__)


class Control4Transport:
    """Coordinate inventory, state, WebSocket delivery, and slow REST repair.

    The caller owns the REST client's aiohttp session and must call stop()
    before closing it. Only tracked item IDs are synchronized or subscribed.
    """

    def __init__(
        self,
        rest: DirectorRestClient,
        tracked_ids: Iterable[int],
        *,
        reconciliation_seconds: float | None = None,
        websocket_factory: Callable[..., C4Websocket] = C4Websocket,
    ) -> None:
        self.rest = rest
        self.tracked_ids = set(int(item_id) for item_id in tracked_ids)
        if not self.tracked_ids:
            raise ValueError("at least one tracked Control4 item ID is required")
        if reconciliation_seconds is not None and reconciliation_seconds <= 0:
            raise ValueError("reconciliation_seconds must be positive")
        self.reconciliation_seconds = reconciliation_seconds
        self.websocket_factory = websocket_factory
        self.inventory = InventoryCache(rest)
        self.state = DeviceStateCache()
        self.events = EventDispatcher()
        self._connection_subscribers: set[ConnectionCallback] = set()
        self._reported_connected = False
        self._websocket: C4Websocket | None = None
        self._run_task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._connect_event = asyncio.Event()
        self._disconnect_event = asyncio.Event()
        self._ready_event = asyncio.Event()
        self._connected = False
        self._ever_connected = False
        self._sync_lock = asyncio.Lock()

    @property
    def connected(self) -> bool:
        return self._connected and self._socket_active()

    def subscribe_connection(self, callback: ConnectionCallback) -> Callable[[], None]:
        self._connection_subscribers.add(callback)
        return lambda: self._connection_subscribers.discard(callback)

    def _report_connection(self, connected: bool) -> None:
        if self._reported_connected == connected:
            return
        self._reported_connected = connected
        for callback in tuple(self._connection_subscribers):
            try:
                callback(connected)
            except Exception as exc:
                _LOGGER.warning("connection subscriber failed: %s", type(exc).__name__)

    def _socket_active(self) -> bool:
        if self._websocket is None:
            return False
        socket = getattr(self._websocket, "_sio", None)
        if not getattr(socket, "connected", False):
            return False
        handlers = getattr(socket, "namespace_handlers", {})
        return any(
            getattr(handler, "uri", None) == "/api/v1/items/datatoui"
            and getattr(handler, "connected", False)
            and bool(getattr(handler, "subscription_id", None))
            for handler in handlers.values()
        )

    async def start(self, *, ready_timeout: float = 30.0) -> None:
        if self._run_task is not None:
            raise RuntimeError("transport is already started")
        # Upstream Socket.IO debug logs can include JWT headers and raw events.
        for name in ("pyControl4.websocket", "socketio_v4", "engineio_v3"):
            logging.getLogger(name).setLevel(logging.CRITICAL)
        await self.inventory.refresh()
        unknown = self.tracked_ids - self.inventory.items.keys()
        if unknown:
            raise ValueError(f"tracked IDs are absent from REST inventory: {sorted(unknown)}")
        self._stop_event.clear()
        self._ready_event.clear()
        self._run_task = asyncio.create_task(self._run(), name="control4-transport")
        try:
            await asyncio.wait_for(self._ready_event.wait(), timeout=ready_timeout)
        except BaseException:
            await self.stop()
            raise

    async def stop(self) -> None:
        self._stop_event.set()
        if self._websocket is not None:
            try:
                await self._websocket.sio_disconnect()
            except Exception as exc:
                _LOGGER.warning("WebSocket disconnect failed: %s", type(exc).__name__)
        task = self._run_task
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._run_task = None
        self._websocket = None
        self._connected = False
        self._ready_event.clear()
        self._report_connection(False)

    async def sync_all(self, *, reason: str = "reconciliation") -> None:
        """Read tracked REST variables without overwriting newer push fields."""
        del reason  # useful to callers; intentionally never logged with payloads
        async with self._sync_lock:
            limit = asyncio.Semaphore(6)

            async def sync_one(device_id: int) -> None:
                if self._stop_event.is_set():
                    return
                async with limit:
                    await self.sync_device(device_id)

            await asyncio.gather(*(sync_one(device_id) for device_id in sorted(self.tracked_ids)))

    async def sync_device(self, device_id: int) -> None:
        """Refresh one tracked item after a push trigger, preserving newer fields."""
        if device_id not in self.tracked_ids:
            raise ValueError("item is not tracked")
        baseline = self.state.revision
        try:
            variables = await self.rest.get_variables(device_id)
        except Exception as exc:
            _LOGGER.warning("REST state sync failed for %s: %s", device_id, type(exc).__name__)
            return
        event = normalize_rest_variables(device_id, self.inventory.proxy(device_id), variables)
        accepted = self.state.apply(event, only_if_unchanged_since=baseline)
        await self.events.publish(replace(event, changes=accepted, authoritative=bool(accepted)))

    async def track_device(self, device_id: int) -> None:
        """Start routing a newly bound inventory item without reconnecting.

        pyControl4 callback registration is local: Director sends the global
        datatoui stream, and this callback selects messages by iddevice.
        """
        if type(device_id) is not int or device_id not in self.inventory.items:
            raise ValueError("item is absent from REST inventory")
        if device_id in self.tracked_ids:
            return
        self.tracked_ids.add(device_id)
        if self._websocket is not None:
            self._websocket.add_item_callback(device_id, self._on_websocket_event)
        await self.sync_device(device_id)

    async def _on_connect(self) -> None:
        self._connected = True
        self._connect_event.set()

    async def _on_disconnect(self) -> None:
        self._connected = False
        self._report_connection(False)
        self._disconnect_event.set()

    async def _on_websocket_event(self, routed_device_id: int, raw: Any) -> None:
        try:
            event = normalize_websocket_event(raw, self.inventory.proxy(routed_device_id))
            if event is None or event.device_id not in self.tracked_ids:
                return
            if event.device_id != routed_device_id:
                _LOGGER.warning("Control4 WebSocket event route mismatch; ignoring")
                return
            self.state.apply(event)
            await self.events.publish(event)
        except Exception as exc:
            # pyControl4 logs callback exception text, so never let one escape.
            _LOGGER.warning("WebSocket event processing failed: %s", type(exc).__name__)

    async def _run(self) -> None:
        delay = 1.0
        try:
            while not self._stop_event.is_set():
                self._connect_event.clear()
                self._disconnect_event.clear()
                try:
                    token = await self.rest.token_provider.get_token()
                    self._websocket = self.websocket_factory(
                        self.rest.host,
                        self.rest.session,
                        self._on_connect,
                        self._on_disconnect,
                    )
                    for device_id in tuple(self.tracked_ids):
                        self._websocket.add_item_callback(device_id, self._on_websocket_event)
                    await self._websocket.sio_connect(token.value)
                    if not self._connected:
                        await asyncio.wait_for(self._connect_event.wait(), timeout=20)
                    await self._wait_for_subscription()
                    await self.sync_all(reason="initial" if not self._ever_connected else "reconnect")
                    self._ever_connected = True
                    self._report_connection(True)
                    self._ready_event.set()
                    delay = 1.0
                    await self._watch_connection(token.expires_at)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    _LOGGER.warning("Control4 connection failed: %s", type(exc).__name__)
                finally:
                    if self._websocket is not None:
                        try:
                            await self._websocket.sio_disconnect()
                        except Exception as exc:
                            _LOGGER.warning("WebSocket cleanup failed: %s", type(exc).__name__)
                        self._websocket = None
                    self._connected = False
                    self._report_connection(False)
                if not self._stop_event.is_set():
                    try:
                        await asyncio.wait_for(self._stop_event.wait(), timeout=delay)
                    except asyncio.TimeoutError:
                        pass
                    delay = min(delay * 2, 30.0)
        finally:
            self._connected = False
            self._report_connection(False)

    async def _watch_connection(self, token_expires_at: float) -> None:
        next_reconcile = (
            time.monotonic() + self.reconciliation_seconds
            if self.reconciliation_seconds is not None
            else float("inf")
        )
        while not self._stop_event.is_set():
            now = time.monotonic()
            refresh_at = token_expires_at - 300
            if now >= refresh_at:
                return
            if not self._socket_active():
                self._report_connection(False)
                return  # rebuild if upstream omitted the disconnect callback
            deadline = min(refresh_at, next_reconcile)
            timeout = min(1.0, max(0.1, deadline - now))
            try:
                await asyncio.wait_for(self._disconnect_event.wait(), timeout=timeout)
                self._disconnect_event.clear()
                if not self._connected:
                    self._connect_event.clear()
                    if not self._connected:
                        try:
                            await asyncio.wait_for(self._connect_event.wait(), timeout=30)
                        except asyncio.TimeoutError:
                            return  # create a fresh WebSocket and token
                if self._connected:
                    await self._wait_for_subscription()
                    await self.sync_all(reason="reconnect")
                    self._report_connection(True)
            except asyncio.TimeoutError:
                if not self._socket_active():
                    self._report_connection(False)
                    return
                if time.monotonic() >= next_reconcile:
                    await self.sync_all(reason="reconciliation")
                    next_reconcile = time.monotonic() + (self.reconciliation_seconds or 0)

    async def _wait_for_subscription(self, timeout: float = 15.0) -> None:
        """Wait for Director's subscription ID, not just Socket.IO connect.

        pyControl4 2.0.2 does not expose this readiness state publicly. Its
        registered namespace sets connected/subscription_id after the
        datatoui GET succeeds and startSubscription has been emitted.
        """
        if self._websocket is None:
            raise RuntimeError("WebSocket was not created")
        socket = getattr(self._websocket, "_sio", None)
        handlers = getattr(socket, "namespace_handlers", {})
        namespace = next(
            (
                handler
                for handler in handlers.values()
                if getattr(handler, "uri", None) == "/api/v1/items/datatoui"
            ),
            None,
        )
        if namespace is None:
            raise RuntimeError("pyControl4 datatoui namespace is unavailable")
        deadline = time.monotonic() + timeout
        while not self._stop_event.is_set():
            if getattr(namespace, "connected", False) and getattr(namespace, "subscription_id", None):
                return
            if time.monotonic() >= deadline:
                raise TimeoutError("Director subscription did not become ready")
            await asyncio.sleep(0.05)
