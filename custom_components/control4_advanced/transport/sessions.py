"""Native Control4 media-session discovery and reconciliation."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
from dataclasses import dataclass
import logging

from .rest import DirectorRestClient, DirectorRestError


_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class MediaSession:
    """The small, non-sensitive part of a Director media session we need."""

    session_id: int
    device_id: int
    room_ids: tuple[int, ...]


def parse_media_sessions(
    payload: object, allowed_room_ids: Iterable[int]
) -> tuple[MediaSession, ...]:
    """Validate Director session records and retain only exposed rooms."""
    if not isinstance(payload, list):
        raise DirectorRestError("Director media sessions have an unexpected shape")
    allowed = frozenset(allowed_room_ids)
    sessions: list[MediaSession] = []
    seen_rooms: set[int] = set()
    for record in payload:
        if not isinstance(record, dict):
            raise DirectorRestError("Director media sessions have an unexpected shape")
        session_id = record.get("sessionid")
        device_id = record.get("deviceid")
        raw_rooms = record.get("roomids")
        if (
            type(session_id) is not int
            or not 0 < session_id <= 2147483647
            or type(device_id) is not int
            or not 0 < device_id <= 2147483647
            or not isinstance(raw_rooms, list)
            or not raw_rooms
            or not all(
                type(room_id) is int and 0 < room_id <= 2147483647
                for room_id in raw_rooms
            )
        ):
            raise DirectorRestError("Director media sessions have an unexpected shape")
        rooms = tuple(dict.fromkeys(
            room_id for room_id in raw_rooms if room_id in allowed
        ))
        if not rooms:
            continue
        if seen_rooms.intersection(rooms):
            raise DirectorRestError("Director media sessions contain duplicate rooms")
        seen_rooms.update(rooms)
        sessions.append(MediaSession(session_id, device_id, rooms))
    return tuple(sessions)


class MediaSessionCoordinator:
    """Poll the authoritative session list and notify media-player entities."""

    def __init__(
        self,
        rest: DirectorRestClient,
        room_ids: Iterable[int],
        *,
        interval: float = 15.0,
    ) -> None:
        rooms = frozenset(room_ids)
        if len(rooms) < 2 or not all(type(room_id) is int and room_id > 0 for room_id in rooms):
            raise ValueError("media sessions require at least two positive room IDs")
        if interval <= 0:
            raise ValueError("media-session interval must be positive")
        self.rest = rest
        self.room_ids = rooms
        self.interval = interval
        self.sessions: tuple[MediaSession, ...] = ()
        self.ready = False
        self._callbacks: set[Callable[[], None]] = set()
        self._task: asyncio.Task[None] | None = None
        self._refresh_lock = asyncio.Lock()

    async def start(self) -> None:
        if self._task is not None:
            raise RuntimeError("media-session coordinator is already started")
        await self.refresh()
        self._task = asyncio.create_task(self._poll())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        self._callbacks.clear()

    async def refresh(self) -> None:
        async with self._refresh_lock:
            sessions = parse_media_sessions(
                await self.rest.get_media_sessions(), self.room_ids
            )
            changed = not self.ready or sessions != self.sessions
            self.sessions = sessions
            self.ready = True
        if changed:
            for callback in tuple(self._callbacks):
                callback()

    async def _poll(self) -> None:
        while True:
            await asyncio.sleep(self.interval)
            try:
                await self.refresh()
            except Exception as exc:
                _LOGGER.warning(
                    "Control4 media-session refresh failed: %s", type(exc).__name__
                )

    def subscribe(self, callback: Callable[[], None]) -> Callable[[], None]:
        self._callbacks.add(callback)
        return lambda: self._callbacks.discard(callback)

    def group_room_ids(self, room_id: int) -> tuple[int, ...]:
        for session in self.sessions:
            if room_id in session.room_ids:
                return session.room_ids
        return (room_id,) if room_id in self.room_ids else ()

    def same_group(self, leader_room_id: int, member_room_ids: Iterable[int]) -> bool:
        group = frozenset(self.group_room_ids(leader_room_id))
        return leader_room_id in group and set(member_room_ids).issubset(group)
