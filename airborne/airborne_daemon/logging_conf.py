"""Logging configuration.

A single place to configure the root logger so every module can simply call
``logging.getLogger(__name__)``. Format is human-readable by default; set
``DRONE_LOG_JSON=1`` for line-delimited JSON suitable for log shippers.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time

_CONFIGURED = False


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "thread": record.threadName,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, separators=(",", ":"))


def configure_logging(level: str | int | None = None, *, json_logs: bool | None = None) -> None:
    """Idempotently configure the root logger.

    Level resolves from the ``level`` arg, then ``DRONE_LOG_LEVEL``, then INFO.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return

    if level is None:
        level = os.environ.get("DRONE_LOG_LEVEL", "INFO")
    if json_logs is None:
        json_logs = os.environ.get("DRONE_LOG_JSON", "0") == "1"

    handler = logging.StreamHandler(sys.stderr)
    if json_logs:
        handler.setFormatter(_JsonFormatter())
    else:
        fmt = "%(asctime)s %(levelname)-5s [%(threadName)s] %(name)s: %(message)s"
        formatter = logging.Formatter(fmt, datefmt="%H:%M:%S")
        formatter.converter = time.localtime
        handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
    _CONFIGURED = True
