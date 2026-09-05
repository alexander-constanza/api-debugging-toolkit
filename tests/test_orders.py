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


def test_create_order_empty_object_reports_all_missing_fields(client):
    """{} is valid JSON, so the useful answer names the absent fields."""
    resp = client.post("/orders", json={})
    assert resp.status_code == 400
    body = resp.get_json()
    assert body["error"] == "missing_fields"
    assert set(body["fields"]) == {"customer_name", "item", "quantity"}


def test_create_order_json_array_is_invalid_json(client):
    resp = client.post("/orders", json=[])
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "invalid_json"


def test_create_order_explicit_nulls_report_missing_fields(client):
    resp = client.post(
        "/orders", json={"customer_name": None, "item": None, "quantity": None}
    )
    assert resp.status_code == 400
    body = resp.get_json()
    assert body["error"] == "missing_fields"
    assert set(body["fields"]) == {"customer_name", "item", "quantity"}


def test_create_order_structured_values_are_422_not_500(client):
    """A dict/list where a string belongs is the client's bug, not ours."""
    resp = client.post(
        "/orders", json={"customer_name": {"a": 1}, "item": ["z"], "quantity": 1}
    )
    assert resp.status_code == 422
    body = resp.get_json()
    assert body["error"] == "invalid_field"
    assert body["field"] == "customer_name"


def test_create_order_boolean_quantity_is_rejected(client):
    """bool subclasses int, so True would otherwise be stored as 1."""
    resp = client.post(
        "/orders", json={"customer_name": "Alex", "item": "Widget", "quantity": True}
    )
    assert resp.status_code == 422
    assert resp.get_json()["error"] == "invalid_quantity"


def test_create_order_whitespace_only_strings_are_rejected(client):
    resp = client.post(
        "/orders", json={"customer_name": "   ", "item": "Widget", "quantity": 1}
    )
    assert resp.status_code == 422
    body = resp.get_json()
    assert body["error"] == "invalid_field"
    assert body["field"] == "customer_name"


def test_create_order_strips_surrounding_whitespace(client):
    resp = client.post(
        "/orders", json={"customer_name": "  Alex  ", "item": " Widget ", "quantity": 2}
    )
    assert resp.status_code == 201
    body = resp.get_json()
    assert body["customer_name"] == "Alex"
    assert body["item"] == "Widget"


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
