"""Control4 Advanced Home Assistant integration.

Home Assistant imports are local to setup functions so transport tests remain
runnable without installing Home Assistant.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
import logging
from typing import TYPE_CHECKING

import aiohttp

from .const import (
    CONF_TIDAL_PLAYLISTS,
    CONF_TUNEIN_FAVORITES,
    DEFAULT_RECONCILIATION_SECONDS,
    PLATFORMS,
)
from .registry_cleanup import (
    migrate_tidal_playlist_registry_entries,
    remove_stale_registry_entries,
)
from .selection import (
    command_metadata_ids, is_candidate_item, registered_media_room_ids,
    supported_media_room_ids,
)
from .transport import (
    AccountTokenProvider,
    Control4Transport,
    DeviceCommandClient,
    DirectorRestClient,
    MediaSessionCoordinator,
    RoomTuneInCatalog,
    RoomTidalCatalog,
    RoomVolumeCoordinator,
    normalize_rest_variables,
    parse_tidal_playlists,
    parse_tunein_favorites,
    room_tidal_catalogs,
    room_tunein_catalogs,
)
from .transport.auth import AuthenticationError, AuthenticationTransportError
from .transport.rest import DirectorRestError
from .transport.sources import RoomSource, room_sources

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant


_LOGGER = logging.getLogger(__name__)


@dataclass
class Control4Runtime:
    session: aiohttp.ClientSession
    transport: Control4Transport
    commands: DeviceCommandClient
    media_coordinators: dict[int, RoomVolumeCoordinator]
    media_sources: dict[int, tuple[RoomSource, ...]] = field(default_factory=dict)
    tidal_catalogs: dict[int, tuple[RoomTidalCatalog, ...]] = field(default_factory=dict)
    tunein_catalogs: dict[int, tuple[RoomTuneInCatalog, ...]] = field(default_factory=dict)
    media_session_coordinator: MediaSessionCoordinator | None = None
    media_entity_ids: dict[int, str] = field(default_factory=dict)


async def _async_register_frontend(hass: HomeAssistant) -> None:
    """Register the optional Control4 dashboard card frontend."""
    from .frontend import async_register_frontend

    await async_register_frontend(hass)


async def _async_register_group_presets(hass: HomeAssistant) -> None:
    """Register persistent media-group preset WebSocket commands."""
    from .group_presets import async_register_group_presets

    await async_register_group_presets(hass)


async def _discover_media_room_ids(
    transport: Control4Transport, known_room_ids: set[int] | None = None,
) -> list[int]:
    """Read rooms once; retain registered media rooms while powered off."""
    room_ids = [
        room_id
        for room_id, item in sorted(transport.inventory.items.items())
        if item.get("typeName") == "room" and item.get("proxy") == "roomdevice"
    ]
    limit = asyncio.Semaphore(4)

    async def inspect(room_id: int) -> tuple[int, dict] | None:
        async with limit:
            try:
                variables = await asyncio.wait_for(
                    transport.rest.get_variables(room_id), timeout=5
                )
            except Exception as exc:
                # Optional media discovery must not hold up existing devices.
                _LOGGER.warning("Control4 room state discovery failed: %s", type(exc).__name__)
                return None
            return room_id, normalize_rest_variables(room_id, "roomdevice", variables).changes

    snapshots = dict(
        result for result in await asyncio.gather(*(inspect(room_id) for room_id in room_ids))
        if result is not None
    )
    return supported_media_room_ids(transport.inventory.items, snapshots, known_room_ids or ())


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Authenticate, load an initial snapshot, then forward push-capable entities."""
    from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
    from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady

    await _async_register_frontend(hass)
    await _async_register_group_presets(hass)
    host = entry.data[CONF_HOST]
    provider = AccountTokenProvider(entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD])
    session = aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False))
    transport = None
    media_coordinators: dict[int, RoomVolumeCoordinator] = {}
    media_session_coordinator: MediaSessionCoordinator | None = None
    setup_complete = False
    try:
        rest = DirectorRestClient(host, provider, session)
        items = await rest.get_items()
        tracked_ids = [
            int(item["id"])
            for item in items
            if is_candidate_item(item)
        ]
        if not tracked_ids:
            raise ConfigEntryNotReady("Director has no supported items")
        transport = Control4Transport(
            rest,
            tracked_ids,
            reconciliation_seconds=entry.options.get(
                "reconciliation_seconds", DEFAULT_RECONCILIATION_SECONDS
            ),
        )
        await transport.start(ready_timeout=90)
        commands = DeviceCommandClient(rest)
        await commands.refresh(command_metadata_ids(transport.inventory.items, tracked_ids))
        known_media_rooms: set[int] = set()
        try:
            from homeassistant.helpers import entity_registry as er

            known_media_rooms = registered_media_room_ids(
                er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id),
                config_entry_id=entry.entry_id,
                host=host,
                items=transport.inventory.items,
            )
        except Exception as exc:
            _LOGGER.warning("Control4 media registry lookup failed: %s", type(exc).__name__)
        volume_room_ids = set(
            await _discover_media_room_ids(transport, known_media_rooms)
        )
        all_room_ids = [
            room_id
            for room_id, item in sorted(transport.inventory.items.items())
            if item.get("typeName") == "room" and item.get("proxy") == "roomdevice"
        ]
        media_sources: dict[int, tuple[RoomSource, ...]] = {}
        try:
            ui_configuration = await rest.get_ui_configuration()
        except Exception as exc:
            # Source discovery is optional and raw UI configuration may
            # include account or media labels; never log it.
            _LOGGER.warning("Control4 source discovery failed: %s", type(exc).__name__)
        else:
            media_sources = {
                room_id: sources
                for room_id in all_room_ids
                if (
                    sources := room_sources(
                        ui_configuration, room_id, transport.inventory.items
                    )
                )
            }
        media_room_ids = sorted(volume_room_ids | set(media_sources))
        if media_room_ids:
            try:
                await commands.refresh(media_room_ids)
            except DirectorRestError:
                # Media controls are optional; failed metadata leaves source
                # rooms visible but without unsupported commands.
                pass
        for room_id in media_room_ids:
            coordinator = RoomVolumeCoordinator(transport, room_id)
            try:
                await asyncio.wait_for(coordinator.start(), timeout=10)
            except Exception as exc:
                _LOGGER.warning("Control4 room volume setup failed: %s", type(exc).__name__)
                continue
            coordinator.enable_volume_fallback()
            media_coordinators[room_id] = coordinator
        media_sources = {
            room_id: sources
            for room_id, sources in media_sources.items()
            if room_id in media_coordinators
        }
        for room_id, sources in media_sources.items():
            commands.register_room_sources(room_id, sources)
            if any(
                commands.supports_room_source(room_id, source.experience)
                for source in sources
            ):
                media_coordinators[room_id].enable_source_fallback()
        digital_media_ids = [
            item_id
            for item_id, item in transport.inventory.items.items()
            if item.get("proxy") == "control4_digitalaudio"
            and item.get("filename") == "control4_digitalaudio.c4i"
        ]
        if len(media_coordinators) >= 2 and len(digital_media_ids) == 1:
            candidate = MediaSessionCoordinator(rest, media_coordinators)
            try:
                await asyncio.wait_for(candidate.start(), timeout=10)
            except Exception as exc:
                _LOGGER.warning(
                    "Control4 media-session setup failed: %s", type(exc).__name__
                )
            else:
                media_session_coordinator = candidate
                commands.register_media_grouping(
                    digital_media_ids[0], media_coordinators
                )
        tidal_catalogs: dict[int, tuple[RoomTidalCatalog, ...]] = {}
        raw_playlists = entry.options.get(CONF_TIDAL_PLAYLISTS, "")
        configured_playlists = ()
        if raw_playlists:
            try:
                configured_playlists = parse_tidal_playlists(raw_playlists)
            except ValueError:
                # Invalid stored options must not prevent the rest of Control4
                # from loading. The options form prevents new invalid values.
                _LOGGER.warning("Stored Control4 TIDAL playlist configuration is invalid")
        tidal_catalogs = room_tidal_catalogs(
            media_sources, transport.inventory.items, configured_playlists
        )
        if configured_playlists:
            protocol_ids = {
                catalog.protocol_id
                for catalogs in tidal_catalogs.values()
                for catalog in catalogs
                if catalog.playlists
            }
            if protocol_ids:
                try:
                    await commands.refresh(protocol_ids)
                except DirectorRestError:
                    tidal_catalogs = {
                        room_id: tuple(
                            replace(catalog, playlists=()) for catalog in catalogs
                        )
                        for room_id, catalogs in tidal_catalogs.items()
                    }
                else:
                    tidal_catalogs = {
                        room_id: tuple(
                            catalog
                            if not catalog.playlists
                            or commands.supports_tidal_playlist(catalog.protocol_id)
                            else replace(catalog, playlists=())
                            for catalog in catalogs
                        )
                        for room_id, catalogs in tidal_catalogs.items()
                    }
                    for room_id, catalogs in tidal_catalogs.items():
                        for catalog in catalogs:
                            if catalog.playlists:
                                commands.register_tidal_playlists(
                                    catalog.protocol_id, room_id, catalog.playlists
                                )
        tunein_catalogs: dict[int, tuple[RoomTuneInCatalog, ...]] = {}
        raw_favorites = entry.options.get(CONF_TUNEIN_FAVORITES, "")
        if raw_favorites:
            try:
                configured_favorites = parse_tunein_favorites(raw_favorites)
            except ValueError:
                _LOGGER.warning("Stored Control4 TuneIn favorite configuration is invalid")
            else:
                tunein_catalogs = room_tunein_catalogs(
                    media_sources, transport.inventory.items, configured_favorites
                )
                protocol_ids = {
                    catalog.protocol_id
                    for catalogs in tunein_catalogs.values()
                    for catalog in catalogs
                }
                if protocol_ids:
                    try:
                        await commands.refresh(protocol_ids)
                    except DirectorRestError:
                        tunein_catalogs = {}
                    else:
                        tunein_catalogs = {
                            room_id: tuple(
                                catalog
                                for catalog in catalogs
                                if commands.supports_tunein_favorite(catalog.protocol_id)
                            )
                            for room_id, catalogs in tunein_catalogs.items()
                        }
                        tunein_catalogs = {
                            room_id: catalogs
                            for room_id, catalogs in tunein_catalogs.items()
                            if catalogs
                        }
                        for room_id, catalogs in tunein_catalogs.items():
                            for catalog in catalogs:
                                commands.register_tunein_favorites(
                                    catalog.protocol_id, room_id, catalog.favorites
                                )
        entry.runtime_data = Control4Runtime(
            session=session,
            transport=transport,
            commands=commands,
            media_coordinators=media_coordinators,
            media_sources=media_sources,
            tidal_catalogs=tidal_catalogs,
            tunein_catalogs=tunein_catalogs,
            media_session_coordinator=media_session_coordinator,
        )
        try:
            migrate_tidal_playlist_registry_entries(
                hass,
                config_entry_id=entry.entry_id,
                host=host,
                preferred_protocol_ids={
                    room_id: next(
                        (
                            catalog.protocol_id
                            for catalog in catalogs
                            if catalog.source_name.casefold() == "tidal"
                        ),
                        catalogs[0].protocol_id,
                    )
                    for room_id, catalogs in tidal_catalogs.items()
                },
            )
        except Exception as exc:
            # Registry migration is nonessential; platform setup may continue.
            _LOGGER.warning("Control4 TIDAL registry migration failed: %s", type(exc).__name__)
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        try:
            remove_stale_registry_entries(
                hass,
                config_entry_id=entry.entry_id,
                host=host,
                first_inventory_ids={
                    item["id"] for item in items if isinstance(item.get("id"), int)
                },
                second_inventory_ids=set(transport.inventory.items),
            )
        except Exception as exc:
            # Registry cleanup is nonessential; never break otherwise healthy entities.
            _LOGGER.warning("Control4 stale registry cleanup failed: %s", type(exc).__name__)
        setup_complete = True
        return True
    except AuthenticationTransportError:
        raise ConfigEntryNotReady("Control4 account service is temporarily unavailable") from None
    except AuthenticationError:
        raise ConfigEntryAuthFailed("Control4 account authentication failed") from None
    except (DirectorRestError, aiohttp.ClientError, TimeoutError, OSError) as exc:
        raise ConfigEntryNotReady(f"Control4 connection failed ({type(exc).__name__})") from None
    finally:
        if not setup_complete:
            if media_session_coordinator is not None:
                await media_session_coordinator.stop()
            for coordinator in media_coordinators.values():
                await coordinator.stop()
            if transport is not None:
                await transport.stop()
            await session.close()


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Remove entities, subscriptions, WebSocket, and owned HTTP session."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        runtime: Control4Runtime = entry.runtime_data
        if runtime.media_session_coordinator is not None:
            await runtime.media_session_coordinator.stop()
        for coordinator in runtime.media_coordinators.values():
            await coordinator.stop()
        await runtime.transport.stop()
        await runtime.session.close()
    return unloaded
