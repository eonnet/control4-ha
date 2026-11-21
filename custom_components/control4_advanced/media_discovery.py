"""Read-only room discovery shared by the setup form and integration startup."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping
import logging
from typing import Any

from .selection import media_room_ids, supported_media_room_ids
from .transport.events import normalize_rest_variables
from .transport.rest import DirectorRestClient
from .transport.sources import RoomSource, room_sources


_LOGGER = logging.getLogger(__name__)
_ROOM_STATE_TIMEOUT = 5
_MEDIA_SOURCES_TIMEOUT = 15


async def discover_volume_room_ids(
    rest: DirectorRestClient,
    items: Mapping[int, dict[str, Any]],
    known_room_ids: Iterable[int] = (),
) -> list[int]:
    """Find rooms with compatible volume feedback, retaining known off rooms."""
    limit = asyncio.Semaphore(4)

    async def inspect(room_id: int) -> tuple[int, dict[str, Any]] | None:
        async with limit:
            try:
                variables = await asyncio.wait_for(
                    rest.get_variables(room_id), timeout=_ROOM_STATE_TIMEOUT
                )
            except Exception as exc:
                _LOGGER.warning("Control4 room state discovery failed: %s", type(exc).__name__)
                return None
            return room_id, normalize_rest_variables(room_id, "roomdevice", variables).changes

    snapshots = dict(
        result
        for result in await asyncio.gather(*(inspect(room_id) for room_id in media_room_ids(items)))
        if result is not None
    )
    return supported_media_room_ids(items, snapshots, known_room_ids)


async def discover_media_sources(
    rest: DirectorRestClient, items: Mapping[int, dict[str, Any]],
) -> dict[int, tuple[RoomSource, ...]]:
    """Find inventory-backed Watch/Listen sources even when rooms are off."""
    room_ids = media_room_ids(items)
    if not room_ids:
        return {}
    try:
        configuration = await asyncio.wait_for(
            rest.get_ui_configuration(), timeout=_MEDIA_SOURCES_TIMEOUT
        )
    except Exception as exc:
        _LOGGER.warning("Control4 source discovery failed: %s", type(exc).__name__)
        return {}
    return {
        room_id: sources
        for room_id in room_ids
        if (sources := room_sources(configuration, room_id, items))
    }
