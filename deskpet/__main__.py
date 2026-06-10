"""`python -m deskpet` entry point."""

from __future__ import annotations

import sys


def main() -> int:
    from .app import Application

    config = None
    args = sys.argv[1:]
    if "--config" in args:
        i = args.index("--config")
        if i + 1 < len(args):
            config = args[i + 1]
    return Application(config_path=config).main()


if __name__ == "__main__":
    raise SystemExit(main())
