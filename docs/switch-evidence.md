# Switch and on/off-light evidence

On 2026-10-03, a focused standalone probe watched inventory ID `319` while the
user switched Vent off and on in Control4. Live REST inventory identified that
ID as `Extract Fan`, proxy `uibutton`, `typeName: device`. The WebSocket
connected and received `OnDataToUI` events routed to `iddevice: 319`.

At 22:12:47.434Z, the captured `devicecommand.UPDATE_PROPERTY` messages
reported `Driver Information` values `Timer selected: Off` and `Device
switched off externally`. At 22:13:04.260Z, they reported `Timer selected:
10` and `Device switched on externally`. The accompanying `LUA_OUTPUT`
messages repeated the same text. These are correlated diagnostic messages,
not a structured on/off state field. A subsequent read-only REST metadata
check listed variables `COUNTDOWN`, `RUNTIME`, and `STATE`, plus the sole
command `SetCountdown`. The values and semantics of those variables and the
command's parameters have not been inspected. No initial on/off state or
direct REST `ON`/`OFF` command has been verified.

The upstream `hass-control4` switch platform targets relay proxies and uses
`relay_state.current_state` to interpret push state. It does not establish
equivalent semantics for this `uibutton` item:
<https://github.com/lawtancool/hass-control4/blob/master/custom_components/control4/switch.py>.

At 22:33:05.967Z, a later live full-inventory read revealed a distinct item:
ID `1846`, named `Vent`, proxy `relaysingle_relay_c4`, room Bathroom. It is one
of nine basic relay candidates. This corrects the earlier assumption that the
user-visible Vent had no relay representation. ID `319` remains the separate
`Extract Fan` timer `uibutton` and must not be treated as relay state.

Decision: do not expose ID `319` as a generic switch based on driver log text.
ID `1846` may qualify as a regular relay, but its REST `RelayState`, commands,
and push event shape have not yet been checked. Other live relay candidates
include ID `362` HTPC Relay and ID `431` Sony Plug; no device should be toggled
automatically for this investigation. A name search at 22:40:32.989Z found no
inventory item named `iKid Heating Panel`. A broader live search at
22:45:59.040Z identified the actual item as ID `579`, `Kid Heating Panel`,
room Kid, proxy `relaysingle_radiantfloor_c4`. The extra `i` in the earlier
name was wrong. This radiant-floor proxy was outside the probe's
basic/wireless relay list; it is not evidence that the item lacks relay
behavior. The installed pyControl4 `C4Relay` source reads `RelayState` and
offers `OPEN`/`CLOSE` methods generically, but its comments leave the
open/closed meaning uncertain for non-lock relays.

At 22:48:47.450Z, read-only live metadata for ID `579` listed variables
`LastActionTime`, `RelayState`, and `StateVerified`, with `RelayState: 1` at
that instant. It advertised parameterless `OPEN`, `CLOSE`, and `TOGGLE`
commands. This proved REST state and command metadata exist; the physical
meaning and push behavior were established by the later manual trace.

At 22:48:47.485Z, read-only metadata for Bathroom Ceiling ID `1150` showed
`LIGHT_STATE: 0` and advertised parameterless `ON`/`OFF` commands. Its live
inventory says `light_v2`, `on_off=true`, `dimmer=false`.

During a focused WebSocket subscription at 22:50:29.450Z, the user turned
Kid Heating Panel from on to off and then on. Director sent `state:false`
at 22:50:39.624Z, then `relay_state.current_state: OPENED` with
`feedback_bound:true` and `is_verified:true` at 22:50:39.820Z. The reverse
change produced `state:true` at 22:50:43.617Z, then verified `CLOSED` at
22:50:43.814Z. The user confirmed the physical order and that the earlier
`RelayState:1` snapshot was on. Thus, for this proxy on this Core3, verified
`CLOSED` means on and verified `OPENED` means off. REST `RelayState:1` was
observed while on; `RelayState:0` mapping to off is inferred from the verified
push pair, not separately measured with an off-state REST read. The generic
`state` message is treated as an early hint, not settled feedback.

The user also changed Bathroom Ceiling from off to on to off. Director
emitted `LIGHT_LEVEL:100` at 22:50:50.499Z and `LIGHT_LEVEL:0` at
22:50:52.074Z, each with matching `light_brightness_changed` feedback. This
proves useful push for the on/off-only `light_v2` variant. The new HA switch
and on/off-only light paths have offline parser/selection tests but still need
Home Assistant runtime verification. No agent-issued Control4 write was
performed.
