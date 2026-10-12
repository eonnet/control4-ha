"""Serve and register the Control4 media dashboard cards."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .const import DOMAIN


_LOGGER = logging.getLogger(__name__)

FRONTEND_URL_PATH = f"/{DOMAIN}/frontend/control4-media-group-card.js"
FRONTEND_URL = f"{FRONTEND_URL_PATH}?v=0.1.58"
FRONTEND_FILE = Path(__file__).parent / "frontend" / "control4-media-group-card.js"
_REGISTERED_KEY = f"{DOMAIN}_frontend_registered"


async def _async_register_lovelace_resource(hass: Any) -> bool:
    """Register after Lovelace bootstrap so the scoped registry cannot lose it."""
    from homeassistant.components.lovelace import LOVELACE_DATA

    lovelace_data = hass.data.get(LOVELACE_DATA)
    resources = getattr(lovelace_data, "resources", None)
    if resources is None or not callable(
        getattr(resources, "async_create_item", None)
    ):
        return False

    try:
        # Current storage collections load lazily. Ensure existing resources are
        # read before checking or creating one, so nothing can be overwritten.
        if callable(getattr(resources, "async_get_info", None)):
            await resources.async_get_info()
        for item in resources.async_items():
            url = item.get("url")
            if not isinstance(url, str) or url.split("?", 1)[0] != FRONTEND_URL_PATH:
                continue
            if url != FRONTEND_URL:
                item_id = item.get("id")
                if not isinstance(item_id, str):
                    return False
                await resources.async_update_item(
                    item_id, {"res_type": "module", "url": FRONTEND_URL}
                )
            return True
        await resources.async_create_item(
            {"res_type": "module", "url": FRONTEND_URL}
        )
        return True
    except Exception as exc:
        _LOGGER.warning(
            "Could not register the Control4 dashboard resource; using the "
            "frontend module fallback: %s",
            type(exc).__name__,
        )
        return False


async def async_register_frontend(hass: Any) -> None:
    """Serve the card and load it through Lovelace whenever possible."""
    from homeassistant.components.frontend import add_extra_js_url
    from homeassistant.components.http import StaticPathConfig

    if hass.data.get(_REGISTERED_KEY):
        return
    await hass.http.async_register_static_paths(
        [StaticPathConfig(FRONTEND_URL_PATH, str(FRONTEND_FILE), False)]
    )
    if not await _async_register_lovelace_resource(hass):
        # YAML resource mode is read-only. Keep automatic loading there and let
        # the JavaScript re-register itself after HA swaps element registries.
        add_extra_js_url(hass, FRONTEND_URL)
    hass.data[_REGISTERED_KEY] = True
