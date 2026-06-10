"""OpenAI provider (gpt-4o-style multimodal) via raw httpx.

Structured output through `response_format=json_schema`. Images as a base64
data-uri content part. The custom-base-url variant subclasses this.
"""

from __future__ import annotations

import base64
from typing import Optional

import httpx

from ...log import get
from .base import LLMProvider, ProviderAuthError, ProviderUnreachable

log = get("provider.openai")


def _data_uri(image: bytes, fmt: str = "png") -> str:
    b64 = base64.b64encode(image).decode("ascii")
    return f"data:image/{fmt};base64,{b64}"


class OpenAIProvider(LLMProvider):
    name = "openai"
    default_base = "https://api.openai.com"

    def _base(self) -> str:
        return (self.cfg.base_url or self.default_base).rstrip("/")

    def _client(self) -> httpx.Client:
        headers = {"Authorization": f"Bearer {self.cfg.api_key}"}
        return httpx.Client(base_url=self._base(), timeout=self.cfg.timeout_s, headers=headers)

    def _response_format(self, schema: dict) -> dict:
        return {
            "type": "json_schema",
            "json_schema": {"name": "intent", "schema": schema, "strict": False},
        }

    def _raw_complete(self, system, user_text, image: Optional[bytes], schema: dict):
        content: list[dict] = [{"type": "text", "text": user_text}]
        if image:
            content.append({"type": "image_url", "image_url": {"url": _data_uri(image)}})
        body = {
            "model": self.cfg.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": content},
            ],
            "temperature": self.cfg.temperature,
            "response_format": self._response_format(schema),
        }
        return self._post(body)

    def _post(self, body: dict) -> str:
        try:
            with self._client() as c:
                r = c.post("/v1/chat/completions", json=body)
                if r.status_code in (401, 403):
                    raise ProviderAuthError(f"{self.name}: auth failed ({r.status_code})")
                r.raise_for_status()
        except httpx.ConnectError as e:
            raise ProviderUnreachable(f"can't reach {self.name} at {self._base()}") from e
        data = r.json()
        return data["choices"][0]["message"]["content"]

    def health(self) -> bool:
        try:
            with self._client() as c:
                r = c.get("/v1/models")
                return r.status_code < 500
        except httpx.HTTPError:
            return False
