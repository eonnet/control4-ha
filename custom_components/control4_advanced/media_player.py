"""Control4 room media controls and validated native browsing."""

from __future__ import annotations

import asyncio
import math
from typing import Any

from homeassistant.components.media_player import (
    BrowseError,
    BrowseMedia,
    MediaClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import Control4Runtime
from .entity import Control4Entity
from .source_logos import SOURCE_LOGO_TYPE, source_logo_bytes, source_logo_key
from .transport.commands import UnsupportedCommand
from .transport.events import normalize_rest_variables
from .transport.favorites import RoomTuneInCatalog, TuneInFavorite
from .transport.playlists import RoomTidalCatalog, TidalPlaylist
from .transport.sources import RoomSource


CONTROL4_ROOT_TYPE = "control4_root"
SOURCE_GROUP_TYPE = MediaType.APPS
SOURCE_TYPE = MediaType.APP
TIDAL_ROOT_TYPE = "control4_tidal"
TIDAL_ACCOUNT_TYPE = "control4_tidal_account"
TUNEIN_ROOT_TYPE = "control4_tunein"
TUNEIN_SOURCE_TYPE = "control4_tunein_source"


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    runtime: Control4Runtime = entry.runtime_data
    items = runtime.transport.inventory.items
    async_add_entities([
        Control4RoomMediaPlayer(runtime, items[room_id])
        for room_id in sorted(runtime.media_coordinators)
    ])


class Control4RoomMediaPlayer(Control4Entity, MediaPlayerEntity):
    """Expose only commands supported by this room's Director metadata."""

    def __init__(self, runtime: Control4Runtime, item: dict) -> None:
        super().__init__(runtime, item)
        self._remove_session = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.runtime.media_entity_ids[self.device_id] = self.entity_id
        coordinator = self.runtime.media_session_coordinator
        if coordinator is not None:
            self._remove_session = coordinator.subscribe(self._on_session_update)

    async def async_will_remove_from_hass(self) -> None:
        if self._remove_session is not None:
            self._remove_session()
            self._remove_session = None
        if self.runtime.media_entity_ids.get(self.device_id) == self.entity_id:
            self.runtime.media_entity_ids.pop(self.device_id, None)
        await super().async_will_remove_from_hass()

    def _on_session_update(self) -> None:
        self.async_write_ha_state()

    @property
    def extra_state_attributes(self) -> dict[str, int]:
        """Mark room players so the packaged dashboard card can find them."""
        return {"control4_room_id": self.device_id}

    @property
    def supported_features(self) -> MediaPlayerEntityFeature:
        features = MediaPlayerEntityFeature(0)
        if self._volume_ready() and self.runtime.commands.supports_room_volume(self.device_id):
            features |= MediaPlayerEntityFeature.VOLUME_SET
        if self._volume_ready() and self.runtime.commands.supports_room_mute(self.device_id):
            features |= MediaPlayerEntityFeature.VOLUME_MUTE
        if self._room_is_on() and self.runtime.commands.supports_room_off(self.device_id):
            features |= MediaPlayerEntityFeature.TURN_OFF
        if self._room_is_on():
            for command, feature in (
                ("SKIP_REV", MediaPlayerEntityFeature.PREVIOUS_TRACK),
                ("PLAY", MediaPlayerEntityFeature.PLAY),
                ("PAUSE", MediaPlayerEntityFeature.PAUSE),
                ("STOP", MediaPlayerEntityFeature.STOP),
                ("SKIP_FWD", MediaPlayerEntityFeature.NEXT_TRACK),
            ):
                if self.runtime.commands.supports_room_transport(self.device_id, command):
                    features |= feature
        if self._grouping_ready():
            features |= MediaPlayerEntityFeature.GROUPING
        if self._sources() or self._tidal_catalogs() or self._tunein_catalogs():
            features |= (
                MediaPlayerEntityFeature.BROWSE_MEDIA
                | MediaPlayerEntityFeature.PLAY_MEDIA
            )
        return features

    def _browser_root_id(self) -> str:
        return f"control4_advanced:media:{self.device_id}"

    def _source_group_id(self, experience: str) -> str:
        return f"{self._browser_root_id()}:sources:{experience}"

    def _source_id(self, source: RoomSource) -> str:
        return (
            f"{self._browser_root_id()}:source:"
            f"{source.experience}:{source.device_id}"
        )

    def _logo_thumbnail(self, key: str) -> str:
        return self.get_browse_image_url(SOURCE_LOGO_TYPE, key)

    def _source_thumbnail(self, source: RoomSource) -> str:
        item = self.runtime.transport.inventory.items.get(source.device_id)
        return self._logo_thumbnail(
            source_logo_key(source.name, source.experience, item)
        )

    @staticmethod
    def _source_title(source: RoomSource) -> str:
        """Keep duplicate-name disambiguation without repeating the folder name."""
        return source.label.removeprefix(f"{source.experience.title()}: ")

    def _tidal_catalogs(self) -> tuple[RoomTidalCatalog, ...]:
        return self.runtime.tidal_catalogs.get(self.device_id, ())

    def _tidal_root_id(self) -> str:
        return f"control4_advanced:tidal:{self.device_id}"

    def _tidal_account_id(self, catalog: RoomTidalCatalog) -> str:
        return f"{self._tidal_root_id()}:account:{catalog.source_id}"

    def _tidal_playlist_id(
        self, catalog: RoomTidalCatalog, playlist: TidalPlaylist
    ) -> str:
        return (
            f"{self._tidal_root_id()}:playlist:"
            f"{catalog.source_id}:{playlist.playlist_id}"
        )

    def _catalog_for_media_id(self, media_id: str) -> RoomTidalCatalog | None:
        return next(
            (
                catalog
                for catalog in self._tidal_catalogs()
                if self._tidal_account_id(catalog) == media_id
            ),
            None,
        )

    def _playlist_for_media_id(
        self, media_id: str
    ) -> tuple[RoomTidalCatalog, TidalPlaylist] | None:
        for catalog in self._tidal_catalogs():
            for playlist in catalog.playlists:
                if self._tidal_playlist_id(catalog, playlist) == media_id:
                    return catalog, playlist
        return None

    def _tunein_catalogs(self) -> tuple[RoomTuneInCatalog, ...]:
        return self.runtime.tunein_catalogs.get(self.device_id, ())

    def _tunein_root_id(self) -> str:
        return f"control4_advanced:tunein:{self.device_id}"

    def _tunein_source_id(self, catalog: RoomTuneInCatalog) -> str:
        return f"{self._tunein_root_id()}:source:{catalog.source_id}"

    def _tunein_favorite_id(
        self, catalog: RoomTuneInCatalog, favorite: TuneInFavorite
    ) -> str:
        return (
            f"{self._tunein_root_id()}:station:"
            f"{catalog.source_id}:{favorite.item_id}"
        )

    def _tunein_catalog_for_media_id(
        self, media_id: str
    ) -> RoomTuneInCatalog | None:
        return next(
            (
                catalog
                for catalog in self._tunein_catalogs()
                if self._tunein_source_id(catalog) == media_id
            ),
            None,
        )

    def _tunein_favorite_for_media_id(
        self, media_id: str
    ) -> tuple[RoomTuneInCatalog, TuneInFavorite] | None:
        for catalog in self._tunein_catalogs():
            for favorite in catalog.favorites:
                if self._tunein_favorite_id(catalog, favorite) == media_id:
                    return catalog, favorite
        return None

    def _source_group_for_media_id(self, media_id: str) -> str | None:
        return next(
            (
                experience
                for experience in ("watch", "listen")
                if self._source_group_id(experience) == media_id
                and any(
                    source.experience == experience for source in self._sources()
                )
            ),
            None,
        )

    def _source_for_media_id(self, media_id: str) -> RoomSource | None:
        return next(
            (
                source
                for source in self._sources()
                if self._source_id(source) == media_id
            ),
            None,
        )

    async def async_browse_media(
        self,
        media_content_type: MediaType | str | None = None,
        media_content_id: str | None = None,
    ) -> BrowseMedia:
        """Show validated room sources and configured media favorites."""
        sources = self._sources()
        catalogs = tuple(
            catalog for catalog in self._tidal_catalogs() if catalog.playlists
        )
        tunein_catalogs = self._tunein_catalogs()
        if not sources and not catalogs and not tunein_catalogs:
            raise BrowseError("No Control4 media is available in this room")

        if media_content_id in (None, self._browser_root_id()):
            children = [
                BrowseMedia(
                    title=experience.title(),
                    media_class=MediaClass.DIRECTORY,
                    media_content_id=self._source_group_id(experience),
                    media_content_type=SOURCE_GROUP_TYPE,
                    can_play=False,
                    can_expand=True,
                    thumbnail=self._logo_thumbnail(experience),
                    children_media_class=MediaClass.APP,
                )
                for experience in ("watch", "listen")
                if any(source.experience == experience for source in sources)
            ]
            if catalogs:
                children.append(
                    BrowseMedia(
                        title="TIDAL",
                        media_class=MediaClass.DIRECTORY,
                        media_content_id=self._tidal_root_id(),
                        media_content_type=TIDAL_ROOT_TYPE,
                        can_play=False,
                        can_expand=True,
                        thumbnail=self._logo_thumbnail("tidal"),
                        children_media_class=MediaClass.DIRECTORY,
                    )
                )
            if tunein_catalogs:
                children.append(
                    BrowseMedia(
                        title="TuneIn",
                        media_class=MediaClass.DIRECTORY,
                        media_content_id=self._tunein_root_id(),
                        media_content_type=TUNEIN_ROOT_TYPE,
                        can_play=False,
                        can_expand=True,
                        thumbnail=self._logo_thumbnail("tunein"),
                        children_media_class=MediaClass.DIRECTORY,
                    )
                )
            return BrowseMedia(
                title="Control4",
                media_class=MediaClass.DIRECTORY,
                media_content_id=self._browser_root_id(),
                media_content_type=CONTROL4_ROOT_TYPE,
                can_play=False,
                can_expand=True,
                children=children,
                children_media_class=MediaClass.DIRECTORY,
            )

        source_group = self._source_group_for_media_id(media_content_id)
        if source_group is not None:
            return BrowseMedia(
                title=source_group.title(),
                media_class=MediaClass.DIRECTORY,
                media_content_id=self._source_group_id(source_group),
                media_content_type=SOURCE_GROUP_TYPE,
                can_play=False,
                can_expand=True,
                children=[
                    BrowseMedia(
                        title=self._source_title(source),
                        media_class=MediaClass.APP,
                        media_content_id=self._source_id(source),
                        media_content_type=SOURCE_TYPE,
                        can_play=True,
                        can_expand=False,
                        thumbnail=self._source_thumbnail(source),
                    )
                    for source in sources
                    if source.experience == source_group
                ],
                children_media_class=MediaClass.APP,
            )

        if media_content_id == self._tidal_root_id() and catalogs:
            return BrowseMedia(
                title="TIDAL",
                media_class=MediaClass.DIRECTORY,
                media_content_id=self._tidal_root_id(),
                media_content_type=TIDAL_ROOT_TYPE,
                can_play=False,
                can_expand=True,
                children=[
                    BrowseMedia(
                        title=catalog.source_name,
                        media_class=MediaClass.DIRECTORY,
                        media_content_id=self._tidal_account_id(catalog),
                        media_content_type=TIDAL_ACCOUNT_TYPE,
                        can_play=False,
                        can_expand=True,
                        thumbnail=self._logo_thumbnail("tidal"),
                        children_media_class=MediaClass.PLAYLIST,
                    )
                    for catalog in catalogs
                ],
                children_media_class=MediaClass.DIRECTORY,
            )

        if media_content_id == self._tunein_root_id() and tunein_catalogs:
            return BrowseMedia(
                title="TuneIn",
                media_class=MediaClass.DIRECTORY,
                media_content_id=self._tunein_root_id(),
                media_content_type=TUNEIN_ROOT_TYPE,
                can_play=False,
                can_expand=True,
                children=[
                    BrowseMedia(
                        title=catalog.source_name,
                        media_class=MediaClass.DIRECTORY,
                        media_content_id=self._tunein_source_id(catalog),
                        media_content_type=TUNEIN_SOURCE_TYPE,
                        can_play=False,
                        can_expand=True,
                        thumbnail=self._logo_thumbnail("tunein"),
                        children_media_class=MediaClass.CHANNEL,
                    )
                    for catalog in tunein_catalogs
                ],
                children_media_class=MediaClass.DIRECTORY,
            )

        tunein_catalog = self._tunein_catalog_for_media_id(media_content_id)
        if tunein_catalog is not None:
            return BrowseMedia(
                title=tunein_catalog.source_name,
                media_class=MediaClass.DIRECTORY,
                media_content_id=self._tunein_source_id(tunein_catalog),
                media_content_type=TUNEIN_SOURCE_TYPE,
                can_play=False,
                can_expand=True,
                children=[
                    BrowseMedia(
                        title=favorite.name,
                        media_class=MediaClass.CHANNEL,
                        media_content_id=self._tunein_favorite_id(
                            tunein_catalog, favorite
                        ),
                        media_content_type=MediaType.CHANNEL,
                        can_play=True,
                        can_expand=False,
                    )
                    for favorite in tunein_catalog.favorites
                ],
                children_media_class=MediaClass.CHANNEL,
            )

        catalog = self._catalog_for_media_id(media_content_id)
        if catalog is None:
            raise BrowseError("Control4 media is not configured for this room")
        return BrowseMedia(
            title=catalog.source_name,
            media_class=MediaClass.DIRECTORY,
            media_content_id=self._tidal_account_id(catalog),
            media_content_type=TIDAL_ACCOUNT_TYPE,
            can_play=False,
            can_expand=True,
            children=[
                BrowseMedia(
                    title=playlist.name,
                    media_class=MediaClass.PLAYLIST,
                    media_content_id=self._tidal_playlist_id(catalog, playlist),
                    media_content_type=MediaType.PLAYLIST,
                    can_play=True,
                    can_expand=False,
                )
                for playlist in catalog.playlists
            ],
            children_media_class=MediaClass.PLAYLIST,
        )

    async def async_get_browse_image(
        self,
        media_content_type: str,
        media_content_id: str,
        media_image_id: str | None = None,
    ) -> tuple[bytes | None, str | None]:
        """Serve only packaged, allowlisted source logos through HA's proxy."""
        if media_content_type != SOURCE_LOGO_TYPE or media_image_id is not None:
            return None, None
        content = await self.hass.async_add_executor_job(
            source_logo_bytes, media_content_id
        )
        return (content, "image/svg+xml") if content is not None else (None, None)

    async def async_play_media(
        self, media_type: MediaType | str, media_id: str, **kwargs: Any
    ) -> None:
        """Select an allowlisted source or start a configured TIDAL playlist."""
        if not isinstance(media_id, str):
            raise UnsupportedCommand("Control4 media ID must be text")
        if media_type == SOURCE_TYPE:
            source = self._source_for_media_id(media_id)
            if source is None:
                raise UnsupportedCommand("source is not advertised for this room")
            await self._async_select_source(source)
            return
        if media_type == MediaType.CHANNEL:
            resolved_favorite = self._tunein_favorite_for_media_id(media_id)
            if resolved_favorite is None:
                raise UnsupportedCommand(
                    "TuneIn favorite is not configured for this room"
                )
            catalog, favorite = resolved_favorite
            await self.runtime.commands.tunein_favorite(
                catalog.protocol_id, self.device_id, favorite
            )
            return
        if media_type != MediaType.PLAYLIST:
            raise UnsupportedCommand("Control4 media type is not supported")
        resolved = self._playlist_for_media_id(media_id)
        if resolved is None:
            raise UnsupportedCommand("TIDAL playlist is not configured for this room")
        catalog, playlist = resolved
        await self.runtime.commands.tidal_playlist(
            catalog.protocol_id, self.device_id, playlist
        )

    def _grouping_ready(self) -> bool:
        coordinator = self.runtime.media_session_coordinator
        return (
            coordinator is not None
            and coordinator.ready
            and len(coordinator.room_ids) >= 2
        )

    @property
    def assumed_state(self) -> bool:
        """Show each stateless playback action without inventing media state."""
        return self._room_is_on() and any(
            self.runtime.commands.supports_room_transport(self.device_id, command)
            for command in ("SKIP_REV", "PLAY", "PAUSE", "STOP", "SKIP_FWD")
        )

    def _sources(self) -> tuple[RoomSource, ...]:
        discovered = self.runtime.media_sources.get(self.device_id, ())
        return tuple(
            source for source in discovered
            if self.runtime.commands.supports_room_source(self.device_id, source.experience)
        )

    def _room_is_on(self) -> bool:
        return self.state_data.get("is_on") is True

    def _volume_ready(self) -> bool:
        bound_id = self.state_data.get("volume_device_id")
        bound = (
            self.runtime.transport.inventory.items.get(bound_id)
            if type(bound_id) is int and bound_id > 0
            else None
        )
        volume = self.state_data.get("volume_percent")
        return (
            bound is not None
            and bound.get("proxy") == "aswitch"
            and type(volume) in (int, float)
            and 0 <= volume <= 100
            and isinstance(self.state_data.get("is_muted"), bool)
        )

    async def async_set_volume_level(self, volume: float) -> None:
        if not isinstance(volume, (int, float)) or isinstance(volume, bool):
            raise UnsupportedCommand("Home Assistant room volume must be numeric")
        if not math.isfinite(volume) or not 0 <= volume <= 1:
            raise UnsupportedCommand("Home Assistant room volume must be between 0 and 1")
        await self.runtime.commands.room_volume(self.device_id, math.floor(volume * 100 + 0.5))

    async def async_mute_volume(self, mute: bool) -> None:
        await self.runtime.commands.room_mute(self.device_id, mute)

    async def async_media_play(self) -> None:
        await self._async_room_transport("PLAY")

    async def async_media_pause(self) -> None:
        await self._async_room_transport("PAUSE")

    async def async_media_stop(self) -> None:
        await self._async_room_transport("STOP")

    async def async_media_previous_track(self) -> None:
        await self._async_room_transport("SKIP_REV")

    async def async_media_next_track(self) -> None:
        await self._async_room_transport("SKIP_FWD")

    async def _async_room_transport(self, command: str) -> None:
        if (
            not self._room_is_on()
            or not self.runtime.commands.supports_room_transport(self.device_id, command)
        ):
            raise UnsupportedCommand(
                "room playback command is unavailable while the room is off"
            )
        # Control4 supplies no playback-state feedback. Re-read verified room
        # power immediately before sending this stateless command.
        variables = await self.runtime.transport.rest.get_variables(self.device_id)
        live_state = normalize_rest_variables(
            self.device_id, "roomdevice", variables
        ).changes
        if live_state.get("is_on") is not True or not self.runtime.transport.connected:
            raise UnsupportedCommand(
                "room playback command requires a live powered-on room"
            )
        await self.runtime.commands.room_transport(self.device_id, command)
        if command in {"SKIP_REV", "SKIP_FWD"}:
            # Give Director a brief moment to advance its CURRENT MEDIA INFO,
            # then update the visible title without waiting for the fallback.
            await asyncio.sleep(0.5)
            await self.runtime.transport.sync_room_source(self.device_id)

    @property
    def media_title(self) -> str | None:
        value = self.state_data.get("media_title")
        if isinstance(value, str):
            return value
        selected = self._selected_source()
        return selected.name if self._room_is_on() and selected is not None else None

    @property
    def media_artist(self) -> str | None:
        value = self.state_data.get("media_artist")
        return value if isinstance(value, str) else None

    @property
    def media_album_name(self) -> str | None:
        value = self.state_data.get("media_album_name")
        return value if isinstance(value, str) else None

    @property
    def media_image_url(self) -> str | None:
        """Return Director artwork, or the selected source's packaged logo."""
        value = self.state_data.get("media_image_url")
        if isinstance(value, str):
            return value
        selected = self._selected_source()
        if not self._room_is_on() or selected is None:
            return None
        return self._source_thumbnail(selected)

    async def async_get_media_image(self) -> tuple[bytes | None, str | None]:
        """Fetch Director artwork or serve the selected source logo directly."""
        if isinstance(self.state_data.get("media_image_url"), str):
            return await super().async_get_media_image()
        selected = self._selected_source()
        if not self._room_is_on() or selected is None:
            return None, None
        item = self.runtime.transport.inventory.items.get(selected.device_id)
        content = await self.hass.async_add_executor_job(
            source_logo_bytes,
            source_logo_key(selected.name, selected.experience, item),
        )
        return (content, "image/svg+xml") if content is not None else (None, None)

    @property
    def media_image_remotely_accessible(self) -> bool:
        """Keep the upstream artwork URL behind Home Assistant's proxy."""
        return False

    @property
    def source(self) -> str | None:
        selected = self._selected_source()
        return selected.label if selected is not None else None

    def _selected_source(self) -> RoomSource | None:
        selected_id = self.state_data.get("selected_source_id")
        if type(selected_id) is not int or selected_id <= 0:
            return None
        return next(
            (source for source in self._sources() if source.device_id == selected_id),
            None,
        )

    async def _async_select_source(self, selected: RoomSource) -> None:
        """Select one already resolved, allowlisted browser source."""
        await self.runtime.commands.room_source(self.device_id, selected)
        # A command response is not state feedback. Only a REST read may
        # confirm the selected source; retry briefly for Director's settle.
        for delay in (0, 0.25, 0.5, 1.0, 1.0):
            if delay:
                await asyncio.sleep(delay)
            await self.runtime.transport.sync_device(self.device_id)
            if self.state_data.get("selected_source_id") == selected.device_id:
                break

    @property
    def group_members(self) -> list[str] | None:
        coordinator = self.runtime.media_session_coordinator
        if coordinator is None or not coordinator.ready or not getattr(self, "entity_id", None):
            return None
        return [
            entity_id
            for room_id in coordinator.group_room_ids(self.device_id)
            if (entity_id := self.runtime.media_entity_ids.get(room_id)) is not None
        ]

    async def async_join_players(self, group_members: list[str]) -> None:
        coordinator = self.runtime.media_session_coordinator
        if coordinator is None or not self._grouping_ready():
            raise UnsupportedCommand("Control4 media grouping is unavailable")
        # Home Assistant's group dialog always submits the remaining members
        # through media_player.join before issuing media_player.unjoin for each
        # deselected room. An empty list is therefore a valid preparatory no-op.
        if not group_members:
            return
        if not self._room_is_on() or self._selected_source() is None:
            raise UnsupportedCommand("room must have an active source before grouping")
        entity_rooms = {
            entity_id: room_id
            for room_id, entity_id in self.runtime.media_entity_ids.items()
        }
        if any(
            entity_id not in entity_rooms for entity_id in group_members
        ):
            raise UnsupportedCommand("group members must be Control4 room media players")
        requested = tuple(dict.fromkeys(
            entity_rooms[entity_id]
            for entity_id in group_members
            if entity_rooms[entity_id] != self.device_id
        ))
        if not requested:
            return
        await coordinator.refresh()
        missing = tuple(
            room_id
            for room_id in requested
            if not coordinator.same_group(self.device_id, (room_id,))
        )
        if not missing:
            return
        await self.runtime.commands.add_rooms_to_session(self.device_id, missing)
        for delay in (0.25, 0.5, 1.0, 1.0, 1.0):
            await asyncio.sleep(delay)
            await coordinator.refresh()
            if coordinator.same_group(self.device_id, missing):
                await asyncio.gather(*(
                    self.runtime.transport.sync_room_source(room_id)
                    for room_id in missing
                ))
                return
        raise UnsupportedCommand("Control4 did not confirm the requested media group")

    async def async_unjoin_player(self) -> None:
        coordinator = self.runtime.media_session_coordinator
        if coordinator is None:
            raise UnsupportedCommand("Control4 media grouping is unavailable")
        await coordinator.refresh()
        if len(coordinator.group_room_ids(self.device_id)) <= 1:
            return
        if not self.runtime.commands.supports_room_off(self.device_id):
            raise UnsupportedCommand("room off is required to leave a Control4 session")
        await self.runtime.commands.room_off(self.device_id)
        for delay in (0.25, 0.5, 1.0, 1.0, 1.0):
            await asyncio.sleep(delay)
            await asyncio.gather(
                self.runtime.transport.sync_device(self.device_id),
                coordinator.refresh(),
            )
            if (
                len(coordinator.group_room_ids(self.device_id)) <= 1
                and self.state == MediaPlayerState.OFF
            ):
                return
        raise UnsupportedCommand("Control4 did not confirm that the room left the session")

    async def async_turn_off(self) -> None:
        if not self._room_is_on() or not self.runtime.commands.supports_room_off(self.device_id):
            raise UnsupportedCommand("room off is unavailable for this room state")
        await self.runtime.commands.room_off(self.device_id)
        # The command response is not state feedback; confirm from Director.
        for delay in (0, 0.25, 0.5, 1.0, 1.0):
            if delay:
                await asyncio.sleep(delay)
            await self.runtime.transport.sync_device(self.device_id)
            if self.state == MediaPlayerState.OFF:
                break

    @property
    def state(self) -> MediaPlayerState | None:
        powered = self.state_data.get("is_on")
        if powered is False:
            return MediaPlayerState.OFF
        if powered is not True:
            return None
        selected = self.state_data.get("selected_source_id")
        return (
            MediaPlayerState.PLAYING
            if type(selected) is int and selected > 0
            else MediaPlayerState.IDLE
        )

    @property
    def volume_level(self) -> float | None:
        percent = self.state_data.get("volume_percent")
        if type(percent) in (int, float) and 0 <= percent <= 100:
            return percent / 100
        return None

    @property
    def is_volume_muted(self) -> bool | None:
        muted = self.state_data.get("is_muted")
        return muted if isinstance(muted, bool) else None

    @property
    def available(self) -> bool:
        return (
            super().available
            and self.state is not None
            and (self._volume_ready() or bool(self._sources()))
        )
