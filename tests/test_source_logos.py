"""Offline tests for local Control4 source-logo matching and loading."""

from __future__ import annotations

import unittest

from custom_components.control4_advanced.source_logos import (
    source_logo_bytes,
    source_logo_key,
)


class SourceLogoTests(unittest.TestCase):
    def test_known_source_names_map_to_brand_tiles(self) -> None:
        cases = {
            "Netflix": "netflix",
            "Plex": "plex",
            "Apple TV": "appletv",
            "HBO Max": "hbomax",
            "Xplore TV": "xploretv",
            "Sony Television": "sony",
            "MI BOX": "xiaomi",
            "WiiM": "wiim",
            "Spotify": "spotify",
            "YouTube": "youtube",
            "TuneIn Premium": "tunein",
        }
        for name, expected in cases.items():
            with self.subTest(name=name):
                self.assertEqual(source_logo_key(name, "watch"), expected)

    def test_protocol_filename_identifies_named_tidal_account(self) -> None:
        self.assertEqual(
            source_logo_key(
                "Miki",
                "listen",
                {"protocolFilename": "tidal.c4z"},
            ),
            "tidal",
        )

    def test_unknown_sources_use_experience_fallback(self) -> None:
        self.assertEqual(source_logo_key("Game console", "watch"), "watch")
        self.assertEqual(source_logo_key("Audio matrix", "listen"), "listen")

    def test_only_allowlisted_packaged_svg_assets_are_loaded(self) -> None:
        for key in ("netflix", "tidal", "tunein", "watch", "listen"):
            with self.subTest(key=key):
                content = source_logo_bytes(key)
                self.assertIsNotNone(content)
                self.assertTrue(content.startswith((b"<svg", b"<?xml")))
        self.assertIsNone(source_logo_bytes("../../secrets"))


if __name__ == "__main__":
    unittest.main()
