"""Assemble the prompt packet: system persona + scene text + memories (+image)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..types import MemoryRecord, WorldSnapshot
from .personality import system_prompt
from .scene import scene_text
from .schema import intent_json_schema
from ..memory.retrieval import format_for_prompt


@dataclass
class PromptPacket:
    system: str
    user_text: str
    image: Optional[bytes]
    schema: dict


def build(
    world: WorldSnapshot,
    memories: list[MemoryRecord],
    image: Optional[bytes],
    persona: str = "mochi",
) -> PromptPacket:
    user = scene_text(world)
    user += "\n\nRELEVANT MEMORIES:\n" + format_for_prompt(memories)
    if world.user_said:
        user += f'\n\nThe user just said to you: "{world.user_said}"'
    user += "\n\nDecide your next action. Respond with the JSON intent only."
    return PromptPacket(
        system=system_prompt(persona),
        user_text=user,
        image=image,
        schema=intent_json_schema(),
    )
