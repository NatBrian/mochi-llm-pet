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


def infer_activity(world: WorldSnapshot) -> str:
    """A coarse read of what the human is doing, from the active app + idle. Used
    so the cat reacts differently to a gamer vs a reader vs someone who's away."""
    if world.idle_s >= 120:
        return "away / not at the desk right now"
    cg = world.foreground.content_guess if world.foreground else None
    proc = (world.foreground.process if world.foreground else "").lower()
    if cg in ("youtube", "video", "media"):
        return "watching a video"
    if cg == "code":
        return "coding / working"
    if cg == "terminal":
        return "typing commands / working"
    if cg == "web":
        return "browsing the web"
    if any(g in proc for g in ("game", "steam", "minecraft", "league", "valorant")):
        return "playing a game"
    return "busy at the computer"


def context_hints(world: WorldSnapshot, hour: int) -> list[str]:
    """Time / session-length context the cat can act on (clingy late at night,
    bored after an hour in the same app, etc.)."""
    hints: list[str] = []
    if hour >= 23 or hour < 6:
        hints.append("it's late at night — the human should be asleep")
    elif hour < 9:
        hints.append("it's early morning")
    dwell = world.pet.time_in_app_s
    if dwell >= 3600:
        hints.append("the human has been glued to the same app for over an hour")
    elif dwell >= 1800:
        hints.append("the human has been in the same app a long time")
    if world.pet.energy < 0.3:
        hints.append("you're running low on energy")
    return hints


def _cap(title: str, n: int = 70) -> str:
    title = (title or "").strip()
    return title if len(title) <= n else title[: n - 1] + "…"


def scene_text(world: WorldSnapshot, now: float | None = None,
               share_titles: bool = True) -> str:
    now = now or world.t or time.time()
    lt = time.localtime(now)
    lines: list[str] = []
    lines.append(f"TIME: {time.strftime('%H:%M', lt)} ({_part_of_day(lt.tm_hour)})")

    if world.foreground:
        f = world.foreground
        cg = f" [content: {f.content_guess}]" if f.content_guess else ""
        title = f' "{_cap(f.title)}"' if (share_titles and f.title) else ""
        dwell = _dur(world.pet.time_in_app_s)
        lines.append(f"ACTIVE: {f.process}{title}{cg} for {dwell}")
    else:
        lines.append("ACTIVE: (none)")

    # Raw window titles let the LLM recognise ANY app by name (its own knowledge),
    # not just the allow-listed content types. Capped + gated by share_titles
    # (off = process names only, for cloud-privacy).
    others = [w for w in world.windows if not w.is_foreground][:5]
    if others:
        lines.append("WINDOWS:")
        for w in others:
            cg = f" [{w.content_guess}]" if w.content_guess else ""
            title = f' "{_cap(w.title)}"' if (share_titles and w.title) else ""
            lines.append(f"  - {w.process}{title}{cg}")

    over = ""
    if world.foreground and world.foreground.rect.contains(world.cursor):
        over = " over=ACTIVE"
    lines.append(f"CURSOR: ({int(world.cursor.x)},{int(world.cursor.y)}){over}")
    lines.append(f"USER_IDLE: {_dur(world.idle_s)}")
    lines.append(f"ACTIVITY: the human is {infer_activity(world)}")

    p = world.pet
    lines.append(
        f"PET: energy {p.energy:.2f}({_need_word(p.energy)}) mood {_mood_word(p.mood)} "
        f"bond {p.bond:.2f} level {p.level}"
    )

    hints = context_hints(world, lt.tm_hour)
    if hints:
        lines.append("CONTEXT: " + "; ".join(hints))

    if world.user_said:
        lines.append(f'USER SAID: "{world.user_said}"')

    lines.append("NAMES: " + ", ".join(world.names()))
    return "\n".join(lines)
