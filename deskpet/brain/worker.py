"""BrainWorker — runs the slow LLM decision loop on its own QThread so the 60fps
render loop never blocks. Emits `intentReady` (a Qt queued signal) back to the
main thread, where the body swaps the intent atomically.

Single-flight by construction (one thread = serial decisions). A monotonic `gen`
counter tags each intent so a stale one returning after a reflex can be dropped.
"""

from __future__ import annotations

import time

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from ..config import Config
from ..log import get
from ..triggers.events import EventBus
from ..triggers.manager import TriggerManager
from ..types import Intent
from .agent import BrainAgent

log = get("brain.worker")


class BrainWorker(QObject):
    intentReady = pyqtSignal(object)   # emits Intent

    def __init__(self, cfg: Config, agent: BrainAgent, world, bus: EventBus):
        super().__init__()
        self.cfg = cfg
        self.agent = agent
        self.world = world
        self.bus = bus
        self.manager = TriggerManager(cfg.triggers, clock=time.monotonic)
        self._stop = False
        self._gen = 0
        self.poll_s = 0.4

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        log.debug("brain worker started")
        # Seed an INSTANT rule-based intent so the pet is lively from frame one,
        # instead of idling while the (possibly slow) first LLM call runs.
        from . import fallback

        seed = fallback.rule_based(self.world.read())
        self._gen += 1
        self.intentReady.emit(seed.with_gen(self._gen))
        # now make the first real LLM decision
        self._decide("startup")
        while not self._stop:
            time.sleep(self.poll_s)
            if self._stop:
                break
            events = self.bus.drain()
            snap = self.world.read()
            decision = self.manager.evaluate(events, snap)
            if decision.wake:
                self._decide(decision.reason)

    def _decide(self, reason: str) -> None:
        try:
            snap = self.world.read()
            intent = self.agent.decide(snap)
        except Exception as e:  # noqa: BLE001 — the brain must never kill the app
            log.warning("decision failed (%s)", e)
            return
        self._gen += 1
        intent = intent.with_gen(self._gen)
        log.info("[%s] %s target=%s emotion=%s", reason, intent.verb.value,
                 intent.target, intent.emotion.value)
        self.intentReady.emit(intent)


def start_worker(cfg: Config, agent: BrainAgent, world, bus: EventBus):
    """Create the worker + thread, wire run/stop, return (thread, worker)."""
    thread = QThread()
    worker = BrainWorker(cfg, agent, world, bus)
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    thread.finished.connect(worker.deleteLater)
    return thread, worker
