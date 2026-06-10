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
    recent: Optional[list[str]] = None,
    changes: Optional[list[str]] = None,
    share_titles: bool = True,
) -> PromptPacket:
    user = scene_text(world, share_titles=share_titles)
    if changes:
        user += ("\n\nJUST HAPPENED (react to this — it's why you're deciding now):\n"
                 + "\n".join(f"  - {c}" for c in changes))
    user += "\n\nRELEVANT MEMORIES:\n" + format_for_prompt(memories)
    if recent:
        user += (
            "\n\nWHAT YOU JUST DID (your last few turns, newest last). DO NOT "
            "repeat the same action or reuse the same words — a real cat does "
            "something DIFFERENT. Change your verb, your mood, AND your line:\n"
            + "\n".join(f"  - {r}" for r in recent)
        )
    if world.user_said:
        user += f'\n\nThe user just said to you: "{world.user_said}"'
    user += "\n\nDecide your next action. Respond with the JSON intent only."
    return PromptPacket(
        system=system_prompt(persona),
        user_text=user,
        image=image,
        schema=intent_json_schema(),
    )
