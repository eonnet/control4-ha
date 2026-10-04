"""Normalize only Control4 state fields observed in Phase 1."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from .proxies import (
    CONTACT_SENSOR_PROXIES,
    SUPPORTED_RELAY_PROXIES,
)


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


def _label(value: Any) -> str | None:
    """Preserve Director's display casing for named thermostat settings."""
    if not isinstance(value, str):
        return None
    result = value.strip()
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


def _rest_binary_state(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str) and value.strip() in {"0", "1"}:
        return value.strip() == "1"
    return None


def _contact_changes(data: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
    """Use the verified, user-mapped contact state rather than early hints."""
    contact = data.get("contact_state")
    if not isinstance(contact, Mapping):
        return {}, False
    if contact.get("feedback_bound") is not True or contact.get("is_verified") is not True:
        return {}, False
    current = contact.get("current_state")
    if current in ("OPENED", "CLOSED"):
        return {"director_contact_state": current, "is_on": current == "OPENED"}, True
    return {}, False


def _volume_device_changes(data: Mapping[str, Any]) -> dict[str, Any]:
    """Decode the observed Wiim aswitch OutputStatus without assuming a room."""
    status = data.get("OutputStatus")
    if not isinstance(status, Mapping):
        return {}
    output_id = status.get("OutputID")
    if type(output_id) is not int or not 0 <= output_id <= 2147483647:
        return {}
    changes: dict[str, Any] = {}
    volume = _number(status.get("VolumeLevel"))
    if volume is not None and 0 <= volume <= 100:
        changes["output_volume_percent"] = volume
    muted = status.get("MuteFlag")
    if isinstance(muted, bool):
        changes["output_is_muted"] = muted
    if changes:
        changes["output_id"] = output_id
    return changes


def _thermostat_changes(data: Mapping[str, Any]) -> dict[str, Any]:
    settings = data.get("settings")
    settings = settings if isinstance(settings, Mapping) else {}
    changes: dict[str, Any] = {}

    scale = _mode(_first(data, "scale") or settings.get("scale"))
    if scale:
        changes["scale"] = scale

    # Core3 emits the UI labels separately. Do not use `holdmode` or
    # `settings.holdmode`: the captured transition briefly mixed old and new
    # values in those legacy-shaped fields before the direct `hold_mode` update.
    hold_mode = _label(data.get("hold_mode"))
    if hold_mode:
        changes["hold_mode"] = hold_mode
    if "preset" in data:
        preset_mode = _label(data["preset"])
        if preset_mode:
            changes["preset_mode"] = preset_mode
        elif isinstance(data["preset"], str) and not data["preset"].strip():
            # GREE sends an empty preset after a manual change. Clear the
            # displayed choice rather than retaining an unconfirmed one.
            changes["preset_mode"] = None
    # Core3 emitted the display label as `fan_mode`. The parallel legacy
    # `fanmode` and `settings.fanmode` fields can lag during a preset change.
    fan_mode = _label(data.get("fan_mode"))
    if fan_mode:
        changes["fan_mode"] = fan_mode

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
        elif proxy in SUPPORTED_RELAY_PROXIES:
            changes, authoritative = _relay_changes(data)
        elif proxy in CONTACT_SENSOR_PROXIES:
            changes, authoritative = _contact_changes(data)
        elif proxy == "aswitch":
            changes = _volume_device_changes(data)
            authoritative = bool(changes)
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
    elif proxy in SUPPORTED_RELAY_PROXIES:
        state = _rest_binary_state(_first(by_name, "RELAYSTATE"))
        if state is not None:
            changes = {"is_on": state}
    elif proxy in CONTACT_SENSOR_PROXIES:
        state = _rest_binary_state(_first(by_name, "CONTACTSTATE"))
        if state is not None:
            changes = {"director_contact_state": "CLOSED" if state else "OPENED", "is_on": not state}
    elif proxy == "roomdevice":
        power = _rest_binary_state(by_name.get("POWER_STATE"))
        if power is not None:
            changes["is_on"] = power
        selected = by_name.get("CURRENT_SELECTED_DEVICE")
        if type(selected) is int and 0 <= selected <= 2147483647:
            changes["selected_source_id"] = selected
        elif isinstance(selected, str) and selected.isdecimal() and len(selected) <= 10:
            parsed = int(selected)
            if parsed <= 2147483647:
                changes["selected_source_id"] = parsed
        volume = _number(by_name.get("CURRENT_VOLUME"))
        if volume is not None and 0 <= volume <= 100:
            changes["volume_percent"] = volume
        muted = _rest_binary_state(by_name.get("IS_MUTED"))
        if muted is not None:
            changes["is_muted"] = muted
        volume_device_id = by_name.get("CURRENT_VOLUME_DEVICE_ID")
        if type(volume_device_id) is int and 0 <= volume_device_id <= 2147483647:
            changes["volume_device_id"] = volume_device_id
        elif isinstance(volume_device_id, str) and volume_device_id.isdecimal() and len(volume_device_id) <= 10:
            changes["volume_device_id"] = int(volume_device_id)
    elif proxy == "thermostatV2":
        scale = _mode(_first(by_name, "SCALE", "V1 SCALE"))
        if scale:
            changes["scale"] = scale
        single = _number(by_name.get("SINGLE_SETPOINT_C"))
        heat = _number(by_name.get("HEAT_SETPOINT_C"))
        cool = _number(by_name.get("COOL_SETPOINT_C"))
        # GREE reports its active Celsius target in SINGLE_SETPOINT_C even
        # while heating or cooling. Its heat/cool-specific REST fields are 0.
        # Require all three fields before selecting this device profile; a
        # missing field alone is not evidence of a single-setpoint thermostat.
        if single is not None and single > 0 and heat == 0 and cool == 0:
            changes["setpoint_profile"] = "SINGLE"
        hold_mode = _label(_first(by_name, "HOLD_MODE"))
        if hold_mode:
            changes["hold_mode"] = hold_mode
        preset_mode = _label(_first(by_name, "PRESET"))
        if preset_mode:
            changes["preset_mode"] = preset_mode
        fan_mode = _label(_first(by_name, "FAN_MODE"))
        if fan_mode:
            changes["fan_mode"] = fan_mode
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
