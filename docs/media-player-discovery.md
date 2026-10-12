# Media-player discovery (read-only)

Status: live read-only push discovery and initial handler check,
2026-10-04. The user confirmed that Living volume and mute work both ways in
Home Assistant after installing version 0.1.10. The agent issued no Control4
media write during development. The first candidate is the Living room.

## Observed on Core3 OS 3.4.3

- REST inventory contains a Living room item, ID `22`, and a Bedroom room,
  ID `23`. Child media devices also exist, but the room item provides the
  ordinary room-level media controls.
- ID `22` advertises `ROOM_OFF`, `SET_VOLUME_LEVEL`, `MUTE_ON`, `MUTE_OFF`,
  `PLAY`, `PAUSE`, `STOP`, `SELECT_AUDIO_DEVICE`, and `SELECT_VIDEO_DEVICE`.
  These are advertised commands, not tested write paths.
- The command metadata names `LEVEL` for `SET_VOLUME_LEVEL` and `deviceid`
  plus `deselect` for source selection. A later read-only metadata check on
  Living ID `22` found `LEVEL` is an integer range from `0` to `100`;
  `MUTE_ON` and `MUTE_OFF` are parameterless. Their working result has not
  been established on this Core3.
- A read-only REST snapshot for ID `22` returned `POWER_STATE=1`,
  `CURRENT_VOLUME=16`, `IS_MUTED=0`, `CURRENT_AUDIO_DEVICE=100002`,
  `CURRENT_SELECTED_DEVICE=100002`, `CURRENT_VIDEO_DEVICE=0`, and
  `PLAYING_AUDIO_DEVICE=1366`. Inventory maps `100002` to Digital Media and
  `1366` to a media-service device named Juliane. These fields may represent
  different parts of the active media route; their exact semantics are not
  established by this snapshot.
- The WebSocket connected and listened for ID `22`. No initial snapshot or
  event was observed during a 45-second passive session.
- During a later 150-second room-only capture, REST room volume changed from
  `16` to `41` and the selected source changed to HBO Max (ID `1751`), but no
  ID `22` event was delivered to the probe. The exact moment of each change
  relative to the subscription start was not independently recorded.
- During a 120-second capture watching the room and four relevant source IDs,
  REST room volume changed from `41` to `42`; no event was delivered for those
  watched IDs. This is evidence against those IDs providing room-volume push,
  not proof that the Director emitted no event under another ID.
- A further 120-second inventory-wide safe capture received many events from
  other devices, proving that the socket remained active. It observed an
  `EVENT`-shaped notification for Living Media Streaming (ID `624`), but no
  volume scalar. Room REST volume remained `42` across that capture, so it
  did not test a new volume transition. The active media source had changed
  during earlier checks; source state must not be inferred from the unrelated
  `EVENT` notification.

## Synchronized Living volume test

The user clarified the Control4 project wiring: Living has a `VOLUME_LEVEL`
room variable/binding, currently connected to Wiim. A different volume
device could be connected to that room binding. Sony Television's volume is
mirrored from Living through Control4 Programming; its matching push events
are therefore **not** an independent or authoritative room-volume feed.
This wiring description is user-provided; the agent has not read Composer
Programming or changed any connection. Director REST separately exposes the
observed room fields `CURRENT_VOLUME` and `CURRENT_VOLUME_DEVICE_ID`.

At 11:33:02 UTC the pre-capture REST room volume was `42`. After the
inventory-wide WebSocket subscription was confirmed listening, two
`OnDataToUI` notifications arrived for Sony Television ID `1227`:
`volume_level: 43` at 11:33:41.425 and `volume_level: 44` at 11:33:45.581.
The subsequent REST read of Living room ID `22` returned `CURRENT_VOLUME=44`.
Inventory places the Sony Television in Living. This directly establishes a
matching mirrored numeric push value for those two transitions, but it must
not be used as the HA room-volume source.

The room's `CURRENT_VOLUME_DEVICE_ID` was `623`, mapped by inventory to a
Wiim `aswitch` child of Living LinkPlay. ID `623` emitted `OutputStatus`
notifications at the same two timestamps as the television's volume events.
A later read-only REST snapshot of ID `623` returned
`OUTPUT_01_VOLUME_LEVEL=44`, `OUTPUT_01_MUTE=0`, and
`OUTPUT_02_VOLUME_LEVEL=-1`. This is a stronger dynamic association with the
room's active volume device.

Further synchronized Wiim-only captures observed `OutputStatus` as a mapping
with `OutputID`, `VolumeLevel`, and `MuteFlag` among its fields. The first
capture coincided with room and Wiim REST volume moving `44` → `43`; the
shape-only probe did not print the values. The second returned
`VolumeLevel=42`, `MuteFlag=false` and REST volume `43` → `42`. The third
returned `OutputID=4000`, `VolumeLevel=41`, `MuteFlag=false` and both room
and Wiim output-1 REST volumes moved `42` → `41`. No title, URL, input detail,
or arbitrary media value was emitted by the safe probe.

The transport now normalizes the observed Wiim `aswitch` push as a
device-scoped output ID, volume percentage, and mute flag, while preserving
raw data internally for diagnostics. It separately normalizes the room's
read-only REST power, volume, mute, and active volume-device ID. It does **not**
yet associate output ID `4000` with room ID `22`, or assume this binding holds
after a Control4 connection change. A source change alone does not establish
that the volume-device binding changed.

The room ID `22` itself emitted no event in the synchronized capture. A
media-player implementation must not assume that subscribing to the room ID
alone supplies volume feedback or use Sony ID `1227` as a volume source.

## Living mute transition

Starting from room and Wiim output-1 volume `41`, with both REST mute fields
at `0`, a manual mute/unmute sequence produced Wiim ID `623` `OutputStatus`
events with output ID `4000`: `MuteFlag=false, VolumeLevel=0`, then
`true, 0`, then `true, 41`, then `false, 41`. Sony Television ID `1227`
also emitted `volume_level=0` followed by `41`. The final REST read showed
room `CURRENT_VOLUME=41`, `IS_MUTED=0` and Wiim
`OUTPUT_01_VOLUME_LEVEL=41`, `OUTPUT_01_MUTE=0`. No event was observed for
room ID `22` itself. The first zero in device push must not be treated as a
persisted room volume setting without a room read. The intermediate room REST
state was not sampled in this earlier capture; a later quick-toggle test did
confirm that REST can report muted at 0% before reporting unmuted at 40%.

## Room-volume transport handler

`RoomVolumeCoordinator` now starts from a room REST snapshot, dynamically
registers the device named by `CURRENT_VOLUME_DEVICE_ID`, and treats only
that device's normalized output-volume/mute WebSocket updates as triggers.
It immediately starts a targeted room REST read when bound-device push arrives.
Concurrent push events share an in-flight read; an event received during that
read requests one follow-up read. There is no fixed delay. The room read
supplies the displayed volume and mute state;
the output notification itself never overwrites room state. Sony's
Programming-mirrored notification cannot trigger this refresh. A later room
REST snapshot with a different binding ID registers the new device and
ignores the old one's push for this room. Reconnect and slow reconciliation
also refresh the room binding. On 2026-10-04 a
read-only live run connected the WebSocket, loaded Living room volume `41`
and mute `false` from REST, read `CURRENT_VOLUME_DEVICE_ID=623`, and
dynamically tracked that bound item. In a subsequent manual mute/unmute test,
bound-device push arrived in two bursts 1.09 seconds apart. The room reads
showed muted at 0%, then unmuted at 40%. The user confirmed they intentionally
toggled mute quickly; these were two real states, not evidence of a false
intermediate state. The user requested removal of the fixed delay so separate
actions can be reflected as soon as the room REST state is available. A
subsequent manual volume step produced bound Wiim output volume `41` at
12:33:02.889 UTC and Living room REST volume `41` at 12:33:02.928 UTC,
39 milliseconds later in the probe's timestamps. This measures push-to-REST
observation, not physical-change-to-HA latency. Live binding-change behavior
remains unverified. The probe's first SIGINT shutdown exited with a local
packet-queue error despite leaving no test process. The upstream Engine.IO
client had replaced the probe's signal handler during connection; restoring
the handler after connection was verified by a repeat SIGINT test that emitted
`INTERRUPTED` and exited with status `0`.

## First HA entity slice

Version 0.1.9 adds a read-only `media_player` platform. At setup it reads
roomdevice REST state and selects only rooms with power, volume, mute, and a
bound `aswitch` volume device. The coordinator then tracks the room and that
bound item. The HA entity uses cached room REST values for `on`/`off`,
`volume_level`, and `is_volume_muted`; a bound-device push triggers the
immediate room refresh. It advertises zero media-player control features and
implements no Control4 write commands. If the binding changes to an
unverified proxy or state becomes incomplete, the entity is unavailable
until a supported state returns. Live Director evidence supports the initial
Living/Wiim profile. The user confirmed the HA entity appears and that
volume and mute feedback work. Startup selection and reload/unload behavior
have only offline tests so far. No physical-to-HA latency has been measured.

The user subsequently confirmed that both Living volume and mute feedback
work in Home Assistant. Version 0.1.10 reads room command metadata at setup
and enables only `VOLUME_SET` and `VOLUME_MUTE` when Director advertises the
exact volume and mute contract. HA volume 0–1 is converted to integer 0–100;
`MUTE_ON`/`MUTE_OFF` are sent without parameters. Neither method changes the
displayed state optimistically; bound-device push and room REST still supply
settled feedback. If metadata cannot be read or differs, the entity remains
read-only. No live room command was issued by the agent. After publication,
the user confirmed that HA-originated volume and mute changes work and that
their feedback returns to HA. This confirms the current Living/Wiim binding,
not a future volume-device binding change.

## Read-only power and source discovery

At 13:48:07 UTC on 2026-10-04, Living room ID `22` had `POWER_STATE=1`,
`CURRENT_SELECTED_DEVICE=1273`, `CURRENT_VIDEO_DEVICE=1273`,
`CURRENT_VIDEO_AUDIO_DEVICE=1273`, `CURRENT_AUDIO_DEVICE=0`, and
`CURRENT_VOLUME_DEVICE_ID=623`. Inventory identifies `1273` as Xplore TV.
Bedroom room ID `23` had `POWER_STATE=0`, selected device `0`, volume
device `0`, and no usable room volume level. This is a snapshot, not proof of
power-change feedback.

During a short read-only REST sampler, Living's selected/video/video-audio
device changed from `1273` at 13:50:11 to `346` at 13:50:17. Inventory
identifies `346` as Plex. `POWER_STATE` stayed true and the volume-device
binding stayed at `623`. The trigger of the change was not independently
recorded. A concurrent WebSocket probe was connected and listening for room
`22`, bound Wiim `623`, and old source `1273`, but received no event for those
IDs during the observed change. The new source `346` was not watched in that
probe, so this does not rule out a source-device event or another routing ID.
An inventory-wide safe capture later received many unrelated events, but no
controlled source or power transition took place during that capture.

The room's `SELECT_AUDIO_DEVICE` and `SELECT_VIDEO_DEVICE` command metadata
advertises same-controller read-only GET sources at
`/api/v1/locations/rooms/22/audio_devices` and
`/api/v1/locations/rooms/22/video_devices`. Each returned an object with
`visible` and `hidden` lists, all empty at the time of the read, despite
Plex being selected in the earlier room snapshot. The command parameter is
an integer `deviceid` plus a boolean `deselect`; the metadata does not embed
choices. These empty API lists are not evidence that Control4 has no
selectable sources. At this discovery stage, `ROOM_OFF` was advertised but
no power transition or turn-on route had yet been observed; later tests and
the 0.1.13 field report below supersede that initial uncertainty.

The [pyControl4 room documentation](https://lawtancool.github.io/pyControl4/room.html)
explicitly warns that the room-specific device lists may not work and points
to `GET /api/v1/agents/ui_configuration` for sources playable in a room. Its
source methods use `SELECT_AUDIO_DEVICE` and `SELECT_VIDEO_DEVICE` with a
`deviceid` argument. This is third-party library documentation, not an
official Control4 protocol guarantee. A read-only GET against this Core3 on
2026-10-04 returned ten Watch and ten Listen entries for Living room `22`.
The inventory-backed allowlist currently selects six Watch and eight Listen
entries; special synthetic IDs and `uibutton` entries are withheld because
their selection semantics are unproven. REST state
`CURRENT_SELECTED_DEVICE` supplies the initial/current source ID. The HA
native media-browser source entries gate their commands on the room's exact
advertised metadata and refresh REST state after a selection, without
optimistic feedback. Version 0.1.29 replaces the earlier source dropdown with
these playable Watch and Listen app tiles. The command effect and external
source-change latency still require user testing.
Version 0.1.30 additionally joins configured `Station/s...` identifiers to
visible TuneIn `media_service` sources. This Director advertises `Play Item`
with `Item` type `CUSTOM_SELECT:GetItemList` and a `Room` device selector, but
REST does not return the dynamic item choices. The integration therefore
stores only user-configured favorite labels and station IDs, validates the
exact command schema at startup, and sends an allowlisted station only to its
registered room.
Version 0.1.31 adds local SVG thumbnails to the Watch and Listen source
browser. The integration maps validated inventory/source metadata to a fixed
logo allowlist, uses generic Watch or Listen artwork for unknown names, and
serves the files through `MediaPlayerEntity.async_get_browse_image`. The image
handler cannot resolve arbitrary paths or remote URLs.
Version 0.1.32 additionally maps the observed `mediainfo.img` value from
`CURRENT MEDIA INFO` to the room entity's current artwork. The normalizer
accepts only bounded absolute HTTP(S) URLs without embedded credentials, and
the entity leaves `media_image_remotely_accessible` false so Home Assistant
uses its signed media-player proxy instead of exposing that upstream URL to
the frontend.
Version 0.1.33 decodes the observed Base64-wrapped TIDAL artwork value before
running the same URL validation. Invalid Base64 and decoded non-HTTP(S) URLs
remain rejected.
Version 0.1.34 exposes the same validated room-volume path as a native
percentage number slider for each eligible media room. Sliders keep independent
room IDs and settled Director feedback, allowing a dashboard to show all group
member volumes without turning the leader's value into a synthetic group
volume.
Version 0.1.38 discovers TIDAL account/source names from the same validated
inventory-backed Listen sources even when no playlists are configured. TuneIn
source names were already discovered through that Listen-source path. A
2026-10-12 read-only Navigator check sent `GetTabs` to both the TIDAL media
proxy and protocol parent using the WebSocket subscription ID assigned by
Director. Both REST requests returned HTTP 200, but neither produced a
Navigator response. By contrast, a live Control4 app session produced the
TIDAL tab response under its own Navigator identity. The authenticated REST
command metadata also continues to omit TIDAL playlist UUIDs and TuneIn's
dynamic `GetItemList` values. Automatic account/source discovery is therefore
safe, while playlists and favorites remain explicitly configured until a
reliable integration-owned Navigator/MSP channel is available.
At version 0.1.12, media-player discovery still required a supported volume
binding at integration startup. Version 0.1.13 retains an already registered
room while its off-state binding is zero; new source-only or unproven rooms
are still excluded.

## Next evidence gate

Playback commands still need separate state and command evidence before
their HA features are advertised. The room Off command was added in 0.1.13
and subsequently user-tested in HA; see the field report below.

Volume, mute, and source selection were confirmed by the user to work both
ways. Source feedback relies on targeted REST reconciliation; a corresponding
push route remains unproven.

## External source feedback fallback

The user confirmed HA-to-Control4 source selection but reported no feedback
after changing the source in Control4. A read-only Director REST check on
2026-10-04 returned Living `CURRENT_SELECTED_DEVICE=342`; inventory maps
`342` to Netflix and the Living Watch catalog includes it. Thus the selected
source is available through the existing REST parser and source list.
The earlier controlled WebSocket capture observed no room-22 event during a
source change; a source-device event has not been ruled out. The pyControl4
WebSocket documentation describes `iddevice` routing but not a reliable
room-source change notification. We do not infer one from unrelated events.

The user subsequently confirmed that external Control4 source changes now
reach HA through the 15-second fallback. This confirms working feedback, not
a WebSocket source notification or a measured change-to-HA latency.

For rooms with a discovered source catalog, a targeted fallback now reads
the room's REST variables every 15 seconds while connected, applies only
`selected_source_id` when it changes, and leaves volume/mute on their
push-triggered path. This is a source-only reconciliation, not a claim of
real-time source push. It stops on integration unload. The exact external
change-to-HA latency still needs testing after deployment; a future proven
push trigger can replace or augment this fallback.

## Living room power preparation

On 2026-10-04 at 15:20 UTC, a read-only Living room metadata check returned
`POWER_STATE=1` and advertised the parameterless `ROOM_OFF` command. The
installed pyControl4 room client reads `POWER_STATE` and documents source
selection as a way to turn a room on. A 150-second sanitized WebSocket capture for
room `22` and bound Wiim `623`, plus a two-second REST sampler, observed no
power transition; the room stayed on. Since no manual off/on occurred during
that window, this does not establish whether power push exists.

At 15:33–15:34 UTC the user manually turned Living off and back on in
Control4 while both probes were listening. The one-second REST sampler
recorded `(POWER_STATE, CURRENT_SELECTED_DEVICE, CURRENT_VOLUME_DEVICE_ID)`
as `(1, 346, 623)` at 15:33:34, `(0, 0, 0)` at 15:33:53, and `(1, 346, 623)`
at 15:34:04. Wiim `623` emitted `OutputStatus` WebSocket notifications around
both transitions. No room-`22` event appeared during those transitions in
the watched WebSocket stream; this does not rule out another device's event.
The WebSocket probe received SIGINT after the capture, disconnected, and
exited with a library cleanup `RuntimeError`; no probe process remained.

Version 0.1.13's fallback applies `is_on`, `selected_source_id`,
and `volume_device_id` from the same 15-second REST room read. Only when the
binding changes does it also re-sync volume and mute from that snapshot;
otherwise they remain push-triggered. This adds no REST requests and does
not itself advertise a generic On command. Offline tests cover on/off
transitions, binding recovery, stable-binding isolation, and a power-only
snapshot. The user later verified the HA behavior described below.

The user subsequently confirmed that selecting a Living source in HA while
the room is off turns it on. This establishes source selection as a turn-on
route, but does not choose which source a generic HA power-on button should
select. A separate 120-second REST sampler saw no off transition while waiting
for that test and therefore did not independently capture the source-on cycle.

Version 0.1.13 adds `TURN_OFF` only when Director
advertises a parameterless `ROOM_OFF` and the room is on. It sends that REST
command with no parameters and confirms settled state via readback. After
observing that an off room has
`CURRENT_VOLUME_DEVICE_ID=0`, candidate discovery was extended to retain only
an already registered media-player room whose live inventory and off snapshot
match this shape. New, unproven rooms remain excluded. No generic `TURN_ON`
button is included until the last-source behavior is decided and validated.

## 0.1.13 Home Assistant field confirmation

The user reported that Living turned off using its HA control. After reloading
the integration while the room was off, the Living media-player entity
remained visible and showed Off. Selecting a source then turned the room on;
the previous volume and mute values appeared, and the user separately
confirmed both volume and mute feedback worked after the power cycle. These
are user-observed HA results, not an agent-recorded timing trace. They verify
the current Living/Wiim binding and off-state reload path, but do not prove
identical behavior for another room or after changing the Control4 volume
binding. The agent issued no Control4 media write during this verification.

Next read-only gate: inspect Living's playback state and notification routing
while the user manually plays and pauses a source. `PLAY`, `PAUSE`, and `STOP`
are advertised commands, but no playback state or safe HA mapping has yet
been established. Do not expose those features on command metadata alone.

### Read-only playback baseline (2026-10-04)

The live Director metadata for room `22` advertised parameterless `PLAY`,
`PAUSE`, and `STOP` among 97 commands. Its 57 variable names included
`CURRENT_MEDIA`, `CURRENT MEDIA INFO`, and `PLAYING_AUDIO_DEVICE`, but no
explicit playing/paused state field. The first two values were inspected by
shape only: they contained media ID/type and, for the latter, device/queue
IDs; no title, URL, or raw media payload was logged. In this baseline,
`PLAYING_AUDIO_DEVICE` was `0` and `CURRENT_SELECTED_DEVICE` resolved to
HBO Max ID `1751`. The selected `media_player` device advertised no command
metadata and its five variable names had no explicit playback state. Bound
Wiim `aswitch` ID `623` likewise advertised no command metadata or playback
state variable. This is a single REST snapshot, not proof that no playback
notification exists. No Control4 write was issued.

The next controlled test is a sanitized WebSocket capture of room `22`, the
current selected-source ID, and bound volume-device ID during a *manual*
play/pause transition. Compare a small REST before/after snapshot without
printing media titles, URLs, or raw payloads. Until a deterministic feedback
route is established, do not advertise HA playback features.

The user ran a 120-second WebSocket probe at 17:36 UTC with `--media-safe`
active and watched IDs `22`, `1751`, and `623`. The probe reached `LISTENING`
and its complete shared output contained no `event` records. Inventory in
that run identified `1751` as HBO Max. The user later confirmed pressing
Play/Pause during the listening window. This rules out a usable notification
on those three watched IDs for that observed action, but does not rule out an
event routed through another inventory item.

A subsequent agent-run 150-second `--media-safe` probe subscribed to all 582
inventory IDs from 17:40:50 to about 17:43:20 UTC and exited successfully.
It collected 383 sanitized events from 69 IDs, with none routed to room `22`,
bound Wiim `623`, or HBO Max child `1751`. HBO Max parent `1750` emitted 16
`devicecommand`-shaped events between 17:41:14.907 and 17:41:29.458 UTC.
Their content was deliberately hidden; live read-only metadata showed that
parent has no advertised commands and only four app/configuration variables.
Living Media Streaming `624` emitted generic `EVENT` shapes at 17:41:07,
17:42:07, and 17:43:07 UTC, a roughly one-minute pattern. Neither group can
be identified as playback feedback without the user's action timestamps and
a more discriminating, still-sanitized capture. The agent issued no
Control4 command during this probe.

The probe's `--media-safe` projection now prints only exact allowlisted
protocol labels from `devicecommand.command` or `EVENT.NAME`; unknown labels,
all parameters, and media text remain hidden. A 120-second read-only follow-up
watched `22`, `623`, `624`, `1750`, and `1751` from 17:49:24 to 17:51:24 UTC.
It saw four `LUA_OUTPUT` events on parent `1750` at 17:50:00 UTC and two
unclassified `EVENT` shapes on `624` at 17:50:07 and 17:51:07 UTC. There
were no events on `22`, `623`, or `1751`, and no allowlisted Play/Pause/Stop
label. The user confirmed pressing Play/Pause after `LISTENING` during this
follow-up interval. These actions did not produce identifiable playback-state
feedback on the watched IDs. The `LUA_OUTPUT` messages are driver log output,
not an authoritative playing/paused state. This does not prove no feedback
exists elsewhere in Director. The agent issued no Control4 command.

The transport now has a parameterless, metadata-gated `PLAY`/`PAUSE`/`STOP`
command helper, tested without sending a live command. The unpublished
0.1.14 candidate exposes separate stateless Play, Pause, and Stop HA buttons
under each eligible room device. They require advertised command metadata and
are unavailable while the room is off or disconnected. A press checks live
room power over REST before sending its command; a failed or inconclusive
read sends nothing. Home Assistant's standard media control renders a
combined Play/Pause action from the entity's
state; this room only reports `on`, so that action cannot select Pause
reliably. The separate buttons retain accurate room power state without
claiming playback feedback. Their live behavior remains to be field-tested.
Do not set `playing` or `paused` optimistically.

At 18:06 UTC, a fresh read-only Director metadata check for Living room `22`
again advertised parameterless `PLAY`, `PAUSE`, and `STOP` among 97 commands.
No command was sent during that check.
