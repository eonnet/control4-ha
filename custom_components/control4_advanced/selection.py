"""Conservative inventory eligibility based on live Core3 metadata."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any

from .transport.proxies import SUPPORTED_RELAY_PROXIES


def is_candidate_item(item: dict[str, Any]) -> bool:
    if item.get("typeName") != "device" or not isinstance(item.get("id"), int):
        return False
    proxy = item.get("proxy")
    if isinstance(proxy, str) and proxy in SUPPORTED_RELAY_PROXIES:
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
