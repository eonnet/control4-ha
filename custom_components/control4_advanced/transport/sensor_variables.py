"""Exact numeric variables exposed by Control4's Z-Wave sensor proxies."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Literal


SensorKind = Literal["battery", "temperature", "humidity", "illumination"]


@dataclass(frozen=True)
class ZWaveSensorMetric:
    """One dynamically created variable on a Z-Wave common sensor."""

    variable_name: str
    kind: SensorKind
    endpoint: int | None

    @property
    def entity_key(self) -> str:
        return self.kind if self.endpoint is None else f"{self.kind}_{self.endpoint}"

    @property
    def state_key(self) -> str:
        return f"zwave_sensor_{self.entity_key}"

    @property
    def label(self) -> str:
        base = {
            "battery": "Battery",
            "temperature": "Temperature",
            "humidity": "Humidity",
            "illumination": "Illumination",
        }[self.kind]
        return base if self.endpoint in (None, 0) else f"{base} {self.endpoint}"


_VARIABLE_PATTERNS: tuple[tuple[re.Pattern[str], SensorKind], ...] = (
    (re.compile(r"Battery Level(?: (\d+))?", re.IGNORECASE), "battery"),
    (re.compile(r"Local Temperature (\d+)", re.IGNORECASE), "temperature"),
    (re.compile(r"Humidity percentage (\d+)", re.IGNORECASE), "humidity"),
    (re.compile(r"Illumination (\d+)", re.IGNORECASE), "illumination"),
    (re.compile(r"BatteryPercent", re.IGNORECASE), "battery"),
    (re.compile(r"TemperatureC", re.IGNORECASE), "temperature"),
    (re.compile(r"IlluminanceLux", re.IGNORECASE), "illumination"),
)


def zwave_sensor_metric(variable_name: Any) -> ZWaveSensorMetric | None:
    """Recognize only variables documented by the bundled Control4 driver."""
    if not isinstance(variable_name, str):
        return None
    name = variable_name.strip()
    if not name or len(name) > 80:
        return None
    for pattern, kind in _VARIABLE_PATTERNS:
        match = pattern.fullmatch(name)
        if match is None:
            continue
        endpoint_value = match.group(1) if pattern.groups else None
        endpoint = int(endpoint_value) if endpoint_value is not None else None
        if endpoint is not None and endpoint > 255:
            return None
        return ZWaveSensorMetric(name, kind, endpoint)
    return None


def valid_zwave_sensor_value(metric: ZWaveSensorMetric, value: float) -> bool:
    """Reject impossible or unbounded values without inventing conversions."""
    if metric.kind in {"battery", "humidity"}:
        return 0 <= value <= 100
    if metric.kind == "temperature":
        return -273.15 <= value <= 1000
    return 0 <= value <= 1_000_000_000
