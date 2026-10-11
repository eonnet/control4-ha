# Thermostat hold and preset investigation

Status: REST inventory, current state, command choices, and Living AC
WebSocket feedback observed on 2026-10-04. Version 0.1.5 implements an
offline-tested HA slice. HA runtime behavior and HA-originated hold/preset
commands were subsequently reported working in both directions by the user;
the user also explicitly confirmed that hold works. The individual options
and devices tested were not specified. No Control4
thermostat command has been sent by this investigation.

The current HA climate entity exposes advertised HVAC modes and Celsius
setpoints. The local pyControl4 source suggests REST variables `HOLD_MODE` and
`HOLD_MODES_LIST`, and command names `SET_MODE_HOLD` and `SET_PRESET`. The
production MCP code also reads hold state from REST and validates schedule
mode against its live, C4SOAP-backed setup. These sources were clues prior
to the focused REST probe below. C4SOAP will not be made a dependency of
ordinary HA climate operation.

Home Assistant climate provides one native `preset_mode` selector, not
independent hold and named-preset selectors. The observed Living AC named
presets use that selector. Hold appears as `control4_hold_mode` on the climate
entity and as a companion `select` on the same HA device. These are separate
axes; neither is conflated with HVAC mode. Both write methods are limited to
the current device's REST-advertised choices.

Run this **read-only** probe from the normal SSH terminal that can reach the
Director. It reads inventory and variables/command metadata for the two known
thermostat child IDs, then exits without opening a WebSocket or issuing a
device command:

```bash
/opt/control4-mcp/.venv/bin/python -u /opt/control4-ha/tools/websocket_probe.py \
  --inventory-only --watch-ids 2646,2811 --inspect-ids 2646,2811
```

The command was attempted in the sandboxed development terminal on
2026-10-04. It emitted `START` followed immediately by `PermissionError`
before `TCP_REACHABLE`; no Director REST read or device command occurred.
The user ran it from a normal SSH terminal at 05:22 UTC. It reached
`REST_AUTH_OK`, and fresh inventory matched both IDs to `thermostatV2` child
devices. These are Director REST observations:

| Device | Current `HOLD_MODE` | Advertised `SET_MODE_HOLD.MODE` choices | Current `PRESET` | Advertised `SET_PRESET.NAME` choices |
| --- | --- | --- | --- | --- |
| Living AC `2646` | `Off` | `Off`, `2 Hours`, `Until Next`, `Permanent` | `Off` | `Morning`, `Home`, `Away`, `Morning Auto`, `Cool`, `Heat`, `Off` |
| Kid AC `2811` | `Until Next` | `Off`, `2 Hours`, `Until Next`, `Permanent` | no usable value in sanitized output | no `SET_PRESET` command |

Both devices also expose `HOLD_MODES_LIST` as
`Off,2 Hours,Until Next,Permanent`. Living AC's `ANA_HOLDMODE` was `Off` and
`V1 HOLDMODE` was `OFF`; Kid AC's were `Until Next` and `TO_NEXT`. The
canonical `HOLD_MODE` is used for HA display. The Kid AC `PRESET` variable
name exists, yet
the probe's conservative label filter emitted no value; that is not proof
the device has no preset state. The absence of `SET_PRESET` in Kid AC's
current command metadata *does* mean it must not receive a preset control.
Neither a command advertisement nor a REST snapshot proves that an
HA-originated hold or preset command will work. The transport normalizes
the observed `HOLD_MODE` and `PRESET` REST values for initial sync and
reconciliation.

The user ran a focused, read-only WebSocket probe at 05:36 UTC, watched IDs
`2646` and `2811`, and manually changed Living AC settings in Control4:

```bash
/opt/control4-mcp/.venv/bin/python -u /opt/control4-ha/tools/websocket_probe.py \
  --duration 180 --watch-ids 2646,2811
```

Living AC emitted separate `OnDataToUI` payloads with `hold_mode: Off`, then
`hold_mode: Until Next` at 05:36:15.243Z, and `preset: Morning Auto` at the
same time. Further `preset` payloads included `Away` at 05:36:19.631Z,
`Morning` at 05:36:22.633Z, and `Cool` at 05:36:25.560Z. Setpoints, fan
mode, and sometimes HVAC mode arrived as separate subsequent events, so
each message must be merged as a partial update. `settings.holdmode: OFF`
and `holdmode: TO_NEXT` were also emitted during the same transition; the
normalizer deliberately uses only the direct UI-label `hold_mode` field so
those legacy-shaped values cannot overwrite it. The user's exact action
timestamps were not recorded, so this trace does not measure physical-to-HA
latency. No Kid AC hold change was captured.

The 0.1.5 code uses direct `hold_mode`/`preset` push, REST initial state and
reconnect sync, and command metadata to expose the HA controls. It does not
optimistically change displayed state; Director push or REST reconciliation
must confirm it. The user reports that the new controls work in both
directions in HA. A focused follow-up would check individual preset and hold
choices and capture a Kid AC hold push event. Applying a preset may change
HVAC mode/setpoints.
