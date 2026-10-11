"""Offline tests for TIDAL playlist parsing and room/account joins."""

from __future__ import annotations

import unittest

from custom_components.control4_advanced.transport.playlists import (
    parse_tidal_playlists,
    room_tidal_catalogs,
)
from custom_components.control4_advanced.transport.sources import RoomSource


class TidalPlaylistTests(unittest.TestCase):
    def test_parser_preserves_labels_and_normalizes_ids(self) -> None:
        playlists = parse_tidal_playlists(
            "# account | name | id\n"
            "TIDAL | Evening Mix | 01234567-89AB-CDEF-0123-456789ABCDEF\n"
            "Juliane | Acoustic | 11111111-2222-4333-8444-555555555555\n"
        )
        self.assertEqual(
            [(item.source_name, item.name, item.playlist_id) for item in playlists],
            [
                ("TIDAL", "Evening Mix", "01234567-89ab-cdef-0123-456789abcdef"),
                ("Juliane", "Acoustic", "11111111-2222-4333-8444-555555555555"),
            ],
        )

    def test_parser_rejects_ambiguous_or_invalid_lines(self) -> None:
        for value in (
            "TIDAL | Missing ID",
            "TIDAL | Mix | not-a-uuid",
            "TIDAL | Mix | 01234567-89ab-cdef-0123-456789abcdef\n"
            "tidal | MIX | 11111111-2222-4333-8444-555555555555",
        ):
            with self.assertRaises(ValueError):
                parse_tidal_playlists(value)

    def test_catalog_uses_only_visible_inventory_backed_tidal_sources(self) -> None:
        playlists = parse_tidal_playlists(
            "TIDAL | Evening Mix | 01234567-89ab-cdef-0123-456789abcdef\n"
            "Hidden | Secret | 11111111-2222-4333-8444-555555555555"
        )
        sources = {
            22: (
                RoomSource(94, "listen", "TIDAL", "DIGITAL_AUDIO_SERVER", "Listen: TIDAL"),
                RoomSource(346, "watch", "Plex", "RF_MINI_APP", "Watch: Plex"),
            )
        }
        inventory = {
            94: {
                "id": 94,
                "name": "TIDAL",
                "proxy": "media_service",
                "protocolId": 93,
                "protocolFilename": "tidal.c4z",
            },
            346: {"id": 346, "name": "Plex", "proxy": "media_player"},
        }
        catalogs = room_tidal_catalogs(sources, inventory, playlists)
        self.assertEqual(list(catalogs), [22])
        catalog = catalogs[22][0]
        self.assertEqual((catalog.source_id, catalog.protocol_id, catalog.source_name), (94, 93, "TIDAL"))
        self.assertEqual([item.name for item in catalog.playlists], ["Evening Mix"])


if __name__ == "__main__":
    unittest.main()
