"""Speech guard — a structural backstop that keeps the pet from sounding like an
AI assistant, independent of the prompt.

The persona does the heavy lifting, but LLMs (especially small local ones) drift
back into their trained "helpful assistant" register: offering help, asking what
you'd like them to do, claiming capabilities. Those lines break the illusion of a
pet more than anything else. We can't perfectly detect screen-narration, but the
*service register* has reliable, low-false-positive tells — and a cat would never
say them. When we catch one, we drop the line (silence reads as "cat", an
assistant sentence does not).
"""

from __future__ import annotations

import re

# Offer-of-service / capability / assistant tells. Kept deliberately narrow so we
# don't muzzle legitimately cat-like speech ("feed me", "pet me", demands, etc.).
_ASSISTANT_TELLS = [
    r"\bwant me to\b",
    r"\bwhat (?:do|can|would|should) (?:you|i) (?:want|like|need)\b",
    r"\bwhat can i do for\b",
    r"\bwhat (?:would|can) (?:you like|i do)\b",
    r"\bhow (?:can|may) i (?:help|assist)\b",
    r"\b(?:need|want) (?:a hand|some help|help|assistance)\b",
    r"\blet me know (?:if|what|when|how)\b",
    r"\bi can (?:do (?:anything|that|it)|help|assist)\b",
    r"\bi(?:'?m| am) here to (?:help|assist)\b",
    r"\b(?:happy|glad) to help\b",
    r"\bat your service\b",
    r"\bhow may i\b",
    r"\bwould you like me to\b",
    r"\banything (?:else )?i can (?:do|help)\b",
    r"\bdo you (?:want|need) (?:me|any help)\b",
]

_ASSISTANT_RE = re.compile("|".join(_ASSISTANT_TELLS), re.IGNORECASE)


def sounds_like_assistant(text: str | None) -> bool:
    """True if the line reads as an AI assistant offering service / claiming
    abilities — things a cat would never say."""
    if not text:
        return False
    return bool(_ASSISTANT_RE.search(text))
