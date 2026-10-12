"""Persist and expose Control4 media-group presets to the frontend."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from copy import deepcopy
import math
from typing import Any
from uuid import uuid4

from .const import DOMAIN


STORAGE_KEY = f"{DOMAIN}.group_presets"
STORAGE_VERSION = 1
MAX_PRESETS = 32
MAX_MEMBERS = 64
MAX_NAME_LENGTH = 40
MAX_MEDIA_TYPE_LENGTH = 64
MAX_MEDIA_ID_LENGTH = 512
MAX_MEDIA_TITLE_LENGTH = 120
_DATA_KEY = f"{DOMAIN}_group_preset_store"


def _entity_id(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value.startswith("media_player.") or len(value) <= len("media_player."):
        return None
    return value


def _room_id(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _volume_level(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    if not math.isfinite(value) or not 0 <= value <= 1:
        return None
    return round(value, 4)


def _normalize_member(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    entity_id = _entity_id(value.get("entity_id"))
    room_id = _room_id(value.get("room_id"))
    if entity_id is None or room_id is None:
        return None
    raw_volume = value.get("volume_level")
    volume = _volume_level(raw_volume)
    if raw_volume is not None and volume is None:
        return None
    muted = value.get("is_volume_muted")
    if muted is not None and not isinstance(muted, bool):
        return None
    return {
        "entity_id": entity_id,
        "room_id": room_id,
        "volume_level": volume,
        "is_volume_muted": muted,
    }


def _bounded_text(value: Any, maximum: int) -> str | None:
    if not isinstance(value, str) or not (value := value.strip()):
        return None
    return value if len(value) <= maximum else None


def _normalize_media(value: Any) -> dict[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        return None
    content_type = _bounded_text(value.get("content_type"), MAX_MEDIA_TYPE_LENGTH)
    content_id = _bounded_text(value.get("content_id"), MAX_MEDIA_ID_LENGTH)
    title = _bounded_text(value.get("title"), MAX_MEDIA_TITLE_LENGTH)
    if content_type is None or content_id is None or title is None:
        return None
    return {
        "content_type": content_type,
        "content_id": content_id,
        "title": title,
    }


def _normalize_preset(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    preset_id = value.get("id")
    name = value.get("name")
    leader_entity_id = _entity_id(value.get("leader_entity_id"))
    leader_room_id = _room_id(value.get("leader_room_id"))
    raw_members = value.get("members")
    raw_media = value.get("media")
    media = _normalize_media(raw_media)
    if (
        not isinstance(preset_id, str)
        or not preset_id.strip()
        or not isinstance(name, str)
        or not (name := name.strip())
        or len(name) > MAX_NAME_LENGTH
        or leader_entity_id is None
        or leader_room_id is None
        or not isinstance(raw_members, list)
        or not 1 <= len(raw_members) <= MAX_MEMBERS
        or (raw_media is not None and media is None)
    ):
        return None

    members: list[dict[str, Any]] = []
    seen_rooms: set[int] = set()
    for raw_member in raw_members:
        member = _normalize_member(raw_member)
        if member is None or member["room_id"] in seen_rooms:
            return None
        seen_rooms.add(member["room_id"])
        members.append(member)
    if leader_room_id not in seen_rooms:
        return None
    return {
        "id": preset_id.strip(),
        "name": name,
        "leader_entity_id": leader_entity_id,
        "leader_room_id": leader_room_id,
        "members": members,
        "media": media,
    }


class GroupPresetStorage:
    """Small validated wrapper around Home Assistant's JSON Store."""

    def __init__(self, store: Any) -> None:
        self._store = store
        self._presets: list[dict[str, Any]] = []
        self._lock = asyncio.Lock()

    @property
    def presets(self) -> list[dict[str, Any]]:
        """Return a detached snapshot suitable for a WebSocket response."""
        return deepcopy(self._presets)

    async def async_load(self) -> None:
        """Load presets, ignoring malformed or duplicate stored records."""
        stored = await self._store.async_load()
        raw_presets = stored.get("presets", []) if isinstance(stored, Mapping) else []
        if not isinstance(raw_presets, list):
            return
        presets: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        seen_names: set[str] = set()
        for raw_preset in raw_presets[:MAX_PRESETS]:
            preset = _normalize_preset(raw_preset)
            if preset is None:
                continue
            name_key = preset["name"].casefold()
            if preset["id"] in seen_ids or name_key in seen_names:
                continue
            seen_ids.add(preset["id"])
            seen_names.add(name_key)
            presets.append(preset)
        self._presets = presets

    async def async_save_preset(
        self,
        *,
        name: str,
        leader_entity_id: str,
        leader_room_id: int,
        members: list[dict[str, Any]],
        media: dict[str, Any] | None = None,
        preset_id: str | None = None,
    ) -> dict[str, Any]:
        """Create a preset or update one by stable ID or matching name."""
        name = name.strip()
        async with self._lock:
            if preset_id is not None:
                preset_id = preset_id.strip() if isinstance(preset_id, str) else ""
                if not preset_id:
                    raise ValueError("Invalid group preset ID")
                existing = next(
                    (preset for preset in self._presets if preset["id"] == preset_id),
                    None,
                )
                if existing is None:
                    raise ValueError("Group preset not found")
            else:
                existing = next(
                    (
                        preset
                        for preset in self._presets
                        if preset["name"].casefold() == name.casefold()
                    ),
                    None,
                )
            name_conflict = next(
                (
                    preset
                    for preset in self._presets
                    if preset["name"].casefold() == name.casefold()
                    and (existing is None or preset["id"] != existing["id"])
                ),
                None,
            )
            if name_conflict is not None:
                raise ValueError("A group preset with this name already exists")
            candidate = _normalize_preset(
                {
                    "id": existing["id"] if existing is not None else uuid4().hex,
                    "name": name,
                    "leader_entity_id": leader_entity_id,
                    "leader_room_id": leader_room_id,
                    "members": members,
                    "media": media,
                }
            )
            if candidate is None:
                raise ValueError("Invalid group preset")
            if existing is None:
                if len(self._presets) >= MAX_PRESETS:
                    raise ValueError(f"A maximum of {MAX_PRESETS} presets is supported")
                self._presets.append(candidate)
            else:
                index = next(
                    index
                    for index, preset in enumerate(self._presets)
                    if preset["id"] == existing["id"]
                )
                self._presets[index] = candidate
            await self._store.async_save({"presets": self._presets})
        return deepcopy(candidate)

    async def async_reorder_presets(
        self, preset_ids: list[str]
    ) -> list[dict[str, Any]]:
        """Persist an exact client-requested ordering of all presets."""
        async with self._lock:
            current_ids = [preset["id"] for preset in self._presets]
            if (
                not isinstance(preset_ids, list)
                or any(not isinstance(item, str) for item in preset_ids)
                or len(set(preset_ids)) != len(preset_ids)
                or set(preset_ids) != set(current_ids)
            ):
                raise ValueError("Preset order must contain every preset exactly once")
            by_id = {preset["id"]: preset for preset in self._presets}
            self._presets = [by_id[preset_id] for preset_id in preset_ids]
            await self._store.async_save({"presets": self._presets})
        return self.presets

    async def async_delete_preset(self, preset_id: str) -> bool:
        """Delete one preset and report whether it existed."""
        async with self._lock:
            remaining = [
                preset for preset in self._presets if preset["id"] != preset_id
            ]
            if len(remaining) == len(self._presets):
                return False
            self._presets = remaining
            await self._store.async_save({"presets": self._presets})
        return True


async def async_register_group_presets(hass: Any) -> GroupPresetStorage:
    """Load the preset store and register its WebSocket commands once."""
    if manager := hass.data.get(_DATA_KEY):
        return manager

    import voluptuous as vol

    from homeassistant.components import websocket_api
    from homeassistant.helpers import config_validation as cv
    from homeassistant.helpers.storage import Store

    manager = GroupPresetStorage(Store(hass, STORAGE_VERSION, STORAGE_KEY))
    await manager.async_load()

    member_schema = {
        vol.Required("entity_id"): cv.entity_id,
        vol.Required("room_id"): vol.All(int, vol.Range(min=1)),
        vol.Optional("volume_level"): vol.Any(
            None, vol.All(vol.Coerce(float), vol.Range(min=0, max=1))
        ),
        vol.Optional("is_volume_muted"): vol.Any(None, bool),
    }
    media_schema = {
        vol.Required("content_type"): vol.All(
            str, lambda value: value.strip(), vol.Length(min=1, max=MAX_MEDIA_TYPE_LENGTH)
        ),
        vol.Required("content_id"): vol.All(
            str, lambda value: value.strip(), vol.Length(min=1, max=MAX_MEDIA_ID_LENGTH)
        ),
        vol.Required("title"): vol.All(
            str, lambda value: value.strip(), vol.Length(min=1, max=MAX_MEDIA_TITLE_LENGTH)
        ),
    }

    @websocket_api.websocket_command(
        {vol.Required("type"): f"{DOMAIN}/group_presets/list"}
    )
    @websocket_api.async_response
    async def ws_list_group_presets(hass: Any, connection: Any, msg: dict) -> None:
        connection.send_result(msg["id"], {"presets": manager.presets})

    @websocket_api.websocket_command(
        {
            vol.Required("type"): f"{DOMAIN}/group_presets/save",
            vol.Required("name"): vol.All(
                str, lambda value: value.strip(), vol.Length(min=1, max=MAX_NAME_LENGTH)
            ),
            vol.Required("leader_entity_id"): cv.entity_id,
            vol.Required("leader_room_id"): vol.All(int, vol.Range(min=1)),
            vol.Optional("preset_id"): cv.string,
            vol.Optional("media"): vol.Any(None, media_schema),
            vol.Required("members"): vol.All(
                [member_schema], vol.Length(min=1, max=MAX_MEMBERS)
            ),
        }
    )
    @websocket_api.async_response
    async def ws_save_group_preset(hass: Any, connection: Any, msg: dict) -> None:
        try:
            preset = await manager.async_save_preset(
                name=msg["name"],
                leader_entity_id=msg["leader_entity_id"],
                leader_room_id=msg["leader_room_id"],
                members=msg["members"],
                media=msg.get("media"),
                preset_id=msg.get("preset_id"),
            )
        except ValueError as err:
            connection.send_error(
                msg["id"], websocket_api.ERR_INVALID_FORMAT, str(err)
            )
            return
        connection.send_result(msg["id"], {"preset": preset})

    @websocket_api.websocket_command(
        {
            vol.Required("type"): f"{DOMAIN}/group_presets/delete",
            vol.Required("preset_id"): cv.string,
        }
    )
    @websocket_api.async_response
    async def ws_delete_group_preset(hass: Any, connection: Any, msg: dict) -> None:
        if not await manager.async_delete_preset(msg["preset_id"]):
            connection.send_error(
                msg["id"], websocket_api.ERR_NOT_FOUND, "Group preset not found"
            )
            return
        connection.send_result(msg["id"], {"deleted": msg["preset_id"]})

    @websocket_api.websocket_command(
        {
            vol.Required("type"): f"{DOMAIN}/group_presets/reorder",
            vol.Required("preset_ids"): vol.All(
                [cv.string], vol.Length(max=MAX_PRESETS)
            ),
        }
    )
    @websocket_api.async_response
    async def ws_reorder_group_presets(
        hass: Any, connection: Any, msg: dict
    ) -> None:
        try:
            presets = await manager.async_reorder_presets(msg["preset_ids"])
        except ValueError as err:
            connection.send_error(
                msg["id"], websocket_api.ERR_INVALID_FORMAT, str(err)
            )
            return
        connection.send_result(msg["id"], {"presets": presets})

    websocket_api.async_register_command(hass, ws_list_group_presets)
    websocket_api.async_register_command(hass, ws_save_group_preset)
    websocket_api.async_register_command(hass, ws_delete_group_preset)
    websocket_api.async_register_command(hass, ws_reorder_group_presets)
    hass.data[_DATA_KEY] = manager
    return manager
