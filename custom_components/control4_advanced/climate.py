"""Push-driven Control4 thermostatV2 climate entities (Celsius first)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import ClimateEntity, ClimateEntityFeature, HVACMode
from homeassistant.components.climate.const import ATTR_HVAC_MODE
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import Control4Runtime
from .entity import Control4Entity
from .transport.commands import UnsupportedCommand


WIRE_MODES = {
    "OFF": HVACMode.OFF,
    "HEAT": HVACMode.HEAT,
    "COOL": HVACMode.COOL,
    "AUTO": HVACMode.AUTO,
}
MODE_COMMAND_KIND = {
    HVACMode.HEAT: "HEAT",
    HVACMode.COOL: "COOL",
    HVACMode.AUTO: "SINGLE",
}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    runtime: Control4Runtime = entry.runtime_data
    items = runtime.transport.inventory.items
    async_add_entities([
        Control4Climate(runtime, items[device_id])
        for device_id in sorted(runtime.transport.tracked_ids)
        if items[device_id].get("proxy") == "thermostatV2"
        and runtime.transport.state.snapshot(device_id).get("scale") in {"CELSIUS", "C"}
        and runtime.commands.supports(device_id, "SET_MODE_HVAC")
        and any(runtime.commands.supports(device_id, f"SET_SETPOINT_{kind}")
                for kind in MODE_COMMAND_KIND.values())
    ])


class Control4Climate(Control4Entity, ClimateEntity):
    """Expose advertised Celsius, preset, and fan controls with hold status."""

    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = 1.0
    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE

    def __init__(self, runtime: Control4Runtime, item: dict[str, Any]) -> None:
        super().__init__(runtime, item)
        self._preset_options: list[str] = []
        if runtime.commands.supports(self.device_id, "SET_PRESET"):
            try:
                choices = runtime.commands.choices(self.device_id, "SET_PRESET", "NAME")
            except UnsupportedCommand:
                choices = []
            # GREE advertises commands while omitting the active value at startup.
            if choices:
                self._preset_options = choices
                self._attr_supported_features |= ClimateEntityFeature.PRESET_MODE
        self._fan_options: list[str] = []
        if runtime.commands.supports(self.device_id, "SET_MODE_FAN"):
            try:
                choices = runtime.commands.choices(self.device_id, "SET_MODE_FAN", "MODE")
            except UnsupportedCommand:
                choices = []
            # Keep the selector available; push can supply its value later.
            if choices:
                self._fan_options = choices
                self._attr_supported_features |= ClimateEntityFeature.FAN_MODE

    @property
    def preset_modes(self) -> list[str]:
        return self._preset_options

    @property
    def preset_mode(self) -> str | None:
        current = self.state_data.get("preset_mode")
        if not isinstance(current, str):
            return None
        return next(
            (choice for choice in self._preset_options if choice.casefold() == current.casefold()),
            None,
        )

    @property
    def fan_modes(self) -> list[str]:
        return self._fan_options

    @property
    def fan_mode(self) -> str | None:
        current = self.state_data.get("fan_mode")
        if not isinstance(current, str):
            return None
        return next(
            (choice for choice in self._fan_options if choice.casefold() == current.casefold()),
            None,
        )

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        hold = self.state_data.get("hold_mode")
        return {"control4_hold_mode": hold} if isinstance(hold, str) else {}

    @property
    def hvac_modes(self) -> list[HVACMode]:
        choices = self.runtime.commands.choices(self.device_id, "SET_MODE_HVAC", "MODE")
        return [WIRE_MODES[choice.upper()] for choice in choices if choice.upper() in WIRE_MODES]

    @property
    def hvac_mode(self) -> HVACMode | None:
        raw = self.state_data.get("hvac_mode")
        return WIRE_MODES.get(raw) if isinstance(raw, str) else None

    @property
    def current_temperature(self) -> float | None:
        return self.state_data.get("current_temperature_c")

    @property
    def target_temperature(self) -> float | None:
        state = self.state_data
        if self.hvac_mode == HVACMode.HEAT:
            return state.get("heat_setpoint_c")
        if self.hvac_mode == HVACMode.COOL:
            return state.get("cool_setpoint_c")
        if self.hvac_mode == HVACMode.AUTO:
            return state.get("target_temperature_c")
        return None

    @property
    def min_temp(self) -> float:
        return self._setpoint_bounds()[0]

    @property
    def max_temp(self) -> float:
        return self._setpoint_bounds()[1]

    def _setpoint_bounds(self) -> tuple[float, float]:
        kind = MODE_COMMAND_KIND.get(self.hvac_mode, "SINGLE")
        if self.runtime.commands.supports(self.device_id, f"SET_SETPOINT_{kind}"):
            bounds = self.runtime.commands.range(
                self.device_id, f"SET_SETPOINT_{kind}", "CELSIUS"
            )
            if bounds is not None:
                return bounds
        return (6.0, 32.0)

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if hvac_mode not in self.hvac_modes:
            raise HomeAssistantError("HVAC mode is not advertised by this thermostat")
        await self.runtime.commands.hvac_mode(self.device_id, hvac_mode.value)

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        if preset_mode not in self._preset_options:
            raise HomeAssistantError("Preset is not advertised by this thermostat")
        await self.runtime.commands.preset(self.device_id, preset_mode)

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        if fan_mode not in self._fan_options:
            raise HomeAssistantError("Fan mode is not advertised by this thermostat")
        await self.runtime.commands.fan_mode(self.device_id, fan_mode)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            raise HomeAssistantError("A target temperature is required")
        mode = kwargs.get(ATTR_HVAC_MODE, self.hvac_mode)
        if mode not in MODE_COMMAND_KIND:
            raise HomeAssistantError("Choose Heat, Cool, or Auto before setting a temperature")
        kind = MODE_COMMAND_KIND[mode]
        if not self.runtime.commands.supports(self.device_id, f"SET_SETPOINT_{kind}"):
            raise HomeAssistantError("This thermostat does not advertise that setpoint command")
        if ATTR_HVAC_MODE in kwargs and mode != self.hvac_mode:
            await self.async_set_hvac_mode(mode)
        await self.runtime.commands.setpoint(self.device_id, kind, temperature, "CELSIUS")
