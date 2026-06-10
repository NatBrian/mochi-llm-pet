"""Memory retrieval — blend recency + keyword relevance into top-K bullets.

Keyword-first (ships zero-config). Embeddings are an optional re-rank behind a
config flag; the default NullEmbedder keeps it pure keyword.
"""

from __future__ import annotations

import time
from typing import Optional, Protocol

from ..types import MemoryRecord, WorldSnapshot
from .store import MemoryStore


class Embedder(Protocol):
    def embed(self, text: str) -> Optional[list[float]]: ...


class NullEmbedder:
    def embed(self, text: str) -> Optional[list[float]]:
        return None


def _query_text(world: WorldSnapshot) -> str:
    parts = []
    if world.foreground:
        parts.append(world.foreground.process)
        parts.append(world.foreground.title)
        if world.foreground.content_guess:
            parts.append(world.foreground.content_guess)
    if world.user_said:
        parts.append(world.user_said)
    return " ".join(parts)


def retrieve(
    store: MemoryStore, world: WorldSnapshot, k: int = 5
) -> list[MemoryRecord]:
    """Top-K memories: union of keyword matches on the current context and the
    most recent, scored by relevance + recency decay + salience."""
    query = _query_text(world)
    now = time.time()

    candidates: dict[int, MemoryRecord] = {}
    for r in store.search(query, k=k * 3):
        candidates[r.id] = r
    for r in store.recent(n=k * 2):
        candidates.setdefault(r.id, r)

    hits = {r.id for r in store.search(query, k=k * 3)}

    def score(r: MemoryRecord) -> float:
        age_days = max(0.0, (now - r.ts) / 86400.0)
        recency = 1.0 / (1.0 + age_days)          # 1.0 now -> decays
        relevance = 1.0 if r.id in hits else 0.0
        return 0.6 * relevance + 0.3 * recency + 0.1 * r.salience

    ranked = sorted(candidates.values(), key=score, reverse=True)
    return ranked[:k]


def format_for_prompt(records: list[MemoryRecord]) -> str:
    if not records:
        return "(no notable memories yet)"
    return "\n".join(f"- {r.text}" for r in records)
