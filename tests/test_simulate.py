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
