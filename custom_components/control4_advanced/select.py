"""Push-driven Control4 thermostat hold-mode select entities."""

from __future__ import annotations

from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import Control4Runtime
from .entity import Control4Entity
from .transport.commands import UnsupportedCommand
from .transport.playlists import RoomTidalCatalog, TidalPlaylist


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    runtime: Control4Runtime = entry.runtime_data
    items = runtime.transport.inventory.items
    entities: list[SelectEntity] = []
    for device_id in sorted(runtime.transport.tracked_ids):
        if items[device_id].get("proxy") != "thermostatV2":
            continue
        if not runtime.commands.supports(device_id, "SET_MODE_HOLD"):
            continue
        try:
            options = runtime.commands.choices(device_id, "SET_MODE_HOLD", "MODE")
        except UnsupportedCommand:
            continue
        current = runtime.transport.state.snapshot(device_id).get("hold_mode")
        if not isinstance(current, str) or not any(
            option.casefold() == current.casefold() for option in options
        ):
            continue
        entities.append(Control4HoldSelect(runtime, items[device_id], options))
    for room_id, catalogs in sorted(runtime.tidal_catalogs.items()):
        room = items.get(room_id)
        if not isinstance(room, dict):
            continue
        entities.extend(
            Control4TidalPlaylistSelect(runtime, room, catalog)
            for catalog in catalogs
        )
    async_add_entities(entities)


class Control4HoldSelect(Control4Entity, SelectEntity):
    """Select Director-advertised hold modes; wait for push to confirm state."""

    def __init__(
        self, runtime: Control4Runtime, item: dict[str, Any], options: list[str]
    ) -> None:
        super().__init__(runtime, item)
        self._attr_unique_id = f"{runtime.transport.rest.host}_{self.device_id}_hold_mode"
        self._attr_name = f"{self._attr_name} Hold mode"
        self._attr_options = options

    @property
    def current_option(self) -> str | None:
        current = self.state_data.get("hold_mode")
        if not isinstance(current, str):
            return None
        return next(
            (option for option in self.options if option.casefold() == current.casefold()),
            None,
        )

    async def async_select_option(self, option: str) -> None:
        if option not in self.options:
            raise HomeAssistantError("Hold mode is not advertised by this thermostat")
        await self.runtime.commands.hold_mode(self.device_id, option)


class Control4TidalPlaylistSelect(Control4Entity, SelectEntity):
    """Stateless room playlist picker for one Control4 TIDAL account."""

    _attr_icon = "mdi:playlist-music"

    def __init__(
        self,
        runtime: Control4Runtime,
        room: dict[str, Any],
        catalog: RoomTidalCatalog,
    ) -> None:
        super().__init__(runtime, room)
        self.catalog = catalog
        self._by_name: dict[str, TidalPlaylist] = {
            playlist.name: playlist for playlist in catalog.playlists
        }
        self._attr_unique_id = (
            f"{runtime.transport.rest.host}_{self.device_id}_"
            f"tidal_playlist_{catalog.protocol_id}"
        )
        self._attr_name = f"{self._attr_name} {catalog.source_name} playlist"
        self._attr_options = list(self._by_name)

    @property
    def current_option(self) -> str | None:
        """Control4 does not report which playlist started the current queue."""
        return None

    async def async_select_option(self, option: str) -> None:
        playlist = self._by_name.get(option)
        if playlist is None:
            raise HomeAssistantError("Playlist is not configured for this TIDAL source")
        await self.runtime.commands.tidal_playlist(
            self.catalog.protocol_id, self.device_id, playlist
        )
