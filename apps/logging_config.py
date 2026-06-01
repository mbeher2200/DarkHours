"""Structured JSON logging for the Lambda API and worker.

Call configure() once at the entry-point before any engine code is imported.
All existing log.*/logger.* calls across the engine emit JSON records;
Lambda's stdout is captured verbatim → CloudWatch Logs. Log level is
controlled by the LOG_LEVEL env var (default INFO).
"""
import logging
import os

from pythonjsonlogger.json import JsonFormatter

_configured = False


def configure() -> None:
    global _configured
    if _configured:
        return
    _configured = True
    level = getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO)
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
