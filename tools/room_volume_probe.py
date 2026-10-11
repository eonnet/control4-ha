#!/usr/bin/env python3
"""Read-only Living room volume handler probe; prints allowlisted state only."""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime
import json
from pathlib import Path
import signal
import sys
import time
from typing import Any

import aiohttp

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from control4_transport import (  # noqa: E402
    Control4Transport, DirectorRestClient, DirectorToken, RoomVolumeCoordinator,
)
from control4_transport.events import NormalizedEvent  # noqa: E402
from tools.websocket_probe import DEFAULT_PYTHON, DEFAULT_TOKEN_HELPER, get_director_token  # noqa: E402


def emit(milestone: str, **values: Any) -> None:
    print(json.dumps({
        "received_at": datetime.now(UTC).isoformat(timespec="milliseconds"),
        "milestone": milestone,
        **values,
    }, sort_keys=True), flush=True)


def safe_event_record(
    event: NormalizedEvent, room_id: int, bound_device_id: int | None,
    room_state: dict[str, Any],
) -> tuple[str, dict[str, Any]] | None:
    """Project only validated volume fields; never inspect or print raw data."""
    if event.device_id == room_id and event.source == "rest" and event.authoritative:
        return "ROOM_REST_STATE", {
            "room_id": room_id,
            "volume_percent": room_state.get("volume_percent"),
            "is_muted": room_state.get("is_muted"),
            "bound_device_id": room_state.get("volume_device_id"),
        }
    if (
        event.device_id == bound_device_id
        and event.source == "websocket"
        and event.authoritative
        and ("output_volume_percent" in event.changes or "output_is_muted" in event.changes)
    ):
        return "BOUND_DEVICE_PUSH", {
            "device_id": event.device_id,
            "output_id": event.changes.get("output_id"),
            "output_volume_percent": event.changes.get("output_volume_percent"),
            "output_is_muted": event.changes.get("output_is_muted"),
        }
    return None


class HelperTokenProvider:
    def __init__(self, token: str) -> None:
        self._token = DirectorToken(token, time.monotonic() + 1800)

    async def get_token(self, *, force_refresh: bool = False) -> DirectorToken:
        return self._token


async def run(args: argparse.Namespace) -> None:
    emit("START", room_id=args.room_id)
    stop_requested = asyncio.Event()
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGINT, stop_requested.set)
    try:
        token = await asyncio.to_thread(get_director_token, args.token_python, args.token_helper)
        if stop_requested.is_set():
            emit("INTERRUPTED")
            return
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session:
            rest = DirectorRestClient(args.host, HelperTokenProvider(token), session)
            transport = Control4Transport(rest, [args.room_id])
            coordinator = RoomVolumeCoordinator(transport, args.room_id)

            async def on_event(event: NormalizedEvent) -> None:
                room_state = transport.state.snapshot(args.room_id)
                record = safe_event_record(
                    event, args.room_id, room_state.get("volume_device_id"), room_state,
                )
                if record is not None:
                    emit(record[0], **record[1])

            remove_event = transport.events.subscribe(on_event)
            try:
                await transport.start(ready_timeout=35)
                # engineio_v3 replaces SIGINT during connect with a handler
                # that stops the event loop before cleanup can finish.
                loop.add_signal_handler(signal.SIGINT, stop_requested.set)
                emit("WS_CONNECTED")
                await coordinator.start()
                state = transport.state.snapshot(args.room_id)
                emit("LISTENING", room_id=args.room_id,
                     bound_device_id=state.get("volume_device_id"),
                     bound_device_tracked=state.get("volume_device_id") in transport.tracked_ids)
                try:
                    await asyncio.wait_for(stop_requested.wait(), timeout=args.duration)
                except asyncio.TimeoutError:
                    emit("COMPLETE")
                else:
                    emit("INTERRUPTED")
            finally:
                remove_event()
                await coordinator.stop()
                await transport.stop()
    finally:
        loop.remove_signal_handler(signal.SIGINT)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="192.168.0.24")
    parser.add_argument("--room-id", type=int, default=22)
    parser.add_argument("--duration", type=float, default=120)
    parser.add_argument("--token-helper", default=DEFAULT_TOKEN_HELPER)
    parser.add_argument("--token-python", default=DEFAULT_PYTHON)
    args = parser.parse_args()
    if args.room_id <= 0 or args.duration <= 0:
        parser.error("room ID and duration must be positive")
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        emit("INTERRUPTED")
    except Exception as exc:
        emit("ERROR", error_type=type(exc).__name__)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
