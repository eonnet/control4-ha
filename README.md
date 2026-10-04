# Control4 Home Assistant integration

Phase 1 established usable Director WebSocket push events on this Core3.
Phase 2 provides a small Home Assistant-independent transport layer. Phase 3
now contains a custom-component slice for `light_v2`, Celsius
`thermostatV2`, and opt-in radiant-floor relay switches. A user has installed
the earlier light/climate version in Home Assistant 2026.9.4 and reports that
light control works. Separately, a manual Control4 Hallway dimmer change
appears in Home Assistant about 3–4 seconds later. That interval has not been
instrumented end-to-end. The user also reports that thermostat state changes
and HA setpoint/HVAC-mode commands work in both directions.

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
