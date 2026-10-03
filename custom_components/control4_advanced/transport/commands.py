"""REST command validation against live Director metadata.

This module never sends a command unless one of its explicit methods is called.
The REST body shape follows the existing MCP client. A user has reported live
light control through this integration; climate commands remain unverified.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
import math
from typing import Any

from .rest import DirectorRestClient


class UnsupportedCommand(ValueError):
    """Director metadata does not support the requested command or value."""


class DeviceCommandClient:
    def __init__(self, rest: DirectorRestClient) -> None:
        self.rest = rest
        self._metadata: dict[int, dict[str, dict[str, Any]]] = {}

    async def refresh(self, device_ids: Iterable[int]) -> None:
        limit = asyncio.Semaphore(6)

        async def load_one(device_id: int) -> None:
            async with limit:
                records = await self.rest.get_commands(device_id)
                self._metadata[device_id] = {
                    record["command"]: record
                    for record in records
                    if record.get("deviceId") == device_id
                    and isinstance(record.get("command"), str)
                }

        await asyncio.gather(*(load_one(device_id) for device_id in sorted(set(device_ids))))

    def supports(self, device_id: int, command: str) -> bool:
        return command in self._metadata.get(device_id, {})

    def choices(self, device_id: int, command: str, parameter: str) -> list[str]:
        spec = self._parameter(device_id, command, parameter)
        values = spec.get("values")
        if not isinstance(values, list):
            return []
        return [str(option["value"]) for option in values if isinstance(option, dict) and "value" in option]

    def range(self, device_id: int, command: str, parameter: str) -> tuple[float, float] | None:
        spec = self._parameter(device_id, command, parameter)
        low, high = spec.get("low"), spec.get("high")
        if isinstance(low, (int, float)) and isinstance(high, (int, float)):
            return float(low), float(high)
        return None

    def _parameter(self, device_id: int, command: str, name: str) -> dict[str, Any]:
        record = self._metadata.get(device_id, {}).get(command)
        if record is None:
            raise UnsupportedCommand(f"{command} is not advertised for device {device_id}")
        for spec in record.get("params") or []:
            if isinstance(spec, dict) and spec.get("name") == name:
                return spec
        raise UnsupportedCommand(f"{command} does not advertise parameter {name}")

    def _validated_params(self, device_id: int, command: str, params: dict[str, Any]) -> dict[str, Any]:
        record = self._metadata.get(device_id, {}).get(command)
        if record is None:
            raise UnsupportedCommand(f"{command} is not advertised for device {device_id}")
        specs = record.get("params") or []
        names = {spec["name"] for spec in specs if isinstance(spec, dict) and isinstance(spec.get("name"), str)}
        if set(params) != names:
            raise UnsupportedCommand(f"{command} expects parameters {sorted(names)}")
        result: dict[str, Any] = {}
        for name, value in params.items():
            spec = self._parameter(device_id, command, name)
            choices = self.choices(device_id, command, name)
            if choices:
                match = next((choice for choice in choices if choice.casefold() == str(value).casefold()), None)
                if match is None:
                    raise UnsupportedCommand(f"{name} is not an advertised value for {command}")
                result[name] = match
                continue
            limits = self.range(device_id, command, name)
            if limits is not None:
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise UnsupportedCommand(f"{name} must be a finite number")
                if spec.get("valueType") == "INTEGER" and not float(value).is_integer():
                    raise UnsupportedCommand(f"{name} must be an integer")
                if not limits[0] <= value <= limits[1]:
                    raise UnsupportedCommand(f"{name} is outside Director's advertised range")
                resolution = spec.get("resolution")
                if isinstance(resolution, (int, float)) and resolution > 0:
                    if not math.isclose(value / resolution, round(value / resolution), abs_tol=1e-7):
                        raise UnsupportedCommand(f"{name} does not match Director's resolution")
            result[name] = str(value) if spec.get("valueType") == "STRING" else value
        return result

    async def send(self, device_id: int, command: str, params: dict[str, Any] | None = None) -> Any:
        validated = self._validated_params(device_id, command, params or {})
        body: dict[str, Any] = {"command": command}
        if validated:
            body["params"] = validated
        return await self.rest.post_json(f"/api/v1/items/{device_id}/commands", body)

    async def light_on(self, device_id: int) -> Any:
        return await self.send(device_id, "ON")

    async def light_off(self, device_id: int) -> Any:
        return await self.send(device_id, "OFF")

    async def light_level(self, device_id: int, level_percent: int) -> Any:
        return await self.send(device_id, "SET_LEVEL", {"LEVEL": level_percent})

    async def hvac_mode(self, device_id: int, mode: str) -> Any:
        return await self.send(device_id, "SET_MODE_HVAC", {"MODE": mode})

    async def setpoint(self, device_id: int, kind: str, temperature: float, scale: str) -> Any:
        if kind not in {"HEAT", "COOL", "SINGLE"} or scale not in {"CELSIUS", "FAHRENHEIT"}:
            raise UnsupportedCommand("unsupported setpoint kind or scale")
        return await self.send(device_id, f"SET_SETPOINT_{kind}", {scale: temperature})
