"""Offline tests for conservative Watch/Listen source discovery."""

from __future__ import annotations

import unittest

from custom_components.control4_advanced.transport.sources import room_sources


class RoomSourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.inventory = {
            346: {"id": 346, "name": "Plex", "typeName": "device", "proxy": "media_player"},
            1273: {"id": 1273, "name": "Xplore TV", "typeName": "device", "proxy": "media_player"},
            623: {"id": 623, "name": "Wiim", "typeName": "device", "proxy": "aswitch"},
            473: {"id": 473, "name": "Nintendo", "typeName": "device", "proxy": "uibutton"},
        }

    def test_room_filter_inventory_validation_and_duplicate_labels(self) -> None:
        configuration = {"experiences": [
            {"room_id": 22, "type": "watch", "username": "not retained", "sources": {"source": [
                {"id": 346, "type": "RF_MINI_APP", "name": "Ignored UI name"},
                {"id": 1273, "type": "RF_MINI_APP"},
                {"id": 346, "type": "RF_MINI_APP"},
                {"id": 473, "type": "UIButton"},
                {"id": 9999, "type": "HDMI"},
                {"id": True, "type": "HDMI"},
            ]}},
            {"room_id": 22, "type": "listen", "sources": {"source": [
                {"id": 623, "type": "AUDIO_SELECTION"},
                {"id": 4294966299, "type": "SPECIAL_AUDIO"},
            ]}},
            {"room_id": 23, "type": "watch", "sources": {"source": [
                {"id": 346, "type": "RF_MINI_APP"},
            ]}},
        ]}
        sources = room_sources(configuration, 22, self.inventory)
        self.assertEqual([(s.device_id, s.experience, s.label) for s in sources], [
            (346, "watch", "Watch: Plex"),
            (1273, "watch", "Watch: Xplore TV"),
            (623, "listen", "Listen: Wiim"),
        ])
        self.assertNotIn("not retained", repr(sources))
        self.assertNotIn("Ignored UI name", repr(sources))

    def test_singleton_source_and_duplicate_inventory_names(self) -> None:
        self.inventory[1273]["name"] = "Plex"
        configuration = {"experiences": [{"room_id": 22, "type": "watch", "sources": {
            "source": [{"id": 346, "type": "RF_MINI_APP"},
                       {"id": 1273, "type": "RF_MINI_APP"}],
        }}, {"room_id": 22, "type": "listen", "sources": {
            "source": {"id": 623, "type": "AUDIO_SELECTION"},
        }}]}
        sources = room_sources(configuration, 22, self.inventory)
        self.assertEqual([source.label for source in sources], [
            "Watch: Plex (346)", "Watch: Plex (1273)", "Listen: Wiim",
        ])

    def test_malformed_configuration_never_expands_source_set(self) -> None:
        for payload in (None, [], {}, {"experiences": {}}, {"experiences": [None, {}]}):
            self.assertEqual(room_sources(payload, 22, self.inventory), ())
        self.assertEqual(room_sources({"experiences": []}, True, self.inventory), ())


if __name__ == "__main__":
    unittest.main()
