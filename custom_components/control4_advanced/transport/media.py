"""Room volume push handling and a narrow source-state REST fallback."""

from __future__ import annotations

import asyncio
import logging

from .client import Control4Transport
from .events import NormalizedEvent


_LOGGER = logging.getLogger(__name__)


class RoomVolumeCoordinator:
    """Use bound-device push for room volume and optional REST source repair.

    Start after the transport is connected. It registers the room and the
    current REST-advertised volume device, then follows future binding changes.
    No Control4 commands are sent and no media fields are guessed from push.
    """

    def __init__(self, transport: Control4Transport, room_id: int) -> None:
        self.transport = transport
        self.room_id = room_id
        self._remove_event = None
        self._binding_task: asyncio.Task[None] | None = None
        self._refresh_task: asyncio.Task[None] | None = None
        self._refresh_requested = False
        self._source_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._remove_event is not None:
            raise RuntimeError("room volume coordinator is already started")
        item = self.transport.inventory.items.get(self.room_id)
        if not item or item.get("proxy") != "roomdevice":
            raise ValueError("room ID is absent or is not a roomdevice")
        self._remove_event = self.transport.events.subscribe(self._on_event)
        try:
            await self.transport.track_device(self.room_id)
            if "volume_device_id" not in self.transport.state.snapshot(self.room_id):
                await self.transport.sync_device(self.room_id)
            await self._ensure_bound_device()
        except BaseException:
            await self.stop()
            raise

    async def stop(self) -> None:
        if self._remove_event is not None:
            self._remove_event()
            self._remove_event = None
        self._refresh_requested = False
        for task in (self._binding_task, self._refresh_task, self._source_task):
            if task is not None:
                task.cancel()
        await asyncio.gather(
            *(task for task in (self._binding_task, self._refresh_task, self._source_task) if task is not None),
            return_exceptions=True,
        )
        self._binding_task = None
        self._refresh_task = None
        self._source_task = None

    def enable_source_fallback(self, *, interval: float = 15.0) -> None:
        """Periodically read only source state until a push route is proven."""
        if self._remove_event is None:
            raise RuntimeError("room coordinator is not started")
        if interval <= 0:
            raise ValueError("source fallback interval must be positive")
        if self._source_task is None:
            self._source_task = asyncio.create_task(self._reconcile_source(interval))

    async def _reconcile_source(self, interval: float) -> None:
        while True:
            await asyncio.sleep(interval)
            if self.transport.connected:
                try:
                    await self.transport.sync_room_source(self.room_id)
                except Exception as exc:
                    _LOGGER.warning("Room source fallback failed: %s", type(exc).__name__)

    def _bound_device_id(self) -> int | None:
        value = self.transport.state.snapshot(self.room_id).get("volume_device_id")
        return value if type(value) is int and value > 0 else None

    async def _ensure_bound_device(self) -> None:
        device_id = self._bound_device_id()
        if device_id is None or device_id not in self.transport.inventory.items:
            return
        try:
            await self.transport.track_device(device_id)
        except Exception as exc:
            _LOGGER.warning("Volume-device tracking failed: %s", type(exc).__name__)

    async def _on_event(self, event: NormalizedEvent) -> None:
        if event.device_id == self.room_id:
            if "volume_device_id" in event.changes:
                if self._binding_task is not None:
                    self._binding_task.cancel()
                self._binding_task = asyncio.create_task(self._ensure_bound_device())
            return
        if (
            event.source == "websocket"
            and event.authoritative
            and event.device_id == self._bound_device_id()
            and ("output_volume_percent" in event.changes or "output_is_muted" in event.changes)
        ):
            self._refresh_requested = True
            if self._refresh_task is None or self._refresh_task.done():
                self._refresh_task = asyncio.create_task(self._refresh_room())

    async def _refresh_room(self) -> None:
        """Read immediately; coalesce only pushes received during a read."""
        while self._refresh_requested:
            self._refresh_requested = False
            await self.transport.sync_device(self.room_id)
