"""Conservative inventory eligibility based on live Core3 metadata."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any


def is_candidate_item(item: dict[str, Any]) -> bool:
    if item.get("typeName") != "device" or not isinstance(item.get("id"), int):
        return False
    capabilities = item.get("capabilities")
    if not isinstance(capabilities, dict):
        return False
    if item.get("proxy") == "light_v2":
        # Phase 1 proved dimmable-light push, not the on/off-only variant.
        return capabilities.get("on_off") is True and capabilities.get("dimmer") is True
    if item.get("proxy") == "thermostatV2":
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
