"""Read-only numeric sensors from Control4's common Z-Wave driver."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import Control4Runtime
from .entity import Control4Entity
from .transport.proxies import ZWAVE_COMMON_SENSOR_PROXY
from .transport.sensor_variables import ZWaveSensorMetric, zwave_sensor_metric


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    runtime: Control4Runtime = entry.runtime_data
    items = runtime.transport.inventory.items
    entities: list[Control4ZWaveSensor] = []
    for device_id in sorted(runtime.transport.tracked_ids):
        item = items[device_id]
        if item.get("proxy") != ZWAVE_COMMON_SENSOR_PROXY:
            continue
        state = runtime.transport.state.snapshot(device_id)
        variable_names = state.get("zwave_sensor_variables", ())
        if not isinstance(variable_names, (tuple, list)):
            continue
        for variable_name in variable_names:
            metric = zwave_sensor_metric(variable_name)
            if metric is not None:
                entities.append(Control4ZWaveSensor(runtime, item, metric))
    async_add_entities(entities)


class Control4ZWaveSensor(Control4Entity, SensorEntity):
    """One validated numeric variable belonging to a Control4 sensor device."""

    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        runtime: Control4Runtime,
        item: dict[str, Any],
        metric: ZWaveSensorMetric,
    ) -> None:
        super().__init__(runtime, item)
        self.metric = metric
        self._attr_unique_id = (
            f"{runtime.transport.rest.host}_{self.device_id}_{metric.entity_key}"
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
