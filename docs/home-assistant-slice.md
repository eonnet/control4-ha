# Phase 3: first Home Assistant slice

The deployable custom component now lives at
`custom_components/control4_advanced/`. Its `transport/` package remains
Home Assistant-independent; `control4_transport` at the repository root is a
compatibility import for standalone scripts and tests. Copy the component
directory into Home Assistant's `custom_components/` and restart Home
Assistant. The manifest installs pinned `pyControl4` 2.0.2. This component
has **not yet been run inside a Home Assistant instance**.

The config flow accepts a local Director hostname/IP and Control4 account
credentials. The account service issues a Director JWT; a separate local
REST/WebSocket connection uses that token. Only the local self-signed Director
certificate is exempt from TLS verification. Credentials are stored in the
Home Assistant config entry, never in this repository, and never logged.

On setup, inventory discovers `light_v2` and `thermostatV2` devices. The
transport subscribes to their `OnDataToUI` events, waits for a real Director
subscription ID, then reads REST variables once. It repeats REST sync after
reconnect and every 15 minutes by default. Normal state changes use push;
entities set `should_poll = False`. Loss of the push connection marks entities
unavailable until reconnection/resync. Unload removes subscriptions and closes
the owned connection/session. Diagnostics include only aggregate counts and
redacted configuration, never raw payloads, tokens, locations, or names.

The light platform currently selects only dimmable `light_v2` items with
observed push semantics. It offers on/off and brightness only if Director
advertises the matching commands/capabilities. On/off-only light variants were
readable via REST `LIGHT_STATE` but their WebSocket payload has not been
observed, so they are intentionally not exposed yet. The climate platform currently exposes
only Celsius `thermostatV2` devices and advertised Off/Heat/Cool/Auto modes.
Heat/Cool/Auto setpoint routing uses Director's advertised
`SET_SETPOINT_HEAT`, `SET_SETPOINT_COOL`, and `SET_SETPOINT_SINGLE` metadata.
Device registry metadata comes from inventory (`roomName`, manufacturer,
model, proxy). Color, fan, hold, and humidity controls are intentionally out
of scope. Switch, sensor, and binary sensor await device-specific push and
command evidence.

Command requests are checked against a live read of `/items/{id}/commands`
before sending. The JSON POST body follows the production MCP REST client:
`{"command": "...", "params": {...}}`. The command names, parameter names,
allowed modes, and setpoint ranges were confirmed by read-only Director
metadata on 2026-10-03. **No command POST has been issued by this project**;
successful live writes and subsequent HA state transitions remain unverified.

A read-only Core3 smoke run on 2026-10-03 found 107 items with one of the
target proxy names, but only 25 dimmable lights and 7 heating/cooling
thermostats matched the proven first-slice semantics. The narrowed transport
subscribed and normalized all 32 initial REST states in 24.1 seconds; all 32
command metadata reads completed by 41.7 seconds total. It stopped cleanly.
An earlier broad 107-item run timed out during serial initial sync, prompting
bounded (six-way) read concurrency and conservative inventory selection.

Run the offline tests from the repository root:

```bash
/opt/control4-mcp/.venv/bin/python -m unittest discover -s tests -v
```

The existing MCP virtualenv is used only as a convenient test interpreter;
the component itself does not depend on MCP or C4SOAP.
