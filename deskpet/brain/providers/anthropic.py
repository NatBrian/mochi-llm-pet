"""Anthropic Claude provider via the official SDK.

JSON via forced tool-use: we define one tool `emit_intent` whose input_schema is
the Intent schema and force `tool_choice` to it. The model's `tool_use.input`
arrives as a dict — the single most reliable path of the five (no text parsing).
"""

from __future__ import annotations

import base64
from typing import Optional

from ...log import get
from ..schema import anthropic_tool
from .base import LLMProvider, ProviderAuthError, ProviderUnreachable

log = get("provider.anthropic")


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, cfg):
        super().__init__(cfg)
        self._sdk = None

    def _client(self):
        if self._sdk is None:
            import anthropic  # lazy import; only needed for this provider

            kwargs = {"api_key": self.cfg.api_key, "timeout": self.cfg.timeout_s}
            if self.cfg.base_url and "anthropic.com" not in self.cfg.base_url:
                kwargs["base_url"] = self.cfg.base_url
            self._sdk = anthropic.Anthropic(**kwargs)
        return self._sdk

    def _raw_complete(self, system, user_text, image: Optional[bytes], schema: dict):
        import anthropic

        content: list[dict] = [{"type": "text", "text": user_text}]
        if image:
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": base64.b64encode(image).decode("ascii"),
                },
            })
        tool = anthropic_tool()
        try:
            msg = self._client().messages.create(
                model=self.cfg.model,
                max_tokens=1024,
                temperature=self.cfg.temperature,
                system=system,
                tools=[tool],
                tool_choice={"type": "tool", "name": tool["name"]},
                messages=[{"role": "user", "content": content}],
            )
        except anthropic.AuthenticationError as e:
            raise ProviderAuthError(f"anthropic: {e}") from e
        except anthropic.APIConnectionError as e:
            raise ProviderUnreachable(f"anthropic unreachable: {e}") from e

        for block in msg.content:
            if getattr(block, "type", None) == "tool_use":
                return dict(block.input)  # already a dict — no parsing needed
        # fallback: maybe it answered in text
        for block in msg.content:
            if getattr(block, "type", None) == "text":
                return block.text
        return ""

    def health(self) -> bool:
        # SDK has no cheap ping; trust construction. Real failures surface on call.
        return bool(self.cfg.api_key)
