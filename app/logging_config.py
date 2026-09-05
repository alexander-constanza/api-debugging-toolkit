"""Structured JSON logging setup.

Support/FDE engineers need logs that are greppable and parseable, not
free-text prose. This configures the root logger to emit one JSON object
per line, with every field passed via `extra` promoted to a top-level key
so it can be filtered on directly (path, status, duration_ms, request_id)
rather than parsed back out of a message string.
"""
import json
import logging
import sys
import time

# Attributes every LogRecord carries by default. Anything outside this set
# arrived via `extra=` and belongs in the JSON payload as its own key.
# Built by instantiating a real record so it stays correct across Python
# versions rather than hardcoding a list that silently drifts.
_RESERVED = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {
    "message",
    "asctime",
    "taskName",
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        payload.update({k: v for k, v in record.__dict__.items() if k not in _RESERVED})
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
