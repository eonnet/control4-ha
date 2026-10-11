# Thermostat fan-mode investigation

Status (2026-10-04): user-reported bidirectional HA fan operation. No fan-mode
command has been sent to the Director by the agent. Live REST fan
values and `SET_MODE_FAN.MODE` choices were captured for Living AC `2646` and
Kid AC `2811` at 07:56 UTC. Kid AC direct fan-change WebSocket feedback was
captured at 08:00 UTC. The exact HA-tested device and fan choices were not
specified, and the installed HACS version was not independently verified.

## Evidence and open questions

- The user-provided Living AC WebSocket trace from 05:36 UTC included direct
  `fan_mode` labels `Medium`, `Auto`, and `Low` during manual Control4 preset
  changes. Parallel `fanmode` uppercase events and a `settings.fanmode` field
  also appeared; the direct `fan_mode` label is used for normalization to
  avoid stale legacy-shaped values during transitions.
- A fresh read-only Director metadata probe at 07:56 UTC found both devices
  at `FAN_MODE: Low`, with `FAN_MODES_LIST: Auto,Low,Medium,High` and identical
  `SET_MODE_FAN.MODE` choices: `Auto`, `Low`, `Medium`, `High`. No choice was
  redacted or truncated. Their legacy `V1 FANMODES` was `On,Auto`, which is
  narrower than the canonical command choices and is not used for the HA
  selector. The installed pyControl4 source agrees on the REST variable and
  command names, but the Director read is the authority here.
- A second read-only WebSocket probe subscribed to both IDs at 08:00:18 UTC.
  Kid AC `2811` emitted direct `fan_mode` values `Medium`, `High`, `Medium`,
  `Auto`, and `Low` from 08:00:24.141 to 08:00:28.737 UTC during the user's
  manual changes. Each value had a parallel uppercase `fanmode` event.
  A transient `is_connected: false` then `true` also appeared at 08:00:24.141.
  No Living AC fan event appeared in this second capture; its direct fan
  labels remain supported by the earlier preset-change trace. The user's
  action timestamps were not recorded, so physical-to-push latency is unknown.
- Version 0.1.7 exposes Home Assistant's climate fan selector when that
  device advertises nonempty `SET_MODE_FAN.MODE` choices. It no longer
  requires a matching initial REST `FAN_MODE`; GREE ID `404` advertises five
  choices but its initial state was absent from the sanitized REST read. The
  current value remains unknown until a direct `fan_mode` push or readable
  REST value arrives. Command values are checked against the device's own
  live metadata. Displayed state does not change optimistically after a
  command.
- Fan *mode* and fan *running state* are distinct. `FAN_STATE` is collected
  for diagnosis but is not used as the selectable fan mode.

## Read-only Director check

From the normal SSH terminal that can reach the Director:

```bash
/opt/control4-mcp/.venv/bin/python -u /opt/control4-ha/tools/websocket_probe.py \
  --inventory-only --inspect-ids 2646,2811
```

The user ran this command and shared the two `thermostat_metadata` records.
The probe redacts credential-like data and sends no device command. If a
future read has missing, truncated, redacted, or mismatched choices, the
metadata gate keeps fan control hidden for that thermostat.

The user subsequently reported that fan mode works in both directions in HA.
An independent manual fan change on Living AC, individual mode choices, and
reconnect behavior remain optional follow-up checks. Initial state is read
from REST because neither focused WebSocket trace established an initial
snapshot.

The sandboxed development terminal attempted the metadata read at 07:50 UTC
but received `PermissionError` before `TCP_REACHABLE`, so it obtained no live
Director state. The normal SSH terminal is required for this check.

GREE's read-only REST metadata at 08:37 UTC advertised `SET_MODE_FAN.MODE`
choices `LOW`, `MEDIUM`, `HIGH`, `AUTO`, `STRONG`. A focused WebSocket trace at
08:39 UTC then showed direct `fan_mode` updates `LOW`, `MEDIUM`, `LOW` on ID
`404`. These establish a state path, but GREE's HA fan selector and an
HA-originated fan command remain untested in the installed integration.
