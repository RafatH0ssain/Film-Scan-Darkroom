"""Entry point for the packaged app.

A bundle needs one script to start from, and it must be
``fsd.app.server.main`` that runs so the packaged app behaves exactly like
``film-scan-darkroom`` from source.
"""

from __future__ import annotations

import sys


def run() -> int:
    from fsd.app.server import main

    return main()


if __name__ == "__main__":
    sys.exit(run())
