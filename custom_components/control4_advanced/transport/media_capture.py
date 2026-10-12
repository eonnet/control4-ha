"""Capture user-opened TIDAL playlists and TuneIn favorites from Core3."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import re
from typing import Any, Literal
from uuid import UUID

from .client import Control4Transport
from .events import NormalizedEvent
from .favorites import TuneInFavorite, parse_tunein_favorites
from .playlists import TidalPlaylist, parse_tidal_playlists
from .sources import RoomSource


MediaCaptureService = Literal["tidal", "tunein"]
_SERVICE_FILENAMES: dict[MediaCaptureService, frozenset[str]] = {
    "tidal": frozenset({"tidal.c4z"}),
    "tunein": frozenset({"tunein.c4z", "tunein-premium.c4z"}),
}
_TUNEIN_STATION_ID = re.compile(r"Station/s([1-9][0-9]{0,19})", re.IGNORECASE)
_TIDAL_CREATED_PLAYLISTS_PATH = re.compile(
    r"users/[1-9][0-9]{0,19}/playlists", re.IGNORECASE
)


class MediaCaptureError(Exception):
    """Base error for one guided media capture."""


class MediaCaptureDisconnected(MediaCaptureError):
    """The live Director event stream is unavailable."""


class MediaCaptureTimeout(MediaCaptureError):
    """No matching browser list arrived before the workflow expired."""


@dataclass(frozen=True)
class MediaCaptureTarget:
    """One inventory-backed media service that can emit browser responses."""

    source_id: int
    source_name: str
    service: MediaCaptureService


@dataclass(frozen=True)
class CapturedMediaItem:
    """One validated item captured from a media-service browser response."""

    source_id: int
    source_name: str
    name: str
    item_id: str


def _clean_label(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    label = value.strip()
    if (
        not label
        or len(label) > 256
        or "|" in label
        or any(ord(character) < 32 for character in label)
    ):
        return None
    return label


def media_capture_targets(
    room_sources_by_id: Mapping[int, Sequence[RoomSource]],
    inventory: Mapping[int, Mapping[str, Any]],
    service: MediaCaptureService,
) -> tuple[MediaCaptureTarget, ...]:
    """Return each visible media-service source once, independent of room."""
    filenames = _SERVICE_FILENAMES[service]
    result: list[MediaCaptureTarget] = []
    seen: set[int] = set()
    for sources in room_sources_by_id.values():
        for source in sources:
            if source.experience != "listen" or source.device_id in seen:
                continue
            item = inventory.get(source.device_id)
            if not isinstance(item, Mapping) or item.get("proxy") != "media_service":
                continue
            filename = item.get("protocolFilename")
            if filename not in filenames:
                continue
            source_name = _clean_label(item.get("name"))
            if source_name is None:
                source_name = _clean_label(source.name)
            if source_name is None:
                continue
            seen.add(source.device_id)
            result.append(
                MediaCaptureTarget(source.device_id, source_name, service)
            )
    return tuple(result)


def _response_items(raw: Any) -> tuple[Mapping[str, Any], ...]:
    """Read only the observed OnDataToUI RESPONSE/DATA/List/item shape."""
    if not isinstance(raw, Mapping):
        return ()
    payload = raw.get("data")
    if not isinstance(payload, Mapping):
        payload = raw.get("payload")
    if not isinstance(payload, Mapping):
        payload = raw
    response = payload.get("RESPONSE")
    if not isinstance(response, Mapping):
        return ()
    data = response.get("DATA")
    if not isinstance(data, Mapping):
        return ()
    media_list = data.get("List")
    if not isinstance(media_list, Mapping):
        return ()
    items = media_list.get("item")
    if isinstance(items, Mapping):
        items = [items]
    if not isinstance(items, list):
        return ()
    return tuple(item for item in items if isinstance(item, Mapping))


def captured_media_items(
    raw: Any, target: MediaCaptureTarget
) -> tuple[CapturedMediaItem, ...]:
    """Validate one live browser response for the requested service."""
    result: list[CapturedMediaItem] = []
    seen_ids: set[str] = set()
    seen_names: set[str] = set()
    for item in _response_items(raw):
        name = _clean_label(item.get("title") or item.get("name") or item.get("label"))
        raw_id = item.get("id")
        if name is None or not isinstance(raw_id, str):
            continue
        if target.service == "tidal":
            if item.get("itemType") != "playlist":
                continue
            try:
                item_id = str(UUID(raw_id))
            except (ValueError, AttributeError):
                continue
        else:
            match = _TUNEIN_STATION_ID.fullmatch(raw_id.strip())
            if match is None:
                continue
            item_id = f"Station/s{match.group(1)}"
        folded_name = name.casefold()
        if item_id.casefold() in seen_ids or folded_name in seen_names:
            continue
        seen_ids.add(item_id.casefold())
        seen_names.add(folded_name)
        result.append(
            CapturedMediaItem(
                target.source_id, target.source_name, name, item_id
            )
        )
    return tuple(result)


def _is_tidal_created_playlists_menu(raw: Any) -> bool:
    """Recognize the observed language-independent link to owned playlists."""
    return any(
        item.get("itemType") == "link"
        and isinstance(item.get("id"), str)
        and _TIDAL_CREATED_PLAYLISTS_PATH.fullmatch(item["id"].strip())
        for item in _response_items(raw)
    )


async def capture_media_items(
    transport: Control4Transport,
    targets: Sequence[MediaCaptureTarget],
    *,
    timeout: float = 180.0,
    settle_seconds: float = 6.0,
) -> tuple[CapturedMediaItem, ...]:
    """Listen until every source has a stable matching list or time expires.

    A later list replaces an earlier list from the same source. This lets the
    user navigate through service landing pages before reaching the instructed
    playlist/favorites screen without retaining intermediate recommendations.
    """
    if not targets:
        raise ValueError("at least one media capture target is required")
    if timeout <= 0 or settle_seconds <= 0:
        raise ValueError("capture timing must be positive")
    if not transport.connected:
        raise MediaCaptureDisconnected

    targets_by_id = {target.source_id: target for target in targets}
    captured_by_id: dict[int, tuple[CapturedMediaItem, ...]] = {}
    tidal_sources_armed: set[int] = set()
    updated = asyncio.Event()
    temporarily_tracked: list[int] = []

    async def observe(event: NormalizedEvent) -> None:
        if event.source != "websocket" or event.event_type != "OnDataToUI":
            return
        target = targets_by_id.get(event.device_id)
        if target is None:
            return
        if target.service == "tidal":
            if _is_tidal_created_playlists_menu(event.raw):
                tidal_sources_armed.add(event.device_id)
                return
            if event.device_id not in tidal_sources_armed:
                return
        items = captured_media_items(event.raw, target)
        if not items:
            return
        captured_by_id[event.device_id] = items
        updated.set()

    unsubscribe = transport.events.subscribe(observe)
    try:
        for target in targets:
            if target.source_id in transport.tracked_ids:
                continue
            await transport.track_device(target.source_id, sync=False)
            temporarily_tracked.append(target.source_id)

        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            now = loop.time()
            if now >= deadline:
                break
            all_captured = targets_by_id.keys() <= captured_by_id.keys()
            wait_seconds = (
                min(settle_seconds, deadline - now)
                if all_captured
                else deadline - now
            )
            updated.clear()
            try:
                await asyncio.wait_for(updated.wait(), timeout=wait_seconds)
            except asyncio.TimeoutError:
                if all_captured:
                    break
                continue

        if not captured_by_id:
            raise MediaCaptureTimeout
        return tuple(
            item
            for target in targets
            for item in captured_by_id.get(target.source_id, ())
        )
    finally:
        unsubscribe()
        for source_id in reversed(temporarily_tracked):
            await transport.untrack_device(source_id)


def merge_tidal_playlists(
    existing: str, captured: Sequence[CapturedMediaItem]
) -> str:
    """Merge captured playlists while preserving uncaptured manual entries."""
    values = list(parse_tidal_playlists(existing))
    for item in captured:
        values = [
            value
            for value in values
            if not (
                value.source_name.casefold() == item.source_name.casefold()
                and (
                    value.name.casefold() == item.name.casefold()
                    or value.playlist_id.casefold() == item.item_id.casefold()
                )
            )
        ]
        values.append(TidalPlaylist(item.source_name, item.name, item.item_id))
    return "\n".join(
        f"{value.source_name} | {value.name} | {value.playlist_id}"
        for value in values
    )


def merge_tunein_favorites(
    existing: str, captured: Sequence[CapturedMediaItem]
) -> str:
    """Merge captured stations while preserving uncaptured manual entries."""
    values = list(parse_tunein_favorites(existing))
    for item in captured:
        values = [
            value
            for value in values
            if not (
                value.source_name.casefold() == item.source_name.casefold()
                and (
                    value.name.casefold() == item.name.casefold()
                    or value.item_id.casefold() == item.item_id.casefold()
                )
            )
        ]
        values.append(TuneInFavorite(item.source_name, item.name, item.item_id))
    return "\n".join(
        f"{value.source_name} | {value.name} | {value.item_id}"
        for value in values
    )
