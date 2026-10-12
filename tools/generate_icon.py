"""Retrieve the official Control4 brand assets used by Home Assistant."""

from __future__ import annotations

import hashlib
from pathlib import Path
from urllib.request import urlopen


BRANDS_COMMIT = "ba54a5eb87172d6f20b74741e4a96335f94cbd1c"
SOURCE = (
    "https://raw.githubusercontent.com/home-assistant/brands/"
    f"{BRANDS_COMMIT}/core_integrations/control4"
)
OUTPUT = (
    Path(__file__).resolve().parents[1]
    / "custom_components/control4_advanced/brand"
)
ASSETS = {
    "icon.png": "077f6a56334c14a75484283241e2f1c17b6c827fa58d15e4e51c3f138bd057e7",
    "icon@2x.png": "d777a8a6844c43f5bbb11ffb29e3ac419a5d6619f0a90ff582ab4fd278b54d8a",
    "logo.png": "790ba1a0a14dfabc31f5ef3065e68853d4afb6f10ed0f76f445207ef4a2d1586",
    "logo@2x.png": "75886d3ba57540fb9e7799c6a866ede215339533af796ca2c58fd045f2fd29a7",
}


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for filename, expected_digest in ASSETS.items():
        with urlopen(f"{SOURCE}/{filename}", timeout=30) as response:
            data = response.read()
        digest = hashlib.sha256(data).hexdigest()
        if digest != expected_digest:
            raise RuntimeError(f"unexpected digest for {filename}: {digest}")
        (OUTPUT / filename).write_bytes(data)


if __name__ == "__main__":
    main()
