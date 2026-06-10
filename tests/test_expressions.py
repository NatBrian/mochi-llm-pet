"""Tests for the animation richness layer: variant pools + emote catalog, the
parse-level emote coercion, and the body's emote/variant selection."""

import os
import random

from deskpet.sprite.expressions import (
    EMOTE_CATALOG, EMOTE_TOKENS, POOLS, is_oneshot_emote, pick_variant,
    resolve_emote, prompt_menu,
)
from deskpet.brain.parse import coerce_intent
from deskpet.body.body import Body
from deskpet.types import Emotion, Intent, Rect, Vec2, Verb, WorldSnapshot


def test_catalog_well_formed():
    assert EMOTE_TOKENS == set(EMOTE_CATALOG)
    for token, (tags, desc) in EMOTE_CATALOG.items():
        assert tags and isinstance(tags, list)
        assert desc
    # the prompt menu lists every token
    menu = prompt_menu()
    for token in EMOTE_TOKENS:
        assert token in menu


def test_real_pack_fully_covered():
    """Every non-junk tag in the default pack is reachable via a pool, an emote,
    or a canonical alias — so no downloaded animation is dead weight."""
    from deskpet.sprite.manifest import load
    path = os.path.join(os.path.dirname(__file__), "..", "assets", "anim_manifest.yaml")
    if not os.path.exists(path):
        import pytest
        pytest.skip("real manifest not generated")
    m = load(path)
    tags = set(m.specs)
    covered = set(m.aliases.values())
    for v in POOLS.values():
        covered |= set(v)
    for t, _ in EMOTE_CATALOG.values():
        covered |= set(t)
    JUNK = {"Frame"}
    assert tags - covered - JUNK == set(), tags - covered - JUNK


def test_resolve_emote_filters_to_available():
    rng = random.Random(0)
    avail = {"Idle_Yes", "Sit_Yes", "Idle_1"}
    assert resolve_emote("nod", avail, rng) in {"Idle_Yes", "Sit_Yes"}
    assert resolve_emote("dig", avail, rng) is None        # no dig tags present
    assert resolve_emote("bogus", avail, rng) is None
    assert resolve_emote(None, avail, rng) is None


def test_pick_variant():
    rng = random.Random(0)
    avail = {"Idle_1", "Idle_2", "W_1"}
    assert pick_variant("idle", avail, rng) in {"Idle_1", "Idle_2"}
    assert pick_variant("walk", avail, rng) == "W_1"
    assert pick_variant("nonexistent", avail, rng) is None


def test_is_oneshot():
    assert is_oneshot_emote("nod")
    assert not is_oneshot_emote("scratch")
    assert not is_oneshot_emote(None)


def test_parse_coerces_emote():
    assert coerce_intent({"verb": "idle", "emotion": "happy", "emote": "nod"}).emote == "nod"
    # synonyms normalized (spaces/dashes -> underscores, lowercased)
    assert coerce_intent({"verb": "idle", "emotion": "neutral", "emote": "Shake-Head"}).emote == "shake_head"
    # unknown token dropped (closed vocabulary)
    assert coerce_intent({"verb": "idle", "emotion": "neutral", "emote": "Idle_99"}).emote is None
    assert coerce_intent({"verb": "idle", "emotion": "neutral"}).emote is None


# ---- body integration (no Qt: frames are placeholder objects) -------------- #
def _player(tags, loops):
    from deskpet.sprite.player import AnimationClip, AnimationPlayer
    clips = {t: AnimationClip(t, ["a", "b", "c", "d"], fps=8, loop=loops.get(t, True))
             for t in tags}
    clips["idle"] = AnimationClip("idle", ["a", "b"], fps=8, loop=True)
    clips["happy"] = AnimationClip("happy", ["a", "b"], fps=8, loop=True)
    return AnimationPlayer(clips)


def _snap():
    return WorldSnapshot(monitors=(Rect(0, 0, 1920, 1080),), cursor=Vec2(500, 500))


def test_body_variant_variety():
    b = Body(start_pos=Vec2(500, 500))
    b.attach_player(_player(["Idle_1", "Idle_2", "Idle_3"], {}))
    seen = set()
    for _ in range(60):
        b._variant_for = None  # force re-entry
        b.set_intent(Intent(verb=Verb.IDLE, emotion=Emotion.NEUTRAL))
        b.step(0.016, _snap())
        seen.add(b.player.state)
    assert len(seen & {"Idle_1", "Idle_2", "Idle_3"}) >= 2  # actually rotates


def test_body_oneshot_emote_reverts():
    b = Body(start_pos=Vec2(500, 500))
    b.attach_player(_player(["Idle_Yes"], {"Idle_Yes": True}))  # loop clip, token is one-shot
    b.set_intent(Intent(verb=Verb.IDLE, emotion=Emotion.HAPPY, emote="nod"))
    states = []
    for _ in range(60):
        b.step(0.016, _snap())
        states.append(b.player.state)
    assert states[0] == "Idle_Yes"          # emote played first
    assert b._emote_done                     # one cycle elapsed -> done
    assert states[-1] != "Idle_Yes"          # reverted to normal anim


def test_body_loop_emote_persists():
    b = Body(start_pos=Vec2(500, 500))
    b.attach_player(_player(["Scratching_1"], {"Scratching_1": True}))
    b.set_intent(Intent(verb=Verb.IDLE, emotion=Emotion.NEUTRAL, emote="scratch"))
    for _ in range(120):
        b.step(0.016, _snap())
    assert b.player.state == "Scratching_1"  # loops, never reverts
    assert not b._emote_done


def test_body_emote_suppressed_while_moving():
    b = Body(start_pos=Vec2(0, 500))
    b.attach_player(_player(["Dance", "W_1"], {}))
    b.set_intent(Intent(verb=Verb.WALK_TO, point=Vec2(1800, 500), emote="dance"))
    b.step(0.016, _snap())
    assert b.player.state != "Dance"         # walking suppresses the emote
