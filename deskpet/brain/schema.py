"""The Intent JSON schema — the SINGLE source of truth for what the LLM emits.

Every provider derives its structured-output config from here, so the body, the
prompt, and all five backends stay in lockstep. Also exposes provider-specific
shapes (Anthropic tool, Gemini schema) and a human-readable summary for
prompt-enforced backends.
"""

from __future__ import annotations

from ..types import Emotion, Verb
from ..sprite.expressions import EMOTE_TOKENS

VERBS: list[str] = [v.value for v in Verb]
EMOTIONS: list[str] = [e.value for e in Emotion]
EMOTES: list[str] = sorted(EMOTE_TOKENS)


def intent_json_schema() -> dict:
    """JSON Schema (draft subset) for the Intent object.

    Deliberately small: the LLM has ONE job — control the pet. It outputs an
    action (verb + where), a mood, optional expression + speech, and a private
    reason. Memory, persistence, confidence, pixel math etc. are handled by code,
    not the model, so it never has to think about them."""
    return {
        "type": "object",
        "properties": {
            "thought": {"type": "string", "description": "your private reason, <=200 chars (not shown)"},
            "verb": {"type": "string", "enum": VERBS, "description": "the action to take"},
            "target": {
                "type": ["string", "null"],
                "description": "where: a name from the NAMES list, or null",
            },
            "edge": {"type": ["string", "null"], "enum": ["top", "bottom", "left", "right", None],
                     "description": "for sit_on: which side of the window to perch on"},
            "emotion": {"type": "string", "enum": EMOTIONS, "description": "your current mood"},
            "emote": {
                "type": ["string", "null"],
                "enum": EMOTES + [None],
                "description": "optional expressive animation (see persona menu), or null",
            },
            "say": {"type": ["string", "null"], "description": "what you say out loud, <=220 chars, or null for silence"},
        },
        "required": ["verb", "emotion"],
        "additionalProperties": False,
    }


def anthropic_tool() -> dict:
    """Anthropic tool definition — the model fills `input` which arrives as a dict."""
    return {
        "name": "emit_intent",
        "description": "Emit the pet's next action as a structured intent.",
        "input_schema": intent_json_schema(),
    }


def gemini_schema() -> dict:
    """Gemini's responseSchema dialect: no union types, uses `nullable`, no
    `additionalProperties`, enums only on plain strings."""
    def field(t: str, **extra) -> dict:
        d = {"type": t}
        d.update(extra)
        return d

    return {
        "type": "OBJECT",
        "properties": {
            "thought": field("STRING"),
            "verb": field("STRING", enum=VERBS),
            "target": field("STRING", nullable=True),
            "edge": field("STRING", nullable=True),
            "emotion": field("STRING", enum=EMOTIONS),
            "emote": field("STRING", nullable=True),
            "say": field("STRING", nullable=True),
        },
        "required": ["verb", "emotion"],
    }


def summary_for_prompt() -> str:
    """Human-readable schema for prompt-enforced backends (custom OpenAI proxies
    that lack a JSON mode)."""
    return (
        "Respond with ONLY a JSON object, no prose, with these fields:\n"
        '  "thought": string (your private reasoning, <=200 chars)\n'
        f'  "verb": one of {VERBS}\n'
        '  "target": a name from the NAMES list, or null\n'
        '  "edge": "top"|"bottom"|"left"|"right"|null (only for sit_on)\n'
        f'  "emotion": one of {EMOTIONS}\n'
        f'  "emote": optional expressive animation, one of {EMOTES}, or null\n'
        '  "say": what the pet says, 1-3 short sentences, <=220 chars, or null\n'
    )
