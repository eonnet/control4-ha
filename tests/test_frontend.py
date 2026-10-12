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
    FRONTEND_URL_PATH,
    _async_register_lovelace_resource,
    async_register_frontend,
)


class FrontendTests(unittest.IsolatedAsyncioTestCase):
    def _modules(self):
        frontend = ModuleType("homeassistant.components.frontend")
        frontend.add_extra_js_url = Mock()
        http = ModuleType("homeassistant.components.http")
        http.StaticPathConfig = lambda url, path, cache: SimpleNamespace(
            url_path=url, path=path, cache_headers=cache
        )
        components = ModuleType("homeassistant.components")
        components.__path__ = []
        lovelace = ModuleType("homeassistant.components.lovelace")
        lovelace.LOVELACE_DATA = "lovelace"
        homeassistant = ModuleType("homeassistant")
        homeassistant.__path__ = []
        return homeassistant, components, frontend, http, lovelace

    async def test_registers_packaged_card_as_lovelace_resource(self) -> None:
        homeassistant, components, frontend, http, lovelace = self._modules()
        resources = SimpleNamespace(
            async_get_info=AsyncMock(return_value={"resources": 0}),
            async_items=Mock(return_value=[]),
            async_create_item=AsyncMock(),
            async_update_item=AsyncMock(),
        )
        hass = SimpleNamespace(
            data={"lovelace": SimpleNamespace(resources=resources)},
            http=SimpleNamespace(async_register_static_paths=AsyncMock()),
        )

        with patch.dict(
            sys.modules,
            {
                "homeassistant": homeassistant,
                "homeassistant.components": components,
                "homeassistant.components.frontend": frontend,
                "homeassistant.components.http": http,
                "homeassistant.components.lovelace": lovelace,
            },
        ):
            await async_register_frontend(hass)
            await async_register_frontend(hass)

        hass.http.async_register_static_paths.assert_awaited_once()
        paths = hass.http.async_register_static_paths.await_args.args[0]
        self.assertEqual(len(paths), 1)
        self.assertEqual(paths[0].url_path, FRONTEND_URL_PATH)
        self.assertEqual(paths[0].path, str(FRONTEND_FILE))
        self.assertFalse(paths[0].cache_headers)
        resources.async_get_info.assert_awaited_once()
        resources.async_create_item.assert_awaited_once_with(
            {"res_type": "module", "url": FRONTEND_URL}
        )
        frontend.add_extra_js_url.assert_not_called()

    async def test_yaml_resource_mode_uses_extra_module_fallback(self) -> None:
        homeassistant, components, frontend, http, lovelace = self._modules()
        hass = SimpleNamespace(
            data={"lovelace": SimpleNamespace(resources=SimpleNamespace())},
            http=SimpleNamespace(async_register_static_paths=AsyncMock()),
        )
        with patch.dict(
            sys.modules,
            {
                "homeassistant": homeassistant,
                "homeassistant.components": components,
                "homeassistant.components.frontend": frontend,
                "homeassistant.components.http": http,
                "homeassistant.components.lovelace": lovelace,
            },
        ):
            await async_register_frontend(hass)

        frontend.add_extra_js_url.assert_called_once_with(hass, FRONTEND_URL)

    async def test_existing_resource_is_updated_for_cache_busting(self) -> None:
        homeassistant, components, _, _, lovelace = self._modules()
        resources = SimpleNamespace(
            async_get_info=AsyncMock(return_value={"resources": 1}),
            async_items=Mock(
                return_value=[
                    {
                        "id": "control4-card",
                        "type": "module",
                        "url": FRONTEND_URL_PATH,
                    }
                ]
            ),
            async_create_item=AsyncMock(),
            async_update_item=AsyncMock(),
        )
        hass = SimpleNamespace(
            data={"lovelace": SimpleNamespace(resources=resources)}
        )
        with patch.dict(
            sys.modules,
            {
                "homeassistant": homeassistant,
                "homeassistant.components": components,
                "homeassistant.components.lovelace": lovelace,
            },
        ):
            self.assertTrue(await _async_register_lovelace_resource(hass))

        resources.async_update_item.assert_awaited_once_with(
            "control4-card", {"res_type": "module", "url": FRONTEND_URL}
        )
        resources.async_create_item.assert_not_awaited()

    def test_card_asset_contains_group_and_individual_volume_controls(self) -> None:
        asset = Path(FRONTEND_FILE)
        self.assertTrue(asset.is_file())
        javascript = asset.read_text(encoding="utf-8")
        self.assertIn('const CARD_TAG = "control4-media-group-card"', javascript)
        self.assertIn('const PLAYER_CARD_TAG = "control4-media-player-card"', javascript)
        self.assertIn('await helpers.createCardElement(this._nativeConfig())', javascript)
        self.assertIn('classList?.contains("join-media")', javascript)
        self.assertIn('element?.id === "grouping-button"', javascript)
        self.assertIn('element?.localName === "more-info-media_player"', javascript)
        self.assertIn('element?.localName === "ha-more-info-dialog"', javascript)
        self.assertIn('<dialog class="modal-shell"', javascript)
        self.assertIn('modal.addEventListener("cancel"', javascript)
        self.assertIn("modal.showModal();", javascript)
        self.assertNotIn("moreInfoDialog.closeDialog();", javascript)
        self.assertIn("openControl4GroupDialog(hass, entityId);", javascript)
        self.assertIn("registerMoreInfoGrouping();", javascript)
        self.assertIn('"media_player",\n        "join"', javascript)
        self.assertIn('"media_player",\n        "volume_set"', javascript)
        self.assertIn("control4_room_id", javascript)
        self.assertIn("grid-template-rows: auto auto minmax(0, 1fr) auto", javascript)
        self.assertIn("position: fixed;\n        z-index: 10000", javascript)
        self.assertIn("bottom: 0;", javascript)
        self.assertIn(
            "padding-bottom: calc(96px + env(safe-area-inset-bottom, 0px))",
            javascript,
        )
        self.assertIn(
            "registerCard();\nregisterMoreInfoGrouping();\n(async () =>",
            javascript,
        )


if __name__ == "__main__":
    unittest.main()
