"""Control4 Advanced Home Assistant integration.

Home Assistant imports are local to setup functions so transport tests remain
runnable without installing Home Assistant.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
from typing import TYPE_CHECKING

import aiohttp

from .const import DEFAULT_RECONCILIATION_SECONDS, PLATFORMS
from .registry_cleanup import remove_stale_registry_entries
from .selection import command_metadata_ids, is_candidate_item, supported_media_room_ids
from .transport import (
    AccountTokenProvider,
    Control4Transport,
    DeviceCommandClient,
    DirectorRestClient,
    RoomVolumeCoordinator,
    normalize_rest_variables,
)
from .transport.auth import AuthenticationError, AuthenticationTransportError
from .transport.rest import DirectorRestError

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


async def _discover_media_room_ids(transport: Control4Transport) -> list[int]:
    """Read candidate rooms once; expose only the observed volume profile."""
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
    return supported_media_room_ids(transport.inventory.items, snapshots)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Authenticate, load an initial snapshot, then forward push-capable entities."""
    from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
    from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady

    host = entry.data[CONF_HOST]
    provider = AccountTokenProvider(entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD])
    session = aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False))
    transport = None
    media_coordinators: dict[int, RoomVolumeCoordinator] = {}
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
        for room_id in await _discover_media_room_ids(transport):
            coordinator = RoomVolumeCoordinator(transport, room_id)
            try:
                await asyncio.wait_for(coordinator.start(), timeout=10)
            except Exception as exc:
                _LOGGER.warning("Control4 room volume setup failed: %s", type(exc).__name__)
                continue
            media_coordinators[room_id] = coordinator
        if media_coordinators:
            try:
                await commands.refresh(media_coordinators)
            except DirectorRestError:
                # Media controls are optional; a failed metadata read leaves
                # the room entity read-only without breaking other platforms.
                pass
        entry.runtime_data = Control4Runtime(session, transport, commands, media_coordinators)
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
        for coordinator in runtime.media_coordinators.values():
            await coordinator.stop()
        await runtime.transport.stop()
        await runtime.session.close()
    return unloaded
