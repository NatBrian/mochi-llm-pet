"""World fingerprint — a cheap hash of what matters, so a heartbeat that finds an
unchanged world can SKIP the (expensive) LLM call. Explicit events bypass this.
"""

from __future__ import annotations

import hashlib
from typing import Optional

from ..types import WorldSnapshot


def _band(x: float, step: float) -> int:
    return int(x // step)


def fingerprint(world: WorldSnapshot, screen_phash: Optional[str] = None) -> str:
    """Coarse signature: foreground identity, window set, cursor bucket, clipboard,
    pet need-band, idle-band, optional screenshot perceptual hash."""
    fg = ""
    if world.foreground:
        fg = f"{world.foreground.process}|{world.foreground.title}"
    win_set = ",".join(sorted(w.process for w in world.windows))
    cursor = f"{_band(world.cursor.x, 64)},{_band(world.cursor.y, 64)}"
    need = f"{_band(world.pet.energy, 0.2)},{_band(world.pet.mood + 1, 0.4)}"
    idle = _band(world.idle_s, 30)
    parts = [fg, win_set, cursor, world.clipboard_hash or "", need, str(idle), screen_phash or ""]
    return hashlib.sha1("§".join(parts).encode("utf-8")).hexdigest()[:16]
