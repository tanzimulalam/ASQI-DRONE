"""Entry point: ``python -m airborne_daemon``."""
from __future__ import annotations

import sys

from .daemon import main

if __name__ == "__main__":
    sys.exit(main())
