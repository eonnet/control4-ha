"""Offline shape checks for the guided media options flow."""

from __future__ import annotations

import asyncio
import importlib
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch


class FakeFlowBase:
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__()

    def async_abort(self, *, reason, description_placeholders=None):
        return {
            "type": "abort",
            "reason": reason,
            "description_placeholders": description_placeholders,
        }

    def async_show_progress(self, *, progress_action, progress_task, **kwargs):
        return {
            "type": "progress",
            "progress_action": progress_action,
            "progress_task": progress_task,
            **kwargs,
        }

    def async_show_progress_done(self, *, next_step_id):
        return {"type": "progress_done", "next_step_id": next_step_id}

    def async_create_entry(self, *, data, **kwargs):
        return {"type": "create_entry", "data": data, **kwargs}


def _ha_modules():
    homeassistant = ModuleType("homeassistant")
    homeassistant.__path__ = []
    config_entries = ModuleType("homeassistant.config_entries")
    config_entries.ConfigEntry = type("ConfigEntry", (), {})
    config_entries.ConfigFlow = FakeFlowBase
    config_entries.OptionsFlowWithReload = FakeFlowBase
    const = ModuleType("homeassistant.const")
    const.CONF_HOST = "host"
    const.CONF_PASSWORD = "password"
    const.CONF_USERNAME = "username"
    core = ModuleType("homeassistant.core")
    core.callback = lambda function: function
    helpers = ModuleType("homeassistant.helpers")
    helpers.__path__ = []
    selector = ModuleType("homeassistant.helpers.selector")
    selector.TextSelector = lambda config: config
    selector.TextSelectorConfig = lambda **kwargs: kwargs
    voluptuous = ModuleType("voluptuous")
    voluptuous.Invalid = type("Invalid", (ValueError,), {})
    voluptuous.Schema = lambda value: value
    voluptuous.Required = lambda key, **kwargs: key
    voluptuous.Optional = lambda key, **kwargs: key
    voluptuous.All = lambda *validators: validators[0]
    voluptuous.Length = lambda **kwargs: str
    return {
        "voluptuous": voluptuous,
        "homeassistant": homeassistant,
        "homeassistant.config_entries": config_entries,
        "homeassistant.const": const,
        "homeassistant.core": core,
        "homeassistant.helpers": helpers,
        "homeassistant.helpers.selector": selector,
    }


class MediaCaptureOptionsFlowTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.module_patcher = patch.dict(sys.modules, _ha_modules())
        cls.module_patcher.start()
        cls.flow_module = importlib.import_module(
            "custom_components.control4_advanced.config_flow"
        )

    @classmethod
    def tearDownClass(cls):
        sys.modules.pop("custom_components.control4_advanced.config_flow", None)
        cls.module_patcher.stop()

    def _flow(self):
        flow = self.flow_module.Control4OptionsFlow()
        flow.hass = SimpleNamespace(async_create_task=asyncio.create_task)
        flow.config_entry = SimpleNamespace(
            options={"tunein_favorites": "TuneIn | Existing | Station/s123"},
            runtime_data=SimpleNamespace(
                media_sources={},
                transport=SimpleNamespace(inventory=SimpleNamespace(items={})),
            ),
        )
        return flow

    async def test_tidal_progress_saves_and_preserves_other_options(self) -> None:
        flow = self._flow()
        capture_target = SimpleNamespace(source_name="Miki")
        captured = self.flow_module.CapturedMediaItem(
            1417,
            "Miki",
            "Rock",
            "11111111-2222-4333-8444-555555555555",
        )

        async def retrieve(*args, **kwargs):
            await asyncio.sleep(0)
            return (captured,)

        with (
            patch.object(
                self.flow_module,
                "media_capture_targets",
                return_value=(capture_target,),
            ),
            patch.object(self.flow_module, "capture_media_items", retrieve),
        ):
            first = await flow.async_step_retrieve_tidal()
            self.assertEqual(first["type"], "progress")
            self.assertEqual(first["progress_action"], "retrieve_tidal")
            self.assertEqual(
                first["description_placeholders"], {"sources": "Miki"}
            )
            await first["progress_task"]
            second = await flow.async_step_retrieve_tidal()
            self.assertEqual(
                second,
                {"type": "progress_done", "next_step_id": "retrieve_tidal_finish"},
            )
            result = await flow.async_step_retrieve_tidal_finish()

        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(
            result["data"]["tidal_playlists"],
            "Miki | Rock | 11111111-2222-4333-8444-555555555555",
        )
        self.assertEqual(
            result["data"]["tunein_favorites"],
            "TuneIn | Existing | Station/s123",
        )

    async def test_missing_source_aborts_before_starting_task(self) -> None:
        flow = self._flow()
        with patch.object(
            self.flow_module, "media_capture_targets", return_value=()
        ):
            result = await flow.async_step_retrieve_tunein()
        self.assertEqual(result["type"], "abort")
        self.assertEqual(result["reason"], "no_tunein_sources")


if __name__ == "__main__":
    unittest.main()
