# Phase 3: first Home Assistant slice

The custom component lives at
`custom_components/control4_advanced/`. Its `transport/` package remains
Home Assistant-independent; `control4_transport` at the repository root is a
compatibility import for standalone scripts and tests. Copy the component
directory into Home Assistant's `custom_components/` and restart Home
Assistant. The manifest installs pinned `pyControl4` 2.0.2. A user reports
running the earlier light/climate component in Home Assistant 2026.9.4. The
on/off-only light and radiant-floor switch additions are local changes, not
yet published to HACS or verified in HA.

The config flow accepts a local Director hostname/IP and Control4 account
credentials. The account service issues a Director JWT; a separate local
REST/WebSocket connection uses that token. Only the local self-signed Director
certificate is exempt from TLS verification. Credentials are stored in the
Home Assistant config entry, never in this repository, and never logged.

On setup, inventory discovers supported `light_v2`, `thermostatV2`, and
`relaysingle_radiantfloor_c4` devices. The transport subscribes to their
`OnDataToUI` events, waits for a real Director
subscription ID, then reads REST variables once. It repeats REST sync after
reconnect and every 15 minutes by default. Normal state changes use push;
entities set `should_poll = False`. Loss of the push connection marks entities
unavailable until reconnection/resync. Unload removes subscriptions and closes
the owned connection/session. Diagnostics include only aggregate counts and
redacted configuration, never raw payloads, tokens, locations, or names.
After a successful reload, registry entries owned by this integration are
removed only if their Director IDs are absent from both full REST inventory
reads made during setup. Entries for other config entries, other hosts, or
non-Control4 identifiers are preserved. If either inventory read is empty,
cleanup is skipped. This has offline safety tests but still needs live HA
verification against a deleted Control4 item. Removing a registry entry also
removes its HA-side name/area customization; if the Director item later returns,
automations referring to the old entity or device may need attention.

The light platform selects dimmable and on/off-only `light_v2` items when
Director advertises `ON`/`OFF` and an explicit boolean dimmer capability.
Bathroom Ceiling ID `1150` is an observed on/off-only example: its REST
`LIGHT_STATE` and push `LIGHT_LEVEL` 0/100 were confirmed on 2026-10-03.
Brightness control is offered only when a dimmer and `SET_LEVEL` are advertised.
A supported light is registered even when its initial REST read fails: it
remains unavailable until a settled push event or later REST reconciliation
provides state. The climate
platform currently exposes only Celsius `thermostatV2` devices and advertised
Off/Heat/Cool/Auto modes.
Heat/Cool/Auto setpoint routing uses Director's advertised
`SET_SETPOINT_HEAT`, `SET_SETPOINT_COOL`, and `SET_SETPOINT_SINGLE` metadata.
Device registry metadata comes from inventory (`roomName`, manufacturer,
model, proxy). The switch platform selects only `relaysingle_radiantfloor_c4`
items with a valid initial `RelayState` and advertised `OPEN`/`CLOSE` commands.
They are disabled by default in the HA entity registry because they control
heating loads; the user must deliberately enable the desired switch. This
default applies only when an entity is first added to the registry; it does
not change an existing entity's enabled state. Kid
Heating Panel ID `579` supplied the observed on/off push and physical mapping.
After installing version 0.1.2, find the disabled Kid Heating
Panel switch under Settings → Devices & services → Entities and enable only
that entity. First verify its state by changing the panel in Control4; then
test an HA command while observing the physical panel and settled feedback.
Other same-proxy heating relays have not been individually verified. Basic
relays, `uibutton`, sensors, and binary sensors remain out of scope. Color,
fan, hold, and humidity controls are also out of scope.

Command requests are checked against a live read of `/items/{id}/commands`
before sending. The JSON POST body follows the production MCP REST client:
`{"command": "...", "params": {...}}`. The command names, parameter names,
allowed modes, and setpoint ranges were confirmed by read-only Director
metadata on 2026-10-03. No agent-initiated command POST has been issued.
On 2026-10-03, a user reported that a Home Assistant light command worked.
Separately, changing the Hallway dimmer in Control4 appeared in Home Assistant
about 3–4 seconds later. This is user-reported, not a synchronized latency
measurement. An earlier Director capture for Hallway received a transition
target at 19:29:03.205Z and settled `LIGHT_LEVEL: 0` at 19:29:05.968Z, 2.763
seconds later. The integration waits for the settled event before writing the
new on/off state; that ramp interval is a plausible explanation for most of
the reported delay, but has not been proven for the latest change. Climate
setpoint and HVAC-mode commands and Control4-to-HA state feedback were also
reported working in both directions on Home Assistant 2026.9.4. This is
user-reported runtime evidence, not an automated HA integration test.

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
