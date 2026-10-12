"""Push-driven Control4 select entities."""

from __future__ import annotations

import asyncio
from collections import Counter
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
from .transport.sources import RoomSource


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
        selection = RoomTidalSelection(runtime, room_id, catalogs)
        entities.append(Control4TidalAccountSelect(runtime, room, selection))
        if any(catalog.playlists for catalog in catalogs):
            entities.append(Control4TidalPlaylistSelect(runtime, room, selection))
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


class RoomTidalSelection:
    """Share the selected TIDAL account between a room's two selectors."""

    def __init__(
        self,
        runtime: Control4Runtime,
        room_id: int,
        catalogs: tuple[RoomTidalCatalog, ...],
    ) -> None:
        self.runtime = runtime
        self.room_id = room_id
        counts = Counter(catalog.source_name for catalog in catalogs)
        self._labels = {
            catalog.protocol_id: (
                catalog.source_name
                if counts[catalog.source_name] == 1
                else f"{catalog.source_name} ({catalog.source_id})"
            )
            for catalog in catalogs
        }
        self._by_label = {
            self._labels[catalog.protocol_id]: catalog for catalog in catalogs
        }
        self._by_source_id = {catalog.source_id: catalog for catalog in catalogs}
        selected = self._catalog_for_director_state()
        self._preferred = selected or catalogs[0]
        self._entities: list[Control4Entity] = []

    @property
    def account_options(self) -> list[str]:
        return list(self._by_label)

    @property
    def current_account(self) -> str | None:
        catalog = self._catalog_for_director_state()
        return self._labels[catalog.protocol_id] if catalog is not None else None

    @property
    def active_catalog(self) -> RoomTidalCatalog:
        return self._catalog_for_director_state() or self._preferred

    def catalog_for_account(self, option: str) -> RoomTidalCatalog | None:
        return self._by_label.get(option)

    def room_source(self, catalog: RoomTidalCatalog) -> RoomSource | None:
        return next(
            (
                source
                for source in self.runtime.media_sources.get(self.room_id, ())
                if source.device_id == catalog.source_id
                and source.experience == "listen"
            ),
            None,
        )

    def prefer(self, catalog: RoomTidalCatalog) -> None:
        self._preferred = catalog
        for entity in self._entities:
            if getattr(entity, "hass", None) is not None:
                entity.async_write_ha_state()

    def attach(self, entity: Control4Entity) -> None:
        self._entities.append(entity)

    def _catalog_for_director_state(self) -> RoomTidalCatalog | None:
        selected_id = self.runtime.transport.state.snapshot(self.room_id).get(
            "selected_source_id"
        )
        if type(selected_id) is not int:
            return None
        return self._by_source_id.get(selected_id)


class Control4TidalAccountSelect(Control4Entity, SelectEntity):
    """Select the Control4 TIDAL Listen source for one room."""

    _attr_icon = "mdi:account-music"

    def __init__(
        self,
        runtime: Control4Runtime,
        room: dict[str, Any],
        selection: RoomTidalSelection,
    ) -> None:
        super().__init__(runtime, room)
        self.selection = selection
        self._attr_unique_id = (
            f"{runtime.transport.rest.host}_{self.device_id}_tidal_account"
        )
        self._attr_name = f"{self._attr_name} TIDAL account"
        self._attr_options = selection.account_options
        selection.attach(self)

    @property
    def current_option(self) -> str | None:
        return self.selection.current_account

    async def async_select_option(self, option: str) -> None:
        catalog = self.selection.catalog_for_account(option)
        if catalog is None:
            raise HomeAssistantError("TIDAL account is not configured for this room")
        source = self.selection.room_source(catalog)
        if source is None:
            raise HomeAssistantError("TIDAL account is not exposed in this room")
        await self.runtime.commands.room_source(self.device_id, source)
        self.selection.prefer(catalog)
        # A command response is not state feedback. Confirm the source from
        # Director, allowing the same short settle window as media_player.
        for delay in (0, 0.25, 0.5, 1.0, 1.0):
            if delay:
                await asyncio.sleep(delay)
            await self.runtime.transport.sync_device(self.device_id)
            if self.state_data.get("selected_source_id") == catalog.source_id:
                break
        self.selection.prefer(catalog)


class Control4TidalPlaylistSelect(Control4Entity, SelectEntity):
    """Stateless room playlist picker for the active Control4 TIDAL account."""

    _attr_icon = "mdi:playlist-music"

    def __init__(
        self,
        runtime: Control4Runtime,
        room: dict[str, Any],
        selection: RoomTidalSelection,
    ) -> None:
        super().__init__(runtime, room)
        self.selection = selection
        self._attr_unique_id = (
            f"{runtime.transport.rest.host}_{self.device_id}_tidal_playlist"
        )
        self._attr_name = f"{self._attr_name} TIDAL playlist"
        selection.attach(self)

    @property
    def options(self) -> list[str]:
        return [playlist.name for playlist in self.selection.active_catalog.playlists]

    @property
    def current_option(self) -> str | None:
        """Control4 does not report which playlist started the current queue."""
        return None

    async def async_select_option(self, option: str) -> None:
        catalog = self.selection.active_catalog
        playlist: TidalPlaylist | None = next(
            (playlist for playlist in catalog.playlists if playlist.name == option),
            None,
        )
        if playlist is None:
            raise HomeAssistantError("Playlist is not configured for the selected TIDAL account")
        await self.runtime.commands.tidal_playlist(
            catalog.protocol_id, self.device_id, playlist
        )
