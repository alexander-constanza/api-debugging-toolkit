"""Tests for the client-error vs server-error boundary.

These are the most important tests in this repo: its whole premise is
knowing the difference between a client error, a validation error, and a
server failure. A catch-all exception handler that turns every 404 into a
500 destroys exactly that distinction, and does it silently.
"""


def test_unknown_path_is_404_not_500(client):
    resp = client.get("/nope")
    assert resp.status_code == 404
    assert resp.get_json()["error"] == "not_found"


def test_404_body_carries_request_id(client):
    resp = client.get("/nope")
    body = resp.get_json()
    assert "request_id" in body
    assert body["request_id"] != "unknown"
    assert resp.headers["X-Request-Id"] == body["request_id"]


def test_wrong_method_is_405_not_500(client):
    resp = client.post("/health")
    assert resp.status_code == 405
    assert resp.get_json()["error"] == "method_not_allowed"


def test_unconvertible_path_param_is_404_not_500(client):
    resp = client.get("/orders/notanint")
    assert resp.status_code == 404
    assert resp.get_json()["error"] == "not_found"


def test_real_exception_still_returns_500(client):
    """The HTTPException handler must not shadow genuine server failures."""
    resp = client.get("/simulate/error")
    assert resp.status_code == 500
    assert resp.get_json()["error"] == "internal_server_error"
