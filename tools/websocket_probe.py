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
import math
from pathlib import Path
import re
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
REGULAR_RELAY_PROXIES = frozenset({"relaysingle_relay_c4", "cardaccess_wirelessrelay"})
MAX_RELAY_CANDIDATES = 50
MAX_NAME_MATCHES = 50
MAX_METADATA_ITEMS = 100
SAFE_STATE_VARIABLES = frozenset({"RELAYSTATE", "LIGHT_STATE", "LIGHT_LEVEL", "BRIGHTNESS PERCENT"})


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
        "typeName",
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


async def fetch_inventory(
    session: aiohttp.ClientSession,
    host: str,
    token: str,
    watch_ids: set[int] | None = None,
    name_terms: list[str] | None = None,
) -> set[int]:
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
    item_ids: set[int] = set()
    watched_items: list[dict[str, Any]] = []
    relay_candidates: list[dict[str, Any]] = []
    name_matches: list[dict[str, Any]] = []
    watch_ids = watch_ids or set()
    search_terms = [term.strip().casefold() for term in (name_terms or []) if term.strip()]
    for item in items:
        if not isinstance(item, Mapping):
            continue
        current_ids: set[int] = set()
        for key in ("id", "iddevice"):
            value = item.get(key)
            try:
                current_ids.add(int(value))
            except (TypeError, ValueError):
                continue
        item_ids.update(current_ids)
        if item.get("typeName") == "device" and item.get("proxy") in REGULAR_RELAY_PROXIES:
            relay_candidates.append({
                key: sanitize(item[key], key=key)
                for key in ("id", "iddevice", "name", "proxy", "typeName", "roomName")
                if key in item
            })
        if search_terms and any(term in str(item.get("name", "")).casefold() for term in search_terms):
            match = {
                key: sanitize(item[key], key=key)
                for key in ("id", "iddevice", "name", "proxy", "typeName", "roomName", "parentId")
                if key in item
            }
            capabilities = item.get("capabilities")
            if isinstance(capabilities, Mapping):
                match["capabilities"] = {
                    key: sanitize(capabilities[key], key=key)
                    for key in ("on_off", "dimmer", "can_heat", "can_cool")
                    if key in capabilities
                }
            name_matches.append(match)
        if current_ids & watch_ids:
            watched_items.append({
                key: sanitize(item[key], key=key)
                for key in ("id", "iddevice", "name", "proxy", "typeName")
                if key in item
            })
    emit(
        "inventory",
        item_count=len(summaries),
        relevant_item_count=len(candidates),
        relevant_items_sample=sample_items,
    )
    if watch_ids:
        emit(
            "watched_inventory",
            watched_items=watched_items,
            missing_watch_ids=sorted(watch_ids - item_ids),
        )
    emit(
        "relay_inventory",
        candidate_count=len(relay_candidates),
        items=relay_candidates[:MAX_RELAY_CANDIDATES],
        truncated=len(relay_candidates) > MAX_RELAY_CANDIDATES,
    )
    if search_terms:
        emit(
            "matched_inventory",
            match_count=len(name_matches),
            items=name_matches[:MAX_NAME_MATCHES],
            truncated=len(name_matches) > MAX_NAME_MATCHES,
        )
    return item_ids


def safe_state_value(value: Any) -> bool | int | float | str | None:
    """Show only short, simple state values; never arbitrary variable contents."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and -1 <= value <= 100:
        return value
    if isinstance(value, float) and math.isfinite(value) and -1 <= value <= 100:
        return value
    if isinstance(value, str):
        value = value.strip()
        if value.upper() in {"OPENED", "CLOSED", "OPEN", "CLOSE", "ON", "OFF", "TRUE", "FALSE"}:
            return value
        if len(value) <= 8 and re.fullmatch(r"-?[0-9]+(?:\.[0-9]+)?", value):
            number = float(value)
            if -1 <= number <= 100:
                return value
    return None


async def fetch_device_metadata(
    session: aiohttp.ClientSession,
    host: str,
    token: str,
    device_ids: set[int],
    inventory_ids: set[int],
) -> None:
    """Read bounded variable/command metadata for confirmed inventory IDs."""
    missing = sorted(device_ids - inventory_ids)
    if missing:
        emit("missing_metadata_ids", ids=missing)
    for device_id in sorted(device_ids & inventory_ids):
        payloads: dict[str, Any] = {}
        for kind in ("variables", "commands"):
            url = f"https://{host}/api/v1/items/{device_id}/{kind}"
            async with session.get(url, headers={"Authorization": f"Bearer {token}"}) as response:
                if response.status >= 400:
                    raise RuntimeError(f"{kind} GET returned HTTP {response.status}")
                try:
                    payloads[kind] = json.loads(await response.text())
                except json.JSONDecodeError as exc:
                    raise RuntimeError(f"{kind} GET returned non-JSON data") from exc
        variables = payloads["variables"]
        variables = variables if isinstance(variables, list) else variables.get("variables", []) if isinstance(variables, Mapping) else []
        commands = payloads["commands"]
        commands = commands if isinstance(commands, list) else []
        variable_names = sorted({
            record["varName"] for record in variables
            if isinstance(record, Mapping) and isinstance(record.get("varName"), str)
        })
        state_values = {
            record["varName"]: safe_state_value(record.get("value"))
            for record in variables
            if isinstance(record, Mapping)
            and isinstance(record.get("varName"), str)
            and record["varName"].strip().upper() in SAFE_STATE_VARIABLES
            and safe_state_value(record.get("value")) is not None
        }
        command_summaries = [
            {
                "command": record["command"],
                "parameter_names": [
                    spec["name"] for spec in (record.get("params") or [])
                    if isinstance(spec, Mapping) and isinstance(spec.get("name"), str)
                ],
            }
            for record in commands
            if isinstance(record, Mapping)
            and record.get("deviceId") == device_id
            and isinstance(record.get("command"), str)
        ]
        emit(
            "device_metadata",
            iddevice=device_id,
            variable_count=len(variable_names),
            variable_names=variable_names[:MAX_METADATA_ITEMS],
            variable_names_truncated=len(variable_names) > MAX_METADATA_ITEMS,
            state_values=state_values,
            command_count=len(command_summaries),
            commands=command_summaries[:MAX_METADATA_ITEMS],
            commands_truncated=len(command_summaries) > MAX_METADATA_ITEMS,
        )


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
        self.inspect_ids = set(args.inspect_ids)

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
                inventory_ids = await fetch_inventory(
                    self.session, self.args.host, token, self.watch_ids, self.args.find_name
                )
                milestone("REST_AUTH_OK", inventory_item_count=len(inventory_ids))
                if self.inspect_ids:
                    await fetch_device_metadata(
                        self.session, self.args.host, token, self.inspect_ids, inventory_ids
                    )
                if self.args.inventory_only:
                    self.stop.set()
                    return
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
                if self.args.inventory_only:
                    raise RuntimeError("inventory-only probe failed") from None
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
    parser.add_argument("--inventory-only", action="store_true", help="read inventory and relay candidates, then exit without opening WebSocket")
    parser.add_argument("--find-name", action="append", default=[], help="show bounded inventory matches for this name fragment; repeatable")
    parser.add_argument("--inspect-ids", type=parse_watch_ids, default=set(), help="comma-separated inventory IDs for read-only variable/command metadata")
    parser.add_argument("--token-helper", default=DEFAULT_TOKEN_HELPER)
    parser.add_argument("--token-python", default=DEFAULT_PYTHON)
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


async def async_main(args: argparse.Namespace) -> int:
    if args.duration < 0 or args.force_reconnect_after < 0 or args.max_events < 0:
        raise ValueError("durations must be non-negative")
    if len(args.inspect_ids) > 10:
        raise ValueError("inspect at most ten IDs per run")
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
