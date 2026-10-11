# Binary-sensor investigation and first HA slice

Version 0.1.4 has a binary-sensor platform that the user reports working in
Home Assistant for both Kid Window and Kid PIR. The exact installed version
and public-branch status were not independently verified. The
2026-10-03 Director
inventory identified Kid Window ID `377` as
`contactsingle_windowcontactsensor_c4` and Kid PIR ID `378` as
`contactsingle_motionsensor_c4`, both in the Kid room. A separate Kid Window
ID `2140` used `zwave-common-sensor`; it is not part of this focused test.
These IDs were confirmed against fresh inventory on 2026-10-04.

The locally installed pyControl4 `C4ContactSensor` reads a REST variable named
`ContactState`. Its documentation describes the boolean ambiguously across
window and motion sensors. That source code was only a hint; the following
Core3 trace now proves the variable and WebSocket envelope, but not physical
open/closed or motion polarity.

The sandboxed development terminal failed before TCP with `PermissionError`
on 2026-10-04. The user ran the following focused command in a normal SSH
terminal that could reach Director:

```bash
/opt/control4-mcp/.venv/bin/python -u /opt/control4-ha/tools/websocket_probe.py \
  --duration 180 --watch-ids 377,378 --inspect-ids 377,378
```

This uses only inventory/metadata GETs and a WebSocket subscription; it has
no device-command path. The run reached `LISTENING` at 02:59:00.459Z. Fresh
inventory found both IDs, with no missing watch IDs. Both metadata reads
reported `ContactState:1`, plus `LastActionTime` and `StateVerified`; neither
item advertised commands.

The first observed event was for Kid PIR ID `378` at 02:59:09.299Z:
`Action.Name:state`, `Value.state:false`. It was followed at 02:59:09.496Z
by `state:false` and `contact_state.current_state:OPENED` with
`feedback_bound:true` and `is_verified:true`. Kid Window ID `377` emitted
the same false/`OPENED` pattern at 02:59:15.460–15.652Z, including a
duplicate. At 02:59:16.980–17.177Z it emitted true/verified `CLOSED`,
again with a duplicate. Kid PIR then emitted true/verified `CLOSED` at
02:59:19.518–19.718Z and false/verified `OPENED` at
02:59:20.882–21.080Z. Every event carried its own inventory ID. The trace
contains no event in the first ~8.8 seconds after `LISTENING`; that does not
prove initial snapshots never occur. The exact manual action times/order and
physical states at the initial REST reads were not supplied with the trace.
The user subsequently answered yes to a combined question about Kid Window
opening then closing around 02:59:15–17Z and Kid PIR triggering then clearing
around 02:59:19–21Z. They did not identify both initial physical states. A
later PIR REST snapshot conflicted with the motion polarity inferred from that
answer. The user later provided precise 03:16 trigger/clear times that resolve
the ambiguity in favor of `OPENED`/REST `0` meaning motion.

Director's verified `contact_state` payload is a suitable settled protocol
field; the earlier `Action` and top-level `state` are hints, and duplicates
must be idempotent. The window action order establishes verified `OPENED` as
window open (`is_on:true`) and `CLOSED` as closed. The earlier broad PIR
action-order answer was insufficient to map motion; the precise later timing
below establishes `OPENED` as motion (`is_on:true`) and `CLOSED` as idle. The
first REST run observed only `ContactState:1`.

At 03:07:01Z, a second read-only inventory/metadata run reported
`ContactState:0` for Kid Window ID `377` and `ContactState:1` for Kid PIR ID
`378`. The user confirmed that the window was open and PIR showed no motion
at this read. Thus REST window `0` means open in that observation, whereas
REST PIR `1` coincided with no motion. No WebSocket subscription or device
command was opened by this inventory-only run.

To capture the reverse REST values during a brief PIR trigger, the probe has
an opt-in read-only mode. It issues one bounded variables GET immediately
after each distinct verified contact-state WebSocket event and emits only
`contact_rest_sample` with the associated Director label and allowlisted
`ContactState` value. Early hints and duplicate verified labels cause no
REST sample. Run this in the normal SSH terminal:

```bash
/opt/control4-mcp/.venv/bin/python -u /opt/control4-ha/tools/websocket_probe.py \
  --duration 90 --watch-ids 377,378 --sample-contact-rest
```

In a 03:16 UTC run, after `LISTENING`, the probe captured these paired
verified-push and immediate REST reads:

| Device | Received UTC | Verified Director label | REST `ContactState` |
| --- | --- | --- | ---: |
| Kid Window `377` | 03:16:07.857–07.872 | `CLOSED` | 1 |
| Kid Window `377` | 03:16:16.585–16.603 | `OPENED` | 0 |
| Kid PIR `378` | 03:16:19.501–19.517 | `OPENED` | 0 |
| Kid PIR `378` | 03:16:30.018–30.035 | `CLOSED` | 1 |

The value-to-label relation is therefore the same for both proxies:
`0 ↔ OPENED`, `1 ↔ CLOSED`. The user explicitly confirmed that PIR motion
began at 03:16:19 (`OPENED`/`0`) and had cleared by 03:16:30 (`CLOSED`/`1`).
This agrees with the 03:07 no-motion/REST `1` snapshot and supersedes the
ambiguous interpretation of the first run. The window's physical mapping
is supported by the 03:07 open/REST `0` snapshot and the requested 03:16
close→open sequence. Immediate REST reads might lag push in general, but
each captured pair was consistent. No device command was sent by the probe.

The user subsequently reported that both window and PIR are working
correctly in Home Assistant. This validates their observed HA polarity and
feedback, but not a measured HA latency, initial snapshot timing, or an
explicit reconnect test for these two devices.

On 2026-10-11, fresh inventory identified Main Door ID `335` and Elevator Door
ID `1530` as `contactsingle_doorcontactsensor_c4`. Both exposed the same three
read-only variables as the existing contact sensors: `ContactState`,
`LastActionTime`, and `StateVerified`, with no commands. During a focused Main
Door trace, physical opening produced verified `OPENED` and immediate REST
`ContactState:0`; closing produced verified `CLOSED` and REST `ContactState:1`.
The sequence repeated consistently, and Director's duplicate settled events
were idempotent. No device command was sent. Version 0.1.15 therefore adds this
exact proxy with Home Assistant's `door` device class; HA field validation is
pending.

The transport now maps `is_on` from verified push and REST `ContactState` for
all three exact contact proxy types. The HA platform registers only those
proxies, uses the initial REST snapshot and push cache, and has no command
path. It has offline selection/parser tests; window and motion also have
user-reported HA field validation.
Do not issue Control4 commands for these sensors.
