from deskpet.body.body import Body
from deskpet.body.motion import MotionState, seek, spring
from deskpet.body.physics import Physics
from deskpet.types import (
    Emotion, Intent, Rect, Vec2, Verb, WindowInfo, WorldSnapshot,
)


def _snap(cursor=(500, 500), pet=(300, 300)):
    code = WindowInfo(2, "Code.exe", 200, "x", Rect(0, 0, 960, 1040), 0, True, "code")
    chrome = WindowInfo(1, "chrome.exe", 100, "YouTube", Rect(960, 0, 1920, 1040), 1, False, "youtube")
    return WorldSnapshot(cursor=Vec2(*cursor), foreground=code, windows=(code, chrome),
                         monitors=(Rect(0, 0, 1920, 1080),), taskbar=Rect(0, 1040, 1920, 1080),
                         pet_pos=Vec2(*pet))


def test_seek_arrives():
    m = MotionState(pos=Vec2(0, 0))
    arrived = False
    for _ in range(200):
        arrived = seek(m, Vec2(100, 0), 0.016, 180.0)
        if arrived:
            break
    assert arrived
    assert abs(m.pos.x - 100) < 8


def test_walk_to_completes_and_signals():
    b = Body(start_pos=Vec2(0, 0))
    done = []
    b.on_action_done = lambda: done.append(True)
    b.set_intent(Intent(verb=Verb.WALK_TO, point=Vec2(50, 0)))
    for _ in range(300):
        b.step(0.016, _snap())
        if done:
            break
    assert done
    assert b.anim_state in ("idle", "happy", "curious", "angry", "scared", "bored", "excited", "sleep")


def test_walk_to_uses_walk_anim_enroute():
    b = Body(start_pos=Vec2(0, 0))
    b.set_intent(Intent(verb=Verb.WALK_TO, point=Vec2(1000, 0)))
    b.step(0.016, _snap())
    assert b.anim_state == "walk"
    assert b.motion.facing == 1


def test_chase_tracks_cursor():
    b = Body(start_pos=Vec2(0, 0))
    b.set_intent(Intent(verb=Verb.CHASE, target="cursor"))
    b.step(0.05, _snap(cursor=(400, 0)))
    assert b.motion.pos.x > 0  # moved toward cursor
    assert b.anim_state == "run"


def test_facing_follows_target():
    b = Body(start_pos=Vec2(500, 0))
    b.set_intent(Intent(verb=Verb.WALK_TO, point=Vec2(0, 0)))
    b.step(0.016, _snap())
    assert b.motion.facing == -1


def test_reflex_grab_blocks_intent_swap():
    b = Body(start_pos=Vec2(100, 100))
    b.grab()
    b.set_intent(Intent(verb=Verb.WALK_TO, point=Vec2(0, 0)))  # should be ignored
    assert b.current_intent.verb is not Verb.WALK_TO


def test_throw_settles_on_taskbar():
    b = Body(start_pos=Vec2(500, 100))
    b.grab()
    # simulate a drag then a fast upward-right fling
    b.drag_to(Vec2(500, 100), 0.0)
    b.drag_to(Vec2(520, 90), 0.05)
    b.release(0.05)
    settled = False
    snap = _snap()
    for _ in range(2000):
        b.step(0.016, snap)
        if b.physics.state == "done":
            settled = True
            break
    assert settled
    # came to rest on the taskbar top (1040) minus sprite half
    assert abs(b.motion.pos.y - (1040 - b.physics.half)) < 2


def test_nap_walks_down_then_sleeps():
    b = Body(start_pos=Vec2(500, 400))  # well above the taskbar (top=1040)
    b.set_intent(Intent(verb=Verb.NAP, emotion=Emotion.SLEEPY))
    b.step(0.016, _snap())
    assert b.anim_state == "walk"       # heading down first
    for _ in range(400):
        b.step(0.016, _snap())
    assert b.anim_state == "sleep"      # settled near the taskbar line
    assert abs(b.motion.pos.y - 1040) < 30


def test_poke_reacts_annoyed():
    b = Body()
    b.poke()
    assert b.current_intent.emotion is Emotion.ANNOYED
    assert b.current_intent.say


def test_interaction_callback_fires():
    b = Body(start_pos=Vec2(300, 300))
    got = []
    b.on_interaction = lambda kind: got.append(kind)
    b.poke()                       # quick tap -> "poke"
    b.grab()
    b.drag_to(Vec2(310, 310), 0.0)
    b.release(0.1)                 # drag + release -> "throw"
    assert "poke" in got
    assert "throw" in got


def test_poke_cancels_grab_physics():
    # a press calls grab() (physics held); a tap must cancel it, not freeze
    b = Body(start_pos=Vec2(300, 300))
    b.grab()
    assert b.physics.active          # held
    b.poke()
    assert not b.physics.active      # poke cancelled the grab
    assert b.current_intent.emotion is Emotion.ANNOYED


def test_pet_is_affectionate_reflex():
    b = Body(start_pos=Vec2(300, 300))
    kinds = []
    b.on_interaction = lambda k: kinds.append(k)
    b.pet()
    assert b.current_intent.emotion is Emotion.AFFECTIONATE
    assert not b.physics.active
    assert kinds == ["pet"]


def _throw_until_settled(x, vy, windows=()):
    from deskpet.body.physics import Physics
    from deskpet.body.motion import MotionState
    mon = Rect(0, 0, 1920, 1080)
    taskbar = Rect(0, 1040, 1920, 1080)
    ph = Physics(sprite_half=24)
    ph.state = "thrown"
    m = MotionState(pos=Vec2(x, 120), vel=Vec2(0, vy))
    for _ in range(3000):
        ph.step(m, 0.016, (mon,), taskbar, windows)
        if ph.state == "done":
            break
    return m.pos


def test_throw_lands_on_window_top():
    win = WindowInfo(1, "Code.exe", 1, "x", Rect(800, 400, 1400, 1040), 0, True, "code")
    pos = _throw_until_settled(1000, 200, windows=(win,))   # x over the window
    assert abs(pos.y - (400 - 24)) < 2          # rests on the window's top edge


def test_throw_beside_window_lands_on_taskbar():
    win = WindowInfo(1, "Code.exe", 1, "x", Rect(800, 400, 1400, 1040), 0, True, "code")
    pos = _throw_until_settled(300, 200, windows=(win,))     # x NOT over the window
    assert abs(pos.y - (1040 - 24)) < 2         # falls past to the taskbar
