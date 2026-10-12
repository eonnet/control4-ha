"""Integration-wide recreation of Home Assistant entity IDs."""

from __future__ import annotations

from collections.abc import Iterable
import logging
from typing import Any


_LOGGER = logging.getLogger(__name__)


def entity_id_recreation_plan(
    registry: Any, entity_entries: Iterable[Any]
) -> tuple[tuple[str, str], ...]:
    """Return Home Assistant's automatic ID for every entry that would change."""
    reserved_entity_ids: set[str] = set()
    changes: list[tuple[str, str]] = []
    for entry in sorted(entity_entries, key=lambda candidate: candidate.entity_id):
        new_entity_id = registry.async_regenerate_entity_id(
            entry, reserved_entity_ids=reserved_entity_ids
        )
        reserved_entity_ids.add(new_entity_id)
        if new_entity_id != entry.entity_id:
            changes.append((entry.entity_id, new_entity_id))
    return tuple(changes)


def config_entry_entity_id_recreation_plan(
    hass: Any, config_entry_id: str
) -> tuple[tuple[str, str], ...]:
    """Plan automatic IDs for every entity owned by one config entry."""
    from homeassistant.helpers import entity_registry as er

    registry = er.async_get(hass)
    return entity_id_recreation_plan(
        registry,
        er.async_entries_for_config_entry(registry, config_entry_id),
    )


def recreate_config_entry_entity_ids(
    hass: Any, config_entry_id: str
) -> tuple[tuple[str, str], ...]:
    """Apply automatic IDs to every entity owned by one config entry."""
    from homeassistant.helpers import entity_registry as er

    registry = er.async_get(hass)
    changes = entity_id_recreation_plan(
        registry,
        er.async_entries_for_config_entry(registry, config_entry_id),
    )
    for old_entity_id, new_entity_id in changes:
        registry.async_update_entity(
            old_entity_id, new_entity_id=new_entity_id
        )
    if changes:
        _LOGGER.info(
            "Recreated %d Control4 entity IDs for config entry %s",
            len(changes),
            config_entry_id,
        )
    return changes
