"""Conservative inventory eligibility based on live Core3 metadata."""

from __future__ import annotations

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
