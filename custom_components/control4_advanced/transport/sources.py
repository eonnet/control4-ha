"""Conservative room-source discovery from Director's UI configuration.

The UI configuration lists sources actually offered by Watch and Listen.
Inventory supplies stable device names and prevents synthetic UI entries from
becoming command targets. No media metadata or account names are retained.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal


Experience = Literal["watch", "listen"]

# The Core3 exposes some Watch entries as uibuttons. They are visible in its
# UI but not yet proven to be valid SELECT_VIDEO_DEVICE targets.
_SOURCE_PROXIES = frozenset({
    "media_player", "media_service", "aswitch", "control4_digitalaudio",
})
_SOURCE_TYPES = frozenset({
    "RF_MINI_APP", "HDMI", "VIDEO_SELECTION", "DIGITAL_AUDIO_SERVER",
    "DIGITAL_AUDIO_CLIENT", "AUDIO_SELECTION",
})


@dataclass(frozen=True)
class RoomSource:
    device_id: int
    experience: Experience
    name: str
    source_type: str
    label: str


def room_sources(
    configuration: Any,
    room_id: int,
    inventory: Mapping[int, Mapping[str, Any]],
) -> tuple[RoomSource, ...]:
    """Select only inventory-backed Watch/Listen entries for one room.

    Malformed or unknown entries are ignored. A malformed top-level payload
    yields no controls; it must never broaden the command target set.
    """
    if type(room_id) is not int or room_id <= 0 or not isinstance(configuration, Mapping):
        return ()
    experiences = configuration.get("experiences")
    if not isinstance(experiences, list):
        return ()
    found: list[tuple[int, Experience, str, str]] = []
    seen: set[tuple[str, int]] = set()
    for experience in experiences:
        if not isinstance(experience, Mapping) or experience.get("room_id") != room_id:
            continue
        kind = experience.get("type")
        if kind not in ("watch", "listen"):
            continue
        sources = experience.get("sources")
        entries = sources.get("source") if isinstance(sources, Mapping) else None
        if isinstance(entries, Mapping):
            entries = [entries]
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, Mapping):
                continue
            device_id = entry.get("id")
            source_type = entry.get("type")
            if (
                type(device_id) is not int
                or not 0 < device_id <= 2147483647
                or source_type not in _SOURCE_TYPES
            ):
                continue
            item = inventory.get(device_id)
            if not isinstance(item, Mapping) or item.get("typeName") != "device":
                continue
            if item.get("proxy") not in _SOURCE_PROXIES:
                continue
            name = item.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            key = (kind, device_id)
            if key in seen:
                continue
            seen.add(key)
            found.append((device_id, kind, name.strip(), source_type))

    counts = Counter(f"{kind.title()}: {name}" for _, kind, name, _ in found)
    return tuple(
        RoomSource(device_id, kind, name, source_type,
                   f"{kind.title()}: {name}" + (f" ({device_id})" if counts[f"{kind.title()}: {name}"] > 1 else ""))
        for device_id, kind, name, source_type in found
    )
