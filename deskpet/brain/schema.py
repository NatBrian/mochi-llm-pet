"""The Intent JSON schema — the SINGLE source of truth for what the LLM emits.

Every provider derives its structured-output config from here, so the body, the
prompt, and all five backends stay in lockstep. Also exposes provider-specific
shapes (Anthropic tool, Gemini schema) and a human-readable summary for
prompt-enforced backends.
"""

from __future__ import annotations

from ..types import Emotion, Verb

VERBS: list[str] = [v.value for v in Verb]
EMOTIONS: list[str] = [e.value for e in Emotion]


def intent_json_schema() -> dict:
    """JSON Schema (draft subset) for the Intent object."""
    return {
        "type": "object",
        "properties": {
            "thought": {"type": "string", "description": "private reasoning, <=200 chars"},
            "verb": {"type": "string", "enum": VERBS},
            "target": {
                "type": ["string", "null"],
                "description": "a name from the NAMES list, or null",
            },
            "point": {
                "type": ["object", "null"],
                "properties": {"x": {"type": "number"}, "y": {"type": "number"}},
                "description": "explicit pixel target; usually null",
            },
            "edge": {"type": ["string", "null"], "enum": ["top", "bottom", "left", "right", None]},
            "emotion": {"type": "string", "enum": EMOTIONS},
            "say": {"type": ["string", "null"], "description": "short speech <=120 chars, or null"},
            "remember": {"type": ["string", "null"], "description": "fact worth persisting, or null"},
            "duration_hint_s": {"type": ["number", "null"]},
            "confidence": {"type": "number"},
        },
        "required": ["thought", "verb", "emotion", "say", "confidence"],
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
            "say": field("STRING", nullable=True),
            "remember": field("STRING", nullable=True),
            "duration_hint_s": field("NUMBER", nullable=True),
            "confidence": field("NUMBER"),
        },
        "required": ["thought", "verb", "emotion", "confidence"],
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
        '  "say": short speech <=120 chars, or null\n'
        '  "remember": a fact worth persisting, or null\n'
        '  "duration_hint_s": number or null\n'
        '  "confidence": number 0..1\n'
    )
