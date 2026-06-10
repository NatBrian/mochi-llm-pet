"""Application orchestrator — wires the three clocks and degrades gracefully.

  sensors (main-thread timer)  ->  WorldState
  brain   (worker QThread)     ->  Intent  --queued signal-->  body
  body+render (60fps timer)    ->  motion + animation + window

Guarantees the pet "just runs": placeholder art if none present, rule-based
behaviour if the LLM is unreachable, and a friendly message either way.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from .config import Config
from .log import friendly, get, setup
from .perception.dpi import set_dpi_awareness

log = get("app")


class Application:
    def __init__(self, config_path: str | None = None):
        self.cfg = Config.load(config_path)
        self._intent_gen = 0

    def main(self) -> int:
        setup()
        # DPI awareness MUST be set before QApplication is constructed.
        set_dpi_awareness()

        from PyQt6.QtCore import Qt, QTimer
        from PyQt6.QtGui import QGuiApplication
        from PyQt6.QtWidgets import QApplication

        QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
        qapp = QApplication(sys.argv)
        qapp.setQuitOnLastWindowClosed(False)

        # --- subsystems ---------------------------------------------------- #
        from .memory.store import MemoryStore
        from .memory.pet_state import PetStateManager
        from .brain.agent import BrainAgent
        from .perception.world import WorldState
        from .perception.sensor_loop import Sensors
        from .triggers.events import EventBus, TriggerEvent
        from .sprite import registry
        from .sprite.player import AnimationPlayer
        from .body.body import Body
        from .ui.pet_window import PetWindow
        from .brain.worker import start_worker
        from .types import Vec2

        store = MemoryStore(self.cfg.memory.db_path)
        self.petmgr = PetStateManager(store)
        # memory/persistence bookkeeping
        self._last_save = 0.0
        self._noted_apps: set[str] = set()
        self._last_level = self.petmgr.state.level
        agent = BrainAgent(self.cfg, memory_store=store)
        agent.check_health()

        self.world = WorldState()
        self.bus = EventBus()
        self.sensors = Sensors(self.cfg.perception, self.world, self.bus,
                               idle_threshold_s=self.cfg.triggers.idle_threshold_s)
        # prime the world once so the first frame has data
        self.sensors.set_pet(Vec2(300.0, 300.0), self.petmgr.state)
        self.sensors._rebuild()
        snap0 = self.world.read()
        start = snap0.monitors[0].center if snap0.monitors else Vec2(400.0, 400.0)

        assets_dir = Path(self.cfg.render.assets_dir)
        clips = registry.build(assets_dir)
        self.player = AnimationPlayer(clips)
        self.body = Body(start_pos=Vec2(start.x, start.y - 150))
        self.body.attach_player(self.player)
        self.body.on_action_done = lambda: self.bus.emit(TriggerEvent.BODY_ACTION_DONE)

        self.window = PetWindow(self.body, self.player, scale=self.cfg.render.scale)
        self.window.show()

        # --- brain thread -------------------------------------------------- #
        self.thread, self.worker = start_worker(self.cfg, agent, self.world, self.bus)
        self.worker.intentReady.connect(self._on_intent)
        self.thread.start()

        # --- clocks -------------------------------------------------------- #
        self._last = time.monotonic()
        self._frame_timer = QTimer()
        self._frame_timer.timeout.connect(self._frame)
        self._frame_timer.start(int(1000 / max(1, self.cfg.render.fps)))

        self._state_timer = QTimer()
        self._state_timer.timeout.connect(self._tick_state)
        self._state_timer.start(1000)

        qapp.aboutToQuit.connect(self._shutdown)
        friendly(f"DeskPet is alive! (brain: {self.cfg.llm.provider}"
                 f"{' — degraded/rule-based' if agent.degraded else ''})")
        return qapp.exec()

    # ---- per-frame (60fps) ------------------------------------------------- #
    def _frame(self) -> None:
        now = time.monotonic()
        dt = min(0.1, now - self._last)
        self._last = now
        self.sensors.set_pet(self.body.motion.pos, self.petmgr.state)
        self.sensors.tick(now)
        snap = self.world.read()
        self.body.step(dt, snap)
        self.player.update(dt)
        self.window.tick(snap.cursor)

    # ---- per-second pet needs --------------------------------------------- #
    def _tick_state(self) -> None:
        from .triggers.events import TriggerEvent

        now = time.time()
        snap = self.world.read()
        fg = snap.foreground.process if snap.foreground else None
        napping = self.body.anim_state == "sleep"
        state = self.petmgr.tick(napping=napping, foreground_app=fg)
        if state.energy < 0.15 and not napping:
            self.bus.emit(TriggerEvent.PET_NEED_CRITICAL, state.energy)

        # --- automatic memory formation (so the pet genuinely remembers) ---- #
        if state.level > self._last_level:
            self._last_level = state.level
            self._remember(f"leveled up to {state.level} — getting more attached "
                           f"to the human", salience=0.8)
        if fg and state.time_in_app_s > 300 and fg not in self._noted_apps:
            self._noted_apps.add(fg)
            app = fg[:-4] if fg.lower().endswith(".exe") else fg
            self._remember(f"the human spends a lot of time in {app}",
                           kind="habit", salience=0.6)

        # --- persist pet state periodically (survives a kill, not just a clean
        #     exit) so deskpet.db reflects the live personality drift ---------- #
        if now - self._last_save > 20.0:
            self._last_save = now
            try:
                self.petmgr.save()
            except Exception:  # noqa: BLE001
                pass

    def _remember(self, text: str, *, kind: str = "memory", salience: float = 0.6) -> None:
        try:
            self.petmgr.store.add(text, kind=kind, salience=salience)
            log.info("📝 remembered: %s", text)
        except Exception:  # noqa: BLE001
            pass

    # ---- new intent from the brain (main thread) --------------------------- #
    def _on_intent(self, intent) -> None:
        from .types import Emotion

        self.body.set_intent(intent)
        if intent.say:
            self.window.say(intent.say)
        if intent.emotion in (Emotion.HAPPY, Emotion.AFFECTIONATE, Emotion.EXCITED):
            self.petmgr.reward(mood=0.05, bond=0.01, xp=2)

    # ---- shutdown ---------------------------------------------------------- #
    def _shutdown(self) -> None:
        try:
            self.worker.stop()
            self.thread.quit()
            self.thread.wait(1500)
        except Exception:  # noqa: BLE001
            pass
        try:
            self.petmgr.save()
            self.petmgr.store.prune(self.cfg.memory.max_rows)
        except Exception:  # noqa: BLE001
            pass
        log.debug("shutdown complete")


def main() -> int:
    return Application().main()
