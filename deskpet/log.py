"""Logging setup + friendly user-facing messages.

Keep `thought` and any sensitive context at DEBUG so normal runs don't print
private reasoning. Friendly helpers print short, human lines to the console.
"""

from __future__ import annotations

import logging
import os
import sys

_CONFIGURED = False


def setup(level: str | int | None = None) -> logging.Logger:
    global _CONFIGURED
    if level is None:
        level = os.environ.get("DESKPET_LOG_LEVEL", "INFO")
    if isinstance(level, str):
        level = getattr(logging, level.upper(), logging.INFO)
    if not _CONFIGURED:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s",
                              datefmt="%H:%M:%S")
        )
        root = logging.getLogger("deskpet")
        root.handlers.clear()
        root.addHandler(handler)
        root.setLevel(level)
        root.propagate = False
        _CONFIGURED = True
    return logging.getLogger("deskpet")


def get(name: str) -> logging.Logger:
    return logging.getLogger(f"deskpet.{name}")


# ---- friendly one-liners for the user (always shown) ----------------------- #
def friendly(msg: str) -> None:
    """A short, plain message to the user on stderr, regardless of log level."""
    print(f"🐾 {msg}", file=sys.stderr, flush=True)
