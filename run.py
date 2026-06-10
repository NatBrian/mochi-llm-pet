#!/usr/bin/env python3
"""DeskPet launcher — `python run.py`.

Runs the full desktop pet (Windows). On a non-Windows box it still launches but
window sensing is mocked; intended target is Windows.
"""

from __future__ import annotations

import sys


def main() -> int:
    from deskpet.app import Application

    config = None
    if "--config" in sys.argv:
        i = sys.argv.index("--config")
        if i + 1 < len(sys.argv):
            config = sys.argv[i + 1]
    return Application(config_path=config).main()


if __name__ == "__main__":
    raise SystemExit(main())
