"""Conservative inventory eligibility based on live Core3 metadata."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any

from .const import DOMAIN
from .transport.proxies import CONTACT_SENSOR_PROXIES, SUPPORTED_RELAY_PROXIES


def is_candidate_item(item: dict[str, Any]) -> bool:
    if item.get("typeName") != "device" or not isinstance(item.get("id"), int):
        return False
    proxy = item.get("proxy")
    if isinstance(proxy, str) and proxy in SUPPORTED_RELAY_PROXIES:
        return True
    if isinstance(proxy, str) and proxy in CONTACT_SENSOR_PROXIES:
        return True
    capabilities = item.get("capabilities")
    if not isinstance(capabilities, dict):
        return False
    if proxy == "light_v2":
        # Core3 push is proven for both dimmable and on/off-only light_v2.
        return capabilities.get("on_off") is True and isinstance(capabilities.get("dimmer"), bool)
    if proxy == "thermostatV2":
        return capabilities.get("can_heat") is True or capabilities.get("can_cool") is True
    return False


def supported_light_ids(
    items: Mapping[int, dict[str, Any]],
    tracked_ids: Iterable[int],
    supports_command: Callable[[int, str], bool],
) -> list[int]:
    """Register proven lights even if their initial REST state read failed.

    A missing snapshot leaves the entity unavailable until a settled push event
    or reconciliation supplies state; it must not hide the entity permanently.
    """
    return [
        device_id
        for device_id in sorted(tracked_ids)
        if is_candidate_item(items[device_id])
        and items[device_id].get("proxy") == "light_v2"
        and supports_command(device_id, "ON")
        and supports_command(device_id, "OFF")
    ]


def supported_relay_ids(
    items: Mapping[int, dict[str, Any]],
    tracked_ids: Iterable[int],
    supports_command: Callable[[int, str], bool],
    has_initial_state: Callable[[int], bool],
) -> list[int]:
    """Select observed relay proxies with known state and parameterless commands."""
    return [
        device_id
        for device_id in sorted(tracked_ids)
        if items[device_id].get("proxy") in SUPPORTED_RELAY_PROXIES
        and supports_command(device_id, "OPEN")
        and supports_command(device_id, "CLOSE")
        and has_initial_state(device_id)
    ]


def supported_binary_sensor_ids(
    items: Mapping[int, dict[str, Any]], tracked_ids: Iterable[int]
) -> list[int]:
    """Expose only the two contact proxies with observed REST and push state."""
    return [
        device_id
        for device_id in sorted(tracked_ids)
        if is_candidate_item(items[device_id])
        and items[device_id].get("proxy") in CONTACT_SENSOR_PROXIES
    ]


def supported_media_room_ids(
    items: Mapping[int, dict[str, Any]], snapshots: Mapping[int, Mapping[str, Any]],
    known_room_ids: Iterable[int] = (),
) -> list[int]:
    """Expose proven rooms, retaining an already registered room while off.

    The bound ``aswitch`` OutputStatus stream is proven for Living/Wiim. A
    room with another volume-device proxy may still have useful REST state,
    but its normal push feedback has not yet been established.
    """
    known = set(known_room_ids)
    result: list[int] = []
    for room_id, state in snapshots.items():
        item = items.get(room_id)
        if not item or item.get("typeName") != "room" or item.get("proxy") != "roomdevice":
            continue
        bound_id = state.get("volume_device_id")
        bound = items.get(bound_id) if type(bound_id) is int and bound_id > 0 else None
        volume = state.get("volume_percent")
        supported_on = (
            bound is not None
            and bound.get("proxy") == "aswitch"
            and isinstance(state.get("is_on"), bool)
            and isinstance(state.get("is_muted"), bool)
            and type(volume) in (int, float)
            and 0 <= volume <= 100
        )
        known_off = (
            room_id in known
            and state.get("is_on") is False
            and type(state.get("selected_source_id")) is int
            and state.get("selected_source_id") == 0
            and type(state.get("volume_device_id")) is int
            and state.get("volume_device_id") == 0
        )
        if supported_on or known_off:
            result.append(room_id)
    return sorted(result)


def registered_media_room_ids(
    entity_entries: Iterable[Any], *, config_entry_id: str, host: str,
    items: Mapping[int, dict[str, Any]],
) -> set[int]:
    """Recognize only this integration's existing media entities for live rooms."""
    room_unique_ids = {
        f"{host}_{room_id}": room_id for room_id, item in items.items()
        if item.get("typeName") == "room" and item.get("proxy") == "roomdevice"
    }
    return {
        room_unique_ids[entry.unique_id]
        for entry in entity_entries
        if entry.config_entry_id == config_entry_id
        and entry.platform == DOMAIN
        and isinstance(entry.entity_id, str)
        and entry.entity_id.startswith("media_player.")
        and isinstance(entry.unique_id, str)
        and entry.unique_id in room_unique_ids
    }


def command_metadata_ids(
    items: Mapping[int, dict[str, Any]], tracked_ids: Iterable[int]
) -> list[int]:
    """Do not fetch command metadata for read-only contact sensors."""
    return [
        device_id
        for device_id in sorted(tracked_ids)
        if items[device_id].get("proxy") not in CONTACT_SENSOR_PROXIES
    ]
