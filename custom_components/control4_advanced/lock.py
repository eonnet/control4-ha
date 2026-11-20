"""Push-triggered native Control4 Z-Wave locks."""

from __future__ import annotations

import asyncio
from typing import Any

from homeassistant.components.lock import LockEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import Control4Runtime
from .entity import Control4Entity
from .selection import supported_lock_ids
from .transport.proxies import ZWAVE_LOCK_PROXY


PARALLEL_UPDATES = 1
_CONFIRMATION_DELAYS = (0, 0.5, 1, 2)
_CONFIRMATION_TIMEOUT = 8


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    runtime: Control4Runtime = entry.runtime_data
    items = runtime.transport.inventory.items
    entities: list[Control4NativeLock] = []
    for device_id in supported_lock_ids(
        items,
        runtime.transport.tracked_ids,
        runtime.commands.supports_lock,
    ):
        item = items[device_id]
        parent_id = item.get("parentId")
        parent = items.get(parent_id) if type(parent_id) is int else None
        registry_item = (
            parent
            if parent
            and parent.get("typeName") == "device"
            and parent.get("proxy") == ZWAVE_LOCK_PROXY
            else None
        )
        entities.append(
            Control4NativeLock(runtime, item, registry_item=registry_item)
        )
    async_add_entities(entities)


class Control4NativeLock(Control4Entity, LockEntity):
    """Expose confirmed Director state without optimistic state changes."""

    @property
    def _control4_status(self) -> str | None:
        return self.state_data.get("lock_status")

    @property
    def is_locked(self) -> bool | None:
        if self._control4_status == "locked":
            return True
        if self._control4_status == "unlocked":
            return False
        return None

    @property
    def is_locking(self) -> bool:
        return self._control4_status == "locking"

    @property
    def is_unlocking(self) -> bool:
        return self._control4_status == "unlocking"

    @property
    def is_jammed(self) -> bool:
        return self._control4_status == "jammed"

    @property
    def available(self) -> bool:
        return super().available and self._control4_status is not None

    async def async_lock(self, **kwargs: Any) -> None:
        self._require_available()
        await self.runtime.commands.lock_device(self.device_id)
        await self._async_confirm_status("locked")

    async def async_unlock(self, **kwargs: Any) -> None:
        self._require_available()
        await self.runtime.commands.unlock_device(self.device_id)
        await self._async_confirm_status("unlocked")

    def _require_available(self) -> None:
        if not self.available:
            raise HomeAssistantError("Control4 lock state is unavailable")

    async def _async_confirm_status(self, expected: str) -> None:
        """Read back the state while a motorized lock settles."""
        try:
            async with asyncio.timeout(_CONFIRMATION_TIMEOUT):
                for delay in _CONFIRMATION_DELAYS:
                    if delay:
                        await asyncio.sleep(delay)
                    refreshed = await self.runtime.transport.sync_device(self.device_id)
                    if refreshed and self.available and self._control4_status == expected:
                        return
        except TimeoutError:
            pass
        raise HomeAssistantError(
            f"Control4 did not confirm the lock reached the {expected} state"
        )
