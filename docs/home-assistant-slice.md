# Phase 3: first Home Assistant slice

The custom component lives at
`custom_components/control4_advanced/`. Its `transport/` package remains
Home Assistant-independent; `control4_transport` at the repository root is a
compatibility import for standalone scripts and tests. Copy the component
directory into Home Assistant's `custom_components/` and restart Home
Assistant. The manifest installs pinned `pyControl4` 2.0.2. Version 0.1.2 was
published through HACS and field-tested in Home Assistant 2026.9.4. Version
0.1.3 was also published through HACS, after which the user confirmed Sony
Plug works in both directions in HA. The exact installed version was not
reported separately. The user reports both Kid Window and Kid PIR working
correctly in HA with the binary-sensor slice. The exact installed version
and public-branch status were not independently verified.
The user reports that version 0.1.5's thermostat hold/preset controls work in
both directions in HA. The exact installed version and public-branch status
were not independently verified from this workspace.
Version 0.1.9 initially added a read-only media-player entity for rooms with
complete REST volume/mute state and a bound `aswitch` volume device. Later
releases added volume, mute, source, and off control; the user has field-tested
those paths on Living/Wiim, not on every room or volume binding.

The unpublished 0.1.14 candidate adds separate, stateless room Play/Pause/Stop
buttons. Each requires the exact parameterless command in that room's live
Director metadata, shares the room's HA device, and is unavailable while the
room is off or disconnected. Each press verifies live room power over REST
before sending a command. The media player does not infer playback state.
Version 0.1.15 additionally exposes the live-verified door-contact proxy as a
read-only HA door binary sensor.
Version 0.1.16 adds configured, account-aware TIDAL playlist select entities
to eligible room devices. Each playlist command is allowlisted against a
visible room source and the live `SelectPlaylist` metadata, and sends Shuffle
as `Off`. Playlist mappings are stored in the HA config-entry options because
Director REST omits the dynamic custom-select values that Composer displays.
Version 0.1.28 exposes the same allowlisted accounts and playlists through each
eligible room media player's native Home Assistant browse tree. Browse playback
reuses `SelectPlaylist`; it does not claim unsupported live TIDAL search.
Version 0.1.29 also exposes the validated Watch and Listen sources as playable
app tiles under the native browse tree and removes the media player's source
dropdown. Browser source selection retains the inventory allowlist, exact
room-command metadata gate, and settled REST readback used by the earlier UI.
Version 0.1.30 adds configured TuneIn station favorites under
**TuneIn > source > station** in the same browser. The options parser accepts
only `source | name | Station/s...` records. Catalog construction joins each
record to a visible inventory-backed TuneIn source, and playback requires the
live `Play Item(Item, Room)` metadata plus the registered room/station
allowlist.

The config flow accepts a local Director hostname/IP and Control4 account
credentials. The account service issues a Director JWT; a separate local
REST/WebSocket connection uses that token. Only the local self-signed Director
certificate is exempt from TLS verification. Credentials are stored in the
Home Assistant config entry, never in this repository, and never logged.

On setup, inventory discovers supported `light_v2`, `thermostatV2`,
`relaysingle_radiantfloor_c4`, `relaysingle_relay_c4`,
`contactsingle_windowcontactsensor_c4`, and
`contactsingle_motionsensor_c4` devices. The transport subscribes to their
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
cleanup is skipped. This has offline safety tests and the user confirmed that
a previously deleted Control4 device disappeared after an HA reload. Removing
a registry entry also
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
Living AC also exposed a named `PRESET` REST state, `preset` push updates,
and seven `SET_PRESET.NAME` choices. Version 0.1.5 therefore adds its native
climate preset selector. Kid AC did not advertise `SET_PRESET`, so no preset
control is exposed there. Both devices advertised `SET_MODE_HOLD` and a
current `HOLD_MODE`; hold is shown as the climate attribute
`control4_hold_mode` and as a companion select entity on the same HA device.
The select and preset commands are validated against each device's live
metadata and wait for Director feedback rather than optimistically changing
the displayed state. The Living AC push fields were captured. The user later
reported bidirectional HA operation of the new thermostat controls, without
identifying individual choices or whether Kid AC hold was included. A Kid AC
hold transition has not been captured in the read-only probe.
Device registry metadata comes from inventory (`roomName`, manufacturer,
model, proxy). The switch platform selects only `relaysingle_radiantfloor_c4`
and `relaysingle_relay_c4` items with a valid initial `RelayState` and
advertised parameterless `OPEN`/`CLOSE` commands. All newly discovered relay
switches are disabled by default in the HA entity registry because these
proxies include heating, fans, and other loads; the user must deliberately
enable each desired switch. This
default applies only when an entity is first added to the registry; it does
not change an existing entity's enabled state. Kid
Heating Panel ID `579` supplied the radiant-floor push and physical mapping,
and the user confirmed HA feedback and control. Sony Plug ID `431` supplied
the regular-relay read-only REST and push evidence, and the user subsequently
confirmed HA control and feedback in both directions after 0.1.3 was published.
The user subsequently reported that Vent ID `1846` and Boiler ID `1845`,
also regular relays, work in HA. The exact direction and sequence of each of
those two tests were not described separately. Other same-proxy relays have
not been individually verified.
The binary-sensor platform exposes the three observed contact proxy types as
`window`, `motion`, and `door`, respectively. It reads `ContactState` once for
initial state and after reconnect, then uses only verified `contact_state`
push updates for normal state changes. Director `OPENED`/REST `0` means on
(window or door open, or PIR motion); `CLOSED`/REST `1` means off. The user
confirmed the PIR trigger/clear timestamps separately after an earlier
ambiguous answer, and confirmed both the window mapping and the Main Door
mapping through live physical tests. All three sensor types are read-only and
enabled by default; they do not require Director command metadata. If the
initial REST read fails, their HA state remains unavailable until a verified
push update or later reconciliation.
Window and motion devices have user-reported HA field validation; door entity
registration in HA is still pending. Other sensor proxies,
`cardaccess_wirelessrelay`, and `uibutton` remain out of scope. Color and
humidity controls are still out of scope. Fan mode has a metadata-gated 0.1.6
implementation. Both known ACs' choices/current state and
Kid AC fan-change push have live Director evidence. The user reports fan
control working in both directions in HA, without identifying individual
devices or choices;
see [fan-mode investigation](thermostat-fan-mode.md).
Version 0.1.7 also exposes GREE ID `404` fan and preset selectors from its
advertised command choices despite missing initial active values. Direct
WebSocket updates for both were captured; an empty `preset` update clears
the active preset. GREE does not advertise `SET_MODE_HOLD`, so no writable
hold selector is created. This revision needs a separate HA field test.

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
reported working in both directions on Home Assistant 2026.9.4. In version
0.1.2 the user additionally confirmed on/off-only light and Kid Heating Panel
commands and feedback in both directions. After 0.1.3 was published, the user
confirmed the same for Sony Plug and reported Vent and Boiler working too.
These are user-reported runtime results, not automated HA integration tests.

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
