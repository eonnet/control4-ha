# Phase 2 transport design

The canonical transport package is now
`custom_components/control4_advanced/transport`; the root `control4_transport`
package remains a compatibility import for standalone scripts/tests. The
transport has no Home Assistant or C4SOAP dependency. Its runtime
dependencies are `aiohttp` and the tested `pyControl4` 2.0.2 Socket.IO client.

## Flow

1. `AccountTokenProvider` gets a Director JWT directly from the Control4
   account API and refreshes it before expiry. Temporary network/server
   failures receive a bounded retry; authentication errors do not.
2. `DirectorRestClient` uses that JWT for `/api/v1/items` and tracked device
   `/variables` reads. One HTTP 401 causes a forced token refresh and retry.
3. `InventoryCache` maps REST item IDs to proxy metadata. The WebSocket client
   registers callbacks only for explicit tracked IDs.
4. The connection supervisor waits for Director's `subscriptionId`, then reads
   tracked REST variables. On reconnect it repeats those reads; optional slow
   reconciliation performs the same read at a configured interval.
5. `normalize_websocket_event` turns observed `OnDataToUI` fields into a
   `NormalizedEvent`. The event retains raw data in memory for diagnostics,
   while `DeviceStateCache` accepts only authoritative normalized fields.
6. A REST read cannot overwrite a field updated by push during that read.
   The cache tracks a revision per field for this purpose.

## Verified field mappings

| Proxy | Director field | Normalized field |
| --- | --- | --- |
| `light_v2` | `LIGHT_LEVEL` or settled `light_brightness_changed.light_brightness_current` | `brightness_percent`, `is_on` |
| `light_v2` | `light_brightness_changing.light_brightness_target` | `brightness_target_percent` (non-authoritative transition) |
| `light_v2` REST | `Brightness Percent` | `brightness_percent`, `is_on` |
| on/off `light_v2` REST | `LIGHT_STATE` 0/1 | `is_on` |
| on/off `light_v2` push | `LIGHT_LEVEL` 0/100 | `brightness_percent`, `is_on` |
| `relaysingle_radiantfloor_c4` | verified `relay_state.current_state` `OPENED`/`CLOSED` | `is_on` false/true |
| `relaysingle_radiantfloor_c4` REST | `RelayState` 0/1 | `is_on` false/true (0=off inferred; 1=on observed) |
| `relaysingle_relay_c4` | verified `relay_state.current_state` `OPENED`/`CLOSED` | `is_on` false/true (Sony Plug observed) |
| `relaysingle_relay_c4` REST | `RelayState` 0/1 | `is_on` false/true (0=off inferred; 1=on observed) |
| window contact proxy | verified `contact_state.current_state` `OPENED`/`CLOSED` | `director_contact_state`; `is_on` true/false for window open/closed (user-confirmed push) |
| motion contact proxy | verified `contact_state.current_state` `OPENED`/`CLOSED` | `director_contact_state`; `is_on` true/false for motion/idle (user-confirmed push) |
| door contact proxy | verified `contact_state.current_state` `OPENED`/`CLOSED` | `director_contact_state`; `is_on` true/false for door open/closed (user-confirmed push) |
| window/motion/door contact REST | `ContactState` 0/1 | `director_contact_state` `OPENED`/`CLOSED`; `is_on` true/false for window/door open/closed or PIR motion/idle |
| `thermostatV2` | `hvac_mode` / `hvacmode` | `hvac_mode` |
| `thermostatV2` | `hvac_state` / `hvacstate` | `hvac_state` |
| `thermostatV2` | `setpoint_single_c`, `setpoint_heat_c`, `setpoint_cool_c` | target, heat, cool setpoint in °C |
| `thermostatV2` REST | `TEMPERATURE_C`, `HEAT_SETPOINT_C`, `COOL_SETPOINT_C`, `SINGLE_SETPOINT_C` | Celsius temperature and setpoints |
| `thermostatV2` REST | `HVAC_MODE`, `HVAC_STATE`, `SCALE`, `IS_CONNECTED` | matching normalized mode, state, scale, connectivity |
| `thermostatV2` REST | `HOLD_MODE`, `PRESET` | `hold_mode`, `preset_mode` |
| `thermostatV2` push | direct `hold_mode`, `preset` | `hold_mode`, `preset_mode` (Living AC observed) |
| `thermostatV2` push | direct `fan_mode` | `fan_mode` (Living AC observed during manual preset changes) |

Raw generic setpoint values such as `2921` are deliberately left uninterpreted.
For thermostat hold changes, the direct `hold_mode` UI label is used; the
captured `holdmode` and `settings.holdmode` aliases briefly differed during a
Living AC transition. Presets are normalized from the direct `preset` field.
Fan mode also uses the direct `fan_mode` label, not the parallel `fanmode` or
`settings.fanmode` fields. A live 2026-10-04 REST read confirmed `FAN_MODE:
Low` and the same four `SET_MODE_FAN.MODE` choices for both known ACs; see
[fan-mode investigation](thermostat-fan-mode.md).
For contact sensors, read-only REST sampled `ContactState:0` with verified
`OPENED` and `ContactState:1` with verified `CLOSED` for both proxies.
Kid Window's physical open/closed polarity is consistent with its REST and
push samples. The user clarified that PIR `OPENED`/REST `0` at 03:16:19 was
motion and `CLOSED`/REST `1` at 03:16:30 was idle, agreeing with the earlier
REST `1` idle snapshot. The user reports that Kid Window and Kid PIR both
work correctly in HA; the exact installed version was not independently
verified.
Unknown proxy payloads remain available in the event's `raw` field but do not
mutate normalized state. Raw events and tokens are not logged by the library.

For room volume, `RoomVolumeCoordinator` can dynamically track the room's
REST-advertised `CURRENT_VOLUME_DEVICE_ID`. OutputStatus push from that bound
device triggers an immediate, targeted room REST refresh; it does not directly
set the room volume. This avoids treating a Control4 Programming mirror on
Sony Television as independent feedback. Binding changes are picked up from
room REST snapshots and on slow reconciliation. The read-only HA media-player
entity now consumes the resulting room snapshot; it advertises no controls.

Live verification on 2026-10-03 established a Director subscription and loaded
REST snapshots for Living AC `2646` and Lamp Table `2726` through the new
transport. A focused run then received two settled Lamp Table messages at
19:51:28.552–19:51:28.553Z, both normalized to `brightness_percent: 26` and
`is_on: true`. Director emitted the same settled value twice; consumers should
treat repeated state values as idempotent.

A local client disconnect initially exposed a stale connection flag. After
the supervisor was changed to inspect the actual Socket.IO and subscription
state, a repeated live test reported `connected: false` on disconnect,
reconnected, and emitted a second REST snapshot before clean shutdown. Offline
tests cover normalization, stale-read protection, 401 refresh, subscription
readiness, silent disconnect recovery, reconnection resync, and callback
delivery. Recovery from an actual network outage remains unverified.
