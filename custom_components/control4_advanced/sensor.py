"""Read-only numeric sensors from Control4's Z-Wave drivers."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfIlluminance, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import Control4Runtime
from .entity import Control4Entity
from .transport.proxies import (
    MOTION_CONTACT_PROXY,
    ZWAVE_COMMON_SENSOR_PROXY,
    ZWAVE_NUMERIC_SENSOR_PROXIES,
)
from .transport.sensor_variables import ZWaveSensorMetric, zwave_sensor_metric


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    runtime: Control4Runtime = entry.runtime_data
    items = runtime.transport.inventory.items
    motion_items: dict[tuple[str, str], list[dict[str, Any]]] = {}
    common_items: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in items.values():
        key = _sensor_identity(item)
        if key is None:
            continue
        if item.get("proxy") == MOTION_CONTACT_PROXY:
            motion_items.setdefault(key, []).append(item)
        elif item.get("proxy") == ZWAVE_COMMON_SENSOR_PROXY:
            common_items.setdefault(key, []).append(item)

    sources: list[tuple[dict[str, Any], ZWaveSensorMetric]] = []
    for device_id in sorted(runtime.transport.tracked_ids):
        item = items[device_id]
        if item.get("proxy") not in ZWAVE_NUMERIC_SENSOR_PROXIES:
            continue
        state = runtime.transport.state.snapshot(device_id)
        variable_names = state.get("zwave_sensor_variables", ())
        if not isinstance(variable_names, (tuple, list)):
            continue
        for variable_name in variable_names:
            metric = zwave_sensor_metric(variable_name)
            if metric is not None:
                sources.append((item, metric))

    common_sources = {
        _metric_identity(item, metric): (item, metric)
        for item, metric in sources
        if item.get("proxy") == ZWAVE_COMMON_SENSOR_PROXY
    }
    security_sources = {
        _metric_identity(item, metric): (item, metric)
        for item, metric in sources
        if item.get("proxy") != ZWAVE_COMMON_SENSOR_PROXY
    }
    entities: list[Control4ZWaveSensor] = []
    kind_order = {"battery": 0, "temperature": 1, "humidity": 2, "illumination": 3}

    def sort_key(key: tuple[str, str, str, int]) -> tuple[int, int, int]:
        item, _metric = security_sources.get(key) or common_sources[key]
        return int(item["id"]), kind_order[key[2]], key[3]

    for key in sorted(set(common_sources) | set(security_sources), key=sort_key):
        selected = security_sources.get(key) or common_sources[key]
        item, metric = selected
        replaced_common = common_sources.get(key) if key in security_sources else None
        identity = _sensor_identity(item)
        if replaced_common is None and key in security_sources and identity is not None:
            common_matches = common_items.get(identity, ())
            if len(common_matches) == 1:
                legacy_endpoint = None if metric.kind == "battery" else metric.endpoint or 0
                replaced_common = (
                    common_matches[0],
                    ZWaveSensorMetric(
                        metric.variable_name, metric.kind, legacy_endpoint
                    ),
                )
        matches = motion_items.get(identity, ()) if identity is not None else ()
        registry_item = matches[0] if len(matches) == 1 else None
        entities.append(
            Control4ZWaveSensor(
                runtime,
                item,
                metric,
                registry_item=registry_item,
                unique_id_source=replaced_common,
            )
        )
    async_add_entities(entities)


def _sensor_identity(item: dict[str, Any]) -> tuple[str, str] | None:
    """Match Control4 proxy siblings only when their room and name agree."""
    name = item.get("name")
    room = item.get("roomName")
    if not isinstance(name, str) or not name.strip():
        return None
    if not isinstance(room, str) or not room.strip():
        return None
    return name.strip().casefold(), room.strip().casefold()


def _metric_identity(
    item: dict[str, Any], metric: ZWaveSensorMetric
) -> tuple[str, str, str, int]:
    """Treat endpoint zero and an endpoint-less native metric as equivalent."""
    identity = _sensor_identity(item)
    if identity is None:
        identity = (f"control4-id:{int(item['id'])}", "")
    return identity[0], identity[1], metric.kind, metric.endpoint or 0


class Control4ZWaveSensor(Control4Entity, SensorEntity):
    """One validated numeric variable belonging to a Control4 sensor device."""

    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        runtime: Control4Runtime,
        item: dict[str, Any],
        metric: ZWaveSensorMetric,
        *,
        registry_item: dict[str, Any] | None = None,
        unique_id_source: tuple[dict[str, Any], ZWaveSensorMetric] | None = None,
    ) -> None:
        super().__init__(runtime, item, registry_item=registry_item)
        self.metric = metric
        unique_id_item, unique_id_metric = unique_id_source or (item, metric)
        self._attr_unique_id = (
            f"{runtime.transport.rest.host}_{int(unique_id_item['id'])}_"
            f"{unique_id_metric.entity_key}"
        )
        self._attr_name = f"{self._attr_name} {metric.label}"
        if metric.kind == "battery":
            self._attr_device_class = SensorDeviceClass.BATTERY
            self._attr_native_unit_of_measurement = PERCENTAGE
        elif metric.kind == "temperature":
            self._attr_device_class = SensorDeviceClass.TEMPERATURE
            self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
        elif metric.kind == "humidity":
            self._attr_device_class = SensorDeviceClass.HUMIDITY
            self._attr_native_unit_of_measurement = PERCENTAGE
        elif metric.kind == "illumination":
            self._attr_device_class = SensorDeviceClass.ILLUMINANCE
            self._attr_native_unit_of_measurement = UnitOfIlluminance.LUX

    @property
    def native_value(self) -> float | None:
        value = self.state_data.get(self.metric.state_key)
        return value if type(value) in (int, float) else None

    @property
    def available(self) -> bool:
        # Non-listening Z-Wave sensors sleep between reports and Control4 can
        # label them offline while their last reading is still valid. The
        # driver clears stale variables itself, so availability follows the
        # Director connection and the individual validated value.
        return self.runtime.transport.connected and self.native_value is not None
