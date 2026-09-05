"""Request payload validation.

Kept separate from the route handlers so the rules are readable on their
own and testable without a Flask request context. Every rejection carries
a machine-readable "error" code plus the specific field at fault, because
"400 Bad Request" with no detail is the single most common reason a
support ticket bounces back and forth for a day.

The status codes are deliberate:
- 400 means the request itself is wrong (unparseable, or a required field
  is absent).
- 422 means the request parsed fine but a value is semantically invalid.
"""
from __future__ import annotations

REQUIRED_FIELDS = ("customer_name", "item", "quantity")


def validate_order(data: dict) -> tuple[dict | None, tuple[dict, int] | None]:
    """Validate an order payload.

    Returns (cleaned, None) on success or (None, (body, status)) on
    failure, so the caller can jsonify the error body directly.

    Note that an explicit null counts as missing rather than as a bad
    value: a client sending {"item": null} has the same underlying bug as
    one that omitted the key, and reporting it as missing_fields points
    them at it faster.
    """
    missing = [f for f in REQUIRED_FIELDS if data.get(f) is None]
    if missing:
        return None, ({"error": "missing_fields", "fields": missing}, 400)

    for field in ("customer_name", "item"):
        if not isinstance(data[field], str) or not data[field].strip():
            return None, (
                {
                    "error": "invalid_field",
                    "field": field,
                    "message": f"{field} must be a non-empty string",
                },
                422,
            )

    qty = data["quantity"]
    # bool is a subclass of int, so True would otherwise be stored as 1.
    if isinstance(qty, bool) or not isinstance(qty, int) or qty <= 0:
        return None, (
            {"error": "invalid_quantity", "message": "quantity must be a positive integer"},
            422,
        )

    return {
        "customer_name": data["customer_name"].strip(),
        "item": data["item"].strip(),
        "quantity": qty,
    }, None
