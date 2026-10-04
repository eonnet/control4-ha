"""Normalize only Control4 state fields observed in Phase 1."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from .proxies import RADIANT_FLOOR_RELAY_PROXY


Source = Literal["websocket", "rest"]


@dataclass(frozen=True)
class NormalizedEvent:
    device_id: int
    source: Source
    event_type: str
    changes: dict[str, Any]
    received_at: str
    authoritative: bool
    raw: Any = field(repr=False)


def _timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result and abs(result) != float("inf") else None


def _mode(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    result = value.strip().upper()
    return result if result and result != "-" else None


def _first(mapping: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value is not None and value != "":
            return value
    return None


def _light_changes(data: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
    level = _number(data.get("LIGHT_LEVEL"))
    changed = data.get("light_brightness_changed")
    if level is None and isinstance(changed, Mapping):
        level = _number(changed.get("light_brightness_current"))
    if level is not None and 0 <= level <= 100:
        return {"brightness_percent": level, "is_on": level > 0}, True
    changing = data.get("light_brightness_changing")
    if isinstance(changing, Mapping):
        target = _number(changing.get("light_brightness_target"))
        if target is not None and 0 <= target <= 100:
            return {"brightness_target_percent": target}, False
    return {}, False


def _relay_changes(data: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
    """Use the verified relay feedback, not the earlier generic state hint."""
    relay = data.get("relay_state")
    if not isinstance(relay, Mapping):
        return {}, False
    if relay.get("feedback_bound") is not True or relay.get("is_verified") is not True:
        return {}, False
    current = relay.get("current_state")
    if current == "CLOSED":
        return {"is_on": True}, True
    if current == "OPENED":
        return {"is_on": False}, True
    return {}, False


def _rest_relay_state(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str) and value.strip() in {"0", "1"}:
        return value.strip() == "1"
    return None


def _thermostat_changes(data: Mapping[str, Any]) -> dict[str, Any]:
    settings = data.get("settings")
    settings = settings if isinstance(settings, Mapping) else {}
    changes: dict[str, Any] = {}

    scale = _mode(_first(data, "scale") or settings.get("scale"))
    if scale:
        changes["scale"] = scale

    mode = _mode(_first(data, "hvac_mode", "hvacmode") or _first(settings, "hvacmode"))
    if mode:
        changes["hvac_mode"] = mode
    state = _mode(_first(data, "hvac_state", "hvacstate") or _first(settings, "hvacstate"))
    if state:
        changes["hvac_state"] = state

    # The Core3 emitted explicit Celsius keys. Generic raw setpoint values
    # such as 2921 are deliberately not interpreted as temperatures.
    for output_name, direct_keys, settings_key in (
        ("target_temperature_c", ("setpoint_single_c",), None),
        ("heat_setpoint_c", ("setpoint_heat_c",), "heat_setpoint"),
        ("cool_setpoint_c", ("setpoint_cool_c",), "cool_setpoint"),
        ("current_temperature_c", ("temperature_c",), "temperature"),
    ):
        value = _first(data, *direct_keys)
        if value is None and settings_key and settings.get("scale") == "CELSIUS":
            value = settings.get(settings_key)
        number = _number(value)
        if number is not None:
            changes[output_name] = number

    connected = data.get("is_connected")
    if isinstance(connected, bool):
        changes["is_connected"] = connected
    return changes


def normalize_websocket_event(raw: Any, proxy: str | None) -> NormalizedEvent | None:
    if not isinstance(raw, Mapping):
        return None
    try:
        device_id = int(raw["iddevice"])
    except (KeyError, TypeError, ValueError):
        return None
    event_type = str(raw.get("evtName") or "unknown")
    data = raw.get("data")
    data = data if isinstance(data, Mapping) else {}
    changes: dict[str, Any] = {}
    authoritative = False
    if event_type == "OnDataToUI":
        if proxy == "light_v2":
            changes, authoritative = _light_changes(data)
        elif proxy == RADIANT_FLOOR_RELAY_PROXY:
            changes, authoritative = _relay_changes(data)
        elif proxy == "thermostatV2":
            changes = _thermostat_changes(data)
            authoritative = bool(changes)
    return NormalizedEvent(
        device_id=device_id,
        source="websocket",
        event_type=event_type,
        changes=changes,
        received_at=_timestamp(),
        authoritative=authoritative,
        raw=raw,
    )


def normalize_rest_variables(
    device_id: int, proxy: str | None, variables: list[dict[str, Any]]
) -> NormalizedEvent:
    by_name = {
        str(item.get("varName", "")).strip().upper(): item.get("value")
        for item in variables
        if isinstance(item, dict)
    }
    changes: dict[str, Any] = {}
    if proxy == "light_v2":
        level = _number(_first(by_name, "BRIGHTNESS PERCENT", "LIGHT_LEVEL"))
        if level is not None and 0 <= level <= 100:
            changes = {"brightness_percent": level, "is_on": level > 0}
        else:
            state = _first(by_name, "LIGHT_STATE")
            if state is not None and str(state).strip().upper() in {"0", "1", "FALSE", "TRUE"}:
                changes = {"is_on": str(state).strip().upper() in {"1", "TRUE"}}
    elif proxy == RADIANT_FLOOR_RELAY_PROXY:
        state = _rest_relay_state(_first(by_name, "RELAYSTATE"))
        if state is not None:
            changes = {"is_on": state}
    elif proxy == "thermostatV2":
        scale = _mode(_first(by_name, "SCALE", "V1 SCALE"))
        if scale:
            changes["scale"] = scale
        mode = _mode(_first(by_name, "HVAC_MODE", "V1 HVACMODE", "ANA_HVACMODE"))
        if mode:
            changes["hvac_mode"] = mode
        state = _mode(_first(by_name, "HVAC_STATE", "ANA_HVACSTATE"))
        if state:
            changes["hvac_state"] = state
        celsius_display = scale in {"CELSIUS", "C"}
        for output_name, keys in (
            (
                "current_temperature_c",
                ("TEMPERATURE_C", "DISPLAY_TEMPERATURE", "V1 TEMPERATURE")
                if celsius_display else ("TEMPERATURE_C",),
            ),
            ("target_temperature_c", ("SINGLE_SETPOINT_C",)),
            (
                "heat_setpoint_c",
                ("HEAT_SETPOINT_C", "DISPLAY_HEATSETPOINT", "V1 HEAT_SETPOINT")
                if celsius_display else ("HEAT_SETPOINT_C",),
            ),
            (
                "cool_setpoint_c",
                ("COOL_SETPOINT_C", "DISPLAY_COOLSETPOINT", "V1 COOL_SETPOINT")
                if celsius_display else ("COOL_SETPOINT_C",),
            ),
        ):
            number = _number(_first(by_name, *keys))
            if number is not None:
                changes[output_name] = number
        connected = _first(by_name, "IS_CONNECTED", "ANA_ISCONNECTED")
        if connected is not None:
            changes["is_connected"] = str(connected).strip().upper() in {"1", "TRUE", "YES"}
    return NormalizedEvent(
        device_id=device_id,
        source="rest",
        event_type="snapshot",
        changes=changes,
        received_at=_timestamp(),
        authoritative=bool(changes),
        raw=variables,
    )
