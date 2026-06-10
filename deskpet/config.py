"""Configuration: TOML file + environment overrides (env always wins).

With no config file and no env vars set, the defaults below produce a working
local setup: Ollama `gemma4:12b`, vision on, keyword memory, rule-based fallback.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

try:  # py3.11 stdlib
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore


class LLMConfig(BaseModel):
    provider: str = "ollama"              # ollama|openai|openai_compat|anthropic|gemini
    model: str = "gemma4:12b"
    # Empty = each provider uses its own default (Ollama -> 127.0.0.1:11434,
    # OpenAI/Gemini -> their public API). Set explicitly to point elsewhere.
    base_url: str = ""
    api_key: str = ""
    temperature: float = 0.7
    timeout_s: float = 90.0
    think: bool = False                   # gemma: suppress thinking tokens
    # Constrained JSON-grammar decoding (most reliable, but the first call compiles
    # the grammar and is slower). Turn off to lean on the robust parser instead and
    # cut latency. Applies to Ollama / OpenAI-compatible backends.
    structured: bool = True


class VisionConfig(BaseModel):
    enabled: bool = True
    mode: str = "monitor"                 # monitor=whole desktop | active_window=focused app only
    max_edge: int = 1024
    format: str = "png"                   # png|jpeg


class MemoryConfig(BaseModel):
    db_path: str = "deskpet.db"
    top_k: int = 5
    embeddings: bool = False
    embed_model: str = "nomic-embed-text"
    max_rows: int = 5000


class TriggersConfig(BaseModel):
    heartbeat_s: float = 20.0
    min_interval_s: float = 4.0
    idle_threshold_s: float = 120.0


class PerceptionConfig(BaseModel):
    fast_ms: int = 200
    medium_ms: int = 1000
    slow_ms: int = 5000
    caret: bool = False


class PersonaConfig(BaseModel):
    name: str = "mochi"


class RenderConfig(BaseModel):
    fps: int = 60
    scale: int = 3                        # integer upscale of 32px sprite
    assets_dir: str = "assets"


class Config(BaseModel):
    llm: LLMConfig = Field(default_factory=LLMConfig)
    vision: VisionConfig = Field(default_factory=VisionConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    triggers: TriggersConfig = Field(default_factory=TriggersConfig)
    perception: PerceptionConfig = Field(default_factory=PerceptionConfig)
    persona: PersonaConfig = Field(default_factory=PersonaConfig)
    render: RenderConfig = Field(default_factory=RenderConfig)

    # ---- loading ----------------------------------------------------------- #
    @classmethod
    def load(cls, path: Optional[str | Path] = None) -> "Config":
        data: dict = {}
        cfg_path = _resolve_path(path)
        if cfg_path and cfg_path.exists():
            with open(cfg_path, "rb") as f:
                data = tomllib.load(f)
        cfg = cls.model_validate(data)
        _apply_env(cfg)
        return cfg


def _resolve_path(path: Optional[str | Path]) -> Optional[Path]:
    if path:
        return Path(path)
    env = os.environ.get("DESKPET_CONFIG")
    if env:
        return Path(env)
    # look next to the project root and CWD
    for cand in (Path("config.toml"), Path(__file__).resolve().parent.parent / "config.toml"):
        if cand.exists():
            return cand
    return None


def _apply_env(cfg: Config) -> None:
    """Env overrides. DESKPET_* take precedence; well-known provider keys fill in."""
    g = os.environ.get
    if v := g("DESKPET_LLM_PROVIDER"):
        cfg.llm.provider = v
    if v := g("DESKPET_LLM_MODEL"):
        cfg.llm.model = v
    if v := g("DESKPET_LLM_BASE_URL"):
        cfg.llm.base_url = v
    if v := g("DESKPET_LLM_API_KEY"):
        cfg.llm.api_key = v
    if v := g("DESKPET_PERSONA"):
        cfg.persona.name = v

    # Auto-pick a well-known key for the selected cloud provider if none set.
    if not cfg.llm.api_key:
        provider_key = {
            "openai": "OPENAI_API_KEY",
            "openai_compat": "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
            "gemini": "GEMINI_API_KEY",
        }.get(cfg.llm.provider)
        if provider_key and (v := g(provider_key)):
            cfg.llm.api_key = v
