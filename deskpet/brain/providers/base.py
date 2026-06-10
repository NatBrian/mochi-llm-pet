"""LLMProvider base class.

`complete()` is concrete and shared: it runs `_raw_complete` with retry, then
parses the result into an Intent dict, then does ONE repair round-trip on parse
failure before giving up. Adapters implement only `_raw_complete` + `health`.
"""

from __future__ import annotations

import abc
from typing import Optional

from tenacity import retry, stop_after_attempt, wait_exponential

from ...config import LLMConfig
from ...log import get
from ..parse import IntentParseError, coerce_intent, extract_json

log = get("provider")


class ProviderError(RuntimeError):
    pass


class ProviderUnreachable(ProviderError):
    pass


class ProviderAuthError(ProviderError):
    pass


class ProviderParseError(ProviderError):
    pass


class LLMProvider(abc.ABC):
    name: str = "base"

    def __init__(self, cfg: LLMConfig):
        self.cfg = cfg

    # ---- adapters implement these ----------------------------------------- #
    @abc.abstractmethod
    def _raw_complete(
        self, system: str, user_text: str, image: Optional[bytes], schema: dict
    ) -> str | dict:
        """Return raw model text, OR a dict if the backend hands back structured
        output directly (e.g. Anthropic tool-use)."""

    @abc.abstractmethod
    def health(self) -> bool:
        """Cheap reachability probe."""

    # ---- shared, concrete -------------------------------------------------- #
    def complete(
        self, system: str, user_text: str, image: Optional[bytes], schema: dict
    ) -> dict:
        """Return a raw intent dict (already JSON-parsed, not yet coerced to enums
        — the caller coerces via parse.coerce_intent / safe_parse)."""

        @retry(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=0.5, max=4),
            reraise=True,
        )
        def _call(sys_text: str, usr_text: str) -> str | dict:
            return self._raw_complete(sys_text, usr_text, image, schema)

        raw = _call(system, user_text)
        if isinstance(raw, dict):
            return raw
        try:
            return extract_json(raw)
        except IntentParseError:
            log.warning("%s returned unparseable JSON; attempting one repair", self.name)
            repair = (
                user_text
                + "\n\nYour previous reply was not valid JSON. Reply with ONLY the JSON "
                "object, no prose, no code fences."
            )
            raw2 = _call(system, repair)
            if isinstance(raw2, dict):
                return raw2
            try:
                return extract_json(raw2)
            except IntentParseError as e:
                raise ProviderParseError(str(e)) from e

    # convenience
    def decide(self, system: str, user_text: str, image: Optional[bytes], schema: dict):
        from ...types import Intent  # local import to avoid cycle

        data = self.complete(system, user_text, image, schema)
        return coerce_intent(data)
