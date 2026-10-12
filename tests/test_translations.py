"""Keep runtime translations aligned with the integration source strings."""

from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).parents[1]
INTEGRATION = ROOT / "custom_components" / "control4_advanced"


class TranslationTests(unittest.TestCase):
    def test_english_runtime_translations_match_source_strings(self):
        strings = json.loads((INTEGRATION / "strings.json").read_text())
        translations = json.loads(
            (INTEGRATION / "translations" / "en.json").read_text()
        )
        self.assertEqual(translations, strings)


if __name__ == "__main__":
    unittest.main()
