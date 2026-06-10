"""Ollama provider (DEFAULT). Local, free, private — multimodal via `gemma4:12b`.

Uses /api/chat with `format` set to our JSON schema for structured output, and
`think: false` to suppress Gemma's reasoning tokens. Images go in the message's
`images` array as raw base64 (no data-uri prefix, per Ollama's API).
"""

from __future__ import annotations

import base64
from typing import Optional

import httpx

from ...log import get
from .base import LLMProvider, ProviderUnreachable

log = get("provider.ollama")


class OllamaProvider(LLMProvider):
    name = "ollama"

    DEFAULT_BASE = "http://127.0.0.1:11434"

    def _base(self) -> str:
        return (self.cfg.base_url or self.DEFAULT_BASE).rstrip("/")

    def _client(self) -> httpx.Client:
        return httpx.Client(base_url=self._base(), timeout=self.cfg.timeout_s)

    def _raw_complete(self, system, user_text, image: Optional[bytes], schema: dict):
        user_msg: dict = {"role": "user", "content": user_text}
        if image:
            user_msg["images"] = [base64.b64encode(image).decode("ascii")]
        if not self.cfg.structured:
            from ..schema import summary_for_prompt

            system = system + "\n\n" + summary_for_prompt()
        body = {
            "model": self.cfg.model,
            "messages": [{"role": "system", "content": system}, user_msg],
            "stream": False,
            "think": self.cfg.think,
            "options": {"temperature": self.cfg.temperature},
        }
        if self.cfg.structured:
            body["format"] = schema  # constrained JSON-grammar decoding
        try:
            with self._client() as c:
                r = c.post("/api/chat", json=body)
                r.raise_for_status()
        except httpx.ConnectError as e:
            raise ProviderUnreachable(
                f"can't reach Ollama at {self._base()} — is it running and bound to 0.0.0.0?"
            ) from e
        data = r.json()
        return data.get("message", {}).get("content", "")

    def health(self) -> bool:
        try:
            with self._client() as c:
                r = c.get("/api/tags")
                return r.status_code == 200
        except httpx.HTTPError:
            return False
