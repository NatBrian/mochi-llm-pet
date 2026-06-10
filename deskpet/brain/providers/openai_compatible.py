"""OpenAI-compatible custom endpoint (self-host / proxy / vLLM / LM Studio).

Same wire format as OpenAI but the server may not support `json_schema`. We probe
capability once and degrade: json_schema -> json_object -> prompt-enforced JSON.
"""

from __future__ import annotations

from typing import Optional

import httpx

from ...log import get
from ..schema import summary_for_prompt
from .base import ProviderUnreachable
from .openai import OpenAIProvider

log = get("provider.openai_compat")


class CustomOpenAIProvider(OpenAIProvider):
    name = "openai_compat"

    def __init__(self, cfg):
        super().__init__(cfg)
        # None = unknown; one of "json_schema" | "json_object" | "prompt"
        self._mode: Optional[str] = None

    def _base(self) -> str:
        # custom requires an explicit base_url
        return self.cfg.base_url.rstrip("/")

    def _raw_complete(self, system, user_text, image, schema: dict):
        content: list[dict] = [{"type": "text", "text": user_text}]
        if image:
            from .openai import _data_uri

            content.append({"type": "image_url", "image_url": {"url": _data_uri(image)}})

        msgs = [{"role": "system", "content": system},
                {"role": "user", "content": content}]

        for mode in self._mode_order():
            body = {"model": self.cfg.model, "messages": list(msgs),
                    "temperature": self.cfg.temperature}
            if mode == "json_schema":
                body["response_format"] = self._response_format(schema)
            elif mode == "json_object":
                body["response_format"] = {"type": "json_object"}
            elif mode == "prompt":
                body["messages"][0]["content"] = system + "\n\n" + summary_for_prompt()
            try:
                out = self._post(body)
                self._mode = mode  # remember what worked
                return out
            except httpx.HTTPStatusError as e:
                if e.response is not None and e.response.status_code == 400 and self._mode is None:
                    log.info("custom endpoint rejected %s mode; falling back", mode)
                    continue
                raise
        raise ProviderUnreachable("custom endpoint rejected all JSON modes")

    def _mode_order(self) -> list[str]:
        if self._mode:
            return [self._mode]
        return ["json_schema", "json_object", "prompt"]
