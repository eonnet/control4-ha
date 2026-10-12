# Control4 Advanced for Home Assistant

Control4 Advanced is an unofficial custom integration that brings compatible
Control4 lighting, climate, relay, contact-sensor, and room-media devices into
Home Assistant. It also includes media browsing, multi-room audio controls,
saved group presets, and three dashboard cards.

## Requirements

- Home Assistant with HACS, or access to the Home Assistant
  `custom_components` directory.
- A locally reachable Control4 Director.
- A valid Control4 account username and password.
- Compatible devices and actions exposed by the Control4 project.

## Installation

### HACS

1. Open **HACS > Integrations**.
2. Open the HACS menu and choose **Custom repositories** if Control4 Advanced
   is not already listed.
3. Add `https://github.com/eonnet/control4-ha` as an **Integration** repository.
4. Search for **Control4 Advanced** and download it.
5. Restart Home Assistant.
6. Open **Settings > Devices & services > Add integration** and select
   **Control4 Advanced**.

### Manual installation

Copy `custom_components/control4_advanced` into Home Assistant's
`custom_components` directory, restart Home Assistant, and add the integration
from **Settings > Devices & services**.

## Configuration

The setup form requests:

- **Director address** — local hostname or IP address without a URL scheme,
  port, or path.
- **Control4 account username**.
- **Control4 account password**.

The Director address uniquely identifies the integration entry. Account
credentials are stored in Home Assistant's config entry.

## Supported devices and entities

| Home Assistant entity | Compatible Control4 device | Available features |
| --- | --- | --- |
| Light | `light_v2` | On, off, and brightness for dimmable devices; on/off control for non-dimmable lights |
| Climate | Celsius `thermostatV2` | Heat, Cool, Auto, Off, target temperature, fan mode, named presets, and hold mode when available |
| Switch | Compatible standard, wireless, and radiant-floor relays | On and off; newly discovered relay entities are disabled by default |
| Binary sensor | Window contacts, door contacts, and motion/PIR sensors | Window, door, and motion state |
| Media player | Rooms with compatible Watch or Listen sources | Source browsing, metadata, artwork, transport, power-off, volume, mute, and grouping as available |
| Button | Room playback actions | Previous, Play, Pause, Stop, and Next when available |
| Select | Thermostat and media choices | Hold mode, TIDAL account, and TIDAL playlist when available |

Only features available to a specific Control4 device are exposed on its Home
Assistant entity.

## Climate features

- Celsius thermostats with separate heating/cooling setpoints.
- Single-setpoint systems such as compatible GREE configurations.
- Off, Heat, Cool, and Auto modes when available.
- Target-temperature limits supplied by the thermostat.
- Device-specific fan modes.
- Device-specific named presets.
- A companion hold-mode selector when available.

## Lighting, relays, and sensors

- Dimmable lights expose brightness and power controls.
- Non-dimmable lights expose power controls only.
- Compatible relay loads appear as switches and are disabled by default so the
  user can explicitly enable the required devices.
- Window, door, and motion/PIR devices appear as read-only binary sensors.

## Media players

Each compatible Control4 room is exposed as a Home Assistant media player.
Available features can include:

- Browse and start Control4 **Watch** and **Listen** sources.
- Browse and play configured TIDAL playlists and TuneIn favorites.
- Current title, artist, album, source, and artwork.
- Previous, Play, Pause, Stop, and Next.
- Room power-off.
- Room volume and mute.
- Native multi-room join and leave.
- Individual volume and mute control for each player in a group.
- Relative master group volume.
- Mute all and Unmute all.

Known sources receive packaged logo artwork, including Netflix, Plex, Apple TV,
HBO Max, Xplore TV, Sony, Xiaomi, WiiM, Spotify, YouTube, TIDAL, and TuneIn.
Other sources use generic Watch or Listen artwork.

## TIDAL playlists and TuneIn favorites

Open **Settings > Devices & services > Control4 Advanced > Configure** to use:

- **Retrieve TIDAL playlists** — follow the on-screen instructions and open
  each requested TIDAL playlist screen in the Control4 app.
- **Retrieve TuneIn favorites** — follow the on-screen instructions and open
  each requested TuneIn favorites screen in the Control4 app.
- **Media favorites** — review or manually enter playable items.

Manual TIDAL entries use one line per playlist:

```text
TIDAL source name | Playlist name | Playlist UUID
```

Manual TuneIn entries use one line per favorite:

```text
TuneIn source name | Favorite name | Station/id
```

Items appear only in rooms where the matching source is available.

## Multi-room groups

The custom Control4 group popup is available from all packaged media cards and
from the Group action in the media player's More Info dialog. It provides:

- Room selection with a fixed **Apply** action.
- Separate **Players in this group** and **Available players** sections.
- Individual volume and mute controls under every compatible player.
- Relative master volume for the selected group.
- Mute all and Unmute all.
- Saved group presets.

## Group presets

A group preset can store:

- The group leader.
- All selected rooms.
- The selected playable Watch/Listen source, TIDAL playlist, or TuneIn
  favorite.
- Individual room volume levels.
- Individual room mute states.

Presets can be created, activated, renamed, updated from the current group,
reordered, and deleted in the group popup. They are shared across browsers and
Home Assistant Companion App installations.

Control4's internal **Digital Media** session endpoint is resolved to its
actual playing Listen source when Director supplies it. The internal endpoint
itself is not saved or displayed as a playable source.

Presets can also be activated from automations, scripts, voice assistants, or
NFC tags:

```yaml
action: control4_advanced.activate_group_preset
data:
  name: Whole House
```

The action accepts either the case-insensitive preset `name` or its stable
`preset_id`.

## Dashboard cards

All cards are packaged with the integration and available from the Home
Assistant visual card picker after a restart and browser/app refresh.

### Control4 Media Player

A full native-style media card with artwork, title and artist, playback
controls, Browse Media, power, More Info, and the custom Control4 grouping
popup.

```yaml
type: custom:control4-media-player-card
entity: media_player.kitchen
```

### Control4 Media Group

A compact entry point for group membership, master volume, group mute, and
individual player controls.

```yaml
type: custom:control4-media-group-card
entity: media_player.kitchen
```

### Control4 Media Preset

A compact one-tap preset card showing the saved source, leader, room count, and
activation status. Add it through the visual card picker and select a saved
preset. The card keeps the preset's stable ID if it is later renamed.

```yaml
type: custom:control4-media-preset-card
preset_id: YOUR_PRESET_ID
```

## Integration tools

### Recreate entity IDs

Open **Settings > Devices & services > Control4 Advanced > Configure > Recreate
entity IDs** to preview and apply Home Assistant's current automatic naming
format to every entity in the integration entry.

Entity history is preserved, but dashboards, automations, scripts, and scenes
that use an old entity ID must be updated manually.

### Entity cleanup

Obsolete entities are removed after a successful integration reload when their
Control4 devices are no longer present. Entities owned by other integrations or
other Control4 entries are not changed.

## Current scope

- Climate entities are Celsius-only.
- Relay entities require explicit enabling in Home Assistant.
- TIDAL browsing is limited to configured or retrieved playlists.
- TuneIn browsing is limited to configured or retrieved favorites.
- Unsupported device types and actions are not exposed.

## Updating

After installing an update:

1. Restart Home Assistant.
2. Refresh the browser, or fully close and reopen the Home Assistant Companion
   App so the latest dashboard card module is loaded.

## License

This repository is licensed under the [Apache License 2.0](LICENSE).

Control4 Advanced is an independent project and is not affiliated with or
endorsed by Control4. The packaged Control4 icon and wordmark are copied without
modification from the Home Assistant Brands repository and are used only for
identification.
