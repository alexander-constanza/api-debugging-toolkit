def test_create_order_success(client):
    resp = client.post("/orders", json={"customer_name": "Alex", "item": "Widget", "quantity": 3})
    assert resp.status_code == 201
    body = resp.get_json()
    assert body["customer_name"] == "Alex"
    assert body["quantity"] == 3
    assert "id" in body


def test_create_order_missing_fields(client):
    resp = client.post("/orders", json={"customer_name": "Alex"})
    assert resp.status_code == 400
    body = resp.get_json()
    assert body["error"] == "missing_fields"
    assert set(body["fields"]) == {"item", "quantity"}


def test_create_order_invalid_json(client):
    resp = client.post("/orders", data="not json", content_type="application/json")
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "invalid_json"


def test_create_order_invalid_quantity(client):
    resp = client.post("/orders", json={"customer_name": "Alex", "item": "Widget", "quantity": -1})
    assert resp.status_code == 422
    assert resp.get_json()["error"] == "invalid_quantity"


def test_get_order_found(client):
    create = client.post("/orders", json={"customer_name": "Sam", "item": "Gadget", "quantity": 1})
    order_id = create.get_json()["id"]

    resp = client.get(f"/orders/{order_id}")
    assert resp.status_code == 200
    assert resp.get_json()["item"] == "Gadget"


def test_get_order_not_found(client):
    resp = client.get("/orders/9999")
    assert resp.status_code == 404
    assert resp.get_json()["error"] == "not_found"
