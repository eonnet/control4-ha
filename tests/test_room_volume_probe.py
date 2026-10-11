from __future__ import annotations

import unittest

from control4_transport.events import NormalizedEvent
from tools.room_volume_probe import safe_event_record


class RoomVolumeProbeTests(unittest.TestCase):
    def test_projection_never_exposes_raw_media_or_tokens(self) -> None:
        event = NormalizedEvent(
            device_id=623, source="websocket", event_type="OnDataToUI",
            changes={"output_id": 4000, "output_volume_percent": 41.0,
                     "output_is_muted": False}, received_at="test",
            authoritative=True,
            raw={"Authorization": "private-token", "Title": "private-title"},
        )
        result = safe_event_record(event, 22, 623, {"volume_percent": 41.0})
        self.assertEqual(result, ("BOUND_DEVICE_PUSH", {
            "device_id": 623, "output_id": 4000,
            "output_volume_percent": 41.0, "output_is_muted": False,
        }))
        self.assertNotIn("private", str(result))
        self.assertIsNone(safe_event_record(event, 22, 999, {}))


if __name__ == "__main__":
    unittest.main()
