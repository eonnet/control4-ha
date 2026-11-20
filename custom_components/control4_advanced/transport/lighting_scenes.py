"""Discovery metadata for the Control4 Advanced Lighting scene catalog."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


ADVANCED_LIGHTING_PROXY = "control4_agent_adv_lighting"
LIGHTING_SCENES_PATH = "/api/v1/agents/advanced_lighting"


@dataclass(frozen=True)
class LightingScene:
    scene_id: int
    name: str


def parse_lighting_scenes(records: list[Any]) -> tuple[LightingScene, ...]:
    """Read named scenes from the agent's authoritative catalog."""
    scenes: dict[int, LightingScene] = {}
    ambiguous_ids: set[int] = set()
    for record in records:
        if not isinstance(record, dict):
            continue
        scene_id = record.get("scene_id")
        name = record.get("name")
        if type(scene_id) is not int or not 0 <= scene_id <= 2_147_483_647:
            continue
        if not isinstance(name, str):
            continue
        name = name.strip()
        if not name or len(name) > 255 or not name.isprintable():
            continue
        if scene_id in scenes and scenes[scene_id].name != name:
            ambiguous_ids.add(scene_id)
        scenes[scene_id] = LightingScene(scene_id, name)
    return tuple(
        scene for scene_id, scene in scenes.items() if scene_id not in ambiguous_ids
    )
