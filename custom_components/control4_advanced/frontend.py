"""Serve and register the Control4 media-group dashboard card."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .const import DOMAIN


FRONTEND_URL = f"/{DOMAIN}/frontend/control4-media-group-card.js"
FRONTEND_FILE = Path(__file__).parent / "frontend" / "control4-media-group-card.js"


async def async_register_frontend(hass: Any) -> None:
    """Register the no-cache frontend module once during integration setup."""
    from homeassistant.components.frontend import add_extra_js_url
    from homeassistant.components.http import StaticPathConfig

    await hass.http.async_register_static_paths(
        [StaticPathConfig(FRONTEND_URL, str(FRONTEND_FILE), False)]
    )
    add_extra_js_url(hass, FRONTEND_URL)
