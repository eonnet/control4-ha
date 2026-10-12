# Control4 Home Assistant integration

Phase 1 established usable Director WebSocket push events on this Core3.
Phase 2 provides a small Home Assistant-independent transport layer. Phase 3
contains a custom-component slice for `light_v2`, Celsius `thermostatV2`, and
opt-in relay switches. Version 0.1.4 adds read-only window and motion binary
sensors from the two observed contact proxies. The user reports both Kid
Window and Kid PIR working correctly in HA; the exact installed version and
public-branch status were not independently verified. Version 0.1.2 has been
field-tested in Home Assistant 2026.9.4: on/off lighting, climate, and one
radiant-floor relay work in both directions, and stale device cleanup works
after reload. A manual Control4
Hallway dimmer change appears in HA about 3–4 seconds later; that interval has
not been instrumented end-to-end. Version 0.1.3 adds the observed regular
relay proxy; the user confirmed Sony Plug works in both directions in HA and
subsequently reported that Vent and Boiler work too. Other regular relays have
not been individually verified.
Version 0.1.5 adds Living AC named presets to the climate selector and a
companion hold-mode select for eligible thermostats. The user reports that
the new thermostat controls work in both directions in HA. Individual choices
and Kid AC hold were not identified separately in that report.
Version 0.1.6 adds fan mode: both ACs advertise `Auto`,
`Low`, `Medium`, and `High`, and Kid AC has direct fan-change push feedback.
The user reports fan mode working in both directions in HA; the specific ACs
and choices tested were not identified separately.
Version 0.1.7 makes GREE's advertised fan and preset controls visible even
when Director omits their active values from the initial REST snapshot. Their
displayed values remain unknown until push feedback arrives; an empty GREE
preset update clears the displayed preset. GREE does not advertise a hold
command, so it has no writable hold selector. This change is offline-tested
but has not yet been field-tested in HA.
Version 0.1.8 detects GREE's single-setpoint REST signature and uses its
`setpoint_single_c` push updates for the climate target in Heat and Cool.
It sends the advertised `SET_SETPOINT_SINGLE` command for GREE while retaining
the existing heat/cool command route for separate-setpoint thermostats.
This is offline-tested, and the user subsequently confirmed GREE setpoint
control and feedback working in both directions in HA.

## WebSocket probe

The probe is read-only: it obtains a short-lived Director token through the
already-proven MCP token helper, reads `/api/v1/items` to identify inventory,
then subscribes to Director's `datatoui` WebSocket feed. It never invokes a
Control4 device command.

It deliberately does not read or copy any `.env` file. By default it invokes
the existing trusted helper in `/opt/control4-mcp`; override those paths only
with equivalent local tooling.

```bash
python -m venv .venv
.venv/bin/pip install -r requirements-probe.txt
.venv/bin/python tools/websocket_probe.py --verbose
```

For the currently installed trusted environment, this is also suitable for a
first run without installing new packages:

```bash
/opt/control4-mcp/.venv/bin/python tools/websocket_probe.py --duration 60 --verbose
```

While it runs, manually change a light's power and level and Living AC's
setpoint and HVAC mode. The probe prints JSON Lines with redacted payloads.
Use `--force-reconnect-after 20` only to exercise a client-initiated local
disconnect/reconnect; it does not command a Control4 device.

For a focused, three-minute manual test of the currently identified entities:

```bash
/opt/control4-mcp/.venv/bin/python -u tools/websocket_probe.py \
  --duration 180 --watch-ids 2646,2726,3177
```

The IDs are Living AC, Lamp Table, and Hallway respectively. Record the UTC
time of each manual change; the Director's `time` field has only whole-second
precision, while `received_at` is the probe's millisecond timestamp. With
`--watch-ids`, the probe also emits a separate `watched_inventory` record so
the live REST ID, name, and proxy can be checked without sharing the full
inventory sample.

To list possible regular relay switches without opening a WebSocket or sending
any device command:

```bash
/opt/control4-mcp/.venv/bin/python -u tools/websocket_probe.py --inventory-only
```

Look for the `relay_inventory` record. It lists up to 50 basic or wireless
relay candidates, not automatically enabled HA switches. Gate relays and
`uibutton` devices such as ID `319` Extract Fan are excluded. A separate Vent
relay exists at ID `1846`. The radiant-floor proxy used by Kid Heating Panel
is outside this basic-relay listing; its state and push behavior were tested
separately.

To identify a named switch or on/off-only light before a focused push trace:

```bash
/opt/control4-mcp/.venv/bin/python -u tools/websocket_probe.py --inventory-only \
  --find-name 'Kid Heating Panel' --find-name 'Ceiling'
```

The bounded `matched_inventory` record contains IDs, proxies, and selected
capability flags. It does not include credentials or tokens. Share that record,
then use the confirmed IDs with `--watch-ids` while changing the devices
manually in Control4; do not guess an ID from its display name.

For confirmed IDs, `--inspect-ids` reads only the REST variable and command
metadata. It emits variable/command names plus a small allowlist of simple
state values; it never executes a command or prints arbitrary variable values:

```bash
/opt/control4-mcp/.venv/bin/python -u tools/websocket_probe.py \
  --inventory-only --inspect-ids 1150,579
```

These IDs are Bathroom Ceiling and Kid Heating Panel. The latter has a
specialized `relaysingle_radiantfloor_c4` proxy. Its live state and advertised
commands have been observed. Once this local update is published and installed,
the HA switch will be disabled by default until the user enables it.

See `docs/websocket-protocol.md` for the observed/upstream-derived protocol
model and an evidence checklist.

## Transport layer (Phase 2)

`control4_transport` contains a direct Control4 account token provider, an
authenticated Director REST client, inventory and state caches, an event
dispatcher, and a WebSocket connection supervisor. It reads the tracked items'
REST variables on startup and after reconnection. Periodic reconciliation is
optional (`reconciliation_seconds=None` disables it). Normal push updates come
from WebSocket events.

The account provider accepts credentials in memory from its caller and obtains
a Director token through Control4's account API. It does not invoke the MCP
project or read that project's `.env`. Local Director TLS verification is
controlled by the caller's `aiohttp.ClientSession` connector; cloud account
requests always use verified TLS.

```python
import aiohttp
from control4_transport import AccountTokenProvider, Control4Transport, DirectorRestClient

provider = AccountTokenProvider(username, password, controller_common_name=None)
async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session:
    rest = DirectorRestClient("192.168.0.24", provider, session)
    transport = Control4Transport(rest, tracked_ids=[2646, 2726])
    try:
        transport.events.subscribe(async_callback)
        await transport.start()
        # Read state with transport.state.snapshot(2646).
    finally:
        await transport.stop()
```

The example assumes `username` and `password` were supplied securely by the
caller; do not log them. `DirectorRestClient.post_json()` exists as a generic
REST operation, but the transport itself sends no device command.

Run the offline transport tests with:

```bash
/opt/control4-mcp/.venv/bin/python -m unittest discover -s tests -v
```

See [transport design](docs/transport.md) for field mappings and recovery rules.

## First Home Assistant slice (Phase 3)

See [component scope and verification status](docs/home-assistant-slice.md).
The component is self-contained under `custom_components/control4_advanced/`
and can be installed through HACS after these local changes are published.
Light control and Control4-originated feedback have been reported working in
Home Assistant; verify newly added device types separately before using them
in automations.

The [development backlog](docs/backlog.md) defers numeric sensors. The
[thermostat hold/preset evidence](docs/thermostat-hold-presets.md) describes
the read-only Director trace and the user-reported HA field test.
Read-only [media-player discovery](docs/media-player-discovery.md) found
Wiim output-volume push while Wiim was the Living room's bound volume device.
Sony's matching volume events are a Control4 Programming mirror, not an
independent room-volume source. The media-player platform uses
bound-device push to trigger immediate room REST reads and displays the
room's power, volume, and mute state. The user has confirmed volume and mute
feedback in HA. Version 0.1.10 adds volume and mute controls only when the
room advertises the exact validated command metadata. The user confirmed
those controls work in both directions. Version 0.1.11 adds a source selector
from the Director UI configuration, limited to inventory-backed media sources
and advertised room commands. The user confirmed HA-to-Control4 source
selection but reported that external Control4 source changes were not shown
in HA. Version 0.1.12 reconciles only the selected room source from REST
every 15 seconds when no source push route is known; volume and mute remain
push-triggered. Version 0.1.13 adds room power and volume-binding feedback to
that fallback and retains an already registered room after a reload while it
is off. It adds a guarded Off control with Director readback. The user
confirmed that selecting a source turns Living on; a generic On control is
not offered because no source-selection rule has been established. The user
subsequently confirmed that Living turned off from HA, remained visible as
Off after an integration reload, and turned back on through source selection
with the previous volume and mute values. Both volume and mute feedback
worked after that power cycle. These reports validate the current
Living/Wiim binding, not other rooms.

The unpublished 0.1.14 candidate adds separate Play, Pause, and Stop button
entities under each eligible Control4 room device, only for commands advertised
without parameters by that room. They are unavailable while the room is off or
disconnected, and each press checks live room power over REST before sending
its command. These are stateless command buttons: the media player still
reports verified room power as On/Off and does not claim Playing/Paused without
feedback. Their live behavior has not yet been field-tested.

Version 0.1.15 adds read-only door binary sensors for the observed
`contactsingle_doorcontactsensor_c4` proxy. A live Main Door trace confirmed
verified `OPENED`/REST `0` as open and `CLOSED`/REST `1` as closed, matching
the existing contact transport contract. The user subsequently confirmed the
door entity in Home Assistant.

Version 0.1.17 adds two linked selectors to each eligible Control4 room:
**TIDAL account** and **TIDAL playlist**. Choosing an account selects that
actual Control4 Listen source in the room, and the playlist dropdown then
shows only playlists configured for that account. Director's REST metadata
advertises the playlist command
but omits the dynamic playlist-name/UUID choices shown by Composer, so those
choices are entered once under **Settings > Devices & services > Control4
Advanced > Configure**. Use one line per playlist:

```text
TIDAL source name | Playlist name | Playlist UUID
```

For example:

```text
TIDAL | Evening Mix | 01234567-89ab-cdef-0123-456789abcdef
```

The source name must match the Listen source shown by Control4 (for example
`TIDAL`, `Juliane`, `Miki`, or `Jakob`). The integration joins that source to
its TIDAL protocol driver and adds the selectors only to rooms where the source
is exposed. Selecting a playlist sends Control4's advertised `SelectPlaylist`
command to that same room with `Shuffle` fixed to `Off`. The selector remains
stateless because Director does not report which playlist created the current
queue.

Version 0.1.18 also exposes each room's advertised Play, Pause, and Stop
commands as native controls on the Home Assistant media-player dialog. Because
Director does not report playback state, these controls are explicitly marked
as assumed and never change the room's verified On/Off state. Every press
re-reads live room power before sending the command. The separate button
entities remain available for existing dashboards and automations.

Version 0.1.19 exposes every Control4 room that has at least one validated
Watch or Listen source as a Home Assistant media player. A proven volume
binding is no longer required for the room entity to exist; volume and mute
controls remain conditional on their existing validation, while source and
playback controls use each room's own advertised commands.

Version 0.1.20 corrects the Play, Pause, and Stop request body to the
asynchronous `tParams` envelope used by `pyControl4`'s room implementation.
The earlier generic command envelope was accepted by Director but did not
operate room playback on this installation.

Version 0.1.21 adds native Previous Track and Next Track media-player controls
plus matching room buttons. They use the parameterless `SKIP_REV` and
`SKIP_FWD` commands advertised by every discovered media room, with the same
live room-power guard and asynchronous request envelope as the other playback
actions.

Version 0.1.22 displays the current title, artist, and album from Director's
structured `CURRENT MEDIA INFO` room variable. These labels refresh with the
existing room reconciliation and immediately after Previous or Next; stale
labels are cleared when the room turns off. Artwork URLs and unrelated media
payload fields are deliberately not exposed.

Version 0.1.23 maps the room's verified power and selected-source fields to
Home Assistant media states: Off when room power is off, Playing when the room
is on with a positive selected-source ID, and Idle when the room is on without
a selected source. This is a source-selection state, not play/pause feedback.

Version 0.1.24 displays the selected Control4 source name as the media title
when that source supplies no track or programme metadata. For example, a
selected Apple TV now shows `Apple TV`; real media titles continue to take
priority whenever Director provides them.

Version 0.1.25 adds native multi-room support to the Control4 room media
players. Home Assistant's standard `media_player.join` action uses the
Director's Digital Media `ADD_ROOMS_TO_SESSION` command, and each entity's
`group_members` attribute follows the authoritative `/api/v1/media_sessions`
state. `media_player.unjoin` uses Control4's documented Room Off behavior to
remove only that room from the session. Join and unjoin wait for Director to
confirm the new membership instead of updating Home Assistant optimistically.

Version 0.1.26 keeps native grouping advertised on powered-off room players so
Home Assistant includes them in the active leader's available-player list.
Selecting an off room adds it to the leader's Control4 session and powers it
through the native session command.

Version 0.1.27 accepts Home Assistant's empty preparatory join request when
the last selected member is removed. This lets the dialog continue with its
separate unjoin request, which removes and powers off the deselected room.

Version 0.1.28 adds the configured TIDAL accounts and playlists to Home
Assistant's native media browser on each eligible Control4 room player. The
browse hierarchy is **TIDAL > account > playlist**. Selecting a playlist uses
the same allowlisted `SelectPlaylist` command as the playlist selector and
keeps shuffle off. This intentionally exposes only playlists entered in the
integration options; full TIDAL catalog browsing and search require a
Control4-authorized MSP command channel that Director does not expose through
its authenticated REST/WebSocket API.

Version 0.1.29 moves the room's validated Watch and Listen sources into the
same native media browser. The top-level hierarchy is now
**Control4 > Watch / Listen / TIDAL**, and each source is a playable app tile.
The media player no longer advertises Home Assistant's source-select feature
or a source list, so the old source dropdown is removed. Source commands keep
the existing inventory allowlist, live Director command-metadata gate, and
REST confirmation before Home Assistant displays the changed source.

Version 0.1.30 adds configured TuneIn favorites to that browser under
**Control4 > TuneIn > source > station**. Director advertises its `Play Item`
command but omits the dynamic `GetItemList` choices shown by Composer, so enter
one favorite per line under **Settings > Devices & services > Control4
Advanced > Configure**:

```text
TuneIn source name | Favorite name | Station/id
```

For example:

```text
TuneIn | Example Radio | Station/s12345
```

The source name may match either the visible Listen source (such as
`TuneIn Premium`) or its TuneIn protocol driver (often `TuneIn`). A favorite
appears only in rooms where that inventory-backed source is exposed. Playback
requires the exact live `Play Item(Item, Room)` metadata and accepts only the
configured `Station/s...` IDs; arbitrary URLs and unconfigured IDs are
rejected.

Version 0.1.31 adds local logo thumbnails to the native media browser. Watch
and Listen folders get distinct tiles, and known sources such as Netflix,
Plex, Apple TV, HBO Max, Xplore TV, Sony, Xiaomi, WiiM, Spotify, YouTube,
TIDAL, and TuneIn get matching source tiles. Unknown sources use the generic
Watch or Listen artwork. The packaged SVGs are served through Home Assistant's
signed media-player image proxy, so they do not depend on an external image
CDN or expose an unauthenticated integration asset route.

## License

This repository is licensed under the [Apache License 2.0](LICENSE).
Control4 Advanced is an independent project and is not affiliated with or
endorsed by Control4. The license does not grant rights to Control4's
software, trademarks, or other third-party material.
