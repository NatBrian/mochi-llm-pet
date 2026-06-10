import time

from deskpet.memory.store import MemoryStore
from deskpet.memory.retrieval import retrieve, format_for_prompt
from deskpet.memory.pet_state import PetStateManager
from deskpet.types import Rect, WindowInfo, WorldSnapshot


def _store(tmp_path):
    return MemoryStore(tmp_path / "t.db")


def test_add_and_recent(tmp_path):
    s = _store(tmp_path)
    s.add("user coded for 6 hours again", salience=0.8)
    s.add("user laughed when I chased the cursor", salience=0.6)
    rec = s.recent(5)
    assert len(rec) == 2
    assert rec[0].text.startswith("user laughed")  # most recent first


def test_keyword_search(tmp_path):
    s = _store(tmp_path)
    s.add("user loves watching youtube lofi videos")
    s.add("user hates when I block the screen")
    hits = s.search("youtube", k=5)
    assert any("youtube" in h.text for h in hits)


def test_retrieve_blends_context(tmp_path):
    s = _store(tmp_path)
    s.add("user watches youtube every afternoon", salience=0.7)
    s.add("user prefers tea over coffee")
    w = WindowInfo(1, "chrome.exe", 2, "lofi - YouTube", Rect(0, 0, 100, 100), 0, True, "youtube")
    snap = WorldSnapshot(foreground=w, windows=(w,))
    out = retrieve(s, snap, k=2)
    assert out
    assert "youtube" in out[0].text.lower()
    assert "- " in format_for_prompt(out)


def test_pet_state_persists(tmp_path):
    s = _store(tmp_path)
    mgr = PetStateManager(s)
    mgr.reward(bond=0.2, xp=120)  # cross a level boundary
    mgr.save()
    s2 = MemoryStore(tmp_path / "t.db")
    loaded = s2.load_pet_state()
    assert loaded is not None
    assert loaded.bond > 0.3
    assert loaded.level >= 2


def test_energy_drains_and_regens(tmp_path):
    s = _store(tmp_path)
    mgr = PetStateManager(s)
    mgr.state = mgr.state.__class__(energy=0.5)
    mgr._last = time.time() - 600  # pretend 10 min passed
    awake = mgr.tick(napping=False, foreground_app="code.exe")
    assert awake.energy < 0.5
    mgr._last = time.time() - 600
    napped = mgr.tick(napping=True, foreground_app=None)
    assert napped.energy > awake.energy


def test_prune_keeps_salient(tmp_path):
    s = _store(tmp_path)
    for i in range(10):
        s.add(f"low memory {i}", salience=0.1)
    s.add("very important memory", salience=0.9)
    s.prune(max_rows=3)
    remaining = s.recent(50)
    assert len(remaining) == 3
    assert any("important" in r.text for r in remaining)
