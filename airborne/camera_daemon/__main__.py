"""Entry point: ``python -m camera_daemon``."""
from __future__ import annotations

import sys

from .service import main

if __name__ == "__main__":
    sys.exit(main())
