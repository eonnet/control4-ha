#!/usr/bin/env python3
"""Read-only, sanitized Control4 Director WebSocket protocol probe.

No device command is implemented here.  Credentials and tokens remain outside
this repository and are never written to output.
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime
import json
import logging
from pathlib import Path
import signal
import subprocess
import sys
from typing import Any

import aiohttp
from pyControl4.websocket import C4Websocket


DEFAULT_HOST = "192.168.0.24"
DEFAULT_TOKEN_HELPER = "/opt/control4-mcp/get-token.py"
DEFAULT_PYTHON = "/opt/control4-mcp/.venv/bin/python"
SENSITIVE_KEY_PARTS = ("authorization", "cookie", "credential", "jwt", "password", "secret", "token")
def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def is_sensitive_key(key: object) -> bool:
    lowered = str(key).lower()
    return any(part in lowered for part in SENSITIVE_KEY_PARTS)


def sanitize(value: Any, *, key: object | None = None) -> Any:
    """Recursively redact values under credential-like keys before logging."""
    if key is not None and is_sensitive_key(key):
        return "<redacted>"
    if isinstance(value, Mapping):
        return {str(k): sanitize(v, key=k) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize(item) for item in value]
    if isinstance(value, bytes):
        return f"<bytes:{len(value)}>"
    return value


def emit(record_type: str, **fields: Any) -> None:
    record = {"received_at": utc_now(), "record_type": record_type, **sanitize(fields)}
    print(json.dumps(record, sort_keys=True, default=str), flush=True)


def milestone(name: str, **fields: Any) -> None:
    """Emit protocol progress independently of third-party library logging."""
    emit("milestone", milestone=name, **fields)


def get_director_token(token_python: str, token_helper: str) -> str:
    """Run the existing helper without forwarding its token output to logs."""
    completed = subprocess.run(
        [token_python, token_helper],
        check=True,
        capture_output=True,
        text=True,
        timeout=45,
    )
    token = completed.stdout.strip()
    if not token:
        raise RuntimeError("trusted token helper returned an empty token")
    return token


def item_summary(item: Any) -> dict[str, Any]:
    """Keep inventory output useful but deliberately bounded and sanitized."""
    if not isinstance(item, Mapping):
        return {"unrecognized_item": sanitize(item)}
    wanted = (
        "id",
        "iddevice",
        "name",
        "displayName",
        "type",
        "category",
        "proxy",
        "model",
        "manufacturer",
        "room",
        "location",
        "locationId",
        "idroom",
    )
    return {key: sanitize(item[key], key=key) for key in wanted if key in item}


async def fetch_inventory(session: aiohttp.ClientSession, host: str, token: str) -> set[int]:
    url = f"https://{host}/api/v1/items"
    async with session.get(url, headers={"Authorization": f"Bearer {token}"}) as response:
        body = await response.text()
        if response.status >= 400:
            raise RuntimeError(f"inventory GET returned HTTP {response.status}")
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise RuntimeError("inventory GET returned non-JSON data") from exc
    items = payload if isinstance(payload, list) else payload.get("items", []) if isinstance(payload, Mapping) else []
    summaries = [item_summary(item) for item in items]
    candidates = [
        item
        for item in summaries
        if str(item.get("id", item.get("iddevice", ""))) in {"2646", "2811"}
        or any(term in str(item.get("proxy", "")).lower() for term in ("light", "dimmer", "thermostat"))
    ]
    known_items = [
        item
        for item in candidates
        if str(item.get("id", item.get("iddevice", ""))) in {"2646", "2811"}
    ]
    sample_items = (known_items + [item for item in candidates if item not in known_items])[:24]
    emit(
        "inventory",
        item_count=len(summaries),
        relevant_item_count=len(candidates),
        relevant_items_sample=sample_items,
    )
    item_ids: set[int] = set()
    for item in items:
        if not isinstance(item, Mapping):
            continue
        for key in ("id", "iddevice"):
            value = item.get(key)
            try:
                item_ids.add(int(value))
            except (TypeError, ValueError):
                continue
    return item_ids


async def verify_tcp_reachable(host: str, port: int = 443) -> None:
    """Prove TCP reachability without sending an HTTP request or device command."""
    reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=10)
    del reader
    writer.close()
    await writer.wait_closed()


class Probe:
    def __init__(self, args: argparse.Namespace, session: aiohttp.ClientSession) -> None:
        self.args = args
        self.session = session
        self.stop = asyncio.Event()
        self.connected = asyncio.Event()
        self.websocket: C4Websocket | None = None
        self.connection_number = 0
        self.event_count = 0
        self.watch_ids = set(args.watch_ids)

    async def on_connect(self) -> None:
        self.connection_number += 1
        self.connected.set()
        emit("connected", connection_number=self.connection_number, host=self.args.host)
        milestone("WS_CONNECTED", connection_number=self.connection_number)

    async def on_disconnect(self) -> None:
        self.connected.clear()
        emit("disconnected", connection_number=self.connection_number, host=self.args.host)

    async def on_event(self, routed_device_id: int, message: Any) -> None:
        if isinstance(message, Mapping):
            event_type = message.get("evtName") or message.get("event") or "unknown"
            device_id = message.get("iddevice")
            payload = message.get("data", message)
        else:
            event_type, device_id, payload = "non_mapping", None, message
        effective_device_id = device_id if device_id is not None else routed_device_id
        if self.watch_ids and effective_device_id not in self.watch_ids:
            return
        emit(
            "event",
            iddevice=effective_device_id,
            event_type=event_type,
            state_payload=payload,
            director_time=message.get("time") if isinstance(message, Mapping) else None,
        )
        self.event_count += 1
        if self.event_count == 1:
            milestone("EVENT_RECEIVED", event_count=1, iddevice=effective_device_id)
        if self.args.max_events and self.event_count >= self.args.max_events:
            self.stop.set()

    async def run(self) -> None:
        backoff = 1.0
        while not self.stop.is_set():
            token: str | None = None
            try:
                await verify_tcp_reachable(self.args.host)
                milestone("TCP_REACHABLE", host=self.args.host)
                token = await asyncio.to_thread(
                    get_director_token, self.args.token_python, self.args.token_helper
                )
                milestone("DIRECTOR_TOKEN_OK")
                inventory_ids = await fetch_inventory(self.session, self.args.host, token)
                milestone("REST_AUTH_OK", inventory_item_count=len(inventory_ids))
                self.websocket = C4Websocket(
                    self.args.host,
                    self.session,
                    self.on_connect,
                    self.on_disconnect,
                )
                # pyControl4 routes events only to registered IDs.
                subscribed_ids = self.watch_ids or (inventory_ids | {2646, 2811})
                for item_id in subscribed_ids:
                    self.websocket.add_item_callback(item_id, self.on_event)
                emit("subscription_scope", item_count=len(subscribed_ids), watch_ids=sorted(self.watch_ids))
                milestone("WS_CONNECTING", host=self.args.host)
                await self.websocket.sio_connect(token)
                await asyncio.wait_for(self.connected.wait(), timeout=20)
                milestone("LISTENING", connection_number=self.connection_number)
                backoff = 1.0
                await self.wait_until_disconnect_or_stop()
            except (asyncio.TimeoutError, aiohttp.ClientError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
                # Exception text from HTTP/Socket.IO can include an auth URL.
                emit("connection_error", error_type=type(exc).__name__)
            finally:
                token = None
                if self.websocket is not None:
                    try:
                        await self.websocket.sio_disconnect()
                    except Exception as exc:  # cleanup should not prevent a retry
                        emit("disconnect_cleanup_error", error_type=type(exc).__name__)
                    self.websocket = None
            if not self.stop.is_set():
                emit("reconnect_scheduled", delay_seconds=backoff)
                try:
                    await asyncio.wait_for(self.stop.wait(), timeout=backoff)
                except asyncio.TimeoutError:
                    pass
                backoff = min(backoff * 2, 30.0)

    async def wait_until_disconnect_or_stop(self) -> None:
        # Wait in short intervals so a library-reported disconnect is observed.
        forced_at = asyncio.get_running_loop().time() + self.args.force_reconnect_after if self.args.force_reconnect_after else None
        while not self.stop.is_set():
            if not self.connected.is_set():
                return
            if forced_at is not None and asyncio.get_running_loop().time() >= forced_at:
                emit("forced_local_reconnect", reason="probe option")
                if self.websocket is not None:
                    await self.websocket.sio_disconnect()
                return
            await asyncio.sleep(0.25)


def parse_watch_ids(value: str) -> set[int]:
    if not value:
        return set()
    try:
        ids = {int(part.strip()) for part in value.split(",")}
    except ValueError as exc:
        raise argparse.ArgumentTypeError("watch IDs must be comma-separated integers") from exc
    if any(item_id <= 0 for item_id in ids):
        raise argparse.ArgumentTypeError("watch IDs must be positive integers")
    return ids


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--duration", type=float, default=0, help="seconds; 0 listens until interrupted")
    parser.add_argument("--force-reconnect-after", type=float, default=0, help="locally disconnect after N seconds")
    parser.add_argument("--max-events", type=int, default=0, help="stop after N events; 0 listens without a limit")
    parser.add_argument("--watch-ids", type=parse_watch_ids, default=set(), help="comma-separated REST inventory IDs to listen to")
    parser.add_argument("--token-helper", default=DEFAULT_TOKEN_HELPER)
    parser.add_argument("--token-python", default=DEFAULT_PYTHON)
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


async def async_main(args: argparse.Namespace) -> int:
    if args.duration < 0 or args.force_reconnect_after < 0 or args.max_events < 0:
        raise ValueError("durations must be non-negative")
    if not Path(args.token_helper).is_file():
        raise FileNotFoundError("token helper path does not exist")
    if not Path(args.token_python).is_file():
        raise FileNotFoundError("token Python path does not exist")
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)
    # Third-party debug logs can include HTTP headers; never enable them here.
    for name in (
        "socketio",
        "socketio.client",
        "socketio_v4",
        "socketio_v4.client",
        "engineio",
        "engineio.client",
        "engineio_v3",
        "engineio_v3.client",
        "pyControl4",
    ):
        logging.getLogger(name).setLevel(logging.WARNING)
    connector = aiohttp.TCPConnector(ssl=False)
    milestone("START", host=args.host)
    async with aiohttp.ClientSession(connector=connector) as session:
        probe = Probe(args, session)
        loop = asyncio.get_running_loop()
        for signum in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(signum, probe.stop.set)
        duration_task: asyncio.Task[None] | None = None
        if args.duration:
            duration_task = asyncio.create_task(asyncio.sleep(args.duration))
            duration_task.add_done_callback(lambda _: probe.stop.set())
        try:
            await probe.run()
        finally:
            if duration_task is not None:
                duration_task.cancel()
    return 0


def main() -> int:
    try:
        return asyncio.run(async_main(parse_args()))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        emit("fatal_error", error_type=type(exc).__name__)
        return 1


if __name__ == "__main__":
    sys.exit(main())
