# Control4 Advanced for Home Assistant

An experimental, unofficial push-first Control4 integration. It uses Director
REST for authentication, inventory, initial state, and commands, and the
`/api/v1/items/datatoui` WebSocket for normal state updates. It does not
require C4SOAP or the separate Control4 MCP project.

This is an early preview: a user running Home Assistant 2026.9.4 reports that
light control works. A manual Control4 dimmer change appeared in Home
Assistant about 3–4 seconds later; that interval has not been instrumented
end-to-end. The same user also reports that thermostat state changes and HA
setpoint/HVAC-mode commands work in both directions. These are field reports,
not automated Home Assistant integration tests. Test each device before using
it in automations.

## Install with HACS

1. In HACS, open the top-right menu and choose **Custom repositories**.
2. Add `https://github.com/eonnet/control4-ha` with type **Integration**.
3. Install **Control4 Advanced**, then restart Home Assistant.
4. In **Settings → Devices & services**, add **Control4 Advanced**. Enter the
   local Director hostname/IP and your Control4 account credentials in Home
   Assistant's configuration flow. Do not post credentials in an issue.

The integration selects dimmable and on/off-only `light_v2` devices with
advertised `ON`/`OFF` commands, Celsius `thermostatV2` devices with heat/cool
capability, and `relaysingle_radiantfloor_c4` switches with advertised
`OPEN`/`CLOSE` commands and a readable initial state. The radiant-floor
switches are disabled by default because they control heating loads. Enable
only the intended entity under **Settings → Devices & services → Entities**,
then verify feedback from a manual Control4 change before testing HA control.
On/off-only light and radiant-floor push were observed on a Core3, but these
new HA entity paths have not yet been verified in a live HA installation.
Other relay proxies, `uibutton`, and color/fan/hold controls remain excluded.

State is initialized through REST, then updated from WebSocket events;
entities do not poll for normal state. REST re-syncs after reconnect and on
a slow reconciliation interval. Home Assistant device metadata includes the
Control4 room as a suggested area. Diagnostics omit credentials, raw events,
locations, and device names.
On reload, registry entries for Director items deleted from inventory are
removed only after two successful inventory reads. This cleanup has offline
tests but has not yet been verified in a live HA reload.

Authentication requires access to the Control4 account service. Once a
Director token has been obtained, device communication is local. The local
Director's self-signed TLS certificate is not verified; cloud authentication
still uses normal TLS verification.

Issues and test reports: [GitHub Issues](https://github.com/eonnet/control4-ha/issues).
