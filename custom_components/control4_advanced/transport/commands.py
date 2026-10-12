"""REST command validation against live Director metadata.

This module never sends a command unless one of its explicit methods is called.
The REST body shape follows the existing MCP client. A user has reported live
light and thermostat control through this integration.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
import logging
import math
from typing import Any

from .favorites import TuneInFavorite
from .playlists import TidalPlaylist
from .rest import DirectorRestClient, DirectorRestError
from .sources import RoomSource


_LOGGER = logging.getLogger(__name__)


class UnsupportedCommand(ValueError):
    """Director metadata does not support the requested command or value."""


class DeviceCommandClient:
    def __init__(self, rest: DirectorRestClient) -> None:
        self.rest = rest
        self._metadata: dict[int, dict[str, dict[str, Any]]] = {}
        self._room_sources: dict[int, frozenset[RoomSource]] = {}
        self._tidal_playlists: dict[int, dict[int, frozenset[str]]] = {}
        self._tunein_favorites: dict[int, dict[int, frozenset[str]]] = {}
        self._digital_media_id: int | None = None
        self._grouping_room_ids: frozenset[int] = frozenset()

    def register_media_grouping(
        self, digital_media_id: int, room_ids: Iterable[int]
    ) -> None:
        """Register the standard Digital Media grouping target and visible rooms."""
        rooms = frozenset(room_ids)
        if type(digital_media_id) is not int or digital_media_id <= 0:
            raise ValueError("Digital Media ID must be a positive integer")
        if len(rooms) < 2 or not all(
            type(room_id) is int and room_id > 0 for room_id in rooms
        ):
            raise ValueError("media grouping requires at least two positive room IDs")
        self._digital_media_id = digital_media_id
        self._grouping_room_ids = rooms

    def register_room_sources(self, room_id: int, sources: Iterable[RoomSource]) -> None:
        """Register sources returned by the inventory-validated UI parser."""
        if type(room_id) is not int or room_id <= 0:
            raise ValueError("room ID must be a positive integer")
        values = tuple(sources)
        if not all(isinstance(source, RoomSource) for source in values):
            raise ValueError("room sources must be parsed RoomSource records")
        self._room_sources[room_id] = frozenset(values)

    def register_tidal_playlists(
        self,
        protocol_id: int,
        room_id: int,
        playlists: Iterable[TidalPlaylist],
    ) -> None:
        """Allow only playlist IDs joined to a visible TIDAL source and room."""
        if type(protocol_id) is not int or protocol_id <= 0:
            raise ValueError("TIDAL protocol ID must be a positive integer")
        if type(room_id) is not int or room_id <= 0:
            raise ValueError("room ID must be a positive integer")
        values = tuple(playlists)
        if not values or not all(isinstance(item, TidalPlaylist) for item in values):
            raise ValueError("TIDAL playlists must be parsed playlist records")
        self._tidal_playlists.setdefault(protocol_id, {})[room_id] = frozenset(
            item.playlist_id for item in values
        )

    def register_tunein_favorites(
        self,
        protocol_id: int,
        room_id: int,
        favorites: Iterable[TuneInFavorite],
    ) -> None:
        """Allow only station IDs joined to a visible TuneIn source and room."""
        if type(protocol_id) is not int or protocol_id <= 0:
            raise ValueError("TuneIn protocol ID must be a positive integer")
        if type(room_id) is not int or room_id <= 0:
            raise ValueError("room ID must be a positive integer")
        values = tuple(favorites)
        if not values or not all(isinstance(item, TuneInFavorite) for item in values):
            raise ValueError("TuneIn favorites must be parsed favorite records")
        self._tunein_favorites.setdefault(protocol_id, {})[room_id] = frozenset(
            item.item_id for item in values
        )

    async def refresh(self, device_ids: Iterable[int]) -> None:
        limit = asyncio.Semaphore(6)

        async def load_one(device_id: int) -> bool:
            async with limit:
                try:
                    records = await self.rest.get_commands(device_id)
                except DirectorRestError as exc:
                    # A single unsupported relay must not hide healthy lights/climates.
                    # Do not log the HTTP exception text; it may contain auth data.
                    self._metadata.pop(device_id, None)
                    _LOGGER.warning(
                        "Control4 command metadata unavailable for item %s: %s",
                        device_id, type(exc).__name__,
                    )
                    return False
                self._metadata[device_id] = {
                    record["command"]: record
                    for record in records
                    if record.get("deviceId") == device_id
                    and isinstance(record.get("command"), str)
                }
                return True

        results = await asyncio.gather(*(load_one(device_id) for device_id in sorted(set(device_ids))))
        if results and not any(results):
            raise DirectorRestError("no Control4 command metadata could be read")

    def supports(self, device_id: int, command: str) -> bool:
        return command in self._metadata.get(device_id, {})

    def supports_parameterless(self, device_id: int, command: str) -> bool:
        record = self._metadata.get(device_id, {}).get(command)
        return record is not None and record.get("params") in (None, [], {})

    def choices(self, device_id: int, command: str, parameter: str) -> list[str]:
        spec = self._parameter(device_id, command, parameter)
        values = spec.get("values")
        if not isinstance(values, list):
            return []
        return [str(option["value"]) for option in values if isinstance(option, dict) and "value" in option]

    def range(self, device_id: int, command: str, parameter: str) -> tuple[float, float] | None:
        spec = self._parameter(device_id, command, parameter)
        low, high = spec.get("low"), spec.get("high")
        if isinstance(low, (int, float)) and isinstance(high, (int, float)):
            return float(low), float(high)
        return None

    def supports_room_volume(self, room_id: int) -> bool:
        """Require the exact integer 0–100 room-volume contract seen on Director."""
        record = self._metadata.get(room_id, {}).get("SET_VOLUME_LEVEL")
        if record is None or not isinstance(record.get("params"), list):
            return False
        specs = record["params"]
        if len(specs) != 1 or not isinstance(specs[0], dict):
            return False
        spec = specs[0]
        resolution = spec.get("resolution")
        return (
            spec.get("name") == "LEVEL"
            and spec.get("valueType") == "INTEGER"
            and spec.get("low") == 0
            and spec.get("high") == 100
            and (resolution is None or resolution == 1)
        )

    def supports_room_mute(self, room_id: int) -> bool:
        return self.supports_parameterless(room_id, "MUTE_ON") and self.supports_parameterless(
            room_id, "MUTE_OFF"
        )

    def supports_room_off(self, room_id: int) -> bool:
        """Require Director's parameterless room-off command."""
        return self.supports_parameterless(room_id, "ROOM_OFF")

    def supports_room_transport(self, room_id: int, command: str) -> bool:
        """Expose only the observed parameterless room playback commands."""
        return command in {
            "PLAY", "PAUSE", "STOP", "SKIP_REV", "SKIP_FWD",
        } and self.supports_parameterless(room_id, command)

    def supports_room_source(self, room_id: int, experience: str) -> bool:
        """Require the source command shape observed on this Core3 room."""
        command = {
            "listen": "SELECT_AUDIO_DEVICE", "watch": "SELECT_VIDEO_DEVICE",
        }.get(experience)
        if command is None:
            return False
        record = self._metadata.get(room_id, {}).get(command)
        if record is None or not isinstance(record.get("params"), list):
            return False
        specs = record["params"]
        return (
            len(specs) == 2
            and all(isinstance(spec, dict) for spec in specs)
            and {spec.get("name"): spec.get("valueType") for spec in specs}
            == {"deviceid": "INTEGER", "deselect": "BOOL"}
        )

    def supports_tidal_playlist(self, protocol_id: int) -> bool:
        """Require the exact TIDAL programming command shape seen on Director."""
        record = self._metadata.get(protocol_id, {}).get("SelectPlaylist")
        if record is None or not isinstance(record.get("params"), list):
            return False
        specs = record["params"]
        if len(specs) != 3 or not all(isinstance(spec, dict) for spec in specs):
            return False
        by_name = {spec.get("name"): spec for spec in specs}
        playlist = by_name.get("Playlist", {})
        room = by_name.get("Room", {})
        shuffle = by_name.get("Shuffle", {})
        room_items = room.get("items")
        shuffle_items = shuffle.get("items")
        return (
            set(by_name) == {"Playlist", "Room", "Shuffle"}
            and playlist.get("type") == "CUSTOM_SELECT:SelectPlaylistName"
            and room.get("type") == "DEVICE_SELECTOR"
            and isinstance(room_items, list)
            and "roomdevice.c4i" in room_items
            and room.get("multiselect") is True
            and shuffle.get("type") == "LIST"
            and isinstance(shuffle_items, list)
            and any(str(item).casefold() == "off" for item in shuffle_items)
        )

    def supports_tunein_favorite(self, protocol_id: int) -> bool:
        """Require the exact TuneIn programming command shape seen on Director."""
        record = self._metadata.get(protocol_id, {}).get("Play Item")
        if record is None or not isinstance(record.get("params"), list):
            return False
        specs = record["params"]
        if len(specs) != 2 or not all(isinstance(spec, dict) for spec in specs):
            return False
        by_name = {spec.get("name"): spec for spec in specs}
        item = by_name.get("Item", {})
        room = by_name.get("Room", {})
        room_items = room.get("items")
        return (
            set(by_name) == {"Item", "Room"}
            and item.get("type") == "CUSTOM_SELECT:GetItemList"
            and room.get("type") == "DEVICE_SELECTOR"
            and isinstance(room_items, list)
            and "roomdevice.c4i" in room_items
            and room.get("multiselect") is True
        )

    def _parameter(self, device_id: int, command: str, name: str) -> dict[str, Any]:
        record = self._metadata.get(device_id, {}).get(command)
        if record is None:
            raise UnsupportedCommand(f"{command} is not advertised for device {device_id}")
        for spec in record.get("params") or []:
            if isinstance(spec, dict) and spec.get("name") == name:
                return spec
        raise UnsupportedCommand(f"{command} does not advertise parameter {name}")

    def _validated_params(self, device_id: int, command: str, params: dict[str, Any]) -> dict[str, Any]:
        record = self._metadata.get(device_id, {}).get(command)
        if record is None:
            raise UnsupportedCommand(f"{command} is not advertised for device {device_id}")
        specs = record.get("params") or []
        names = {spec["name"] for spec in specs if isinstance(spec, dict) and isinstance(spec.get("name"), str)}
        if set(params) != names:
            raise UnsupportedCommand(f"{command} expects parameters {sorted(names)}")
        result: dict[str, Any] = {}
        for name, value in params.items():
            spec = self._parameter(device_id, command, name)
            choices = self.choices(device_id, command, name)
            if choices:
                match = next((choice for choice in choices if choice.casefold() == str(value).casefold()), None)
                if match is None:
                    raise UnsupportedCommand(f"{name} is not an advertised value for {command}")
                result[name] = match
                continue
            limits = self.range(device_id, command, name)
            if limits is not None:
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise UnsupportedCommand(f"{name} must be a finite number")
                if spec.get("valueType") == "INTEGER" and not float(value).is_integer():
                    raise UnsupportedCommand(f"{name} must be an integer")
                if not limits[0] <= value <= limits[1]:
                    raise UnsupportedCommand(f"{name} is outside Director's advertised range")
                resolution = spec.get("resolution")
                if isinstance(resolution, (int, float)) and resolution > 0:
                    if not math.isclose(value / resolution, round(value / resolution), abs_tol=1e-7):
                        raise UnsupportedCommand(f"{name} does not match Director's resolution")
            result[name] = str(value) if spec.get("valueType") == "STRING" else value
        return result

    async def send(self, device_id: int, command: str, params: dict[str, Any] | None = None) -> Any:
        validated = self._validated_params(device_id, command, params or {})
        body: dict[str, Any] = {"command": command}
        if validated:
            body["params"] = validated
        return await self.rest.post_json(f"/api/v1/items/{device_id}/commands", body)

    async def light_on(self, device_id: int) -> Any:
        return await self.send(device_id, "ON")

    async def light_off(self, device_id: int) -> Any:
        return await self.send(device_id, "OFF")

    async def light_level(self, device_id: int, level_percent: int) -> Any:
        return await self.send(device_id, "SET_LEVEL", {"LEVEL": level_percent})

    async def relay_close(self, device_id: int) -> Any:
        """Close an advertised relay; physical mapping was observed for both supported proxies."""
        return await self.send(device_id, "CLOSE")

    async def relay_open(self, device_id: int) -> Any:
        """Open an advertised relay; physical mapping was observed for both supported proxies."""
        return await self.send(device_id, "OPEN")

    async def room_volume(self, room_id: int, level_percent: int) -> Any:
        if not self.supports_room_volume(room_id):
            raise UnsupportedCommand("Director did not advertise a compatible room-volume command")
        if type(level_percent) is not int or not 0 <= level_percent <= 100:
            raise UnsupportedCommand("room volume must be an integer percentage from 0 to 100")
        return await self.send(room_id, "SET_VOLUME_LEVEL", {"LEVEL": level_percent})

    async def room_mute(self, room_id: int, muted: bool) -> Any:
        if not self.supports_room_mute(room_id):
            raise UnsupportedCommand("Director did not advertise both room-mute commands")
        if type(muted) is not bool:
            raise UnsupportedCommand("room mute must be a boolean")
        return await self.send(room_id, "MUTE_ON" if muted else "MUTE_OFF")

    async def room_off(self, room_id: int) -> Any:
        if not self.supports_room_off(room_id):
            raise UnsupportedCommand("Director did not advertise a parameterless room-off command")
        return await self.send(room_id, "ROOM_OFF")

    async def room_transport(self, room_id: int, command: str) -> Any:
        """Send a metadata-validated transport command; response is not state feedback."""
        if not self.supports_room_transport(room_id, command):
            raise UnsupportedCommand("Director did not advertise this room transport command")
        # Match pyControl4's C4Room playback wire format. These commands are
        # accepted but ignored on this Director when sent through the generic
        # ``params`` envelope used successfully by the other command groups.
        return await self.rest.post_json(
            f"/api/v1/items/{room_id}/commands",
            {"async": True, "command": command, "tParams": {}},
        )

    async def room_source(self, room_id: int, source: RoomSource) -> Any:
        """Select a validated UI source using pyControl4's documented argument.

        `deselect` is advertised but is not supplied by pyControl4's source
        selection method. This command deliberately sends only `deviceid`.
        """
        if type(room_id) is not int or room_id <= 0:
            raise UnsupportedCommand("room ID must be a positive integer")
        if not isinstance(source, RoomSource) or not self.supports_room_source(
            room_id, source.experience
        ):
            raise UnsupportedCommand("Director did not advertise a compatible room-source command")
        if type(source.device_id) is not int or not 0 < source.device_id <= 2147483647:
            raise UnsupportedCommand("room source ID must be a positive inventory ID")
        if source not in self._room_sources.get(room_id, frozenset()):
            raise UnsupportedCommand("room source is not in the validated UI catalog")
        command = "SELECT_AUDIO_DEVICE" if source.experience == "listen" else "SELECT_VIDEO_DEVICE"
        return await self.rest.post_json(
            f"/api/v1/items/{room_id}/commands",
            {"command": command, "params": {"deviceid": source.device_id}},
        )

    async def add_rooms_to_session(
        self, leader_room_id: int, member_room_ids: Iterable[int]
    ) -> Any:
        """Add exposed rooms to a leader using Control4's Digital Media command."""
        members = tuple(dict.fromkeys(member_room_ids))
        if self._digital_media_id is None:
            raise UnsupportedCommand("Control4 Digital Media grouping is unavailable")
        if leader_room_id not in self._grouping_room_ids:
            raise UnsupportedCommand("session leader is not an exposed media room")
        if (
            not members
            or leader_room_id in members
            or any(room_id not in self._grouping_room_ids for room_id in members)
        ):
            raise UnsupportedCommand("session members must be other exposed media rooms")
        return await self.rest.post_json(
            f"/api/v1/items/{self._digital_media_id}/commands",
            {
                "command": "ADD_ROOMS_TO_SESSION",
                "params": {
                    "ROOM_ID": leader_room_id,
                    "ROOM_ID_LIST": ",".join(str(room_id) for room_id in members),
                },
            },
        )

    async def tidal_playlist(
        self, protocol_id: int, room_id: int, playlist: TidalPlaylist
    ) -> Any:
        """Start one configured TIDAL playlist in one validated room, unshuffled."""
        if not isinstance(playlist, TidalPlaylist):
            raise UnsupportedCommand("playlist must be a parsed TIDAL playlist")
        if not self.supports_tidal_playlist(protocol_id):
            raise UnsupportedCommand("Director did not advertise a compatible TIDAL command")
        allowed = self._tidal_playlists.get(protocol_id, {}).get(room_id, frozenset())
        if playlist.playlist_id not in allowed:
            raise UnsupportedCommand("playlist is not configured for this TIDAL room source")
        return await self.rest.post_json(
            f"/api/v1/items/{protocol_id}/commands",
            {
                "command": "SelectPlaylist",
                "params": {
                    "Playlist": playlist.playlist_id,
                    "Room": str(room_id),
                    "Shuffle": "Off",
                },
            },
        )

    async def tunein_favorite(
        self, protocol_id: int, room_id: int, favorite: TuneInFavorite
    ) -> Any:
        """Play one configured TuneIn favorite in one validated room."""
        if not isinstance(favorite, TuneInFavorite):
            raise UnsupportedCommand("favorite must be a parsed TuneIn record")
        if not self.supports_tunein_favorite(protocol_id):
            raise UnsupportedCommand("Director did not advertise a compatible TuneIn command")
        allowed = self._tunein_favorites.get(protocol_id, {}).get(
            room_id, frozenset()
        )
        if favorite.item_id not in allowed:
            raise UnsupportedCommand("favorite is not configured for this TuneIn room source")
        return await self.rest.post_json(
            f"/api/v1/items/{protocol_id}/commands",
            {
                "command": "Play Item",
                "params": {
                    "Item": favorite.item_id,
                    "Room": str(room_id),
                },
            },
        )

    async def hvac_mode(self, device_id: int, mode: str) -> Any:
        return await self.send(device_id, "SET_MODE_HVAC", {"MODE": mode})

    async def hold_mode(self, device_id: int, mode: str) -> Any:
        """Set only a hold mode explicitly listed in this device's metadata."""
        if not self.choices(device_id, "SET_MODE_HOLD", "MODE"):
            raise UnsupportedCommand("Director did not advertise hold-mode choices")
        return await self.send(device_id, "SET_MODE_HOLD", {"MODE": mode})

    async def fan_mode(self, device_id: int, mode: str) -> Any:
        """Set only a fan mode explicitly listed in this device's metadata."""
        if not self.choices(device_id, "SET_MODE_FAN", "MODE"):
            raise UnsupportedCommand("Director did not advertise fan-mode choices")
        return await self.send(device_id, "SET_MODE_FAN", {"MODE": mode})

    async def humidity_mode(self, device_id: int, mode: str) -> Any:
        """Set only a humidity mode explicitly listed in thermostat metadata."""
        if not self.choices(device_id, "SET_MODE_HUMIDITY", "MODE"):
            raise UnsupportedCommand("Director did not advertise humidity-mode choices")
        return await self.send(device_id, "SET_MODE_HUMIDITY", {"MODE": mode})

    async def humidity_step(
        self, device_id: int, kind: str, *, increase: bool
    ) -> Any:
        """Move one advertised humidify/dehumidify setpoint by one percent."""
        if kind not in {"HUMIDIFY", "DEHUMIDIFY"} or type(increase) is not bool:
            raise UnsupportedCommand("unsupported humidity setpoint adjustment")
        command = f"{'INC' if increase else 'DEC'}_SETPOINT_{kind}"
        if not self.supports_parameterless(device_id, command):
            raise UnsupportedCommand(
                "Director did not advertise a parameterless humidity adjustment"
            )
        return await self.send(device_id, command)

    async def preset(self, device_id: int, name: str) -> Any:
        """Apply only a named preset explicitly listed in this device's metadata."""
        if not self.choices(device_id, "SET_PRESET", "NAME"):
            raise UnsupportedCommand("Director did not advertise preset choices")
        return await self.send(device_id, "SET_PRESET", {"NAME": name})

    async def setpoint(self, device_id: int, kind: str, temperature: float, scale: str) -> Any:
        if kind not in {"HEAT", "COOL", "SINGLE"} or scale not in {"CELSIUS", "FAHRENHEIT"}:
            raise UnsupportedCommand("unsupported setpoint kind or scale")
        return await self.send(device_id, f"SET_SETPOINT_{kind}", {scale: temperature})
