"""Sprite logic tests — manifest + player frame timing, no Qt needed (frames are
plain placeholder objects)."""

from deskpet.sprite.manifest import (
    FALLBACKS, Manifest, AnimSpec, resolve_state, default_manifest_yaml, load,
)
from deskpet.sprite.player import AnimationClip, AnimationPlayer


def test_fallback_resolution():
    available = {"idle", "walk", "sleep", "happy", "sit"}
    assert resolve_state("run", available) == "walk"        # run->walk
    assert resolve_state("chase", available) == "walk"      # chase->run->walk
    assert resolve_state("nap", available) == "sleep"       # nap->sleep
    assert resolve_state("excited", available) == "happy"   # excited->happy
    assert resolve_state("walk", available) == "walk"       # direct
    assert resolve_state("totally_unknown", available) == "idle"


def test_manifest_get_falls_back_and_aliases():
    m = Manifest(
        specs={"Idle_1": AnimSpec("Idle_1", 0, 3), "W_1": AnimSpec("W_1", 4, 9)},
        aliases={"idle": "Idle_1", "walk": "W_1"},
    )
    assert m.get("idle").state == "Idle_1"     # via alias
    assert m.get("run").state == "W_1"         # run->walk->W_1 alias
    assert m.get("zzz").state == "Idle_1"      # ultimate fallback


def test_default_manifest_parses(tmp_path):
    p = tmp_path / "anim_manifest.yaml"
    p.write_text(default_manifest_yaml())
    m = load(p)
    assert m is not None
    assert m.frame_w == 32 and m.columns == 10
    assert "idle" in m.specs
    assert m.specs["walk"].count == 6   # from:4 to:9 -> 6 frames


def _clips():
    f = ["a", "b", "c", "d"]
    return {
        "idle": AnimationClip("idle", f, fps=4, loop=True),
        "pounce": AnimationClip("pounce", f, fps=8, loop=False),
    }


def test_player_loops():
    p = AnimationPlayer(_clips())
    p.set_state("idle")
    p.update(0.25)   # 1 frame at 4fps
    assert p.frame_index == 1
    p.update(1.0)    # +4 frames -> wraps
    assert p.frame_index == 1  # (1*0.25 + 1.0)=1.25 *4 = 5 -> 5%4=1


def test_player_oneshot_completes():
    fired = []
    p = AnimationPlayer(_clips())
    p.on_complete = lambda s: fired.append(s)
    p.set_state("pounce")
    p.update(10.0)
    assert p.finished
    assert p.frame_index == 3   # clamps to last
    assert fired == ["pounce"]


def test_player_facing():
    p = AnimationPlayer(_clips())
    p.set_facing(5)
    assert p.facing == 1
    p.set_facing(-5)
    assert p.facing == -1


def test_real_manifest_loads_all_tags():
    import os
    from deskpet.sprite.manifest import load, CANONICAL_STATES
    path = os.path.join(os.path.dirname(__file__), "..", "assets", "anim_manifest.yaml")
    if not os.path.exists(path):
        import pytest
        pytest.skip("real manifest not generated")
    m = load(path)
    assert m.columns == 10 and m.frame_w == 32
    # many tags present
    assert len(m.specs) >= 90
    # multi-row range parsed correctly: Idle_3 = frames 8..15 (8 frames)
    assert m.specs["Idle_3"].count == 8
    # every canonical body state resolves via alias/fallback
    for s in CANONICAL_STATES:
        assert m.get(s) is not None, s


def test_aseprite_parser():
    import os
    from deskpet.tools.aseprite_to_manifest import parse_aseprite
    ase = os.path.join(os.path.dirname(__file__), "..", "assets",
                       "Cat_85_Animations", "Cat_Ginger.aseprite")
    if not os.path.exists(ase):
        import pytest
        pytest.skip("aseprite file not present")
    frames, w, h, durs, tags = parse_aseprite(ase)
    assert (w, h) == (32, 32)
    assert frames == 483
    names = [t[0] for t in tags]
    assert "Idle_1" in names and "Dance" in names and "Run_1" in names
