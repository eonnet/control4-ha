"""Common push-driven entity lifecycle and device registry metadata."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from . import Control4Runtime
from .const import DOMAIN
from .transport.events import NormalizedEvent


class Control4Entity(Entity):
    """An entity backed by one Director inventory item and state cache entry."""

    _attr_should_poll = False

    def __init__(self, runtime: Control4Runtime, item: dict[str, Any]) -> None:
        self.runtime = runtime
        self.item = item
        self.device_id = int(item["id"])
        self._attr_unique_id = f"{runtime.transport.rest.host}_{self.device_id}"
        self._attr_name = item.get("name") or f"Control4 {self.device_id}"
        self._remove_event = None
        self._remove_connection = None

    @property
    def device_info(self) -> DeviceInfo:
        info: DeviceInfo = {
            "identifiers": {(DOMAIN, self.unique_id)},
            "name": self.name,
        }
        if manufacturer := self.item.get("manufacturer"):
            info["manufacturer"] = manufacturer
        if model := self.item.get("model") or self.item.get("protocolName"):
            info["model"] = model
        if proxy := self.item.get("proxy"):
            info["model_id"] = proxy
        if room := self.item.get("roomName"):
            info["suggested_area"] = room
        return info

    @property
    def available(self) -> bool:
        state = self.runtime.transport.state.snapshot(self.device_id)
        return self.runtime.transport.connected and bool(state) and state.get("is_connected", True)

    @property
    def state_data(self) -> dict[str, Any]:
        return self.runtime.transport.state.snapshot(self.device_id)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._remove_event = self.runtime.transport.events.subscribe(self._on_event)
        self._remove_connection = self.runtime.transport.subscribe_connection(self._on_connection)

    async def async_will_remove_from_hass(self) -> None:
        if self._remove_event is not None:
            self._remove_event()
            self._remove_event = None
        if self._remove_connection is not None:
            self._remove_connection()
            self._remove_connection = None
        await super().async_will_remove_from_hass()

    async def _on_event(self, event: NormalizedEvent) -> None:
        if event.device_id == self.device_id and event.authoritative:
            self.async_write_ha_state()

    def _on_connection(self, connected: bool) -> None:
        self.async_write_ha_state()
