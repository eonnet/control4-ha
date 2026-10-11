# Control4 Advanced for Home Assistant

An experimental, unofficial push-first Control4 integration. It uses Director
REST for authentication, inventory, initial state, and commands, and the
`/api/v1/items/datatoui` WebSocket for normal state updates. It does not
require C4SOAP or the separate Control4 MCP project.

This is an early preview. In Home Assistant 2026.9.4, a user confirmed
bidirectional light, climate, radiant-floor relay, and Sony Plug regular-relay
operation; Vent and Boiler were also reported working. Removal of a deleted
Director device after reload was confirmed. A manual Control4 dimmer change
appeared in HA about 3–4 seconds later; that interval was not instrumented
end-to-end. These are field reports, not automated HA integration tests. Test
each device before using it in automations.

## Install with HACS

1. In HACS, open the top-right menu and choose **Custom repositories**.
2. Add `https://github.com/eonnet/control4-ha` with type **Integration**.
3. Install **Control4 Advanced**, then restart Home Assistant.
4. In **Settings → Devices & services**, add **Control4 Advanced**. Enter the
   local Director hostname/IP and your Control4 account credentials in Home
   Assistant's configuration flow. Do not post credentials in an issue.

The integration selects dimmable and on/off-only `light_v2` devices with
advertised `ON`/`OFF` commands, Celsius `thermostatV2` devices with heat/cool
capability, and `relaysingle_radiantfloor_c4` or `relaysingle_relay_c4`
switches with advertised parameterless `OPEN`/`CLOSE` commands and a readable
initial state. Version 0.1.4 also adds read-only `window` and `motion` binary
sensors for the observed `contactsingle_windowcontactsensor_c4` and
`contactsingle_motionsensor_c4` proxies. Their REST initial-state and settled
WebSocket mappings were traced on a Core3, and the user reports both Kid
Window and Kid PIR working correctly in HA. Version 0.1.5 adds the
Director-advertised named presets to the climate selector for eligible
thermostats, plus a companion hold-mode select for
thermostats with a readable hold state. Living AC supplied the preset and
hold push evidence; Kid AC advertised hold but no `SET_PRESET` command.
The user reports that these new HA controls work in both directions, but did
not identify every option tested or whether Kid AC hold was included. Version
0.1.6 adds climate fan-mode choices from live Director metadata (`Auto`, `Low`,
`Medium`, `High`) on both Living AC and Kid AC. Kid AC fan changes produced
direct WebSocket feedback; Living AC fan labels were observed during preset
changes. The user reports fan mode working in both directions in HA; the
specific ACs and choices tested were not identified separately. Version
0.1.7 exposes GREE's advertised fan and preset controls even though its
initial active values are unavailable. The values remain unknown until
Director feedback arrives, and an empty preset update clears the old value.
GREE does not advertise a hold command, so it has no writable hold selector.
This change has offline tests but still needs a GREE HA field test. All newly
discovered relay switches are disabled by default:
these proxies include heating, fans, and other loads. Enable only the intended
entity under **Settings → Devices & services → Entities**, then verify feedback
from a manual Control4 change before testing HA control. Other relay proxies,
`uibutton`, color, and dedicated fan entities remain excluded.

Version 0.1.8 reads GREE's active target from its single-setpoint Celsius
field in Heat and Cool. When REST confirms the separate heat/cool fields are
zero, HA uses the Director-advertised `SET_SETPOINT_SINGLE` command for that
thermostat; other thermostats keep their existing setpoint behavior. This is
offline-tested, and the user subsequently confirmed GREE setpoint operation
in both directions.

Version 0.1.9 adds a read-only room media player for rooms whose REST power,
volume, and mute state is complete and whose active volume device uses the
observed `aswitch` push profile. Bound-device notifications trigger an
immediate room REST refresh; the device notification itself does not set the
room value. This first media slice advertises no volume, mute, playback,
power, or source controls. Living/Wiim supplied the Director evidence, and
the user confirmed volume and mute feedback in Home Assistant.

Version 0.1.10 adds room volume and mute controls when Director advertises
the exact integer 0–100 volume parameter and both parameterless mute
commands. HA continues to display settled Director feedback rather than
optimistically changing state. The user confirmed volume and mute operation
in both directions. Playback and power controls remain unavailable.

Version 0.1.11 adds a Living room source selector from Director's
`/api/v1/agents/ui_configuration` Watch/Listen catalog. Only entries mapped
to supported inventory devices and a room-advertised source command appear;
synthetic entries and UI buttons are excluded. The selector reads Director
state after a command and does not optimistically claim a new source. The
user confirmed HA-to-Control4 source selection but reported missing feedback
for changes made in Control4. Version 0.1.12 adds a source-only REST fallback
every 15 seconds for rooms with an advertised selector. It updates HA only
when the selected source changes; volume and mute remain push-triggered. The
media player at that version still required a supported volume binding at startup.

Version 0.1.13 also reconciles room power and volume-device binding in that
15-second fallback. It restores volume/mute from Director only when the
binding changes, and keeps an already registered media room available after
a reload while its off-state binding is zero. A guarded Off control is shown
when the room is on and Director advertises `ROOM_OFF`; HA confirms the result
with a settled REST read. The user confirmed that selecting a source turns
Living on. There is no generic On control because no source can be chosen
without a user-defined rule. The user subsequently confirmed that Living
turned off from HA, remained visible as Off after an integration reload,
and turned back on through source selection with the previous volume and
mute values. Both volume and mute feedback worked after that power cycle.
These reports validate the current Living/Wiim binding, not other rooms.

Version 0.1.14 adds separate Play, Pause, and Stop button entities under an
eligible Control4 room device. A button is created only if that room advertises
the corresponding parameterless command. It is unavailable while the room is
off or disconnected, and each press checks live room power over REST before
sending its command. The buttons are stateless: the media player continues to
show verified room On/Off without claiming Playing/Paused feedback. This
version passed offline tests but its playback buttons still need an HA field
test. Test each button with a noncritical source before using it in an
automation.

Version 0.1.15 adds read-only door binary sensors for the observed
`contactsingle_doorcontactsensor_c4` proxy. A live Main Door trace confirmed
verified `OPENED`/REST `0` as open and `CLOSED`/REST `1` as closed, matching
the existing contact transport contract. The user subsequently confirmed the
door entity in Home Assistant.

Version 0.1.17 adds two linked selectors to each eligible Control4 room:
**TIDAL account** and **TIDAL playlist**. Choosing an account selects that
actual Control4 Listen source in the room, and the playlist dropdown then
shows only playlists configured for that account. Director REST advertises
the playlist command but omits
the dynamic name/UUID choices shown by Composer, so enter them once under
**Settings → Devices & services → Control4 Advanced → Configure**, one per
line:

```text
TIDAL source name | Playlist name | Playlist UUID
```

The source name must match the Listen source shown by Control4 (for example
`TIDAL`, `Juliane`, `Miki`, or `Jakob`). The integration adds the selectors only
to rooms where that source is exposed. Selecting a playlist sends the live-
validated `SelectPlaylist` command to the same room with Shuffle fixed to
`Off`. The selector is stateless because Director does not report which
playlist created the current queue.

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

State is initialized through REST, then updated from WebSocket events;
entities do not poll when a usable push path has been proven. REST re-syncs
after reconnect and on a slow reconciliation interval. Home Assistant device metadata includes the
Control4 room as a suggested area. Diagnostics omit credentials, raw events,
locations, and device names.
On reload, registry entries for Director items deleted from inventory are
removed only after two successful inventory reads. This cleanup has offline
tests and has been confirmed for one previously deleted device in a live HA
reload.

Authentication requires access to the Control4 account service. Once a
Director token has been obtained, device communication is local. The local
Director's self-signed TLS certificate is not verified; cloud authentication
still uses normal TLS verification.

Issues and test reports: [GitHub Issues](https://github.com/eonnet/control4-ha/issues).

## License

This repository is licensed under the [Apache License 2.0](LICENSE).
Control4 Advanced is an independent project and is not affiliated with or
endorsed by Control4. The license does not grant rights to Control4's
software, trademarks, or other third-party material.
