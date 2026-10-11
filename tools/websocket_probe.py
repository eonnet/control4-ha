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
import ssl
import subprocess
import sys
from typing import Any
from xml.etree import ElementTree

import aiohttp
import socketio_v4 as socketio
from pyControl4.websocket import C4Websocket, _C4DirectorNamespace


DEFAULT_HOST = "192.168.0.24"
DEFAULT_TOKEN_HELPER = "/opt/control4-mcp/get-token.py"
DEFAULT_PYTHON = "/opt/control4-mcp/.venv/bin/python"
SENSITIVE_KEY_PARTS = (
    "authorization", "bearer", "cookie", "credential", "jwt", "password", "secret", "token",
)
REGULAR_RELAY_PROXIES = frozenset({"relaysingle_relay_c4", "cardaccess_wirelessrelay"})
MAX_RELAY_CANDIDATES = 50
MAX_NAME_MATCHES = 50
MAX_METADATA_ITEMS = 100
SAFE_STATE_VARIABLES = frozenset({
    "RELAYSTATE", "LIGHT_STATE", "LIGHT_LEVEL", "BRIGHTNESS PERCENT", "CONTACTSTATE",
    "OUTPUT_01_MUTE", "OUTPUT_01_VOLUME_LEVEL", "OUTPUT_02_MUTE", "OUTPUT_02_VOLUME_LEVEL",
})
THERMOSTAT_COMMANDS = frozenset({"SET_MODE_HOLD", "SET_PRESET", "SET_MODE_FAN"})
THERMOSTAT_FAN_VARIABLES = frozenset({
    "FAN_MODE", "FAN_MODES_LIST", "FAN_STATE", "ANA_FANMODE", "ANA_FANSTATE",
    "V1 FANMODE", "V1 FANMODES", "V1 FANSTATE",
})
THERMOSTAT_SETPOINT_VARIABLES = frozenset({
    "SINGLE_SETPOINT_C", "HEAT_SETPOINT_C", "COOL_SETPOINT_C",
    "DISPLAY_HEATSETPOINT", "DISPLAY_COOLSETPOINT",
    "V1 HEAT_SETPOINT", "V1 COOL_SETPOINT",
})
THERMOSTAT_SETPOINT_COMMANDS = frozenset({
    "SET_SETPOINT_SINGLE", "SET_SETPOINT_HEAT", "SET_SETPOINT_COOL",
})
MAX_THERMOSTAT_LABEL = 120
MEDIA_ROOM_COMMANDS = frozenset({
    "ROOM_OFF", "SET_VOLUME_LEVEL", "MUTE_ON", "MUTE_OFF",
    "PLAY", "PAUSE", "STOP", "SELECT_AUDIO_DEVICE", "SELECT_VIDEO_DEVICE",
})
MEDIA_ROOM_BOOLEAN_VARIABLES = frozenset({"POWER_STATE", "IS_MUTED"})
MEDIA_ROOM_DEVICE_VARIABLES = frozenset({
    "CURRENT_AUDIO_DEVICE", "CURRENT_VIDEO_DEVICE", "CURRENT_SELECTED_DEVICE",
    "PLAYING_AUDIO_DEVICE", "CURRENT_VOLUME_DEVICE_ID",
    "AUDIO_VOLUME_DEVICE", "VIDEO_VOLUME_DEVICE", "CURRENT_VIDEO_AUDIO_DEVICE",
})
MEDIA_PUSH_NUMBER_KEYS = frozenset({"volume_level", "current_volume"})
MEDIA_PUSH_BOOLEAN_KEYS = frozenset({"is_muted", "power_state"})
MEDIA_SAFE_PROTOCOL_LABELS = frozenset({
    "PLAY", "PAUSE", "STOP", "PLAYING", "PAUSED", "STOPPED",
    "UPDATE_PROPERTY", "LUA_OUTPUT",
})
MAX_MEDIA_KEYS = 40
MAX_MEDIA_SHAPE_BYTES = 4096
MAX_TIDAL_CAPTURE_ITEMS = 80
MAX_TIDAL_CAPTURE_TEXT = 512
MAX_TIDAL_CAPTURE_XML = 32768
TIDAL_NAV_ID_PATTERN = re.compile(r"[A-Za-z0-9._-]{1,128}")
TIDAL_PROBE_SEQUENCE = 900001


class _ProbeDirectorNamespace(_C4DirectorNamespace):
    """Expose the Navigator ID that Control4 assigns to this probe socket."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.client_id: str | None = None
        self.subscription_ready = asyncio.Event()

    async def on_clientId(self, clientId: Any) -> None:
        if isinstance(clientId, str) and clientId:
            self.client_id = clientId
        await super().on_clientId(clientId)
        # pyControl4 renamed this field from subscriptionId to subscription_id.
        # Support both the older system package and the integration's venv.
        subscription_id = self.assigned_subscription_id
        if self.client_id is not None and self.connected and subscription_id:
            self.subscription_ready.set()

    async def on_disconnect(self) -> None:
        self.subscription_ready.clear()
        self.client_id = None
        await super().on_disconnect()

    @property
    def assigned_subscription_id(self) -> str | None:
        """Return the subscription ID across supported pyControl4 versions."""
        value = getattr(
            self,
            "subscription_id",
            getattr(self, "subscriptionId", None),
        )
        return value if isinstance(value, str) and value else None


class ProbeWebsocket(C4Websocket):
    """pyControl4 WebSocket that retains its assigned Navigator client ID."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.namespace: _ProbeDirectorNamespace | None = None

    async def sio_connect(self, director_bearer_token: str) -> None:
        await self.sio_disconnect()
        self._sio = socketio.AsyncClient(ssl_verify=False)
        self.namespace = _ProbeDirectorNamespace(
            token=director_bearer_token,
            url=self.base_url,
            callback=self._callback,
            session=self.session,
            connect_callback=self.connect_callback,
            disconnect_callback=self.disconnect_callback,
        )
        self._sio.register_namespace(self.namespace)
        await self._sio.connect(
            self.wss_url,
            transports=["websocket"],
            headers={"JWT": director_bearer_token},
        )

    async def wait_until_subscription_ready(self, timeout: float = 20) -> str:
        namespace = self.namespace
        if namespace is None:
            raise RuntimeError("WebSocket namespace was not created")
        await asyncio.wait_for(namespace.subscription_ready.wait(), timeout=timeout)
        subscription_id = namespace.assigned_subscription_id
        if subscription_id is None:
            raise RuntimeError("Control4 did not assign a subscription ID")
        # The Socket.IO client ID is only used to create the subscription.
        # MSP needs the Director-assigned subscription identity as NAVID.
        return subscription_id


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


def safe_thermostat_label(value: Any) -> str | None:
    """Permit short display labels, never arbitrary structured or credential data."""
    if not isinstance(value, str):
        return None
    label = value.strip()
    if not label or len(label) > MAX_THERMOSTAT_LABEL or not label.isprintable():
        return None
    if any(char in label for char in ("@", ":", "/", "\\", "=", "<", ">")):
        return None
    if re.fullmatch(r"[A-Za-z0-9_-]{32,}", label):
        return None
    if label.count(".") > 1 or re.search(
        r"bearer|credential|password|private|secret|token|\bjwt\b", label, re.IGNORECASE
    ):
        return None
    return label


def thermostat_metadata_summary(
    variables: list[Any], commands: list[Any], device_id: int
) -> dict[str, Any] | None:
    """Project bounded thermostat evidence from read-only metadata."""
    candidates: list[str] = []
    state_values: dict[str, str] = {}
    setpoint_variable_names: list[str] = []
    setpoint_state_values: dict[str, bool | int | float | str] = {}
    for record in variables:
        if not isinstance(record, Mapping):
            continue
        name = record.get("varName")
        if not isinstance(name, str) or is_sensitive_key(name):
            continue
        if name.strip().upper() in THERMOSTAT_SETPOINT_VARIABLES:
            setpoint_variable_names.append(name)
            value = safe_state_value(record.get("value"))
            if value is not None:
                setpoint_state_values[name] = value
        canonical = re.sub(r"[^A-Z0-9]", "", name.upper())
        if (
            "PRESET" not in canonical
            and "HOLD" not in canonical
            and name.strip().upper() not in THERMOSTAT_FAN_VARIABLES
        ):
            continue
        candidates.append(name)
        label = safe_thermostat_label(record.get("value"))
        if label is not None:
            state_values[name] = label

    command_details: list[dict[str, Any]] = []
    setpoint_command_details: list[dict[str, Any]] = []
    for record in commands:
        if (
            not isinstance(record, Mapping)
            or record.get("deviceId") != device_id
            or not isinstance(record.get("command"), str)
        ):
            continue
        if record["command"] in THERMOSTAT_SETPOINT_COMMANDS:
            parameters: list[dict[str, Any]] = []
            specs = record.get("params")
            for spec in specs if isinstance(specs, list) else []:
                if not isinstance(spec, Mapping) or spec.get("name") != "CELSIUS":
                    continue
                parameter: dict[str, Any] = {"name": "CELSIUS"}
                for key in ("low", "high", "resolution"):
                    value = safe_state_value(spec.get(key))
                    if value is not None and not isinstance(value, bool):
                        parameter[key] = value
                if spec.get("valueType") in {"INTEGER", "FLOAT", "NUMBER", "STRING"}:
                    parameter["value_type"] = spec["valueType"]
                parameters.append(parameter)
            setpoint_command_details.append({
                "command": record["command"], "parameters": parameters,
            })
            continue
        if record["command"] not in THERMOSTAT_COMMANDS:
            continue
        parameters: list[dict[str, Any]] = []
        specs = record.get("params")
        for spec in specs if isinstance(specs, list) else []:
            if not isinstance(spec, Mapping) or spec.get("name") not in {"MODE", "NAME"}:
                continue
            choices = spec.get("values")
            choices = choices if isinstance(choices, list) else []
            safe_choices = [
                label for option in choices
                if isinstance(option, Mapping)
                if (label := safe_thermostat_label(option.get("value"))) is not None
            ]
            parameters.append({
                "name": spec["name"],
                "choice_count": len(choices),
                "choices": safe_choices[:MAX_METADATA_ITEMS],
                "choices_truncated_or_redacted": len(safe_choices) != len(choices)
                or len(safe_choices) > MAX_METADATA_ITEMS,
            })
        command_details.append({"command": record["command"], "parameters": parameters})

    if not candidates and not command_details and not setpoint_variable_names and not setpoint_command_details:
        return None
    return {
        "iddevice": device_id,
        "candidate_variable_names": sorted(set(candidates))[:MAX_METADATA_ITEMS],
        "candidate_variable_names_truncated": len(set(candidates)) > MAX_METADATA_ITEMS,
        "state_values": state_values,
        "commands": command_details,
        "setpoint_variable_names": sorted(set(setpoint_variable_names)),
        "setpoint_state_values": setpoint_state_values,
        "setpoint_commands": setpoint_command_details,
    }


def media_room_metadata_summary(
    variables: list[Any], commands: list[Any], device_id: int,
    inventory_ids: set[int],
) -> dict[str, Any] | None:
    """Show only known room controls and safe scalar state; omit media details."""
    advertised = sorted({
        record["command"] for record in commands
        if isinstance(record, Mapping)
        and record.get("deviceId") == device_id
        and isinstance(record.get("command"), str)
        and record.get("command") in MEDIA_ROOM_COMMANDS
    })
    if "ROOM_OFF" not in advertised:
        return None
    values: dict[str, Any] = {}
    for record in variables:
        if not isinstance(record, Mapping) or not isinstance(record.get("varName"), str):
            continue
        name = record["varName"].strip().upper()
        value = record.get("value")
        if name in MEDIA_ROOM_BOOLEAN_VARIABLES:
            if value in (0, 1, False, True, "0", "1"):
                values[name] = value
        elif name == "CURRENT_VOLUME":
            safe = safe_state_value(value)
            if isinstance(safe, (int, float)) and not isinstance(safe, bool):
                values[name] = safe
            elif isinstance(safe, str) and re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", safe):
                values[name] = safe
        elif name in MEDIA_ROOM_DEVICE_VARIABLES:
            if isinstance(value, bool):
                continue
            if isinstance(value, int):
                source_id = value
            elif isinstance(value, str) and re.fullmatch(r"[0-9]{1,8}", value):
                source_id = int(value)
            else:
                continue
            if source_id == 0 or source_id in inventory_ids:
                values[name] = source_id
    return {"iddevice": device_id, "commands": advertised, "state_values": values}


def safe_media_key(key: Any) -> bool:
    return (
        isinstance(key, str)
        and len(key) <= 40
        and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", key) is not None
        and not is_sensitive_key(key)
    )


def media_output_status_shape(value: Any) -> dict[str, Any]:
    """Describe the Wiim event's structure without printing any raw value."""
    if isinstance(value, str):
        result: dict[str, Any] = {"value_type": "str", "length": len(value)}
        if len(value) > MAX_MEDIA_SHAPE_BYTES:
            result["format"] = "oversize"
            return result
        stripped = value.lstrip()
        if stripped.startswith(("{", "[")):
            result["format"] = "json_like"
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                return result
        elif stripped.startswith("<"):
            result["format"] = "xml_like"
            if "<!" in value:
                return result
            try:
                root = ElementTree.fromstring(value)
            except ElementTree.ParseError:
                return result
            result["xml_tags"] = sorted({node.tag for node in root.iter() if safe_media_key(node.tag)})[:MAX_MEDIA_KEYS]
            result["xml_attributes"] = sorted({key for node in root.iter() for key in node.attrib if safe_media_key(key)})[:MAX_MEDIA_KEYS]
            return result
        elif re.fullmatch(r"-?[0-9]+(?:\.[0-9]+)?", stripped):
            result["format"] = "number_like"
            return result
        else:
            result["format"] = "text"
            return result
    else:
        result = {"value_type": type(value).__name__}
    if isinstance(value, Mapping):
        keys = sorted(key for key in value if safe_media_key(key))[:MAX_MEDIA_KEYS]
        result["keys"] = keys
        result["field_types"] = {key: type(value[key]).__name__ for key in keys}
    elif isinstance(value, list):
        result["length"] = len(value)
        result["item_types"] = sorted({type(item).__name__ for item in value})
        result["item_keys"] = sorted({key for item in value[:MAX_MEDIA_KEYS] if isinstance(item, Mapping) for key in item if safe_media_key(key)})[:MAX_MEDIA_KEYS]
    return result


def media_output_status_values(value: Any) -> dict[str, int | float | bool]:
    """Project only the fields observed in Wiim's structured OutputStatus."""
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, int | float | bool] = {}
    output_id = value.get("OutputID")
    if isinstance(output_id, int) and not isinstance(output_id, bool) and 0 <= output_id <= 2147483647:
        result["OutputID"] = output_id
    volume = value.get("VolumeLevel")
    if isinstance(volume, (int, float)) and not isinstance(volume, bool) and math.isfinite(volume) and 0 <= volume <= 100:
        result["VolumeLevel"] = volume
    muted = value.get("MuteFlag")
    if isinstance(muted, bool):
        result["MuteFlag"] = muted
    return result


def media_event_projection(payload: Any) -> dict[str, Any]:
    """Summarize room push structure without exposing track art, URLs, or text."""
    if not isinstance(payload, Mapping):
        return {"payload_type": type(payload).__name__}

    keys = sorted(key for key in payload if safe_media_key(key))
    values: dict[str, Any] = {}
    for key in MEDIA_PUSH_NUMBER_KEYS:
        value = payload.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and 0 <= value <= 100:
            values[key] = value
    for key in MEDIA_PUSH_BOOLEAN_KEYS:
        value = payload.get(key)
        if isinstance(value, bool):
            values[key] = value
        elif isinstance(value, int) and value in (0, 1):
            values[key] = bool(value)
    nested_keys = {
        key: sorted(child for child in payload[key] if safe_media_key(child))[:MAX_MEDIA_KEYS]
        for key in keys[:MAX_MEDIA_KEYS] if isinstance(payload[key], Mapping)
    }
    result = {
        "payload_keys": keys[:MAX_MEDIA_KEYS],
        "payload_keys_truncated": len(keys) > MAX_MEDIA_KEYS,
        "nested_keys": nested_keys,
        "state_payload": values,
    }
    # Only show exact, known-safe protocol labels. Never print arbitrary
    # devicecommand params, EVENT args, media text, or unknown label values.
    for container_key, label_key in (("devicecommand", "command"), ("EVENT", "NAME")):
        container = payload.get(container_key)
        if not isinstance(container, Mapping):
            continue
        label = container.get(label_key)
        if isinstance(label, str) and label in MEDIA_SAFE_PROTOCOL_LABELS:
            result["safe_protocol_label"] = label
    if "OutputStatus" in payload:
        result["output_status_shape"] = media_output_status_shape(payload["OutputStatus"])
        result["output_status_values"] = media_output_status_values(payload["OutputStatus"])
    return result


def _redact_tidal_text(value: str, *, key: object | None = None) -> str:
    """Keep browse identifiers and labels while removing credentials and URLs."""
    if key is not None and is_sensitive_key(key):
        return "<redacted>"
    stripped = value.strip()
    if re.search(r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]+", stripped):
        return "<redacted>"
    if re.search(r"(?i)\b(?:https?|wss?)://", stripped):
        return "<url-redacted>"
    if re.search(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", stripped):
        return "<email-redacted>"
    if (
        len(stripped) > 80
        and not any(character.isspace() for character in stripped)
        and re.fullmatch(r"[A-Za-z0-9._~+/=-]+", stripped)
    ):
        return "<opaque-redacted>"
    if len(stripped) > MAX_TIDAL_CAPTURE_TEXT:
        return stripped[:MAX_TIDAL_CAPTURE_TEXT] + "<truncated>"
    return stripped


def _sanitize_tidal_xml(value: str) -> str | None:
    """Return bounded XML with URLs, credentials, and opaque secrets removed."""
    if len(value) > MAX_TIDAL_CAPTURE_XML * 4 or "<!" in value:
        return None
    try:
        root = ElementTree.fromstring(value)
    except ElementTree.ParseError:
        return None

    for node in root.iter():
        node.text = _redact_tidal_text(node.text, key=node.tag) if node.text else None
        node.tail = None
        for name, attribute in tuple(node.attrib.items()):
            node.attrib[name] = _redact_tidal_text(attribute, key=name)
    result = ElementTree.tostring(root, encoding="unicode")
    if len(result) > MAX_TIDAL_CAPTURE_XML:
        return result[:MAX_TIDAL_CAPTURE_XML] + "<truncated>"
    return result


def tidal_browse_projection(
    value: Any, *, key: object | None = None, depth: int = 0,
) -> Any:
    """Sanitize a TIDAL MSP event while retaining its browse command contract."""
    if key is not None and is_sensitive_key(key):
        return "<redacted>"
    if depth > 8:
        return "<depth-limit>"
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        named_parameter = next(
            (
                child_value
                for child_key, child_value in value.items()
                if str(child_key).casefold() == "name" and isinstance(child_value, str)
            ),
            None,
        )
        redact_named_value = named_parameter is not None and is_sensitive_key(named_parameter)
        for index, (child_key, child_value) in enumerate(value.items()):
            if index >= MAX_TIDAL_CAPTURE_ITEMS:
                result["_truncated"] = True
                break
            safe_key = str(child_key)[:80]
            if redact_named_value and safe_key.casefold() in {"value", "data", "text"}:
                result[safe_key] = "<redacted>"
            else:
                result[safe_key] = tidal_browse_projection(
                    child_value, key=child_key, depth=depth + 1
                )
        return result
    if isinstance(value, (list, tuple)):
        result = [
            tidal_browse_projection(item, key=key, depth=depth + 1)
            for item in value[:MAX_TIDAL_CAPTURE_ITEMS]
        ]
        if len(value) > MAX_TIDAL_CAPTURE_ITEMS:
            result.append("<truncated>")
        return result
    if isinstance(value, str):
        xml = _sanitize_tidal_xml(value) if value.lstrip().startswith("<") else None
        return xml if xml is not None else _redact_tidal_text(value, key=key)
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else "<non-finite>"
    if isinstance(value, bytes):
        return f"<bytes:{len(value)}>"
    return f"<{type(value).__name__}>"


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
        thermostat_details = thermostat_metadata_summary(variables, commands, device_id)
        if thermostat_details is not None:
            emit("thermostat_metadata", **thermostat_details)
        media_details = media_room_metadata_summary(variables, commands, device_id, inventory_ids)
        if media_details is not None:
            emit("media_room_metadata", **media_details)


async def fetch_contact_state_value(
    session: aiohttp.ClientSession, host: str, token: str, device_id: int
) -> bool | int | float | str | None:
    """Read only one allowlisted sensor value; never emit arbitrary variables."""
    url = f"https://{host}/api/v1/items/{device_id}/variables"
    async with session.get(url, headers={"Authorization": f"Bearer {token}"}) as response:
        if response.status >= 400:
            raise RuntimeError(f"contact variables GET returned HTTP {response.status}")
        try:
            payload = json.loads(await response.text())
        except json.JSONDecodeError as exc:
            raise RuntimeError("contact variables GET returned non-JSON data") from exc
    variables = payload if isinstance(payload, list) else payload.get("variables", []) if isinstance(payload, Mapping) else []
    if not isinstance(variables, list):
        return None
    for record in variables:
        if isinstance(record, Mapping) and str(record.get("varName", "")).strip().upper() == "CONTACTSTATE":
            return safe_state_value(record.get("value"))
    return None


async def verify_tcp_reachable(host: str, port: int = 443) -> None:
    """Prove TCP reachability without sending an HTTP request or device command."""
    reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=10)
    del reader
    writer.close()
    await writer.wait_closed()


async def request_tidal_tabs(
    session: aiohttp.ClientSession,
    host: str,
    token: str,
    device_id: int,
    room_id: int,
    nav_id: str,
) -> None:
    """Request only the TIDAL MSP tab contract; this does not select or play media."""
    body = {
        "async": True,
        "command": "GetTabs",
        "tParams": {
            "ROOMID": room_id,
            "NAVID": nav_id,
            "SEQ": TIDAL_PROBE_SEQUENCE,
            "LOCALE": "en-US",
            "ARGS": "<args></args>",
        },
    }
    url = f"https://{host}/api/v1/items/{device_id}/commands"
    async with session.post(
        url,
        headers={"Authorization": f"Bearer {token}"},
        json=body,
    ) as response:
        raw_response = await response.read()
        response_body: Any = {
            "value_type": "bytes",
            "length": len(raw_response),
        }
        if len(raw_response) <= MAX_TIDAL_CAPTURE_XML * 4:
            try:
                decoded = raw_response.decode("utf-8")
            except UnicodeDecodeError:
                decoded = ""
            if decoded:
                stripped = decoded.lstrip()
                if stripped.startswith(("{", "[")):
                    try:
                        response_body = tidal_browse_projection(json.loads(decoded))
                    except json.JSONDecodeError:
                        pass
                elif stripped.startswith("<"):
                    xml = _sanitize_tidal_xml(decoded)
                    if xml is not None:
                        response_body = xml
        emit(
            "tidal_get_tabs_request",
            iddevice=device_id,
            room_id=room_id,
            nav_id=nav_id,
            http_status=response.status,
            response_body=response_body,
        )
        if response.status >= 400:
            raise RuntimeError(f"TIDAL GetTabs returned HTTP {response.status}")


def build_tidal_tabs_soap(device_id: int, room_id: int, nav_id: str) -> bytes:
    """Build the Navigator SendToDeviceAsync message for read-only GetTabs."""
    root = ElementTree.Element(
        "c4soap",
        {"name": "SendToDeviceAsync", "async": "1", "seq": str(TIDAL_PROBE_SEQUENCE)},
    )
    ElementTree.SubElement(
        root, "param", {"name": "iddevice", "type": "number"}
    ).text = str(device_id)
    data = ElementTree.SubElement(root, "param", {"name": "data", "type": "string"})
    device_command = ElementTree.SubElement(data, "devicecommand")
    ElementTree.SubElement(device_command, "command").text = "GetTabs"
    params = ElementTree.SubElement(device_command, "params")
    for name, value, value_type in (
        ("ROOMID", str(room_id), "INT"),
        ("NAVID", nav_id, "STRING"),
        ("SEQ", str(TIDAL_PROBE_SEQUENCE), "INT"),
        ("LOCALE", "en-US", "STRING"),
        ("ARGS", "<args></args>", "STRING"),
    ):
        param = ElementTree.SubElement(params, "param")
        ElementTree.SubElement(param, "name").text = name
        static = ElementTree.SubElement(
            ElementTree.SubElement(param, "value", {"type": value_type}),
            "static",
        )
        static.text = value
    return ElementTree.tostring(root, encoding="utf-8") + b"\0"


async def request_tidal_tabs_soap(
    host: str,
    device_id: int,
    room_id: int,
    nav_id: str,
    port: int = 5020,
    use_tls: bool = False,
) -> None:
    """Send read-only MSP GetTabs through Navigator's local SOAP channel."""
    ssl_context: ssl.SSLContext | None = None
    if use_tls:
        # Directors use a locally managed certificate.  This probe checks only
        # whether the authenticated Navigator SOAP service accepts a connection;
        # it deliberately supplies no Control4/Composer client certificate.
        ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
    milestone("TIDAL_SOAP_CONNECTING", port=port, tls=use_tls)
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(
            host,
            port,
            ssl=ssl_context,
            server_hostname=None,
        ),
        timeout=10,
    )
    response_state = "not-read"
    response_body: Any = None
    try:
        writer.write(build_tidal_tabs_soap(device_id, room_id, nav_id))
        await asyncio.wait_for(writer.drain(), timeout=10)
        try:
            raw_response = await asyncio.wait_for(
                reader.read(MAX_TIDAL_CAPTURE_XML + 1), timeout=3
            )
        except asyncio.TimeoutError:
            response_state = "timeout"
        else:
            response_state = "received" if raw_response else "closed-without-response"
            response_body = {"value_type": "bytes", "length": len(raw_response)}
            if raw_response and len(raw_response) <= MAX_TIDAL_CAPTURE_XML:
                try:
                    decoded = raw_response.rstrip(b"\0").decode("utf-8")
                except UnicodeDecodeError:
                    decoded = ""
                if decoded:
                    response_body = _sanitize_tidal_xml(decoded) or _redact_tidal_text(decoded)
    finally:
        writer.close()
        await writer.wait_closed()
    emit(
        "tidal_get_tabs_soap_request",
        iddevice=device_id,
        room_id=room_id,
        nav_id=nav_id,
        port=port,
        response_body=response_body,
        response_state=response_state,
        sequence=TIDAL_PROBE_SEQUENCE,
    )


async def fetch_tidal_proxy_contract(
    session: aiohttp.ClientSession,
    host: str,
    token: str,
    device_id: int,
) -> None:
    """Read sanitized item/proxy metadata that may contain MSP screen commands."""
    for endpoint in (f"/api/v1/items/{device_id}", f"/api/v1/items/{device_id}/proxy"):
        async with session.get(
            f"https://{host}{endpoint}",
            headers={"Authorization": f"Bearer {token}"},
        ) as response:
            raw = await response.read()
            payload: Any = {"value_type": "bytes", "length": len(raw)}
            if len(raw) <= MAX_TIDAL_CAPTURE_XML * 8:
                try:
                    decoded = raw.decode("utf-8")
                except UnicodeDecodeError:
                    decoded = ""
                if decoded:
                    try:
                        payload = tidal_browse_projection(json.loads(decoded))
                    except json.JSONDecodeError:
                        xml = _sanitize_tidal_xml(decoded)
                        if xml is not None:
                            payload = xml
            emit(
                "tidal_proxy_contract",
                endpoint=endpoint,
                http_status=response.status,
                payload=payload,
            )


class Probe:
    def __init__(self, args: argparse.Namespace, session: aiohttp.ClientSession) -> None:
        self.args = args
        self.session = session
        self.stop = asyncio.Event()
        self.connected = asyncio.Event()
        self.websocket: ProbeWebsocket | None = None
        self.connection_number = 0
        self.event_count = 0
        self.watch_ids = set(args.watch_ids)
        self.inspect_ids = set(args.inspect_ids)
        self._token: str | None = None
        self._last_contact_state: dict[int, str] = {}
        self._tidal_probe_sent = False
        self._tidal_navigator_id: str | None = None
        self._tidal_navigator_ready = asyncio.Event()

    async def on_connect(self) -> None:
        self.connection_number += 1
        self.connected.set()
        self._last_contact_state.clear()
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
        if getattr(self.args, "media_safe", False):
            director_time = message.get("time") if isinstance(message, Mapping) else None
            emit(
                "event",
                iddevice=effective_device_id if isinstance(effective_device_id, int) else None,
                event_type="OnDataToUI" if event_type == "OnDataToUI" else "other",
                director_time=director_time if isinstance(director_time, int) and 0 <= director_time <= 9999999999 else None,
                **media_event_projection(payload),
            )
        elif getattr(self.args, "tidal_browse_safe", False):
            director_time = message.get("time") if isinstance(message, Mapping) else None
            response = payload.get("RESPONSE") if isinstance(payload, Mapping) else None
            nav_id = response.get("NAVID") if isinstance(response, Mapping) else None
            response_data = response.get("DATA") if isinstance(response, Mapping) else None
            if (
                getattr(self.args, "tidal_learn_nav", False)
                and isinstance(nav_id, str)
                and TIDAL_NAV_ID_PATTERN.fullmatch(nav_id)
                and response_data not in (None, "", [], {})
            ):
                self._tidal_navigator_id = nav_id
                self._tidal_navigator_ready.set()
            emit(
                "tidal_browse_event",
                iddevice=effective_device_id if isinstance(effective_device_id, int) else None,
                event_type="OnDataToUI" if event_type == "OnDataToUI" else "other",
                director_time=director_time if isinstance(director_time, int) and 0 <= director_time <= 9999999999 else None,
                payload=tidal_browse_projection(payload),
            )
        else:
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
        if not getattr(self.args, "sample_contact_rest", False) or self._token is None:
            return
        contact = payload.get("contact_state") if isinstance(payload, Mapping) else None
        if event_type != "OnDataToUI" or not isinstance(contact, Mapping):
            return
        current = contact.get("current_state")
        if (
            contact.get("feedback_bound") is not True
            or contact.get("is_verified") is not True
            or current not in ("OPENED", "CLOSED")
            or not isinstance(effective_device_id, int)
        ):
            return
        if self._last_contact_state.get(effective_device_id) == current:
            return
        try:
            value = await asyncio.wait_for(
                fetch_contact_state_value(
                    self.session, self.args.host, self._token, effective_device_id
                ),
                timeout=3,
            )
            if value is not None:
                self._last_contact_state[effective_device_id] = current
            emit(
                "contact_rest_sample",
                iddevice=effective_device_id,
                director_contact_state=current,
                contact_state_value=value,
            )
        except (asyncio.TimeoutError, aiohttp.ClientError, OSError, RuntimeError) as exc:
            emit("contact_rest_sample_error", iddevice=effective_device_id, error_type=type(exc).__name__)

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
                self._token = token
                milestone("DIRECTOR_TOKEN_OK")
                inventory_ids = await fetch_inventory(
                    self.session, self.args.host, token, self.watch_ids, self.args.find_name
                )
                milestone("REST_AUTH_OK", inventory_item_count=len(inventory_ids))
                if self.inspect_ids:
                    await fetch_device_metadata(
                        self.session, self.args.host, token, self.inspect_ids, inventory_ids
                    )
                if getattr(self.args, "tidal_get_tabs", 0):
                    await fetch_tidal_proxy_contract(
                        self.session,
                        self.args.host,
                        token,
                        self.args.tidal_get_tabs,
                    )
                if self.args.inventory_only:
                    self.stop.set()
                    return
                self.websocket = ProbeWebsocket(
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
                navigator_id = await self.websocket.wait_until_subscription_ready()
                milestone("SUBSCRIPTION_READY", connection_number=self.connection_number)
                milestone("LISTENING", connection_number=self.connection_number)
                if self.args.tidal_get_tabs and not self._tidal_probe_sent:
                    if self.args.tidal_learn_nav:
                        milestone("WAITING_FOR_TIDAL_NAVIGATOR")
                        await asyncio.wait_for(
                            self._tidal_navigator_ready.wait(), timeout=30
                        )
                        if self._tidal_navigator_id is None:
                            raise RuntimeError("TIDAL Navigator ID was not observed")
                        navigator_id = self._tidal_navigator_id
                        milestone("TIDAL_NAVIGATOR_OBSERVED")
                    self._tidal_probe_sent = True
                    if self.args.tidal_soap:
                        soap_port = 5021 if self.args.tidal_soap_secure else 5020
                        await request_tidal_tabs_soap(
                            self.args.host,
                            self.args.tidal_get_tabs,
                            self.args.tidal_room_id,
                            navigator_id,
                            port=soap_port,
                            use_tls=self.args.tidal_soap_secure,
                        )
                    else:
                        command_device_id = (
                            self.args.tidal_protocol_id or self.args.tidal_get_tabs
                        )
                        await request_tidal_tabs(
                            self.session,
                            self.args.host,
                            token,
                            command_device_id,
                            self.args.tidal_room_id,
                            navigator_id,
                        )
                backoff = 1.0
                await self.wait_until_disconnect_or_stop()
            except (asyncio.TimeoutError, aiohttp.ClientError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
                # Exception text from HTTP/Socket.IO can include an auth URL.
                emit("connection_error", error_type=type(exc).__name__)
                if self.args.inventory_only:
                    raise RuntimeError("inventory-only probe failed") from None
            finally:
                self._token = None
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
    parser.add_argument("--sample-contact-rest", action="store_true", help="read ContactState after each verified contact transition for watched IDs only")
    parser.add_argument("--media-safe", action="store_true", help="show only event structure and allowlisted scalar values; with no watch IDs, listen to all inventory IDs without raw payloads")
    parser.add_argument("--tidal-browse-safe", action="store_true", help="capture sanitized TIDAL browse/search contracts; use with confirmed TIDAL IDs")
    parser.add_argument("--tidal-get-tabs", type=int, default=0, metavar="DEVICE_ID", help="send one read-only GetTabs request to a confirmed TIDAL proxy")
    parser.add_argument("--tidal-protocol-id", type=int, default=0, metavar="DEVICE_ID", help="send GetTabs to this TIDAL protocol parent while watching the proxy")
    parser.add_argument("--tidal-room-id", type=int, default=0, metavar="ROOM_ID", help="room context for --tidal-get-tabs")
    parser.add_argument("--tidal-learn-nav", action="store_true", help="wait for a live Control4-app TIDAL response and reuse its valid Navigator ID")
    parser.add_argument("--tidal-soap", action="store_true", help="send GetTabs through Navigator's local SendToDeviceAsync SOAP channel")
    parser.add_argument("--tidal-soap-secure", action="store_true", help="use TLS port 5021 for --tidal-soap, without supplying a client certificate")
    parser.add_argument("--token-helper", default=DEFAULT_TOKEN_HELPER)
    parser.add_argument("--token-python", default=DEFAULT_PYTHON)
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


async def async_main(args: argparse.Namespace) -> int:
    if args.duration < 0 or args.force_reconnect_after < 0 or args.max_events < 0:
        raise ValueError("durations must be non-negative")
    if len(args.inspect_ids) > 10:
        raise ValueError("inspect at most ten IDs per run")
    if args.sample_contact_rest and (not args.watch_ids or len(args.watch_ids) > 10 or args.inventory_only):
        raise ValueError("contact sampling requires 1-10 watch IDs and a WebSocket run")
    if args.media_safe and (len(args.watch_ids) > 10 or args.inventory_only):
        raise ValueError("media-safe capture requires at most ten watch IDs and a WebSocket run")
    if args.tidal_browse_safe and (
        not args.watch_ids or len(args.watch_ids) > 10 or args.inventory_only
    ):
        raise ValueError("TIDAL browse capture requires 1-10 watch IDs and a WebSocket run")
    if args.media_safe and args.tidal_browse_safe:
        raise ValueError("choose either media-safe or TIDAL browse-safe capture")
    if args.tidal_get_tabs and (
        not args.tidal_browse_safe
        or args.tidal_get_tabs not in args.watch_ids
        or args.tidal_room_id <= 0
    ):
        raise ValueError(
            "TIDAL GetTabs requires browse-safe capture, its device in watch IDs, and a positive room ID"
        )
    if args.tidal_learn_nav and not args.tidal_get_tabs:
        raise ValueError("TIDAL Navigator learning requires --tidal-get-tabs")
    if args.tidal_protocol_id < 0 or (
        args.tidal_protocol_id and not args.tidal_get_tabs
    ):
        raise ValueError("a TIDAL protocol ID requires --tidal-get-tabs")
    if args.tidal_soap and (
        not args.tidal_get_tabs or not args.tidal_learn_nav or args.tidal_protocol_id
    ):
        raise ValueError(
            "TIDAL SOAP requires GetTabs and Navigator learning, without a protocol override"
        )
    if args.tidal_soap_secure and not args.tidal_soap:
        raise ValueError("secure TIDAL SOAP requires --tidal-soap")
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
