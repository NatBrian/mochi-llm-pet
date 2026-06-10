from deskpet.config import PerceptionConfig
from deskpet.perception.world import WorldState, WorldStateBuilder
from deskpet.perception.title_parser import content_guess
from deskpet.triggers.events import EventBus, TriggerEvent
from deskpet.perception.sensor_loop import Sensors
from deskpet.types import Vec2


def test_mock_world_resolves():
    b = WorldStateBuilder(PerceptionConfig())
    snap = b.build()  # mock on Linux
    assert snap.resolve("cursor") is not None
    assert snap.resolve("active_window") is not None
    assert snap.resolve("window:chrome") is not None
    assert snap.resolve("taskbar") is not None
    assert snap.resolve("nonsense") is None


def test_worldstate_thread_holder():
    ws = WorldState()
    b = WorldStateBuilder(PerceptionConfig())
    snap = b.build()
    ws.write(snap)
    assert ws.read() is snap


def test_content_guess():
    assert content_guess("chrome.exe", "lofi - YouTube") == "youtube"
    assert content_guess("Code.exe", "main.py") == "code"
    assert content_guess("vlc.exe", "movie.mkv") == "media"
    assert content_guess("explorer.exe", "Downloads") is None


def test_sensors_emit_foreground_event():
    ws = WorldState()
    bus = EventBus()
    s = Sensors(PerceptionConfig(medium_ms=0), ws, bus)
    s.tick(now=100.0)  # forces a medium rebuild
    kinds = {e.kind for e in bus.drain()}
    assert TriggerEvent.FOREGROUND_CHANGED in kinds
