# Control4 Advanced for Home Assistant

An experimental, unofficial push-first Control4 integration. It uses Director
REST for authentication, inventory, initial state, and commands, and the
`/api/v1/items/datatoui` WebSocket for normal state updates. It does not
require C4SOAP or the separate Control4 MCP project.

This is an early preview: the transport has been exercised against a Core3,
but the custom component has **not yet been tested inside Home Assistant**.
The REST command POST path is implemented from existing Control4 tooling and
read-only Director command metadata; **this project has not issued a live
write**. Test it in a non-critical Home Assistant instance before using
controls or automations.

## Install with HACS

1. In HACS, open the top-right menu and choose **Custom repositories**.
2. Add `https://github.com/eonnet/control4-ha` with type **Integration**.
3. Install **Control4 Advanced**, then restart Home Assistant.
4. In **Settings → Devices & services**, add **Control4 Advanced**. Enter the
   local Director hostname/IP and your Control4 account credentials in Home
   Assistant's configuration flow. Do not post credentials in an issue.

The integration currently selects only dimmable `light_v2` devices and
Celsius `thermostatV2` devices with advertised heat/cool capability. Other
proxies, on/off-only lights, and color/fan/hold controls are deliberately
excluded until their push payloads and commands are verified.

State is initialized through REST, then updated from WebSocket events;
entities do not poll for normal state. REST re-syncs after reconnect and on
a slow reconciliation interval. Home Assistant device metadata includes the
Control4 room as a suggested area. Diagnostics omit credentials, raw events,
locations, and device names.

Authentication requires access to the Control4 account service. Once a
Director token has been obtained, device communication is local. The local
Director's self-signed TLS certificate is not verified; cloud authentication
still uses normal TLS verification.

Issues and test reports: [GitHub Issues](https://github.com/eonnet/control4-ha/issues).
