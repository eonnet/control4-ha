"""UI configuration for a locally connected Control4 Director."""

from __future__ import annotations

from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlowWithReload
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig

from .const import CONF_TIDAL_PLAYLISTS, CONF_TUNEIN_FAVORITES, DOMAIN
from .entity_ids import (
    config_entry_entity_id_recreation_plan,
    recreate_config_entry_entity_ids,
)
from .selection import is_candidate_item
from .transport import AccountTokenProvider, DirectorRestClient
from .transport.auth import AuthenticationError, AuthenticationTransportError
from .transport.favorites import parse_tunein_favorites
from .transport.playlists import parse_tidal_playlists
from .transport.rest import DirectorRestError


def _valid_host(value: str) -> str:
    host = value.strip()
    if not host or any(character in host for character in "/:@? "):
        raise vol.Invalid("host must be an IP address or hostname without URL parts")
    return host


class Control4ConfigFlow(ConfigFlow, domain=DOMAIN):
    """Check credentials and the Director inventory before creating an entry."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry):
        return Control4OptionsFlow()

    async def _validate(self, host: str, username: str, password: str) -> str | None:
        provider = AccountTokenProvider(username, password)
        try:
            async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session:
                items = await DirectorRestClient(host, provider, session).get_items()
        except AuthenticationTransportError:
            return "cannot_connect"
        except AuthenticationError:
            return "invalid_auth"
        except (DirectorRestError, aiohttp.ClientError, TimeoutError, OSError):
            return "cannot_connect"
        if not any(is_candidate_item(item) for item in items):
            return "no_devices"
        return None

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                host = _valid_host(user_input[CONF_HOST])
            except vol.Invalid:
                errors[CONF_HOST] = "invalid_host"
            else:
                error = await self._validate(host, user_input[CONF_USERNAME], user_input[CONF_PASSWORD])
                if error is None:
                    await self.async_set_unique_id(host)
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(title=f"Control4 {host}", data={
                        CONF_HOST: host,
                        CONF_USERNAME: user_input[CONF_USERNAME],
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                    })
                errors["base"] = error
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_HOST): str,
                vol.Required(CONF_USERNAME): vol.All(str, vol.Length(min=1)),
                vol.Required(CONF_PASSWORD): vol.All(str, vol.Length(min=1)),
            }),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]):
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            error = await self._validate(
                entry.data[CONF_HOST], entry.data[CONF_USERNAME], user_input[CONF_PASSWORD]
            )
            if error is None:
                await self.async_set_unique_id(entry.data[CONF_HOST])
                self._abort_if_unique_id_mismatch()
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]}
                )
            errors["base"] = error
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): vol.All(str, vol.Length(min=1))}),
            errors=errors,
        )


class Control4OptionsFlow(OptionsFlowWithReload):
    """Configure media favorites and integration-wide maintenance actions."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        return self.async_show_menu(
            step_id="init",
            menu_options=("media_favorites", "recreate_entity_ids"),
        )

    async def async_step_media_favorites(
        self, user_input: dict[str, Any] | None = None
    ):
        """Configure optional media favorites without storing them in code."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                parse_tidal_playlists(user_input.get(CONF_TIDAL_PLAYLISTS, ""))
            except ValueError:
                errors[CONF_TIDAL_PLAYLISTS] = "invalid_tidal_playlists"
            try:
                parse_tunein_favorites(user_input.get(CONF_TUNEIN_FAVORITES, ""))
            except ValueError:
                errors[CONF_TUNEIN_FAVORITES] = "invalid_tunein_favorites"
            if not errors:
                return self.async_create_entry(data=user_input)
        current_tidal = self.config_entry.options.get(CONF_TIDAL_PLAYLISTS, "")
        current_tunein = self.config_entry.options.get(CONF_TUNEIN_FAVORITES, "")
        return self.async_show_form(
            step_id="media_favorites",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_TIDAL_PLAYLISTS,
                        default=current_tidal,
                    ): TextSelector(TextSelectorConfig(multiline=True)),
                    vol.Optional(
                        CONF_TUNEIN_FAVORITES,
                        default=current_tunein,
                    ): TextSelector(TextSelectorConfig(multiline=True))
                }
            ),
            errors=errors,
        )

    async def async_step_recreate_entity_ids(
        self, user_input: dict[str, Any] | None = None
    ):
        """Recreate automatic IDs for every entity in this config entry."""
        changes = config_entry_entity_id_recreation_plan(
            self.hass, self.config_entry.entry_id
        )
        if not changes:
            return self.async_abort(reason="entity_ids_already_current")
        if user_input is not None:
            applied = recreate_config_entry_entity_ids(
                self.hass, self.config_entry.entry_id
            )
            if not applied:
                return self.async_abort(reason="entity_ids_already_current")
            return self.async_abort(
                reason="entity_ids_recreated",
                description_placeholders={"count": str(len(applied))},
            )

        preview_limit = 50
        preview = "\n".join(
            f"- `{old_entity_id}` → `{new_entity_id}`"
            for old_entity_id, new_entity_id in changes[:preview_limit]
        )
        if len(changes) > preview_limit:
            preview += f"\n- … and {len(changes) - preview_limit} more"
        return self.async_show_form(
            step_id="recreate_entity_ids",
            data_schema=vol.Schema({}),
            description_placeholders={
                "count": str(len(changes)),
                "changes": preview,
            },
        )
