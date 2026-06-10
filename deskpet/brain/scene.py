"""WorldState -> compact, labeled "scene text".

Pre-digested, not raw: a few foreground/window facts, cursor location, time, and
the pet's own needs. The NAMES line tells the model exactly which `target`
strings are resolvable. Numbers are rounded and lists capped to bound tokens.
"""

from __future__ import annotations

import time

from ..types import WorldSnapshot


def _part_of_day(hour: int) -> str:
    if hour < 6:
        return "late night"
    if hour < 12:
        return "morning"
    if hour < 18:
        return "afternoon"
    if hour < 22:
        return "evening"
    return "night"


def _need_word(energy: float) -> str:
    if energy < 0.25:
        return "exhausted"
    if energy < 0.5:
        return "low"
    if energy < 0.8:
        return "ok"
    return "full"


def _mood_word(mood: float) -> str:
    if mood < -0.4:
        return "grumpy"
    if mood < -0.1:
        return "down"
    if mood < 0.1:
        return "neutral"
    if mood < 0.4:
        return "content"
    return "happy"


def _dur(seconds: float) -> str:
    m = int(seconds // 60)
    if m <= 0:
        return f"{int(seconds)}s"
    return f"{m}m"


def scene_text(world: WorldSnapshot, now: float | None = None) -> str:
    now = now or world.t or time.time()
    lt = time.localtime(now)
    lines: list[str] = []
    lines.append(f"TIME: {time.strftime('%H:%M', lt)} ({_part_of_day(lt.tm_hour)})")

    if world.foreground:
        f = world.foreground
        cg = f" [content: {f.content_guess}]" if f.content_guess else ""
        dwell = _dur(world.pet.time_in_app_s)
        lines.append(f'ACTIVE: {f.process} "{f.title}"{cg} for {dwell}')
    else:
        lines.append("ACTIVE: (none)")

    others = [w for w in world.windows if not w.is_foreground][:5]
    if others:
        lines.append("WINDOWS:")
        for w in others:
            cg = f" [{w.content_guess}]" if w.content_guess else ""
            lines.append(f'  - {w.process} "{w.title}"{cg}')

    over = ""
    if world.foreground and world.foreground.rect.contains(world.cursor):
        over = " over=ACTIVE"
    lines.append(f"CURSOR: ({int(world.cursor.x)},{int(world.cursor.y)}){over}")
    lines.append(f"USER_IDLE: {_dur(world.idle_s)}")

    p = world.pet
    lines.append(
        f"PET: energy {p.energy:.2f}({_need_word(p.energy)}) mood {_mood_word(p.mood)} "
        f"bond {p.bond:.2f} level {p.level}"
    )
    if world.user_said:
        lines.append(f'USER SAID: "{world.user_said}"')

    lines.append("NAMES: " + ", ".join(world.names()))
    return "\n".join(lines)
