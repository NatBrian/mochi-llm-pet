"""BrainAgent — one perceive->think->act decision.

Builds the prompt packet, calls the provider, coerces to an Intent, applies the
`remember` field to memory. Falls back to the rule-based brain on any provider /
parse failure so a decision is ALWAYS produced.
"""

from __future__ import annotations

from typing import Optional

from ..config import Config
from ..log import friendly, get
from ..types import Intent, WorldSnapshot
from . import fallback
from .context_builder import build
from .parse import coerce_intent
from .providers import ProviderError, make_provider
from .screenshot import capture

log = get("brain.agent")


class BrainAgent:
    def __init__(self, cfg: Config, memory_store=None):
        self.cfg = cfg
        self.memory = memory_store
        self.degraded = False
        self.last_source = "llm"   # "llm" | "rule" — set per decide(), for logging
        try:
            self.provider = make_provider(cfg.llm)
        except ProviderError as e:
            log.warning("provider init failed: %s", e)
            self.provider = None
            self.degraded = True

    def check_health(self) -> bool:
        if not self.provider:
            return False
        try:
            ok = self.provider.health()
        except Exception:  # noqa: BLE001
            ok = False
        if not ok:
            friendly(
                f"Can't reach the brain ({self.cfg.llm.provider} @ "
                f"{self.cfg.llm.base_url or 'default'}). Running on instinct "
                f"(rule-based) until it's back."
            )
            self.degraded = True
        else:
            log.info("brain online: provider=%s model=%s base_url=%s vision=%s",
                     self.cfg.llm.provider, self.cfg.llm.model,
                     self.cfg.llm.base_url or "default",
                     "on" if self.cfg.vision.enabled else "off")
        return ok

    def decide(self, world: WorldSnapshot, *, retrieve_k: int = 5) -> Intent:
        if self.degraded or not self.provider:
            self.last_source = "rule"
            return fallback.rule_based(world)

        memories = []
        if self.memory is not None:
            try:
                from ..memory.retrieval import retrieve

                memories = retrieve(self.memory, world, k=retrieve_k)
                if memories:
                    log.debug("recalled %d memories: %s", len(memories),
                              " | ".join(m.text[:60] for m in memories))
            except Exception as e:  # noqa: BLE001
                log.debug("memory retrieval failed: %s", e)

        image = capture(self.cfg.vision, world)
        packet = build(world, memories, image, persona=self.cfg.persona.name)
        log.debug("vision: %s", f"screenshot {len(image)} bytes" if image
                  else "text-only (no image)")
        log.debug("scene the LLM sees:\n%s", packet.user_text)

        try:
            data = self.provider.complete(
                packet.system, packet.user_text, packet.image, packet.schema
            )
            log.debug("raw LLM response: %s", data)
            intent = coerce_intent(data)
            self.last_source = "llm"
        except ProviderError as e:
            log.warning("provider call failed (%s); using rule-based instinct", e)
            self.last_source = "rule"
            return fallback.rule_based(world)
        except Exception as e:  # noqa: BLE001
            log.warning("unexpected brain error (%s); using rule-based instinct", e)
            self.last_source = "rule"
            return fallback.rule_based(world)

        if intent.remember and self.memory is not None:
            try:
                self.memory.add(intent.remember, kind="memory", salience=0.7)
                log.info("📝 remembered: %s", intent.remember)
            except Exception:  # noqa: BLE001
                pass
        return intent


# --------------------------------------------------------------------------- #
# CLI dry-run: build a fake world and print one decision. Lets us validate the
# default Ollama path on the Linux dev box without any GUI.
# --------------------------------------------------------------------------- #
def main(argv: Optional[list[str]] = None) -> int:
    import argparse

    from .. import log as _log
    from ..types import Rect, Vec2, WindowInfo

    _log.setup()
    ap = argparse.ArgumentParser(description="DeskPet brain dry-run")
    ap.add_argument("--once", action="store_true", help="make one decision and exit")
    ap.add_argument("--no-vision", action="store_true", help="force text-only")
    ap.add_argument("--say", default=None, help="simulate the user talking to the pet")
    args = ap.parse_args(argv)

    cfg = Config.load()
    if args.no_vision:
        cfg.vision.enabled = False

    chrome = WindowInfo(1, "chrome.exe", 100, "lofi hip hop radio - YouTube",
                        Rect(960, 0, 1920, 1040), 0, False, "youtube")
    code = WindowInfo(2, "Code.exe", 200, "agent.py - deskpet",
                      Rect(0, 0, 960, 1040), 1, True, "code")
    world = WorldSnapshot(
        cursor=Vec2(400, 300), foreground=code, windows=(code, chrome),
        monitors=(Rect(0, 0, 1920, 1080),), pet_pos=Vec2(800, 1000),
        user_said=args.say,
    )

    agent = BrainAgent(cfg)
    agent.check_health()
    intent = agent.decide(world)
    print("\n--- DECISION ---")
    print(f"verb:      {intent.verb.value}")
    print(f"target:    {intent.target}")
    print(f"emotion:   {intent.emotion.value}")
    print(f"say:       {intent.say}")
    print(f"thought:   {intent.thought}")
    print(f"remember:  {intent.remember}")
    print(f"confidence:{intent.confidence}")
    print(f"(degraded/rule-based: {agent.degraded})")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
