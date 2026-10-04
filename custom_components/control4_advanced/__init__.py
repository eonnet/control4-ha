"""Control4 Advanced Home Assistant integration.

Home Assistant imports are local to setup functions so transport tests remain
runnable without installing Home Assistant.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import TYPE_CHECKING

import aiohttp

from .const import DEFAULT_RECONCILIATION_SECONDS, PLATFORMS
from .registry_cleanup import remove_stale_registry_entries
from .selection import command_metadata_ids, is_candidate_item
from .transport import AccountTokenProvider, Control4Transport, DeviceCommandClient, DirectorRestClient
from .transport.auth import AuthenticationError, AuthenticationTransportError
from .transport.rest import DirectorRestError

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant


_LOGGER = logging.getLogger(__name__)


@dataclass
class Control4Runtime:
    session: aiohttp.ClientSession
    transport: Control4Transport
    commands: DeviceCommandClient


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Authenticate, load an initial snapshot, then forward push-capable entities."""
    from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
    from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady

    host = entry.data[CONF_HOST]
    provider = AccountTokenProvider(entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD])
    session = aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False))
    transport = None
    setup_complete = False
    try:
        rest = DirectorRestClient(host, provider, session)
        items = await rest.get_items()
        tracked_ids = [
            int(item["id"])
            for item in items
            if is_candidate_item(item)
        ]
        if not tracked_ids:
            raise ConfigEntryNotReady("Director has no supported items")
        transport = Control4Transport(
            rest,
            tracked_ids,
            reconciliation_seconds=entry.options.get(
                "reconciliation_seconds", DEFAULT_RECONCILIATION_SECONDS
            ),
        )
        await transport.start(ready_timeout=90)
        commands = DeviceCommandClient(rest)
        await commands.refresh(command_metadata_ids(transport.inventory.items, tracked_ids))
        entry.runtime_data = Control4Runtime(session, transport, commands)
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        try:
            remove_stale_registry_entries(
                hass,
                config_entry_id=entry.entry_id,
                host=host,
                first_inventory_ids={
                    item["id"] for item in items if isinstance(item.get("id"), int)
                },
                second_inventory_ids=set(transport.inventory.items),
            )
        except Exception as exc:
            # Registry cleanup is nonessential; never break otherwise healthy entities.
            _LOGGER.warning("Control4 stale registry cleanup failed: %s", type(exc).__name__)
        setup_complete = True
        return True
    except AuthenticationTransportError:
        raise ConfigEntryNotReady("Control4 account service is temporarily unavailable") from None
    except AuthenticationError:
        raise ConfigEntryAuthFailed("Control4 account authentication failed") from None
    except (DirectorRestError, aiohttp.ClientError, TimeoutError, OSError) as exc:
        raise ConfigEntryNotReady(f"Control4 connection failed ({type(exc).__name__})") from None
    finally:
        if not setup_complete:
            if transport is not None:
                await transport.stop()
            await session.close()


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Remove entities, subscriptions, WebSocket, and owned HTTP session."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        runtime: Control4Runtime = entry.runtime_data
        await runtime.transport.stop()
        await runtime.session.close()
    return unloaded
