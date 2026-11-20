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

    def __init__(
        self,
        runtime: Control4Runtime,
        item: dict[str, Any],
        *,
        registry_item: dict[str, Any] | None = None,
    ) -> None:
        self.runtime = runtime
        self.item = item
        self.device_id = int(item["id"])
        self.registry_item = registry_item or item
        self.registry_device_id = int(self.registry_item["id"])
        self._attr_unique_id = f"{runtime.transport.rest.host}_{self.device_id}"
        self._attr_name = item.get("name") or f"Control4 {self.device_id}"
        self._remove_event = None
        self._remove_connection = None

    @property
    def device_info(self) -> DeviceInfo:
        info: DeviceInfo = {
            # Most entities use their source item. Related Control4 proxy
            # siblings can explicitly share one logical HA device.
            "identifiers": {
                (
                    DOMAIN,
                    f"{self.runtime.transport.rest.host}_{self.registry_device_id}",
                )
            },
            "name": self.registry_item.get("name")
            or f"Control4 {self.registry_device_id}",
        }
        if manufacturer := self.registry_item.get("manufacturer"):
            info["manufacturer"] = manufacturer
        if model := self.registry_item.get("model") or self.registry_item.get(
            "protocolName"
        ):
            info["model"] = model
        if proxy := self.registry_item.get("proxy"):
            info["model_id"] = proxy
        if room := self.registry_item.get("roomName"):
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
