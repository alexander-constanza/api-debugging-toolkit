def test_health_ok(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ok"
    assert body["dependencies"]["database"] == "ok"


def test_health_includes_request_id_header(client):
    resp = client.get("/health")
    assert "X-Request-Id" in resp.headers
