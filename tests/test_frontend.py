"""Offline checks for the packaged Control4 dashboard card."""

from __future__ import annotations

from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from custom_components.control4_advanced.frontend import (
    FRONTEND_FILE,
    FRONTEND_URL,
    async_register_frontend,
)


class FrontendTests(unittest.IsolatedAsyncioTestCase):
    async def test_registers_packaged_card_as_no_cache_module(self) -> None:
        frontend = ModuleType("homeassistant.components.frontend")
        frontend.add_extra_js_url = Mock()
        http = ModuleType("homeassistant.components.http")
        http.StaticPathConfig = lambda url, path, cache: SimpleNamespace(
            url_path=url, path=path, cache_headers=cache
        )
        components = ModuleType("homeassistant.components")
        components.__path__ = []
        homeassistant = ModuleType("homeassistant")
        homeassistant.__path__ = []
        hass = SimpleNamespace(
            http=SimpleNamespace(async_register_static_paths=AsyncMock())
        )

        with patch.dict(
            sys.modules,
            {
                "homeassistant": homeassistant,
                "homeassistant.components": components,
                "homeassistant.components.frontend": frontend,
                "homeassistant.components.http": http,
            },
        ):
            await async_register_frontend(hass)

        hass.http.async_register_static_paths.assert_awaited_once()
        paths = hass.http.async_register_static_paths.await_args.args[0]
        self.assertEqual(len(paths), 1)
        self.assertEqual(paths[0].url_path, FRONTEND_URL)
        self.assertEqual(paths[0].path, str(FRONTEND_FILE))
        self.assertFalse(paths[0].cache_headers)
        frontend.add_extra_js_url.assert_called_once_with(hass, FRONTEND_URL)

    def test_card_asset_contains_group_and_individual_volume_controls(self) -> None:
        asset = Path(FRONTEND_FILE)
        self.assertTrue(asset.is_file())
        javascript = asset.read_text(encoding="utf-8")
        self.assertIn('const CARD_TAG = "control4-media-group-card"', javascript)
        self.assertIn('"media_player",\n        "join"', javascript)
        self.assertIn('"media_player",\n        "volume_set"', javascript)
        self.assertIn("control4_room_id", javascript)


if __name__ == "__main__":
    unittest.main()
