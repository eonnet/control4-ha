# Control4 Home Assistant integration

Phase 1 established usable Director WebSocket push events on this Core3.
Phase 2 provides a small Home Assistant-independent transport layer. Phase 3
now contains a first `light_v2` and Celsius `thermostatV2` custom-component
slice; it has not yet been loaded in Home Assistant or used to send a command.

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
precision, while `received_at` is the probe's millisecond timestamp.

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
and can be copied into a Home Assistant configuration for an initial integration
test. Do not enable write actions until the REST POST body and resulting push
state have been separately verified against the live Director.
