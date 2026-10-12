"""Offline tests for integration-wide entity ID recreation."""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from custom_components.control4_advanced.entity_ids import (
    config_entry_entity_id_recreation_plan,
    entity_id_recreation_plan,
    recreate_config_entry_entity_ids,
)


class FakeRegistry:
    """Small stand-in for Home Assistant's entity registry."""

    def __init__(self, generated: dict[str, str]) -> None:
        self.generated = generated
        self.reservations: list[tuple[str, set[str]]] = []
        self.async_update_entity = Mock()

    def async_regenerate_entity_id(self, entry, *, reserved_entity_ids):
        self.reservations.append((entry.entity_id, set(reserved_entity_ids)))
        return self.generated[entry.entity_id]


def entry(entity_id: str, *, disabled: bool = False):
    return SimpleNamespace(entity_id=entity_id, disabled_by="user" if disabled else None)


class EntityIdRecreationTests(unittest.TestCase):
    def test_plan_is_stable_and_reserves_every_generated_id(self) -> None:
        registry = FakeRegistry({
            "button.kitchen_play": "button.kitchen_play",
            "light.old_kitchen": "light.kitchen",
            "number.old_volume": "number.kitchen_volume",
        })
        changes = entity_id_recreation_plan(registry, [
            entry("number.old_volume"),
            entry("button.kitchen_play"),
            entry("light.old_kitchen"),
        ])
        self.assertEqual(changes, (
            ("light.old_kitchen", "light.kitchen"),
            ("number.old_volume", "number.kitchen_volume"),
        ))
        self.assertEqual(registry.reservations, [
            ("button.kitchen_play", set()),
            ("light.old_kitchen", {"button.kitchen_play"}),
            (
                "number.old_volume",
                {"button.kitchen_play", "light.kitchen"},
            ),
        ])

    def test_config_entry_plan_includes_disabled_entities(self) -> None:
        registry = FakeRegistry({
            "light.old": "light.new",
            "select.disabled_old": "select.disabled_new",
        })
        entries = [entry("light.old"), entry("select.disabled_old", disabled=True)]
        helpers = ModuleType("homeassistant.helpers")
        helpers.entity_registry = SimpleNamespace(
            async_get=lambda hass: registry,
            async_entries_for_config_entry=lambda candidate, entry_id: entries,
        )
        homeassistant = ModuleType("homeassistant")
        homeassistant.helpers = helpers
        with patch.dict(sys.modules, {
            "homeassistant": homeassistant,
            "homeassistant.helpers": helpers,
        }):
            changes = config_entry_entity_id_recreation_plan(object(), "entry")
        self.assertEqual(changes, (
            ("light.old", "light.new"),
            ("select.disabled_old", "select.disabled_new"),
        ))

    def test_apply_renames_only_ids_that_change(self) -> None:
        registry = FakeRegistry({
            "light.current": "light.current",
            "media_player.old_kitchen": "media_player.kitchen",
        })
        entries = [entry("light.current"), entry("media_player.old_kitchen")]
        helpers = ModuleType("homeassistant.helpers")
        helpers.entity_registry = SimpleNamespace(
            async_get=lambda hass: registry,
            async_entries_for_config_entry=lambda candidate, entry_id: entries,
        )
        homeassistant = ModuleType("homeassistant")
        homeassistant.helpers = helpers
        with patch.dict(sys.modules, {
            "homeassistant": homeassistant,
            "homeassistant.helpers": helpers,
        }):
            changes = recreate_config_entry_entity_ids(object(), "entry")
        self.assertEqual(changes, (
            ("media_player.old_kitchen", "media_player.kitchen"),
        ))
        registry.async_update_entity.assert_called_once_with(
            "media_player.old_kitchen", new_entity_id="media_player.kitchen"
        )


if __name__ == "__main__":
    unittest.main()
