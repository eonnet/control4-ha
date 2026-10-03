# Phase 2 transport design

`control4_transport` has no Home Assistant or C4SOAP dependency. Its runtime
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
| `thermostatV2` | `hvac_mode` / `hvacmode` | `hvac_mode` |
| `thermostatV2` | `hvac_state` / `hvacstate` | `hvac_state` |
| `thermostatV2` | `setpoint_single_c`, `setpoint_heat_c`, `setpoint_cool_c` | target, heat, cool setpoint in °C |
| `thermostatV2` REST | `TEMPERATURE_C`, `HEAT_SETPOINT_C`, `COOL_SETPOINT_C`, `SINGLE_SETPOINT_C` | Celsius temperature and setpoints |
| `thermostatV2` REST | `HVAC_MODE`, `HVAC_STATE`, `SCALE`, `IS_CONNECTED` | matching normalized mode, state, scale, connectivity |

Raw generic setpoint values such as `2921` are deliberately left uninterpreted.
Unknown proxy payloads remain available in the event's `raw` field but do not
mutate normalized state. Raw events and tokens are not logged by the library.

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
