from deskpet.config import TriggersConfig
from deskpet.triggers.diff import fingerprint
from deskpet.triggers.events import Event, EventBus, TriggerEvent
from deskpet.triggers.manager import TriggerManager
from deskpet.types import Rect, Vec2, WindowInfo, WorldSnapshot


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


def _world(cursor=(0, 0), fg_title="a"):
    w = WindowInfo(1, "code.exe", 2, fg_title, Rect(0, 0, 10, 10), 0, True, None)
    return WorldSnapshot(cursor=Vec2(*cursor), foreground=w, windows=(w,))


def cfg():
    return TriggersConfig(heartbeat_s=20, min_interval_s=4, idle_threshold_s=120)


def test_user_spoke_always_fires():
    clk = Clock()
    m = TriggerManager(cfg(), clock=clk)
    d = m.evaluate([Event(TriggerEvent.USER_SPOKE, "hi")], _world())
    assert d.wake and d.reason.startswith("event:user_spoke")


def test_heartbeat_skips_unchanged_world():
    clk = Clock()
    m = TriggerManager(cfg(), clock=clk)
    w = _world()
    # first heartbeat after interval -> changed (no prior fp) fires
    clk.advance(21)
    d1 = m.evaluate([Event(TriggerEvent.HEARTBEAT)], w)
    assert d1.wake
    # identical world next heartbeat -> skip
    clk.advance(21)
    d2 = m.evaluate([Event(TriggerEvent.HEARTBEAT)], w)
    assert not d2.wake and d2.reason == "heartbeat:unchanged"


def test_heartbeat_fires_on_change():
    clk = Clock()
    m = TriggerManager(cfg(), clock=clk)
    clk.advance(21)
    m.evaluate([Event(TriggerEvent.HEARTBEAT)], _world(fg_title="a"))
    clk.advance(21)
    d = m.evaluate([Event(TriggerEvent.HEARTBEAT)], _world(fg_title="b"))
    assert d.wake and d.reason == "heartbeat:changed"


def test_change_event_debounced():
    clk = Clock()
    m = TriggerManager(cfg(), clock=clk)
    # fire once via foreground change
    d1 = m.evaluate([Event(TriggerEvent.FOREGROUND_CHANGED)], _world(fg_title="a"))
    assert d1.wake
    # immediately again -> debounced (< min_interval)
    clk.advance(1)
    d2 = m.evaluate([Event(TriggerEvent.FOREGROUND_CHANGED)], _world(fg_title="b"))
    assert not d2.wake and d2.reason == "debounced"
    # after min interval -> fires
    clk.advance(5)
    d3 = m.evaluate([Event(TriggerEvent.FOREGROUND_CHANGED)], _world(fg_title="c"))
    assert d3.wake


def test_fingerprint_changes_with_cursor_bucket():
    a = fingerprint(_world(cursor=(0, 0)))
    b = fingerprint(_world(cursor=(500, 500)))
    assert a != b
    c = fingerprint(_world(cursor=(1, 1)))  # same 64px bucket
    assert a == c


def test_event_bus_drain():
    bus = EventBus()
    bus.emit(TriggerEvent.HEARTBEAT)
    bus.emit(TriggerEvent.CLIPBOARD_CHANGED, "hash")
    drained = bus.drain()
    assert [e.kind for e in drained] == [TriggerEvent.HEARTBEAT, TriggerEvent.CLIPBOARD_CHANGED]
    assert bus.drain() == []
