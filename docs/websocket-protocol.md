# Control4 Director WebSocket probe protocol

## Status before live probe

The items below are derived from the current upstream `pyControl4` source and
the local production MCP's read-only implementation. They are **not yet live
evidence** for this Core3 running OS 3.4.3.

- REST base: `https://192.168.0.24/api/v1`.
- Director-scoped bearer token: acquired by the proven MCP helper, used as
  `Authorization: Bearer <redacted>` for REST reads.
- Socket transport: Socket.IO compatible with Engine.IO v3, WebSocket-only,
  endpoint `wss://192.168.0.24`, connection header `JWT: <redacted>`.
- Namespace: `/api/v1/items/datatoui`.
- The Director sends `clientId`. The client emits `2probe`, obtains a
  subscription ID with `GET /api/v1/items/datatoui?JWT=<redacted>&SubscriptionClient=<clientId>`,
  then emits `startSubscription` with that subscription ID.
- Messages are received on the subscription-ID event. Status messages are
  acknowledged with `2`; data events are expected to contain `evtName`,
  `iddevice`, and `data`. Upstream Home Assistant handles `evtName ==
  OnDataToUI` and treats `data` as a potentially partial nested update.

## Live evidence — 2026-10-03

The probe successfully acquired a Director-scoped token through the existing
helper, read a 586-item inventory from the Core3, connected to the Director,
and received `OnDataToUI` events. REST metadata deterministically identifies
these relevant IDs:

- `2646`: `Living AC`, `thermostatV2` (Control4 ThermostatV2 child).
- `2811`: `Kid AC`, `thermostatV2` (Control4 ThermostatV2 child).
- `3177`: `Hallway`, `light_v2` child.
- `2726`: `Lamp Table`, `light_v2` child.

Observed event envelopes consistently included `evtName: OnDataToUI`,
`iddevice`, `data`, and a Director `time`. Payloads are partial and vary by
device: examples included `{ "is_connected": true }`, separate single-
setpoint values, `{ "volume_level": 44 }`, and nested `devicecommand` /
`UPDATE_PROPERTY` structures. The first connected session also received a
`light_brightness_changing` payload for `iddevice: 3177` with current level,
target level, and rate. This establishes useful pushed state data, but it was
not a controlled manual light or Living-AC change, so it does not yet establish
the required manual-action latency or the exact Living-AC field mapping.

A client-initiated reconnect was proven: connection 1 at
17:14:52.527Z; local disconnect at 17:15:00.545Z; reconnect scheduled after
one second; connection 2 at 17:15:05.110Z. The ~4.6-second observed interval
includes token acquisition, inventory GET, and subscription setup. This only
proves the client recovery path; a real network-loss recovery remains to be
observed.

The connection began producing many events immediately. That alone is
insufficient to call them an initial snapshot: the probe must compare behavior
before and after controlled state changes and REST reads.

## Flow

```text
trusted local token helper -> Director JWT (memory only)
             |-> GET /api/v1/items (read-only inventory)
             `-> WSS + JWT header -> clientId -> 2probe
                  -> HTTPS datatoui subscription request -> subscriptionId
                  -> startSubscription(subscriptionId)
                  -> subscriptionId event -> OnDataToUI / other payload
```

## Evidence to collect

Run the probe while manually changing the following. Record the emitted JSON
event timestamp and the wall-clock action time (or video timestamp) to measure
latency. Do not infer missing fields from another device type.

| Manual action | Expected device | What to establish |
| --- | ---: | --- |
| Light on/off | discovered from inventory | event, `iddevice`, state fields, latency |
| Light dim level | discovered dimmer | partial vs complete level payload |
| Living AC setpoint | 2646 | event shape and temperature field |
| Living AC HVAC mode | 2646 | event shape and HVAC mode field |
| Another simple device | discovered from inventory | payload variation |

After a socket drop or `--force-reconnect-after`, verify a new `connected`
record followed by fresh event delivery. A socket connection alone is not proof
of an initial snapshot: inspect emitted records before manually changing any
device, and use REST variables separately if no snapshot arrives.
