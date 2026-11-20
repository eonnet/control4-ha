"""Proxy and driver identities recognized by the Control4 transport."""

from collections.abc import Mapping
from typing import Any

RADIANT_FLOOR_RELAY_PROXY = "relaysingle_radiantfloor_c4"
REGULAR_RELAY_PROXY = "relaysingle_relay_c4"
SUPPORTED_RELAY_PROXIES = frozenset({RADIANT_FLOOR_RELAY_PROXY, REGULAR_RELAY_PROXY})

WINDOW_CONTACT_PROXY = "contactsingle_windowcontactsensor_c4"
MOTION_CONTACT_PROXY = "contactsingle_motionsensor_c4"
DOOR_CONTACT_PROXY = "contactsingle_doorcontactsensor_c4"
CONTACT_SENSOR_PROXIES = frozenset({
    WINDOW_CONTACT_PROXY,
    MOTION_CONTACT_PROXY,
    DOOR_CONTACT_PROXY,
})

# Control4's bundled general-purpose Z-Wave sensor driver. The Core3 inventory
# uses the hyphenated proxy name even though driver.xml declares underscores.
ZWAVE_COMMON_SENSOR_PROXY = "zwave-common-sensor"

# Control4's newer native Z-Wave security sensor proxy. It exposes motion,
# battery, temperature, and illuminance variables directly on one item.
ZWAVE_SECURITY_SENSOR_PROXY = "zwave-security-sensor"
ZWAVE_NUMERIC_SENSOR_PROXIES = frozenset({
    ZWAVE_COMMON_SENSOR_PROXY,
    ZWAVE_SECURITY_SENSOR_PROXY,
})

ZIGBEE_CLIMATE_SENSOR_DRIVER = "zigbee-climate-sensor.c4z"


def is_zigbee_climate_sensor(item: Mapping[str, Any]) -> bool:
    """Identify the specific climate driver behind Director's keypad proxy."""
    return (
        item.get("typeName") == "device"
        and item.get("proxy") == "keypad_proxy"
        and ZIGBEE_CLIMATE_SENSOR_DRIVER in (
            item.get("filename"), item.get("protocolFilename"),
        )
    )


# Control4's native Z-Wave lock proxy. The controllable child advertises
# parameterless LOCK/UNLOCK commands and exposes LockStatus over REST.
ZWAVE_LOCK_PROXY = "lock"

# These proxies publish a change notification without one stable state field.
# Treat the notification as a trigger for one targeted, authoritative REST read.
REST_REFRESH_PROXIES = ZWAVE_NUMERIC_SENSOR_PROXIES | {ZWAVE_LOCK_PROXY}
