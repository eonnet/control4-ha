# Control4 Home Assistant integration

This repository is intentionally in Phase 1: proving Control4 Director WebSocket
events before any Home Assistant integration is created.

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

See `docs/websocket-protocol.md` for the observed/upstream-derived protocol
model and an evidence checklist.

