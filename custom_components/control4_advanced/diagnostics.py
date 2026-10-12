"""Privacy-preserving diagnostics for Control4 config entries and devices."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceEntry

from . import Control4Runtime
from .const import (
    CONF_RECONCILIATION_SECONDS,
    CONF_TIDAL_PLAYLISTS,
    CONF_TUNEIN_FAVORITES,
    DEFAULT_RECONCILIATION_SECONDS,
    DOMAIN,
)
from .transport.favorites import parse_tunein_favorites
from .transport.playlists import parse_tidal_playlists


_REDACT = {
    CONF_HOST,
    CONF_USERNAME,
    CONF_PASSWORD,
    CONF_TIDAL_PLAYLISTS,
    CONF_TUNEIN_FAVORITES,
    "access_token",
    "api_key",
    "refresh_token",
    "token",
}
_SAFE_STATE_VALUES = {
    "brightness_percent",
    "cool_setpoint_c",
    "current_temperature_c",
    "heat_setpoint_c",
    "is_connected",
    "is_muted",
    "is_on",
    "output_is_muted",
    "output_volume_percent",
    "target_temperature_c",
    "volume_percent",
}


def _sorted_counter(values: Any) -> dict[str, int]:
    """Return a deterministic JSON-safe counter."""
    return dict(sorted(Counter(str(value) for value in values).items()))


def _configured_count(
    value: Any, parser: Callable[[str], tuple[Any, ...]]
) -> dict[str, int | bool]:
    """Describe configured media without exposing names or opaque IDs."""
    if not isinstance(value, str) or not value.strip():
        return {"valid": True, "count": 0}
    try:
        count = len(parser(value))
    except ValueError:
        return {"valid": False, "count": 0}
    return {"valid": True, "count": count}


def _state_diagnostics(state: Mapping[str, Any]) -> dict[str, Any]:
    """Expose state shape and allowlisted scalar values, never media labels."""
    values = {
        key: value
        for key, value in state.items()
        if key in _SAFE_STATE_VALUES
        and (
            value is None
            or isinstance(value, bool)
            or type(value) in (int, float)
        )
    }
    return {
        "fields": sorted(str(key) for key in state),
        "safe_values": dict(sorted(values.items())),
    }


def _entry_state(entry: ConfigEntry) -> str:
    value = getattr(entry, "state", "unknown")
    return str(getattr(value, "value", value))


def _entity_summary(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    entries = er.async_entries_for_config_entry(
        er.async_get(hass), entry.entry_id
    )
    domains: list[str] = []
    disabled_domains: list[str] = []
    unavailable_domains: list[str] = []
    for entity in entries:
        entity_id = getattr(entity, "entity_id", "")
        domain = entity_id.partition(".")[0] or "unknown"
        domains.append(domain)
        if getattr(entity, "disabled_by", None) is not None:
            disabled_domains.append(domain)
        state = hass.states.get(entity_id)
        if state is None or getattr(state, "state", None) in {
            "unknown",
            "unavailable",
        }:
            unavailable_domains.append(domain)
    return {
        "count": len(entries),
        "by_domain": _sorted_counter(domains),
        "disabled_by_domain": _sorted_counter(disabled_domains),
        "unavailable_by_domain": _sorted_counter(unavailable_domains),
    }


def _media_summary(runtime: Control4Runtime, hass: HomeAssistant) -> dict[str, Any]:
    sources = [
        source
        for room_sources in runtime.media_sources.values()
        for source in room_sources
    ]
    tidal_catalogs = [
        catalog
        for catalogs in runtime.tidal_catalogs.values()
        for catalog in catalogs
    ]
    tunein_catalogs = [
        catalog
        for catalogs in runtime.tunein_catalogs.values()
        for catalog in catalogs
    ]
    session_coordinator = runtime.media_session_coordinator
    sessions = (
        tuple(session_coordinator.sessions)
        if session_coordinator is not None
        else ()
    )
    preset_store = hass.data.get(f"{DOMAIN}_group_preset_store")
    presets = getattr(preset_store, "presets", ())
    if not isinstance(presets, list):
        presets = []
    commands = runtime.commands
    room_ids = tuple(runtime.media_coordinators)
    return {
        "room_count": len(room_ids),
        "registered_player_count": len(runtime.media_entity_ids),
        "source_associations": _sorted_counter(
            source.experience for source in sources
        ),
        "unique_source_count": len({source.device_id for source in sources}),
        "capable_rooms": {
            "volume": sum(
                commands.supports_room_volume(room_id) for room_id in room_ids
            ),
            "mute": sum(
                commands.supports_room_mute(room_id) for room_id in room_ids
            ),
            "power_off": sum(
                commands.supports_room_off(room_id) for room_id in room_ids
            ),
            "watch_source": sum(
                commands.supports_room_source(room_id, "watch")
                for room_id in room_ids
            ),
            "listen_source": sum(
                commands.supports_room_source(room_id, "listen")
                for room_id in room_ids
            ),
        },
        "grouping": {
            "available": session_coordinator is not None,
            "ready": bool(
                session_coordinator is not None
                and session_coordinator.ready
            ),
            "session_count": len(sessions),
            "group_sizes": _sorted_counter(
                len(session.room_ids) for session in sessions
            ),
        },
        "tidal": {
            "account_count": len(
                {(catalog.source_id, catalog.protocol_id) for catalog in tidal_catalogs}
            ),
            "playlist_count": len({
                playlist.playlist_id
                for catalog in tidal_catalogs
                for playlist in catalog.playlists
            }),
        },
        "tunein": {
            "source_count": len(
                {(catalog.source_id, catalog.protocol_id) for catalog in tunein_catalogs}
            ),
            "favorite_count": len({
                favorite.item_id
                for catalog in tunein_catalogs
                for favorite in catalog.favorites
            }),
        },
        "group_presets": {
            "count": len(presets),
            "with_media": sum(bool(preset.get("media")) for preset in presets),
            "group_sizes": _sorted_counter(
                len(preset.get("members", ())) for preset in presets
            ),
        },
    }


def _runtime_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry, runtime: Control4Runtime
) -> dict[str, Any]:
    transport = runtime.transport
    tracked_ids = tuple(sorted(transport.tracked_ids))
    inventory = transport.inventory.items
    metadata = getattr(runtime.commands, "_metadata", {})
    return {
        "transport": {
            "connected": transport.connected,
            "reconciliation_seconds": transport.reconciliation_seconds,
            "inventory_count": len(inventory),
            "tracked_count": len(tracked_ids),
            "synchronized_count": sum(
                bool(transport.state.snapshot(device_id))
                for device_id in tracked_ids
            ),
            "state_revision": transport.state.revision,
            "inventory_proxies": _sorted_counter(
                item.get("proxy") or "unknown" for item in inventory.values()
            ),
            "tracked_proxies": _sorted_counter(
                transport.inventory.proxy(device_id) or "unknown"
                for device_id in tracked_ids
            ),
        },
        "commands": {
            "metadata_device_count": len(metadata),
            "advertised_command_count": sum(
                len(commands)
                for commands in metadata.values()
                if isinstance(commands, Mapping)
            ),
        },
        "entities": _entity_summary(hass, entry),
        "media": _media_summary(runtime, hass),
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return useful aggregate diagnostics without private project data."""
    diagnostics: dict[str, Any] = {
        "config_entry": {
            "state": _entry_state(entry),
            "version": getattr(entry, "version", None),
            "data": async_redact_data(dict(entry.data), _REDACT),
        },
        "options": {
            "reconciliation_seconds": entry.options.get(
                CONF_RECONCILIATION_SECONDS,
                DEFAULT_RECONCILIATION_SECONDS,
            ),
            "tidal_playlists": _configured_count(
                entry.options.get(CONF_TIDAL_PLAYLISTS),
                parse_tidal_playlists,
            ),
            "tunein_favorites": _configured_count(
                entry.options.get(CONF_TUNEIN_FAVORITES),
                parse_tunein_favorites,
            ),
        },
        "runtime_loaded": entry.runtime_data is not None,
    }
    if entry.runtime_data is not None:
        diagnostics.update(
            _runtime_diagnostics(hass, entry, entry.runtime_data)
        )
    return diagnostics


def _control4_device_id(
    runtime: Control4Runtime, device: DeviceEntry
) -> int | None:
    prefix = f"{runtime.transport.rest.host}_"
    for domain, identifier in device.identifiers:
        if domain != DOMAIN or not identifier.startswith(prefix):
            continue
        raw_device_id = identifier.removeprefix(prefix)
        if raw_device_id.isdecimal():
            device_id = int(raw_device_id)
            if device_id in runtime.transport.inventory.items:
                return device_id
    return None


async def async_get_device_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry, device: DeviceEntry
) -> dict[str, Any]:
    """Return one device diagnostic without its name, area, host, or raw media."""
    del hass
    runtime: Control4Runtime | None = entry.runtime_data
    if runtime is None:
        return {"runtime_loaded": False, "device_available": False}
    device_id = _control4_device_id(runtime, device)
    if device_id is None:
        return {"runtime_loaded": True, "device_available": False}

    transport = runtime.transport
    item = transport.inventory.items[device_id]
    metadata = getattr(runtime.commands, "_metadata", {})
    sources = runtime.media_sources.get(device_id, ())
    tidal_catalogs = runtime.tidal_catalogs.get(device_id, ())
    tunein_catalogs = runtime.tunein_catalogs.get(device_id, ())
    capabilities = item.get("capabilities")
    device_commands = metadata.get(device_id, {})
    return {
        "runtime_loaded": True,
        "device_available": True,
        "transport_connected": transport.connected,
        "inventory": {
            "type": item.get("typeName"),
            "proxy": item.get("proxy"),
            "manufacturer": item.get("manufacturer"),
            "model": item.get("model") or item.get("protocolName"),
            "capability_fields": sorted(capabilities)
            if isinstance(capabilities, Mapping)
            else [],
        },
        "tracked": device_id in transport.tracked_ids,
        "state": _state_diagnostics(transport.state.snapshot(device_id)),
        "advertised_commands": sorted(device_commands)
        if isinstance(device_commands, Mapping)
        else [],
        "media": {
            "source_counts": _sorted_counter(
                source.experience for source in sources
            ),
            "tidal_account_count": len(tidal_catalogs),
            "tidal_playlist_count": len({
                playlist.playlist_id
                for catalog in tidal_catalogs
                for playlist in catalog.playlists
            }),
            "tunein_source_count": len(tunein_catalogs),
            "tunein_favorite_count": len({
                favorite.item_id
                for catalog in tunein_catalogs
                for favorite in catalog.favorites
            }),
        },
    }
