"""Offline HA-shape checks for the read-only room media-player entity."""

from __future__ import annotations

from enum import IntFlag, StrEnum
import importlib
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from custom_components.control4_advanced.transport.commands import UnsupportedCommand

from custom_components.control4_advanced.transport.events import NormalizedEvent
from custom_components.control4_advanced.transport.favorites import (
    RoomTuneInCatalog,
    TuneInFavorite,
)
from custom_components.control4_advanced.transport.playlists import (
    RoomTidalCatalog,
    TidalPlaylist,
)
from custom_components.control4_advanced.transport.sources import RoomSource


class FakeEntity:
    @property
    def unique_id(self):
        return self._attr_unique_id

    @property
    def name(self):
        return self._attr_name

    async def async_added_to_hass(self):
        pass

    async def async_will_remove_from_hass(self):
        pass

    def async_write_ha_state(self):
        self.writes = getattr(self, "writes", 0) + 1


class FakeMediaPlayerEntity(FakeEntity):
    @property
    def media_image_remotely_accessible(self):
        return False

    def get_browse_image_url(
        self, media_content_type, media_content_id, media_image_id=None
    ):
        suffix = f"?media_image_id={media_image_id}" if media_image_id else ""
        return f"/browse-image/{media_content_type}/{media_content_id}{suffix}"


class FakeBrowseError(Exception):
    pass


class FakeBrowseMedia:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class FakeMediaClass(StrEnum):
    APP = "app"
    CHANNEL = "channel"
    DIRECTORY = "directory"
    PLAYLIST = "playlist"


class FakeMediaType(StrEnum):
    APP = "app"
    APPS = "apps"
    CHANNEL = "channel"
    PLAYLIST = "playlist"


class FakeMediaPlayerFeature(IntFlag):
    VOLUME_SET = 1
    VOLUME_MUTE = 2
    PLAY = 4
    SELECT_SOURCE = 8
    TURN_OFF = 16
    PAUSE = 32
    STOP = 64
    PREVIOUS_TRACK = 128
    NEXT_TRACK = 256
    GROUPING = 512
    BROWSE_MEDIA = 1024
    PLAY_MEDIA = 2048


class FakeMediaPlayerState(StrEnum):
    ON = "on"
    OFF = "off"
    PLAYING = "playing"
    IDLE = "idle"


class FakeCommands:
    def __init__(self):
        self.volume_supported = True
        self.mute_supported = True
        self.room_volume = AsyncMock()
        self.room_mute = AsyncMock()
        self.room_source = AsyncMock()
        self.room_off = AsyncMock()
        self.room_transport = AsyncMock()
        self.add_rooms_to_session = AsyncMock()
        self.tidal_playlist = AsyncMock()
        self.tunein_favorite = AsyncMock()
        self.source_supported = True
        self.off_supported = True
        self.transport_supported = {
            "SKIP_REV", "PLAY", "PAUSE", "STOP", "SKIP_FWD",
        }

    def supports_room_volume(self, device_id):
        return device_id == 22 and self.volume_supported

    def supports_room_mute(self, device_id):
        return device_id == 22 and self.mute_supported

    def supports_room_source(self, device_id, experience):
        return device_id in (22, 23) and experience in ("watch", "listen") and self.source_supported

    def supports_room_off(self, device_id):
        return device_id == 22 and self.off_supported

    def supports_room_transport(self, device_id, command):
        return device_id == 22 and command in self.transport_supported


class FakeSessionCoordinator:
    def __init__(self, groups):
        self.ready = True
        self.room_ids = frozenset((22, 23))
        self.groups = groups
        self.refresh_count = 0
        self.join_after_refresh = False
        self.unjoin_after_refresh = False

    async def refresh(self):
        self.refresh_count += 1
        if self.join_after_refresh and self.refresh_count >= 2:
            self.groups = {22: (22, 23), 23: (22, 23)}
        if self.unjoin_after_refresh and self.refresh_count >= 2:
            self.groups = {22: (22,), 23: (23,)}

    def group_room_ids(self, room_id):
        return self.groups.get(room_id, (room_id,))

    def same_group(self, leader_room_id, members):
        group = set(self.group_room_ids(leader_room_id))
        return set(members).issubset(group)

    def subscribe(self, callback):
        return lambda: None



def ha_stub_modules():
    names = (
        "homeassistant", "homeassistant.components", "homeassistant.components.media_player",
        "homeassistant.config_entries", "homeassistant.core", "homeassistant.helpers",
        "homeassistant.helpers.device_registry", "homeassistant.helpers.entity",
        "homeassistant.helpers.entity_platform",
    )
    modules = {name: ModuleType(name) for name in names}
    for name in ("homeassistant", "homeassistant.components", "homeassistant.helpers"):
        modules[name].__path__ = []
    media = modules["homeassistant.components.media_player"]
    media.MediaPlayerEntity = FakeMediaPlayerEntity
    media.BrowseError = FakeBrowseError
    media.BrowseMedia = FakeBrowseMedia
    media.MediaClass = FakeMediaClass
    media.MediaPlayerEntityFeature = FakeMediaPlayerFeature
    media.MediaPlayerState = FakeMediaPlayerState
    media.MediaType = FakeMediaType
    modules["homeassistant.config_entries"].ConfigEntry = type("ConfigEntry", (), {})
    modules["homeassistant.core"].HomeAssistant = type("HomeAssistant", (), {})
    modules["homeassistant.helpers.device_registry"].DeviceInfo = dict
    modules["homeassistant.helpers.entity"].Entity = FakeEntity
    modules["homeassistant.helpers.entity_platform"].AddConfigEntryEntitiesCallback = object
    return modules


class MediaPlayerUITests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.patcher = patch.dict(sys.modules, ha_stub_modules())
        cls.patcher.start()
        cls.media_player = importlib.import_module("custom_components.control4_advanced.media_player")

    @classmethod
    def tearDownClass(cls):
        for name in (
            "custom_components.control4_advanced.media_player",
            "custom_components.control4_advanced.entity",
        ):
            sys.modules.pop(name, None)
        cls.patcher.stop()

    async def asyncSetUp(self):
        self.state = {22: {"is_on": True, "volume_percent": 41.0,
                           "is_muted": False, "volume_device_id": 623}}
        self.items = {
            22: {"id": 22, "name": "Living", "proxy": "roomdevice",
                 "typeName": "room", "roomName": "Living"},
            623: {"id": 623, "proxy": "aswitch", "typeName": "device"},
            1227: {"id": 1227, "proxy": "tv", "typeName": "device"},
        }
        events = SimpleNamespace(subscribe=lambda callback: lambda: None)
        self.live_variables = [{"varName": "POWER_STATE", "value": 1}]
        self.get_variables = AsyncMock(side_effect=lambda room_id: self.live_variables)
        transport = SimpleNamespace(
            rest=SimpleNamespace(
                host="192.168.0.24", get_variables=self.get_variables
            ),
            inventory=SimpleNamespace(items=self.items),
            state=SimpleNamespace(snapshot=lambda device_id: self.state.get(device_id, {}).copy()),
            events=events,
            subscribe_connection=lambda callback: lambda: None,
            connected=True,
            sync_device=AsyncMock(),
            sync_room_source=AsyncMock(),
        )
        self.commands = FakeCommands()
        self.runtime = SimpleNamespace(transport=transport, commands=self.commands,
                                       media_coordinators={22: object()}, media_sources={},
                                       tidal_catalogs={}, tunein_catalogs={},
                                       media_session_coordinator=None,
                                       media_entity_ids={})
        self.entry = SimpleNamespace(runtime_data=self.runtime)

    async def test_room_entity_exposes_settled_rest_state_and_narrow_controls(self):
        entities = []
        await self.media_player.async_setup_entry(None, self.entry, entities.extend)
        self.assertEqual(len(entities), 1)
        entity = entities[0]
        self.assertEqual(entity.unique_id, "192.168.0.24_22")
        self.assertEqual(entity.name, "Living")
        self.assertEqual(entity.device_info["suggested_area"], "Living")
        self.assertEqual(entity.state, FakeMediaPlayerState.IDLE)
        self.assertEqual(entity.volume_level, 0.41)
        self.assertFalse(entity.is_volume_muted)
        self.assertIsNone(entity.media_title)
        self.assertIsNone(entity.media_artist)
        self.assertIsNone(entity.media_album_name)
        self.assertTrue(entity.available)
        self.assertEqual(entity.supported_features,
                         FakeMediaPlayerFeature.VOLUME_SET | FakeMediaPlayerFeature.VOLUME_MUTE
                         | FakeMediaPlayerFeature.TURN_OFF | FakeMediaPlayerFeature.PLAY
                         | FakeMediaPlayerFeature.PAUSE | FakeMediaPlayerFeature.STOP
                         | FakeMediaPlayerFeature.PREVIOUS_TRACK
                         | FakeMediaPlayerFeature.NEXT_TRACK)
        self.assertTrue(entity.assumed_state)
        self.assertFalse(entity._attr_should_poll)

        self.state[22].update({"volume_percent": 40, "is_muted": True})
        await entity._on_event(NormalizedEvent(22, "rest", "snapshot", {
            "volume_percent": 40, "is_muted": True,
        }, "2026-10-04T00:00:00Z", True, {}))
        self.assertEqual(entity.writes, 1)
        self.assertEqual(entity.volume_level, 0.4)
        self.assertTrue(entity.is_volume_muted)

    async def test_setup_exposes_source_room_without_supported_volume(self):
        self.items[23] = {
            "id": 23, "name": "Bedroom", "proxy": "roomdevice",
            "typeName": "room", "roomName": "Bedroom",
        }
        self.state[23] = {
            "is_on": False, "selected_source_id": 0, "volume_device_id": 0,
        }
        self.runtime.media_coordinators[23] = object()
        self.runtime.media_sources[23] = (
            RoomSource(623, "listen", "Wiim", "AUDIO_SELECTION", "Listen: Wiim"),
        )
        entities = []
        await self.media_player.async_setup_entry(None, self.entry, entities.extend)
        self.assertEqual([entity.device_id for entity in entities], [22, 23])
        bedroom = entities[1]
        self.assertTrue(bedroom.available)
        self.assertEqual(bedroom.state, FakeMediaPlayerState.OFF)
        self.assertFalse(hasattr(bedroom, "source_list"))
        self.assertEqual(
            bedroom.supported_features,
            FakeMediaPlayerFeature.BROWSE_MEDIA
            | FakeMediaPlayerFeature.PLAY_MEDIA,
        )
        self.assertFalse(
            bedroom.supported_features & FakeMediaPlayerFeature.SELECT_SOURCE
        )

    async def test_browse_configured_tidal_accounts_and_playlists(self):
        evening = TidalPlaylist(
            "Miki", "Evening Mix", "01234567-89ab-cdef-0123-456789abcdef"
        )
        acoustic = TidalPlaylist(
            "Juliane", "Acoustic", "11111111-2222-4333-8444-555555555555"
        )
        self.runtime.media_sources[22] = (
            RoomSource(1417, "listen", "Miki", "AUDIO_SELECTION", "Listen: Miki"),
            RoomSource(
                1366, "listen", "Juliane", "AUDIO_SELECTION", "Listen: Juliane"
            ),
        )
        self.runtime.tidal_catalogs[22] = (
            RoomTidalCatalog(22, 1417, 1416, "Miki", (evening,)),
            RoomTidalCatalog(22, 1366, 1365, "Juliane", (acoustic,)),
        )
        entity = self.media_player.Control4RoomMediaPlayer(
            self.runtime, self.items[22]
        )

        self.assertTrue(
            entity.supported_features & FakeMediaPlayerFeature.BROWSE_MEDIA
        )
        self.assertTrue(
            entity.supported_features & FakeMediaPlayerFeature.PLAY_MEDIA
        )
        root = await entity.async_browse_media()
        self.assertEqual(root.title, "Control4")
        self.assertEqual(root.media_class, FakeMediaClass.DIRECTORY)
        self.assertEqual([child.title for child in root.children], ["Listen", "TIDAL"])

        tidal = await entity.async_browse_media(
            self.media_player.TIDAL_ROOT_TYPE,
            root.children[1].media_content_id,
        )
        self.assertEqual([child.title for child in tidal.children], ["Miki", "Juliane"])
        self.assertTrue(all(child.can_expand for child in tidal.children))

        account = await entity.async_browse_media(
            self.media_player.TIDAL_ACCOUNT_TYPE,
            tidal.children[0].media_content_id,
        )
        self.assertEqual(account.title, "Miki")
        self.assertEqual([child.title for child in account.children], ["Evening Mix"])
        playlist_item = account.children[0]
        self.assertEqual(playlist_item.media_content_type, FakeMediaType.PLAYLIST)
        self.assertTrue(playlist_item.can_play)

        await entity.async_play_media(
            playlist_item.media_content_type, playlist_item.media_content_id
        )
        self.commands.tidal_playlist.assert_awaited_once_with(1416, 22, evening)

    async def test_browse_and_play_reject_unconfigured_tidal_ids(self):
        playlist = TidalPlaylist(
            "Miki", "Evening Mix", "01234567-89ab-cdef-0123-456789abcdef"
        )
        self.runtime.tidal_catalogs[22] = (
            RoomTidalCatalog(22, 1417, 1416, "Miki", (playlist,)),
        )
        entity = self.media_player.Control4RoomMediaPlayer(
            self.runtime, self.items[22]
        )
        with self.assertRaises(FakeBrowseError):
            await entity.async_browse_media(
                self.media_player.TIDAL_ACCOUNT_TYPE,
                "control4_advanced:tidal:22:account:9999",
            )
        with self.assertRaises(UnsupportedCommand):
            await entity.async_play_media(
                FakeMediaType.PLAYLIST,
                "control4_advanced:tidal:22:playlist:1417:not-configured",
            )
        self.commands.tidal_playlist.assert_not_awaited()

    async def test_browse_and_play_configured_tunein_favorites(self):
        klassik = TuneInFavorite(
            "TuneIn", "Klassik Radio", "Station/s25028"
        )
        rock = TuneInFavorite(
            "TuneIn", "Rock Classics", "Station/s258089"
        )
        self.runtime.tunein_catalogs[22] = (
            RoomTuneInCatalog(
                22, 92, 91, "TuneIn Premium", (klassik, rock)
            ),
        )
        entity = self.media_player.Control4RoomMediaPlayer(
            self.runtime, self.items[22]
        )

        root = await entity.async_browse_media()
        self.assertEqual([child.title for child in root.children], ["TuneIn"])
        tunein = await entity.async_browse_media(
            self.media_player.TUNEIN_ROOT_TYPE,
            root.children[0].media_content_id,
        )
        self.assertEqual([child.title for child in tunein.children], ["TuneIn Premium"])
        source = await entity.async_browse_media(
            self.media_player.TUNEIN_SOURCE_TYPE,
            tunein.children[0].media_content_id,
        )
        self.assertEqual(
            [child.title for child in source.children],
            ["Klassik Radio", "Rock Classics"],
        )
        favorite_item = source.children[0]
        self.assertEqual(favorite_item.media_class, FakeMediaClass.CHANNEL)
        self.assertEqual(favorite_item.media_content_type, FakeMediaType.CHANNEL)
        self.assertTrue(favorite_item.can_play)

        await entity.async_play_media(
            favorite_item.media_content_type, favorite_item.media_content_id
        )
        self.commands.tunein_favorite.assert_awaited_once_with(91, 22, klassik)

        with self.assertRaises(UnsupportedCommand):
            await entity.async_play_media(
                FakeMediaType.CHANNEL,
                "control4_advanced:tunein:22:station:92:Station/s99999",
            )
        self.commands.tunein_favorite.assert_awaited_once()

    async def test_volume_and_mute_calls_do_not_optimistically_change_state(self):
        entity = self.media_player.Control4RoomMediaPlayer(self.runtime, self.items[22])
        await entity.async_set_volume_level(0.425)
        await entity.async_mute_volume(True)
        self.commands.room_volume.assert_awaited_once_with(22, 43)
        self.commands.room_mute.assert_awaited_once_with(22, True)
        self.assertEqual(entity.volume_level, 0.41)
        self.assertFalse(entity.is_volume_muted)
        for value in (-0.1, 1.1, float("nan"), True):
            with self.assertRaises(UnsupportedCommand):
                await entity.async_set_volume_level(value)
        self.commands.room_volume.assert_awaited_once()

    async def test_room_off_uses_advertised_command_and_rest_confirmation(self):
        entity = self.media_player.Control4RoomMediaPlayer(self.runtime, self.items[22])

        async def settle(room_id):
            self.assertEqual(room_id, 22)
            self.assertEqual(entity.state, FakeMediaPlayerState.IDLE)
            self.state[22].update({"is_on": False, "selected_source_id": 0,
                                   "volume_device_id": 0})

        self.runtime.transport.sync_device.side_effect = settle
        await entity.async_turn_off()
        self.commands.room_off.assert_awaited_once_with(22)
        self.runtime.transport.sync_device.assert_awaited_once_with(22)
        self.assertEqual(entity.state, FakeMediaPlayerState.OFF)
        self.assertFalse(entity.supported_features & FakeMediaPlayerFeature.TURN_OFF)

        with self.assertRaises(UnsupportedCommand):
            await entity.async_turn_off()
        self.commands.room_off.assert_awaited_once()

        self.state[22]["is_on"] = True
        self.commands.off_supported = False
        with self.assertRaises(UnsupportedCommand):
            await entity.async_turn_off()
        self.commands.room_off.assert_awaited_once()

    async def test_features_vanish_when_metadata_is_missing(self):
        entity = self.media_player.Control4RoomMediaPlayer(self.runtime, self.items[22])
        self.commands.volume_supported = False
        self.assertEqual(entity.supported_features,
                         FakeMediaPlayerFeature.VOLUME_MUTE | FakeMediaPlayerFeature.TURN_OFF
                         | FakeMediaPlayerFeature.PLAY | FakeMediaPlayerFeature.PAUSE
                         | FakeMediaPlayerFeature.STOP | FakeMediaPlayerFeature.PREVIOUS_TRACK
                         | FakeMediaPlayerFeature.NEXT_TRACK)
        self.commands.mute_supported = False
        self.assertEqual(entity.supported_features,
                         FakeMediaPlayerFeature.TURN_OFF | FakeMediaPlayerFeature.PLAY
                         | FakeMediaPlayerFeature.PAUSE | FakeMediaPlayerFeature.STOP
                         | FakeMediaPlayerFeature.PREVIOUS_TRACK
                         | FakeMediaPlayerFeature.NEXT_TRACK)
        self.commands.off_supported = False
        self.assertEqual(entity.supported_features,
                         FakeMediaPlayerFeature.PLAY | FakeMediaPlayerFeature.PAUSE
                         | FakeMediaPlayerFeature.STOP | FakeMediaPlayerFeature.PREVIOUS_TRACK
                         | FakeMediaPlayerFeature.NEXT_TRACK)
        self.commands.transport_supported.clear()
        self.assertEqual(entity.supported_features, 0)
        self.assertFalse(entity.assumed_state)

    async def test_native_playback_controls_are_stateless_and_live_guarded(self):
        entity = self.media_player.Control4RoomMediaPlayer(self.runtime, self.items[22])
        await entity.async_media_play()
        await entity.async_media_pause()
        await entity.async_media_stop()
        await entity.async_media_previous_track()
        await entity.async_media_next_track()
        self.assertEqual(self.get_variables.await_count, 5)
        self.assertEqual(
            [call.args for call in self.commands.room_transport.await_args_list],
            [
                (22, "PLAY"), (22, "PAUSE"), (22, "STOP"),
                (22, "SKIP_REV"), (22, "SKIP_FWD"),
            ],
        )
        self.assertEqual(entity.state, FakeMediaPlayerState.IDLE)
        self.assertEqual(self.runtime.transport.sync_room_source.await_count, 2)

        self.live_variables = [{"varName": "POWER_STATE", "value": 0}]
        with self.assertRaises(UnsupportedCommand):
            await entity.async_media_play()
        self.assertEqual(self.commands.room_transport.await_count, 5)

        self.state[22]["is_on"] = False
        self.live_variables = [{"varName": "POWER_STATE", "value": 1}]
        self.assertFalse(entity.assumed_state)
        self.assertFalse(
            entity.supported_features
            & (
                FakeMediaPlayerFeature.PLAY
                | FakeMediaPlayerFeature.PAUSE
                | FakeMediaPlayerFeature.STOP
                | FakeMediaPlayerFeature.PREVIOUS_TRACK
                | FakeMediaPlayerFeature.NEXT_TRACK
            )
        )
        with self.assertRaises(UnsupportedCommand):
            await entity.async_media_pause()
        self.assertEqual(self.get_variables.await_count, 6)

    async def test_current_media_labels_and_proxied_artwork_are_exposed(self):
        self.state[22].update({
            "media_title": "Tender Surrender",
            "media_artist": "Steve Vai",
            "media_album_name": "Alien Love Secrets",
            "media_image_url": "https://private.invalid/art",
        })
        entity = self.media_player.Control4RoomMediaPlayer(self.runtime, self.items[22])
        self.assertEqual(entity.media_title, "Tender Surrender")
        self.assertEqual(entity.media_artist, "Steve Vai")
        self.assertEqual(entity.media_album_name, "Alien Love Secrets")
        self.assertEqual(entity.media_image_url, "https://private.invalid/art")
        self.assertFalse(entity.media_image_remotely_accessible)

    async def test_selected_source_is_title_fallback_when_media_info_is_empty(self):
        apple_tv = RoomSource(
            509, "watch", "Apple TV", "VIDEO_SELECTION", "Watch: Apple TV"
        )
        self.runtime.media_sources[22] = (apple_tv,)
        self.state[22]["selected_source_id"] = 509
        entity = self.media_player.Control4RoomMediaPlayer(self.runtime, self.items[22])
        self.assertEqual(entity.state, FakeMediaPlayerState.PLAYING)
        self.assertEqual(entity.source, "Watch: Apple TV")
        self.assertEqual(entity.media_title, "Apple TV")
        self.assertIsNone(entity.media_artist)
        self.assertIsNone(entity.media_album_name)

        self.state[22]["media_title"] = "A real programme title"
        self.assertEqual(entity.media_title, "A real programme title")

        self.state[22].update({"is_on": False, "media_title": None})
        self.assertIsNone(entity.media_title)

    async def test_source_browser_is_inventory_discovered_and_rest_confirmed(self):
        plex = RoomSource(346, "watch", "Plex", "RF_MINI_APP", "Watch: Plex")
        wiim = RoomSource(623, "listen", "Wiim", "AUDIO_SELECTION", "Listen: Wiim")
        self.runtime.media_sources[22] = (plex, wiim)
        self.state[22]["selected_source_id"] = 346
        entity = self.media_player.Control4RoomMediaPlayer(self.runtime, self.items[22])
        self.assertEqual(entity.state, FakeMediaPlayerState.PLAYING)
        self.assertFalse(hasattr(entity, "source_list"))
        self.assertEqual(entity.source, "Watch: Plex")
        self.assertFalse(
            entity.supported_features & FakeMediaPlayerFeature.SELECT_SOURCE
        )
        self.assertTrue(
            entity.supported_features & FakeMediaPlayerFeature.BROWSE_MEDIA
        )
        self.assertTrue(
            entity.supported_features & FakeMediaPlayerFeature.PLAY_MEDIA
        )

        root = await entity.async_browse_media()
        self.assertEqual([child.title for child in root.children], ["Watch", "Listen"])
        self.assertTrue(root.children[0].thumbnail.endswith("/watch"))
        self.assertTrue(root.children[1].thumbnail.endswith("/listen"))
        watch = await entity.async_browse_media(
            self.media_player.SOURCE_GROUP_TYPE,
            root.children[0].media_content_id,
        )
        listen = await entity.async_browse_media(
            self.media_player.SOURCE_GROUP_TYPE,
            root.children[1].media_content_id,
        )
        self.assertEqual([child.title for child in watch.children], ["Plex"])
        self.assertEqual([child.title for child in listen.children], ["Wiim"])
        self.assertTrue(watch.children[0].thumbnail.endswith("/plex"))
        self.assertTrue(listen.children[0].thumbnail.endswith("/wiim"))
        source_item = listen.children[0]
        self.assertEqual(source_item.media_class, FakeMediaClass.APP)
        self.assertEqual(source_item.media_content_type, self.media_player.SOURCE_TYPE)
        self.assertTrue(source_item.can_play)

        async def settle(_room_id):
            self.state[22]["selected_source_id"] = 623

        self.runtime.transport.sync_device.side_effect = settle
        await entity.async_play_media(
            source_item.media_content_type, source_item.media_content_id
        )
        self.commands.room_source.assert_awaited_once_with(22, wiim)
        self.runtime.transport.sync_device.assert_awaited_once_with(22)
        self.assertEqual(entity.source, "Listen: Wiim")
        self.assertEqual(entity.state, FakeMediaPlayerState.PLAYING)
        with self.assertRaises(UnsupportedCommand):
            await entity.async_play_media(
                self.media_player.SOURCE_TYPE,
                "control4_advanced:media:22:source:listen:9999",
            )
        self.commands.room_source.assert_awaited_once()

        # A room without a bound volume endpoint remains usable for choosing
        # a source that can power it on; unavailable volume controls disappear.
        self.state[22].update({"is_on": False, "volume_device_id": 0})
        self.assertTrue(entity.available)
        self.assertEqual(
            entity.supported_features,
            FakeMediaPlayerFeature.BROWSE_MEDIA
            | FakeMediaPlayerFeature.PLAY_MEDIA,
        )

        self.commands.source_supported = False
        self.assertIsNone(entity.source)
        self.assertEqual(entity.supported_features, 0)
        self.assertFalse(entity.available)

    async def test_source_logo_proxy_serves_only_packaged_assets(self):
        entity = self.media_player.Control4RoomMediaPlayer(
            self.runtime, self.items[22]
        )

        async def executor(function, *args):
            return function(*args)

        entity.hass = SimpleNamespace(async_add_executor_job=executor)
        content, content_type = await entity.async_get_browse_image(
            self.media_player.SOURCE_LOGO_TYPE, "netflix"
        )
        self.assertTrue(content.startswith(b"<svg"))
        self.assertEqual(content_type, "image/svg+xml")
        self.assertEqual(
            await entity.async_get_browse_image(
                self.media_player.SOURCE_LOGO_TYPE, "../../secrets"
            ),
            (None, None),
        )
        self.assertEqual(
            await entity.async_get_browse_image("unrelated", "netflix"),
            (None, None),
        )

    async def test_native_join_exposes_confirmed_group_members(self):
        self.items[23] = {
            "id": 23, "name": "Bedroom", "proxy": "roomdevice",
            "typeName": "room", "roomName": "Bedroom",
        }
        apple_tv = RoomSource(
            509, "watch", "Apple TV", "VIDEO_SELECTION", "Watch: Apple TV"
        )
        self.runtime.media_sources.update({22: (apple_tv,), 23: (apple_tv,)})
        self.state[22]["selected_source_id"] = 509
        self.state[23] = {"is_on": False, "selected_source_id": 0}
        coordinator = FakeSessionCoordinator({22: (22,), 23: (23,)})
        coordinator.join_after_refresh = True
        self.runtime.media_session_coordinator = coordinator
        self.runtime.media_entity_ids = {
            22: "media_player.living", 23: "media_player.bedroom",
        }
        entity = self.media_player.Control4RoomMediaPlayer(self.runtime, self.items[22])
        entity.entity_id = "media_player.living"
        self.assertTrue(entity.supported_features & FakeMediaPlayerFeature.GROUPING)
        self.assertEqual(entity.group_members, ["media_player.living"])

        with patch.object(self.media_player.asyncio, "sleep", AsyncMock()):
            await entity.async_join_players(["media_player.bedroom"])
        self.commands.add_rooms_to_session.assert_awaited_once_with(22, (23,))
        self.assertEqual(
            entity.group_members,
            ["media_player.living", "media_player.bedroom"],
        )
        self.runtime.transport.sync_room_source.assert_awaited_once_with(23)

        with self.assertRaises(UnsupportedCommand):
            await entity.async_join_players(["media_player.not_control4"])
        self.commands.add_rooms_to_session.assert_awaited_once()

    async def test_empty_join_is_noop_so_ui_can_continue_with_unjoin(self):
        coordinator = FakeSessionCoordinator({22: (22, 23), 23: (22, 23)})
        self.runtime.media_session_coordinator = coordinator
        entity = self.media_player.Control4RoomMediaPlayer(self.runtime, self.items[22])
        await entity.async_join_players([])
        self.commands.add_rooms_to_session.assert_not_awaited()
        self.commands.room_off.assert_not_awaited()

    async def test_off_room_is_advertised_as_available_group_member(self):
        self.items[23] = {
            "id": 23, "name": "Bedroom", "proxy": "roomdevice",
            "typeName": "room", "roomName": "Bedroom",
        }
        apple_tv = RoomSource(
            509, "watch", "Apple TV", "VIDEO_SELECTION", "Watch: Apple TV"
        )
        self.runtime.media_sources.update({22: (apple_tv,), 23: (apple_tv,)})
        self.state[22]["selected_source_id"] = 509
        self.state[23] = {"is_on": False, "selected_source_id": 0}
        coordinator = FakeSessionCoordinator({22: (22,), 23: (23,)})
        self.runtime.media_session_coordinator = coordinator
        self.runtime.media_entity_ids = {
            22: "media_player.living", 23: "media_player.bedroom",
        }
        entity = self.media_player.Control4RoomMediaPlayer(self.runtime, self.items[23])
        entity.entity_id = "media_player.bedroom"
        self.assertEqual(entity.state, FakeMediaPlayerState.OFF)
        self.assertTrue(entity.supported_features & FakeMediaPlayerFeature.GROUPING)

    async def test_unjoin_turns_off_only_this_room_and_confirms_session(self):
        self.items[23] = {
            "id": 23, "name": "Bedroom", "proxy": "roomdevice",
            "typeName": "room", "roomName": "Bedroom",
        }
        coordinator = FakeSessionCoordinator({22: (22, 23), 23: (22, 23)})
        coordinator.unjoin_after_refresh = True
        self.runtime.media_session_coordinator = coordinator
        self.runtime.media_entity_ids = {
            22: "media_player.living", 23: "media_player.bedroom",
        }
        entity = self.media_player.Control4RoomMediaPlayer(self.runtime, self.items[22])
        entity.entity_id = "media_player.living"

        async def settle(room_id):
            self.assertEqual(room_id, 22)
            self.state[22].update({"is_on": False, "selected_source_id": 0})

        self.runtime.transport.sync_device.side_effect = settle
        with patch.object(self.media_player.asyncio, "sleep", AsyncMock()):
            await entity.async_unjoin_player()
        self.commands.room_off.assert_awaited_once_with(22)
        self.assertEqual(entity.group_members, ["media_player.living"])
        self.assertEqual(entity.state, FakeMediaPlayerState.OFF)

    async def test_unknown_binding_and_incomplete_state_are_unavailable(self):
        entity = self.media_player.Control4RoomMediaPlayer(self.runtime, self.items[22])
        self.state[22]["volume_device_id"] = 1227
        self.assertFalse(entity.available)
        self.state[22]["volume_device_id"] = 623
        self.state[22]["is_on"] = False
        self.assertEqual(entity.state, FakeMediaPlayerState.OFF)
        self.assertTrue(entity.available)
        self.state[22].pop("is_muted")
        self.assertFalse(entity.available)
        self.runtime.transport.connected = False
        self.assertFalse(entity.available)

    async def test_state_follows_power_and_selected_source(self):
        entity = self.media_player.Control4RoomMediaPlayer(self.runtime, self.items[22])
        self.assertEqual(entity.state, FakeMediaPlayerState.IDLE)

        for selected in (94, "94"):
            self.state[22]["selected_source_id"] = selected
            expected = (
                FakeMediaPlayerState.PLAYING
                if type(selected) is int
                else FakeMediaPlayerState.IDLE
            )
            self.assertEqual(entity.state, expected)

        self.state[22].update({"is_on": False, "selected_source_id": 94})
        self.assertEqual(entity.state, FakeMediaPlayerState.OFF)

        self.state[22].pop("is_on")
        self.assertIsNone(entity.state)


if __name__ == "__main__":
    unittest.main()
