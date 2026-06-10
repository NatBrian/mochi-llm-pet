"""Provider factory."""

from __future__ import annotations

from ...config import LLMConfig
from .base import (
    LLMProvider,
    ProviderAuthError,
    ProviderError,
    ProviderParseError,
    ProviderUnreachable,
)

_REGISTRY = {
    "ollama": "deskpet.brain.providers.ollama:OllamaProvider",
    "openai": "deskpet.brain.providers.openai:OpenAIProvider",
    "openai_compat": "deskpet.brain.providers.openai_compatible:CustomOpenAIProvider",
    "anthropic": "deskpet.brain.providers.anthropic:AnthropicProvider",
    "gemini": "deskpet.brain.providers.gemini:GeminiProvider",
}

_CLOUD = {"openai", "anthropic", "gemini"}


def make_provider(cfg: LLMConfig) -> LLMProvider:
    key = cfg.provider.lower()
    if key not in _REGISTRY:
        raise ProviderError(
            f"unknown provider '{cfg.provider}'. choose from {sorted(_REGISTRY)}"
        )
    if key in _CLOUD and not cfg.api_key:
        raise ProviderAuthError(
            f"provider '{key}' needs an API key (set DESKPET_LLM_API_KEY or the "
            f"provider's standard env var)"
        )
    if key == "openai_compat" and not cfg.base_url:
        raise ProviderError("openai_compat requires base_url")

    module_path, cls_name = _REGISTRY[key].split(":")
    import importlib

    cls = getattr(importlib.import_module(module_path), cls_name)
    return cls(cfg)


__all__ = [
    "make_provider",
    "LLMProvider",
    "ProviderError",
    "ProviderUnreachable",
    "ProviderAuthError",
    "ProviderParseError",
]
