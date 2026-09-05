def test_simulate_rate_limit(client):
    resp = client.get("/simulate/rate-limit")
    assert resp.status_code == 429
    assert resp.get_json()["error"] == "rate_limited"


def test_simulate_error_is_caught_and_logged(client):
    resp = client.get("/simulate/error")
    assert resp.status_code == 500
    body = resp.get_json()
    assert body["error"] == "internal_server_error"
    assert "request_id" in body


def test_simulate_timeout_respects_cap(client):
    resp = client.get("/simulate/timeout?seconds=0")
    assert resp.status_code == 200
    assert "responded after" in resp.get_json()["message"]


def test_simulate_timeout_rejects_non_numeric_seconds(client):
    resp = client.get("/simulate/timeout?seconds=abc")
    assert resp.status_code == 400
    body = resp.get_json()
    assert body["error"] == "invalid_parameter"
    assert body["parameter"] == "seconds"


def test_simulate_timeout_clamps_negative_seconds_to_zero(client):
    resp = client.get("/simulate/timeout?seconds=-5")
    assert resp.status_code == 200
    assert resp.get_json()["message"] == "responded after 0.0s"


def test_simulate_timeout_rejects_nan(client):
    """float('nan') parses fine but slips through min/max comparisons."""
    resp = client.get("/simulate/timeout?seconds=nan")
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "invalid_parameter"


def test_simulate_timeout_rejects_infinity(client):
    resp = client.get("/simulate/timeout?seconds=inf")
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "invalid_parameter"


def test_simulate_db_down_toggles_health_status(client):
    assert client.get("/health").status_code == 200

    resp = client.post("/simulate/db-down", json={"down": True})
    assert resp.status_code == 200
    assert resp.get_json()["database_forced_down"] is True

    degraded = client.get("/health")
    assert degraded.status_code == 503
    body = degraded.get_json()
    assert body["status"] == "degraded"
    assert body["dependencies"]["database"] == "unreachable"

    client.post("/simulate/db-down", json={"down": False})
    assert client.get("/health").status_code == 200


def test_simulate_db_down_rejects_bad_payload(client):
    resp = client.post("/simulate/db-down", json={"down": "yes"})
    assert resp.status_code == 422
    assert resp.get_json()["error"] == "invalid_field"
