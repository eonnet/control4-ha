"""Push-triggered native Control4 Z-Wave locks."""

from __future__ import annotations

import asyncio
from contextlib import suppress
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
_CONFIRMATION_TIMEOUT = 15
_CONFIRMATION_INTERVAL = 0.75
_CONFIRMATION_READ_TIMEOUT = 2


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
        baseline = self._status_revision()
        await self.runtime.commands.lock_device(self.device_id)
        await self._async_confirm_status("locked", baseline)

    async def async_unlock(self, **kwargs: Any) -> None:
        self._require_available()
        baseline = self._status_revision()
        await self.runtime.commands.unlock_device(self.device_id)
        await self._async_confirm_status("unlocked", baseline)

    def _require_available(self) -> None:
        if not self.available:
            raise HomeAssistantError("Control4 lock state is unavailable")

    def _status_revision(self) -> int:
        return self.runtime.transport.state.field_revision(self.device_id, "lock_status")

    async def _async_confirm_status(self, expected: str, baseline: int) -> None:
        """Wait for fresh Director feedback throughout the confirmation window."""
        transport = self.runtime.transport

        def confirmed() -> bool:
            return self.available and self._status_revision() > baseline and (
                self._control4_status == expected
            )

        try:
            async with asyncio.timeout(_CONFIRMATION_TIMEOUT):
                while transport.connected:
                    if confirmed():
                        return
                    with suppress(TimeoutError):
                        await asyncio.wait_for(
                            transport.sync_device(self.device_id),
                            timeout=_CONFIRMATION_READ_TIMEOUT,
                        )
                    if confirmed():
                        return
                    await asyncio.sleep(_CONFIRMATION_INTERVAL)
        except TimeoutError:
            pass
        if confirmed():
            return
        raise HomeAssistantError(
            f"Control4 did not confirm the lock reached the {expected} state"
        )
