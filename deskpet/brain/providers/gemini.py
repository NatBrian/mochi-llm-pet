"""Google Gemini provider via raw httpx (avoids the heavy SDK).

Structured output via generationConfig.responseSchema + responseMimeType. Images
as inlineData base64 parts.
"""

from __future__ import annotations

import base64
import json
from typing import Optional

import httpx

from ...log import get
from ..schema import gemini_schema
from .base import LLMProvider, ProviderAuthError, ProviderUnreachable

log = get("provider.gemini")

_DEFAULT_BASE = "https://generativelanguage.googleapis.com"


class GeminiProvider(LLMProvider):
    name = "gemini"

    def _base(self) -> str:
        return (self.cfg.base_url or _DEFAULT_BASE).rstrip("/")

    def _raw_complete(self, system, user_text, image: Optional[bytes], schema: dict):
        parts: list[dict] = [{"text": user_text}]
        if image:
            parts.append({
                "inlineData": {
                    "mimeType": "image/png",
                    "data": base64.b64encode(image).decode("ascii"),
                }
            })
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "temperature": self.cfg.temperature,
                "responseMimeType": "application/json",
                "responseSchema": gemini_schema(),
            },
        }
        url = f"/v1beta/models/{self.cfg.model}:generateContent"
        try:
            with httpx.Client(base_url=self._base(), timeout=self.cfg.timeout_s) as c:
                r = c.post(url, params={"key": self.cfg.api_key}, json=body)
                if r.status_code in (401, 403):
                    raise ProviderAuthError(f"gemini: auth failed ({r.status_code})")
                r.raise_for_status()
        except httpx.ConnectError as e:
            raise ProviderUnreachable(f"can't reach Gemini at {self._base()}") from e
        data = r.json()
        try:
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError):
            return json.dumps(data)  # let the parser try; usually a safety block

    def health(self) -> bool:
        try:
            with httpx.Client(base_url=self._base(), timeout=10.0) as c:
                r = c.get("/v1beta/models", params={"key": self.cfg.api_key})
                return r.status_code < 500
        except httpx.HTTPError:
            return False
