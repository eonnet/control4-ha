"""Credential- and location-redacted config entry diagnostics."""

from __future__ import annotations

from collections import Counter
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from . import Control4Runtime


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return only aggregate transport health, never raw events or tokens."""
    runtime: Control4Runtime = entry.runtime_data
    transport = runtime.transport
    return {
        "config": {
            "host": "<redacted>",
            "username": "<redacted>",
            "password": "<redacted>",
            "reconciliation_seconds": entry.options.get("reconciliation_seconds", 900),
        },
        "transport": {
            "connected": transport.connected,
            "tracked_count": len(transport.tracked_ids),
            "proxies": dict(Counter(
                transport.inventory.proxy(device_id) or "unknown"
                for device_id in transport.tracked_ids
            )),
        },
    }
