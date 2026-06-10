from deskpet.brain.scene import scene_text
from deskpet.brain.context_builder import build
from deskpet.types import PetState, Rect, Vec2, WindowInfo, WorldSnapshot


def _world():
    chrome = WindowInfo(1, "chrome.exe", 100, "lofi - YouTube", Rect(960, 0, 1920, 1040), 0, False, "youtube")
    code = WindowInfo(2, "Code.exe", 200, "agent.py", Rect(0, 0, 960, 1040), 1, True, "code")
    return WorldSnapshot(
        cursor=Vec2(400, 300), foreground=code, windows=(code, chrome),
        monitors=(Rect(0, 0, 1920, 1080),), pet=PetState(energy=0.3, mood=0.5),
    )


def test_scene_has_key_sections():
    s = scene_text(_world())
    assert "ACTIVE:" in s
    assert "Code.exe" in s
    assert "youtube" in s
    assert "NAMES:" in s
    assert "window:chrome" in s
    assert "PET:" in s


def test_scene_names_match_resolvable():
    w = _world()
    s = scene_text(w)
    names_line = [ln for ln in s.splitlines() if ln.startswith("NAMES:")][0]
    names = [n.strip() for n in names_line.removeprefix("NAMES:").split(",")]
    # every advertised window name resolves
    for n in names:
        if n.startswith("window:"):
            assert w.resolve(n) is not None


def test_context_packet():
    pkt = build(_world(), [], None, persona="mochi")
    assert "Mochi" in pkt.system
    assert "ACTIVE:" in pkt.user_text
    assert pkt.schema["type"] == "object"
