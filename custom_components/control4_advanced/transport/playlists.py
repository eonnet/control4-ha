"""Validated, account-aware TIDAL playlist configuration."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from .sources import RoomSource


@dataclass(frozen=True)
class TidalPlaylist:
    """One human label and the opaque TIDAL playlist ID Control4 expects."""

    source_name: str
    name: str
    playlist_id: str


@dataclass(frozen=True)
class RoomTidalCatalog:
    """Playlists belonging to one TIDAL source offered in one room."""

    room_id: int
    source_id: int
    protocol_id: int
    source_name: str
    playlists: tuple[TidalPlaylist, ...]


def parse_tidal_playlists(value: str) -> tuple[TidalPlaylist, ...]:
    """Parse ``source | playlist name | UUID`` lines.

    Blank lines and lines beginning with ``#`` are ignored. Duplicate playlist
    names within an account are rejected because Home Assistant select options
    must be unambiguous.
    """
    if not isinstance(value, str):
        raise ValueError("TIDAL playlist configuration must be text")
    result: list[TidalPlaylist] = []
    seen: set[tuple[str, str]] = set()
    for line_number, raw_line in enumerate(value.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [part.strip() for part in line.split("|")]
        if len(parts) != 3 or not all(parts):
            raise ValueError(
                f"line {line_number} must be: TIDAL source | playlist name | UUID"
            )
        source_name, name, raw_id = parts
        try:
            playlist_id = str(UUID(raw_id))
        except (ValueError, AttributeError):
            raise ValueError(f"line {line_number} has an invalid playlist UUID") from None
        key = (source_name.casefold(), name.casefold())
        if key in seen:
            raise ValueError(
                f"line {line_number} duplicates playlist {name!r} for {source_name!r}"
            )
        seen.add(key)
        result.append(TidalPlaylist(source_name, name, playlist_id))
    return tuple(result)


def room_tidal_catalogs(
    room_sources_by_id: Mapping[int, Sequence[RoomSource]],
    inventory: Mapping[int, Mapping[str, Any]],
    playlists: Sequence[TidalPlaylist],
) -> dict[int, tuple[RoomTidalCatalog, ...]]:
    """Discover TIDAL accounts and join any configured playlists to them."""
    by_source: dict[str, list[TidalPlaylist]] = {}
    for playlist in playlists:
        by_source.setdefault(playlist.source_name.casefold(), []).append(playlist)

    result: dict[int, tuple[RoomTidalCatalog, ...]] = {}
    for room_id, sources in room_sources_by_id.items():
        catalogs: list[RoomTidalCatalog] = []
        for source in sources:
            if source.experience != "listen":
                continue
            item = inventory.get(source.device_id)
            if not isinstance(item, Mapping):
                continue
            protocol_id = item.get("protocolId")
            source_name = item.get("name")
            if (
                item.get("proxy") != "media_service"
                or item.get("protocolFilename") != "tidal.c4z"
                or type(protocol_id) is not int
                or protocol_id <= 0
                or not isinstance(source_name, str)
                or not source_name.strip()
            ):
                continue
            account_playlists = by_source.get(source_name.strip().casefold(), [])
            catalogs.append(
                RoomTidalCatalog(
                    room_id,
                    source.device_id,
                    protocol_id,
                    source_name.strip(),
                    tuple(account_playlists),
                )
            )
        if catalogs:
            result[room_id] = tuple(catalogs)
    return result
