"""UI configuration for a locally connected Control4 Director."""

from __future__ import annotations

from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import ConfigFlow
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME

from .const import DOMAIN
from .selection import is_candidate_item
from .transport import AccountTokenProvider, DirectorRestClient
from .transport.auth import AuthenticationError, AuthenticationTransportError
from .transport.rest import DirectorRestError


def _valid_host(value: str) -> str:
    host = value.strip()
    if not host or any(character in host for character in "/:@? "):
        raise vol.Invalid("host must be an IP address or hostname without URL parts")
    return host


class Control4ConfigFlow(ConfigFlow, domain=DOMAIN):
    """Check credentials and the Director inventory before creating an entry."""

    VERSION = 1

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
