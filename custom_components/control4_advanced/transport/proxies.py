"""Proxy names whose state and command mappings have live Director evidence."""

RADIANT_FLOOR_RELAY_PROXY = "relaysingle_radiantfloor_c4"
REGULAR_RELAY_PROXY = "relaysingle_relay_c4"
SUPPORTED_RELAY_PROXIES = frozenset({RADIANT_FLOOR_RELAY_PROXY, REGULAR_RELAY_PROXY})

WINDOW_CONTACT_PROXY = "contactsingle_windowcontactsensor_c4"
MOTION_CONTACT_PROXY = "contactsingle_motionsensor_c4"
CONTACT_SENSOR_PROXIES = frozenset({WINDOW_CONTACT_PROXY, MOTION_CONTACT_PROXY})
