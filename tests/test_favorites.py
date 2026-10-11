"""Offline tests for TuneIn favorite parsing and room/source joins."""

from __future__ import annotations

import unittest

from custom_components.control4_advanced.transport.favorites import (
    parse_tunein_favorites,
    room_tunein_catalogs,
)
from custom_components.control4_advanced.transport.sources import RoomSource


class TuneInFavoriteTests(unittest.TestCase):
    def test_parser_normalizes_station_ids_and_ignores_comments(self) -> None:
        favorites = parse_tunein_favorites(
            "# Radio\n"
            "TuneIn | Klassik Radio | station/S25028\n"
            "TuneIn | Rock Classics | Station/s258089\n"
        )
        self.assertEqual(
            [(item.source_name, item.name, item.item_id) for item in favorites],
            [
                ("TuneIn", "Klassik Radio", "Station/s25028"),
                ("TuneIn", "Rock Classics", "Station/s258089"),
            ],
        )

    def test_parser_rejects_malformed_ids_and_duplicate_names(self) -> None:
        invalid = (
            "TuneIn | Missing ID",
            "TuneIn | Radio | https://example.invalid/stream",
            "TuneIn | Radio | Station/s25028\n"
            "tunein | RADIO | Station/s258089",
        )
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_tunein_favorites(value)

    def test_catalog_uses_only_visible_inventory_backed_tunein_sources(self) -> None:
        favorites = parse_tunein_favorites(
            "TuneIn | Klassik Radio | Station/s25028"
        )
        sources = {
            22: (
                RoomSource(
                    92,
                    "listen",
                    "TuneIn Premium",
                    "DIGITAL_AUDIO_SERVER",
                    "Listen: TuneIn Premium",
                ),
            ),
            23: (
                RoomSource(
                    94,
                    "listen",
                    "TIDAL",
                    "DIGITAL_AUDIO_SERVER",
                    "Listen: TIDAL",
                ),
            ),
        }
        inventory = {
            91: {
                "id": 91,
                "name": "TuneIn",
                "proxy": "media_service",
                "filename": "tunein-premium.c4z",
            },
            92: {
                "id": 92,
                "name": "TuneIn Premium",
                "proxy": "media_service",
                "protocolId": 91,
                "protocolFilename": "tunein-premium.c4z",
            },
            94: {
                "id": 94,
                "name": "TIDAL",
                "proxy": "media_service",
                "protocolId": 93,
                "protocolFilename": "tidal.c4z",
            },
        }
        catalogs = room_tunein_catalogs(sources, inventory, favorites)
        self.assertEqual(set(catalogs), {22})
        catalog = catalogs[22][0]
        self.assertEqual(
            (catalog.source_id, catalog.protocol_id, catalog.source_name),
            (92, 91, "TuneIn Premium"),
        )
        self.assertEqual([item.name for item in catalog.favorites], ["Klassik Radio"])


if __name__ == "__main__":
    unittest.main()
