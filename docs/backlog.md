# Development backlog

## Deferred at user request (2026-10-04)

- Investigate a numeric sensor platform, starting with fresh, read-only Director
  inventory and metadata for Kid Temperature and Kid Humidity candidates.
  Previously seen IDs `1728`, `2968`, and `2975` are leads, not confirmed
  current inventory. Do not add HA sensor entities or interpret values until
  REST units and pushed state fields are observed.

## Current priority

- The user reports hold operation and bidirectional thermostat controls in HA.
  A Kid AC hold push event remains unrecorded, but hold feature work is paused
  in favor of fan mode at the user's request.
- Both Living AC and Kid AC have confirmed live fan-mode metadata; Kid AC
  direct push is confirmed, and Living AC direct labels were observed during
  preset changes. The user reports bidirectional HA fan operation. Exact
  HA-tested devices and choices, independent Living AC fan changes, and
  reconnect behavior are not yet documented; see
  [thermostat-fan-mode.md](thermostat-fan-mode.md). Keep ordinary climate
  operation on REST and WebSocket, without a C4SOAP dependency.
