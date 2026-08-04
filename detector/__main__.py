"""``python -m detector`` entry point."""
from __future__ import annotations

import os
import sys

from .service import main

if __name__ == "__main__":
    rc = main()
    # os._exit skips interpreter teardown on purpose: after a failed CUDA init the
    # jetson_utils/jetson_inference extension modules segfault while being
    # garbage-collected at exit, which turns a clean error report into
    # "Fatal Python error: Segmentation fault". Everything is already shut down
    # and logged by main(); only flushing remains.
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(rc)
