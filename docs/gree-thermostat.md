# GREE thermostat fan, preset, and hold evidence

Status (2026-10-04): read-only Director REST metadata and WebSocket push
observed for GREE child thermostat ID `404`. Version 0.1.8 is offline-tested,
and the user reports that GREE setpoint control and feedback now work in both
directions in Home Assistant. The agent sent no Control4 command.

- REST inventory identifies ID `404` as a Bedroom `thermostatV2` child of
  Sensibo Air/Sky ID `403`.
- `SET_MODE_FAN.MODE` advertises `LOW`, `MEDIUM`, `HIGH`, `AUTO`, `STRONG`.
  `SET_PRESET.NAME` advertises `Off`, `Heat Auto`, `Cool Auto`, `Night Heat`,
  `Away Cool`, `Away Heat`, `Home`. Neither active `FAN_MODE` nor active
  `PRESET` had a usable value in the sanitized initial REST result.
- There is no `SET_MODE_HOLD` command. `HOLD_MODES_LIST` is `-`, so the HA
  hold select remains absent. A push `hold_mode` label alone is not authority
  to invent a writable command.
- At 08:39:25 UTC, direct `OnDataToUI` payloads for ID `404` included
  `preset: "Night Heat"` and `fan_mode: "LOW"`. Later fan values included
  `MEDIUM` and `LOW`. Repeated `preset: ""` events followed manual changes;
  they must clear the previously displayed preset rather than retain it.

The climate entity now offers fan and preset controls from nonempty Director
command choices even when the initial active values are unknown. The current
selector value is `None` until REST or WebSocket supplies a matching value;
commands still use the same per-device metadata validation, and displayed
state waits for Director feedback. GREE fan and preset behavior was observed
over WebSocket, but the user has not separately confirmed every GREE choice
from Home Assistant. No hold command should be tested or issued.

## Setpoint evidence and 0.1.8 candidate

At 10:23 UTC the read-only probe returned GREE `SINGLE_SETPOINT_C=22`, with
`HEAT_SETPOINT_C=0` and `COOL_SETPOINT_C=0`. The displayed heat/cool variables
were also zero; the V1 heat/cool fields were both 32. Director advertises
`SET_SETPOINT_SINGLE(CELSIUS)` with range 8–30, step 1, string value type;
the separate Heat and Cool commands are also advertised. There was no live
command invocation by the agent.

The ID 404 WebSocket trace reported `setpoint_single_c` moving 22 → 21 while
in Cool, then 21 → 20 while in Heat. It did not report heat/cool-specific
setpoint changes; `settings.heat_setpoint` and `settings.cool_setpoint` stayed
at 32 during these changes. This proves the prior HA target-temperature read
path was wrong for GREE: it selected the heat/cool fields by HVAC mode. It
does not yet prove which advertised command the GREE driver honors.

Version 0.1.8 recognizes the explicit nonzero-single/zero-heat/zero-cool REST
signature, reads the single Celsius target in Heat and Cool, and routes writes
through the advertised `SET_SETPOINT_SINGLE` command. The profile persists
across partial WebSocket updates. Other thermostats retain separate heat/cool
routing. Offline tests cover the profile, push merge, UI target, command
selection, and metadata validation. The user subsequently confirmed GREE
setpoint operation in both directions in Home Assistant. The exact values and
round-trip timing were not independently measured by the agent.
