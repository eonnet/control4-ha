"""Offline safety checks for stale Home Assistant registry reconciliation."""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from custom_components.control4_advanced.registry_cleanup import (
    remove_stale_registry_entries, stale_registry_ids,
    tidal_playlist_registry_migration,
)


def entity(unique_id: str, *, entry_id: str = "ours", platform: str = "control4_advanced"):
    return SimpleNamespace(
        entity_id=f"light.{unique_id}", config_entry_id=entry_id,
        platform=platform, unique_id=unique_id,
    )


def device(unique_id: str, *, entry_id: str = "ours", extra_identifier: bool = False):
    identifiers = {("control4_advanced", unique_id)}
    if extra_identifier:
        identifiers.add(("another", "identifier"))
    return SimpleNamespace(
        id=f"registry_{unique_id}", config_entry_id=entry_id,
        identifiers=identifiers,
    )


class StaleRegistryTests(unittest.TestCase):
    def test_only_owned_ids_missing_from_both_inventory_reads_are_removed(self) -> None:
        entities = [
            entity("192.168.0.24_11"),
            entity("192.168.0.24_12"),
            entity("192.168.0.24_13"),
            entity("192.168.0.25_14"),
            entity("192.168.0.24_15", entry_id="other"),
            entity("192.168.0.24_16", platform="other_platform"),
        ]
        devices = [
            device("192.168.0.24_11"),
            device("192.168.0.24_12"),
            device("192.168.0.24_14", extra_identifier=True),
        ]
        stale_entities, stale_devices = stale_registry_ids(
            entities, devices, config_entry_id="ours", host="192.168.0.24",
            first_inventory_ids={12, 99}, second_inventory_ids={13, 99},
        )
        self.assertEqual(stale_entities, ["light.192.168.0.24_11"])
        self.assertEqual(stale_devices, ["registry_192.168.0.24_11"])

    def test_empty_or_untrusted_inventory_never_deletes(self) -> None:
        for first, second in ((set(), {99}), ({99}, set())):
            with self.subTest(first=first, second=second):
                self.assertEqual(
                    stale_registry_ids(
                        [entity("192.168.0.24_11")], [device("192.168.0.24_11")],
                        config_entry_id="ours", host="192.168.0.24",
                        first_inventory_ids=first, second_inventory_ids=second,
                    ),
                    ([], []),
                )

    def test_invalid_unique_ids_are_preserved(self) -> None:
        entities = [entity(value) for value in (
            "192.168.0.24_0", "192.168.0.24_abc", "192.168.0.24_-7",
            "192.168.0.24_１２", "another_11",
        )]
        self.assertEqual(
            stale_registry_ids(
                entities, [], config_entry_id="ours", host="192.168.0.24",
                first_inventory_ids={99}, second_inventory_ids={99},
            ),
            ([], []),
        )

    def test_hold_select_is_cleaned_with_its_director_device(self) -> None:
        entities = [
            entity("192.168.0.24_2646"),
            entity("192.168.0.24_2646_hold_mode"),
            entity("192.168.0.24_2811_hold_mode"),
            entity("192.168.0.24_2646_unrelated"),
        ]
        stale_entities, stale_devices = stale_registry_ids(
            entities, [device("192.168.0.24_2646")],
            config_entry_id="ours", host="192.168.0.24",
            first_inventory_ids={2811, 99}, second_inventory_ids={2811, 99},
        )
        self.assertEqual(stale_entities, [
            "light.192.168.0.24_2646",
            "light.192.168.0.24_2646_hold_mode",
        ])
        self.assertEqual(stale_devices, ["registry_192.168.0.24_2646"])

    def test_playback_buttons_are_cleaned_only_with_their_room(self) -> None:
        entities = [
            entity("192.168.0.24_22_media_play"),
            entity("192.168.0.24_22_media_pause"),
            entity("192.168.0.24_22_media_stop"),
            entity("192.168.0.24_22_media_skip_rev"),
            entity("192.168.0.24_22_media_skip_fwd"),
            entity("192.168.0.24_23_media_play"),
            entity("192.168.0.24_22_media_skip"),
        ]
        stale_entities, _ = stale_registry_ids(
            entities, [], config_entry_id="ours", host="192.168.0.24",
            first_inventory_ids={23, 99}, second_inventory_ids={23, 99},
        )
        self.assertEqual(stale_entities, [
            "light.192.168.0.24_22_media_play",
            "light.192.168.0.24_22_media_pause",
            "light.192.168.0.24_22_media_stop",
            "light.192.168.0.24_22_media_skip_rev",
            "light.192.168.0.24_22_media_skip_fwd",
        ])

    def test_redundant_room_volume_numbers_are_removed_after_upgrade(self) -> None:
        entities = [
            entity("192.168.0.24_22_room_volume"),
            entity("192.168.0.24_23_room_volume"),
        ]
        stale_entities, _ = stale_registry_ids(
            entities, [], config_entry_id="ours", host="192.168.0.24",
            first_inventory_ids={23, 99}, second_inventory_ids={23, 99},
        )
        self.assertEqual(
            stale_entities,
            [
                "light.192.168.0.24_22_room_volume",
                "light.192.168.0.24_23_room_volume",
            ],
        )

    def test_old_per_account_playlist_entities_are_removed(self) -> None:
        entities = [
            entity("192.168.0.24_22_tidal_playlist_93"),
            entity("192.168.0.24_22_tidal_playlist_1365"),
            entity("192.168.0.24_22_tidal_playlist"),
            entity("192.168.0.24_22_tidal_account"),
            entity("192.168.0.25_22_tidal_playlist_93"),
        ]
        stale_entities, _ = stale_registry_ids(
            entities, [], config_entry_id="ours", host="192.168.0.24",
            first_inventory_ids={22, 99}, second_inventory_ids={22, 99},
        )
        self.assertEqual(stale_entities, [
            "light.192.168.0.24_22_tidal_playlist_93",
            "light.192.168.0.24_22_tidal_playlist_1365",
        ])

    def test_playlist_registry_migration_preserves_preferred_account(self) -> None:
        entries = [
            entity("192.168.0.24_22_tidal_playlist_93"),
            entity("192.168.0.24_22_tidal_playlist_1365"),
            entity("192.168.0.24_23_tidal_playlist_1363"),
            entity("192.168.0.25_22_tidal_playlist_93"),
            entity("192.168.0.24_24_tidal_playlist_93", entry_id="other"),
        ]
        updates, removals = tidal_playlist_registry_migration(
            entries,
            config_entry_id="ours",
            host="192.168.0.24",
            preferred_protocol_ids={22: 93, 23: 1363},
        )
        self.assertEqual(updates, [
            (
                "light.192.168.0.24_22_tidal_playlist_93",
                "192.168.0.24_22_tidal_playlist",
            ),
            (
                "light.192.168.0.24_23_tidal_playlist_1363",
                "192.168.0.24_23_tidal_playlist",
            ),
        ])
        self.assertEqual(removals, [
            "light.192.168.0.24_22_tidal_playlist_1365",
        ])

    def test_playlist_registry_migration_does_not_replace_new_entity(self) -> None:
        entries = [
            entity("192.168.0.24_22_tidal_playlist_93"),
            entity("192.168.0.24_22_tidal_playlist"),
        ]
        updates, removals = tidal_playlist_registry_migration(
            entries,
            config_entry_id="ours",
            host="192.168.0.24",
            preferred_protocol_ids={22: 93},
        )
        self.assertEqual(updates, [])
        self.assertEqual(removals, [
            "light.192.168.0.24_22_tidal_playlist_93",
        ])

    def test_new_tidal_selects_are_cleaned_only_with_their_room(self) -> None:
        entities = [
            entity("192.168.0.24_22_tidal_account"),
            entity("192.168.0.24_22_tidal_playlist"),
            entity("192.168.0.24_23_tidal_account"),
        ]
        stale_entities, _ = stale_registry_ids(
            entities, [], config_entry_id="ours", host="192.168.0.24",
            first_inventory_ids={23, 99}, second_inventory_ids={23, 99},
        )
        self.assertEqual(stale_entities, [
            "light.192.168.0.24_22_tidal_account",
            "light.192.168.0.24_22_tidal_playlist",
        ])

    def test_cleanup_calls_registry_only_for_confirmed_stale_ids(self) -> None:
        entity_registry = SimpleNamespace(async_remove=Mock())
        device_registry = SimpleNamespace(
            async_get=Mock(return_value=object()), async_remove_device=Mock(),
        )
        helpers = ModuleType("homeassistant.helpers")
        helpers.entity_registry = SimpleNamespace(
            async_get=lambda hass: entity_registry,
            async_entries_for_config_entry=lambda registry, entry_id: [
                entity("192.168.0.24_11"), entity("192.168.0.24_12"),
            ],
        )
        helpers.device_registry = SimpleNamespace(
            async_get=lambda hass: device_registry,
            async_entries_for_config_entry=lambda registry, entry_id: [
                device("192.168.0.24_11"), device("192.168.0.24_12"),
            ],
        )
        homeassistant = ModuleType("homeassistant")
        homeassistant.helpers = helpers
        with patch.dict(sys.modules, {
            "homeassistant": homeassistant, "homeassistant.helpers": helpers,
        }):
            remove_stale_registry_entries(
                object(), config_entry_id="ours", host="192.168.0.24",
                first_inventory_ids={12, 99}, second_inventory_ids={12, 99},
            )
        entity_registry.async_remove.assert_called_once_with("light.192.168.0.24_11")
        device_registry.async_remove_device.assert_called_once_with("registry_192.168.0.24_11")


if __name__ == "__main__":
    unittest.main()
