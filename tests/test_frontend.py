"""Offline checks for the packaged Control4 dashboard card."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import struct
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
        self.assertIn('const FEATURE_VOLUME_MUTE = 8', javascript)
        self.assertIn('data-mute-id="${escapeHtml(state.entity_id)}"', javascript)
        self.assertIn('"media_player",\n        "volume_mute"', javascript)
        self.assertIn("{ is_volume_muted: muted }", javascript)
        self.assertIn('muted ? "mdi:volume-off" : "mdi:volume-medium"', javascript)
        self.assertIn("control4_room_id", javascript)
        self.assertIn(
            "grid-template-rows: auto auto auto minmax(0, 1fr) auto",
            javascript,
        )
        self.assertIn("position: fixed;\n        z-index: 10000", javascript)
        self.assertIn("bottom: 0;", javascript)
        self.assertIn(
            "padding-bottom: calc(96px + env(safe-area-inset-bottom, 0px))",
            javascript,
        )

    def test_card_asset_contains_relative_group_master_controls(self) -> None:
        javascript = Path(FRONTEND_FILE).read_text(encoding="utf-8")
        self.assertIn('<section class="group-controls"', javascript)
        self.assertIn('data-group-volume', javascript)
        self.assertIn('class="group-mute-button', javascript)
        self.assertIn('const delta = requested - baseline.average', javascript)
        self.assertIn('requested === 0 || requested === 100', javascript)
        self.assertIn('this._setDisplayedVolume(entityId, adjusted)', javascript)
        self.assertIn('async _setGroupMute()', javascript)
        self.assertIn('const muted = !states.every(', javascript)
        self.assertIn('`${selected.length} selected`', javascript)
        self.assertIn(
            "registerCard();\nregisterMoreInfoGrouping();\n(async () =>",
            javascript,
        )

    def test_manifest_declares_hassfest_runtime_dependencies(self) -> None:
        manifest = json.loads(
            (Path(FRONTEND_FILE).parent.parent / "manifest.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(manifest["version"], "0.1.47")
        self.assertIn("http", manifest["dependencies"])
        self.assertEqual(manifest["requirements"], ["pyControl4>=2.0.2"])
        self.assertTrue(FRONTEND_URL.endswith("?v=0.1.47"))

    def test_official_control4_brand_assets_are_packaged(self) -> None:
        brand = Path(FRONTEND_FILE).parent.parent / "brand"
        expected = {
            "icon.png": (
                (256, 256),
                "077f6a56334c14a75484283241e2f1c17b6c827fa58d15e4e51c3f138bd057e7",
            ),
            "icon@2x.png": (
                (512, 512),
                "d777a8a6844c43f5bbb11ffb29e3ac419a5d6619f0a90ff582ab4fd278b54d8a",
            ),
            "logo.png": (
                (1169, 256),
                "790ba1a0a14dfabc31f5ef3065e68853d4afb6f10ed0f76f445207ef4a2d1586",
            ),
            "logo@2x.png": (
                (2337, 512),
                "75886d3ba57540fb9e7799c6a866ede215339533af796ca2c58fd045f2fd29a7",
            ),
        }
        for filename, (dimensions, digest) in expected.items():
            data = (brand / filename).read_bytes()
            self.assertEqual(struct.unpack(">II", data[16:24]), dimensions)
            self.assertEqual(hashlib.sha256(data).hexdigest(), digest)


if __name__ == "__main__":
    unittest.main()
