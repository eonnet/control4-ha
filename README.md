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

State is initialized through REST, then updated from WebSocket events;
entities do not poll for normal state. REST re-syncs after reconnect and on
a slow reconciliation interval. Home Assistant device metadata includes the
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
