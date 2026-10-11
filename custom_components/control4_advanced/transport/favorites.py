"""Validated, source-aware TuneIn favorite configuration."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import re
from typing import Any

from .sources import RoomSource


_TUNEIN_FILENAMES = frozenset({"tunein-premium.c4z", "tunein.c4z"})
_TUNEIN_STATION_ID = re.compile(r"Station/s([1-9][0-9]{0,19})", re.IGNORECASE)


@dataclass(frozen=True)
class TuneInFavorite:
    """One human label and the opaque TuneIn station ID Control4 expects."""

    source_name: str
    name: str
    item_id: str


@dataclass(frozen=True)
class RoomTuneInCatalog:
    """Favorites belonging to one TuneIn source offered in one room."""

    room_id: int
    source_id: int
    protocol_id: int
    source_name: str
    favorites: tuple[TuneInFavorite, ...]


def parse_tunein_favorites(value: str) -> tuple[TuneInFavorite, ...]:
    """Parse ``source | favorite name | Station/id`` lines.

    Blank lines and lines beginning with ``#`` are ignored. Favorite names
    must be unique within one source because browser entries must resolve to a
    single allowlisted TuneIn item.
    """
    if not isinstance(value, str):
        raise ValueError("TuneIn favorite configuration must be text")
    result: list[TuneInFavorite] = []
    seen: set[tuple[str, str]] = set()
    for line_number, raw_line in enumerate(value.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [part.strip() for part in line.split("|")]
        if len(parts) != 3 or not all(parts):
            raise ValueError(
                f"line {line_number} must be: TuneIn source | favorite name | Station/id"
            )
        source_name, name, raw_id = parts
        match = _TUNEIN_STATION_ID.fullmatch(raw_id)
        if match is None:
            raise ValueError(f"line {line_number} has an invalid TuneIn station ID")
        item_id = f"Station/s{match.group(1)}"
        key = (source_name.casefold(), name.casefold())
        if key in seen:
            raise ValueError(
                f"line {line_number} duplicates favorite {name!r} for {source_name!r}"
            )
        seen.add(key)
        result.append(TuneInFavorite(source_name, name, item_id))
    return tuple(result)


def room_tunein_catalogs(
    room_sources_by_id: Mapping[int, Sequence[RoomSource]],
    inventory: Mapping[int, Mapping[str, Any]],
    favorites: Sequence[TuneInFavorite],
) -> dict[int, tuple[RoomTuneInCatalog, ...]]:
    """Join configured favorites to inventory-backed TuneIn room sources."""
    by_source: dict[str, list[TuneInFavorite]] = {}
    for favorite in favorites:
        by_source.setdefault(favorite.source_name.casefold(), []).append(favorite)

    result: dict[int, tuple[RoomTuneInCatalog, ...]] = {}
    for room_id, sources in room_sources_by_id.items():
        catalogs: list[RoomTuneInCatalog] = []
        for source in sources:
            if source.experience != "listen":
                continue
            item = inventory.get(source.device_id)
            if not isinstance(item, Mapping):
                continue
            protocol_id = item.get("protocolId")
            source_name = item.get("name")
            protocol_filename = item.get("protocolFilename")
            if (
                item.get("proxy") != "media_service"
                or protocol_filename not in _TUNEIN_FILENAMES
                or type(protocol_id) is not int
                or protocol_id <= 0
                or not isinstance(source_name, str)
                or not source_name.strip()
            ):
                continue
            configured = by_source.get(source_name.strip().casefold())
            if configured is None:
                protocol = inventory.get(protocol_id)
                protocol_name = (
                    protocol.get("name") if isinstance(protocol, Mapping) else None
                )
                if isinstance(protocol_name, str):
                    configured = by_source.get(protocol_name.strip().casefold())
            if not configured:
                continue
            catalogs.append(
                RoomTuneInCatalog(
                    room_id,
                    source.device_id,
                    protocol_id,
                    source_name.strip(),
                    tuple(configured),
                )
            )
        if catalogs:
            result[room_id] = tuple(catalogs)
    return result
