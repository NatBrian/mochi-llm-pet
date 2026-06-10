"""Rule-based brain — keeps the pet alive when the LLM is unreachable or every
parse fails. A small deterministic cat FSM. Also the first-run experience before
any cloud key is configured."""

from __future__ import annotations

from ..types import Emotion, Intent, Verb, WorldSnapshot


def rule_based(world: WorldSnapshot) -> Intent:
    p = world.pet

    # exhausted -> nap
    if p.energy < 0.2:
        return Intent(verb=Verb.NAP, emotion=Emotion.SLEEPY,
                      thought="(rule) low energy", confidence=0.4)

    # user idle a long time -> nap / sit bored
    if world.idle_s > 180:
        return Intent(verb=Verb.NAP, emotion=Emotion.BORED,
                      thought="(rule) user idle", confidence=0.4)

    # cursor near the pet -> bat at it
    near = (world.cursor - world.pet_pos).length()
    if near < 220:
        return Intent(verb=Verb.CHASE, target="cursor", emotion=Emotion.MISCHIEVOUS,
                      thought="(rule) cursor nearby", confidence=0.4)

    # something playing media -> go watch it
    for w in world.windows:
        if w.content_guess in ("youtube", "media", "video"):
            return Intent(verb=Verb.WATCH, target=f"window:{w.process}",
                          emotion=Emotion.CURIOUS, thought="(rule) media playing",
                          confidence=0.4)

    # a fresh foreground -> look at it, curious
    if world.foreground:
        return Intent(verb=Verb.LOOK_AT, target="active_window",
                      emotion=Emotion.CURIOUS, thought="(rule) watching the active window",
                      confidence=0.3)

    return Intent(verb=Verb.IDLE, emotion=Emotion.NEUTRAL,
                  thought="(rule) nothing going on", confidence=0.3)
