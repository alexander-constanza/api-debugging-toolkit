"""Tests that structured logs are actually structured.

An f-string that bakes path/status/duration_ms into the message text
looks structured in a terminal but cannot be filtered on, which defeats
the point of JSON logs.
"""
import io
import json
import logging

from app.logging_config import JsonFormatter


def _capture(logger_name="api_debugging_toolkit"):
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger(logger_name)
    logger.addHandler(handler)
    return buf, handler, logger


def test_request_completed_fields_are_top_level_json_keys(client):
    buf, handler, logger = _capture()
    try:
        client.get("/health")
    finally:
        logger.removeHandler(handler)

    lines = [json.loads(ln) for ln in buf.getvalue().splitlines() if ln.strip()]
    completed = [ln for ln in lines if ln["message"] == "request_completed"]
    assert completed, "no request_completed line was emitted"

    entry = completed[-1]
    assert entry["path"] == "/health"
    assert entry["status"] == 200
    assert isinstance(entry["duration_ms"], (int, float))
    assert entry["request_id"] != "unknown"


def test_formatter_does_not_leak_reserved_record_attributes():
    """_RESERVED must filter LogRecord internals on this Python version."""
    buf, handler, logger = _capture("test_reserved_probe")
    logger.propagate = False
    try:
        logger.info("probe", extra={"custom_field": "kept"})
    finally:
        logger.removeHandler(handler)

    entry = json.loads(buf.getvalue().strip())
    assert entry["custom_field"] == "kept"
    for leaked in ("args", "levelno", "pathname", "msecs", "relativeCreated", "msg"):
        assert leaked not in entry, f"reserved attribute {leaked} leaked into the payload"
    assert set(entry) == {"timestamp", "level", "logger", "message", "custom_field"}
