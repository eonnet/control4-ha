"""Offline checks for initial setup and the guided media options flow."""

from __future__ import annotations

import asyncio
import importlib
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch


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

    async def async_set_unique_id(self, unique_id):
        self.unique_id = unique_id

    def _abort_if_unique_id_configured(self):
        pass

    def async_show_form(self, *, step_id, data_schema, errors):
        return {"type": "form", "step_id": step_id, "errors": errors}


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


class ConfigFlowTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_setup_form_accepts_media_only_inventory(self):
        items = [
            {"id": 22, "typeName": "room", "proxy": "roomdevice", "name": "Listening Room"},
            {"id": 54, "typeName": "device", "proxy": "media_service", "name": "TIDAL"},
            {"id": 70, "typeName": "device", "proxy": "aswitch", "name": "Amplifier"},
        ]
        listen = {
            "room_id": 22,
            "type": "listen",
            "sources": {"source": [{"id": 54, "type": "DIGITAL_AUDIO_SERVER"}]},
        }
        invalid = {**listen, "sources": {"source": [{"id": 54, "type": []}]}}
        off = [
            {"varName": "POWER_STATE", "value": 0},
            {"varName": "CURRENT_VOLUME_DEVICE_ID", "value": 0},
        ]
        active = [
            {"varName": "POWER_STATE", "value": 1},
            {"varName": "CURRENT_VOLUME_DEVICE_ID", "value": 70},
            {"varName": "CURRENT_VOLUME", "value": 31},
            {"varName": "IS_MUTED", "value": 0},
        ]
        credentials = {"host": "director.example.test", "username": "test", "password": "test"}
        for name, experiences, variables, result_type, state_reads in (
            ("off_with_tidal", [listen], off, "create_entry", 0),
            ("active_with_volume_binding", [], active, "create_entry", 1),
            ("room_without_media", [invalid], off, "form", 1),
        ):
            with self.subTest(name=name):
                rest = SimpleNamespace(
                    get_items=AsyncMock(return_value=items),
                    get_ui_configuration=AsyncMock(return_value={"experiences": experiences}),
                    get_variables=AsyncMock(return_value=variables),
                )
                session = AsyncMock()
                flow = self.flow_module.Control4ConfigFlow()
                with (
                    patch.object(self.flow_module.aiohttp, "TCPConnector", return_value=object()),
                    patch.object(self.flow_module.aiohttp, "ClientSession", return_value=session),
                    patch.object(self.flow_module, "DirectorRestClient", return_value=rest),
                ):
                    result = await flow.async_step_user(credentials)
                self.assertEqual(result["type"], result_type)
                self.assertEqual(rest.get_variables.await_count, state_reads)
                if result_type == "form":
                    self.assertEqual(result["errors"], {"base": "no_devices"})
                else:
                    self.assertEqual(result["data"], credentials)

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
