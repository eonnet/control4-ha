"""Integration constants."""

DOMAIN = "control4_advanced"
PLATFORMS = (
    "light", "climate", "switch", "binary_sensor", "select", "media_player",
    "button", "number",
)
CONF_RECONCILIATION_SECONDS = "reconciliation_seconds"
CONF_TIDAL_PLAYLISTS = "tidal_playlists"
CONF_TUNEIN_FAVORITES = "tunein_favorites"
DEFAULT_RECONCILIATION_SECONDS = 900
