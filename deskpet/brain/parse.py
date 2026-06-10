"""Robust extraction of an Intent from whatever a model returns.

Treats EVERY response as untrusted: strips reasoning/markdown noise, finds the
first balanced JSON object even amid trailing prose, then coerces fields back to
the closed enum vocabularies. `safe_parse` never raises — bad input degrades to
an idle Intent so the body can never be crashed by the brain.
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from ..types import Emotion, Intent, Vec2, Verb
from .schema import EMOTIONS, VERBS

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)


class IntentParseError(ValueError):
    pass


def _strip_noise(text: str) -> str:
    text = _THINK_RE.sub("", text)
    # If fenced, prefer the fenced content.
    m = _FENCE_RE.search(text)
    if m:
        return m.group(1).strip()
    return text.strip()


def _first_json_object(text: str) -> Optional[str]:
    """Scan for the first balanced {...}, respecting strings/escapes."""
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]
    return None


def extract_json(raw: str) -> dict:
    """Locate and parse the JSON object from raw model text. Raises on failure."""
    cleaned = _strip_noise(raw)
    blob = _first_json_object(cleaned)
    if blob is None:
        # maybe the whole thing is already JSON
        blob = cleaned
    try:
        data = json.loads(blob)
    except json.JSONDecodeError as e:
        raise IntentParseError(f"no valid JSON object found: {e}") from e
    if not isinstance(data, dict):
        raise IntentParseError("parsed JSON is not an object")
    return data


def _coerce_enum(value: Any, allowed: list[str], default: str) -> str:
    if isinstance(value, str):
        v = value.strip().lower()
        if v in allowed:
            return v
        # tolerate near-misses like "walk" -> "walk_to"
        for a in allowed:
            if a.startswith(v) or v.startswith(a):
                return a
    return default


def _coerce_point(value: Any) -> Optional[Vec2]:
    if isinstance(value, dict) and "x" in value and "y" in value:
        try:
            return Vec2(float(value["x"]), float(value["y"]))
        except (TypeError, ValueError):
            return None
    if isinstance(value, (list, tuple)) and len(value) == 2:
        try:
            return Vec2(float(value[0]), float(value[1]))
        except (TypeError, ValueError):
            return None
    return None


def _clean_str(value: Any, limit: int) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    value = value.strip()
    if not value or value.lower() in ("null", "none"):
        return None
    return value[:limit]


def _coerce_emote(value: Any) -> Optional[str]:
    """Keep an emote only if it's a known token (closed vocabulary)."""
    from ..sprite.expressions import EMOTE_TOKENS

    if not isinstance(value, str):
        return None
    tok = value.strip().lower().replace(" ", "_").replace("-", "_")
    return tok if tok in EMOTE_TOKENS else None


def coerce_intent(data: dict) -> Intent:
    """Turn a raw dict into a valid Intent, filling/clamping every field."""
    verb = Verb(_coerce_enum(data.get("verb"), VERBS, "idle"))
    emotion = Emotion(_coerce_enum(data.get("emotion"), EMOTIONS, "neutral"))

    edge = data.get("edge")
    if isinstance(edge, str):
        edge = edge.strip().lower()
        if edge not in ("top", "bottom", "left", "right"):
            edge = None
    else:
        edge = None

    try:
        confidence = float(data.get("confidence", 0.5))
    except (TypeError, ValueError):
        confidence = 0.5
    confidence = max(0.0, min(1.0, confidence))

    dur = data.get("duration_hint_s")
    try:
        dur = float(dur) if dur is not None else None
    except (TypeError, ValueError):
        dur = None

    return Intent(
        verb=verb,
        target=_clean_str(data.get("target"), 80),
        point=_coerce_point(data.get("point")),
        edge=edge,
        emotion=emotion,
        emote=_coerce_emote(data.get("emote")),
        say=_clean_str(data.get("say"), 220),
        thought=_clean_str(data.get("thought"), 200) or "",
        remember=_clean_str(data.get("remember"), 200),
        duration_hint_s=dur,
        confidence=confidence,
    )


def safe_parse(raw: str | dict) -> Intent:
    """Never raises. Anything unparseable -> a low-confidence idle Intent."""
    try:
        data = raw if isinstance(raw, dict) else extract_json(raw)
        return coerce_intent(data)
    except Exception:  # noqa: BLE001 — by design, the body must never crash
        return Intent(verb=Verb.IDLE, emotion=Emotion.NEUTRAL, confidence=0.0,
                      thought="(unparseable model output)")
