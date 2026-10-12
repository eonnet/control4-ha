"""Local, allowlisted logos for Control4 media-browser sources."""

from __future__ import annotations

from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
import re
from typing import Any, Literal


LogoKey = Literal[
    "appletv",
    "hbomax",
    "listen",
    "netflix",
    "plex",
    "sony",
    "spotify",
    "tidal",
    "tunein",
    "watch",
    "wiim",
    "xiaomi",
    "xploretv",
    "youtube",
]

SOURCE_LOGO_TYPE = "control4_source_logo"

_LOGO_KEYS = frozenset(
    {
        "appletv",
        "hbomax",
        "listen",
        "netflix",
        "plex",
        "sony",
        "spotify",
        "tidal",
        "tunein",
        "watch",
        "wiim",
        "xiaomi",
        "xploretv",
        "youtube",
    }
)
_LOGO_DIRECTORY = Path(__file__).with_name("source_logos")


def _normalized(value: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value.casefold()).split())


def source_logo_key(
    name: str,
    experience: Literal["watch", "listen"],
    inventory_item: Mapping[str, Any] | None = None,
) -> LogoKey:
    """Return a local logo key using only inventory and validated source data."""
    details = [name]
    if isinstance(inventory_item, Mapping):
        for key in (
            "name",
            "filename",
            "protocolFilename",
            "model",
            "manufacturer",
        ):
            value = inventory_item.get(key)
            if isinstance(value, str):
                details.append(value)
    fingerprint = _normalized(" ".join(details))
    compact = fingerprint.replace(" ", "")
    tokens = set(fingerprint.split())

    if "netflix" in tokens or "netflix" in compact:
        return "netflix"
    if "plex" in tokens or "plex" in compact:
        return "plex"
    if "appletv" in compact:
        return "appletv"
    if "hbomax" in compact or ({"hbo", "max"} <= tokens):
        return "hbomax"
    if "xploretv" in compact:
        return "xploretv"
    if "youtube" in tokens or "youtube" in compact:
        return "youtube"
    if "spotify" in tokens or "spotify" in compact:
        return "spotify"
    if "tunein" in compact:
        return "tunein"
    if "tidal" in tokens or "tidal" in compact:
        return "tidal"
    if "wiim" in tokens or "wiim" in compact or "up2stream" in compact:
        return "wiim"
    if "sony" in tokens:
        return "sony"
    if "xiaomi" in tokens or "mibox" in compact:
        return "xiaomi"
    return experience


@lru_cache(maxsize=None)
def source_logo_bytes(key: str) -> bytes | None:
    """Read one packaged logo without allowing caller-controlled paths."""
    if key not in _LOGO_KEYS:
        return None
    try:
        return (_LOGO_DIRECTORY / f"{key}.svg").read_bytes()
    except OSError:
        return None
