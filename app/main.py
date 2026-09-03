"""API Debugging Toolkit.

A small Flask service with a couple of "real" endpoints (orders backed by
a database) plus a set of /simulate/* endpoints that deliberately
reproduce common failure modes a support/FDE engineer has to diagnose:
timeouts, malformed input, rate limiting, and unhandled exceptions.
"""
import logging
import time
import uuid

from flask import Flask, g, jsonify, request
from sqlalchemy.exc import SQLAlchemyError

from app.db import Order, SessionLocal, check_db_connection, init_db
from app.logging_config import configure_logging

configure_logging()
logger = logging.getLogger("api_debugging_toolkit")

app = Flask(__name__)


@app.before_request
def start_request():
    g.request_id = str(uuid.uuid4())
    g.start_time = time.time()
    logger.info(
        "request_started",
        extra={"request_id": g.request_id},
    )


@app.after_request
def log_response(response):
    duration_ms = round((time.time() - g.start_time) * 1000, 2)
    logger.info(
        f"request_completed path={request.path} status={response.status_code} duration_ms={duration_ms}",
        extra={"request_id": g.get("request_id", "unknown")},
    )
    response.headers["X-Request-Id"] = g.get("request_id", "unknown")
    return response


@app.route("/health", methods=["GET"])
def health():
    """Liveness + dependency check. The first thing a support engineer
    (or a monitoring probe) should hit when something looks broken."""
    db_ok = check_db_connection()
    status = "ok" if db_ok else "degraded"
    code = 200 if db_ok else 503
    return jsonify({"status": status, "dependencies": {"database": "ok" if db_ok else "unreachable"}}), code


@app.route("/orders", methods=["POST"])
def create_order():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "invalid_json", "message": "Request body must be valid JSON"}), 400

    missing = [f for f in ("customer_name", "item", "quantity") if f not in data]
    if missing:
        return jsonify({"error": "missing_fields", "fields": missing}), 400

    if not isinstance(data["quantity"], int) or data["quantity"] <= 0:
        return jsonify({"error": "invalid_quantity", "message": "quantity must be a positive integer"}), 422

    session = SessionLocal()
    try:
        order = Order(customer_name=data["customer_name"], item=data["item"], quantity=data["quantity"])
        session.add(order)
        session.commit()
        session.refresh(order)
        return jsonify({"id": order.id, "customer_name": order.customer_name, "item": order.item, "quantity": order.quantity}), 201
    except SQLAlchemyError:
        session.rollback()
        logger.exception("order_creation_failed", extra={"request_id": g.get("request_id", "unknown")})
        return jsonify({"error": "database_error"}), 500
    finally:
        session.close()


@app.route("/orders/<int:order_id>", methods=["GET"])
def get_order(order_id: int):
    session = SessionLocal()
    try:
        order = session.get(Order, order_id)
        if order is None:
            return jsonify({"error": "not_found", "message": f"No order with id {order_id}"}), 404
        return jsonify({"id": order.id, "customer_name": order.customer_name, "item": order.item, "quantity": order.quantity})
    finally:
        session.close()


@app.route("/simulate/timeout", methods=["GET"])
def simulate_timeout():
    """Mimics a slow downstream dependency. Useful for practicing how to
    diagnose latency issues (e.g. via X-Request-Id + logs + duration_ms)."""
    delay = min(float(request.args.get("seconds", 3)), 10)
    time.sleep(delay)
    return jsonify({"message": f"responded after {delay}s"})


@app.route("/simulate/rate-limit", methods=["GET"])
def simulate_rate_limit():
    return jsonify({"error": "rate_limited", "retry_after_seconds": 30}), 429


@app.route("/simulate/error", methods=["GET"])
def simulate_error():
    """Deliberately raises to exercise the generic error handler + logging."""
    raise RuntimeError("Simulated unhandled exception for debugging practice")


@app.errorhandler(Exception)
def handle_uncaught(err):
    logger.exception("unhandled_exception", extra={"request_id": g.get("request_id", "unknown")})
    return jsonify({"error": "internal_server_error", "request_id": g.get("request_id", "unknown")}), 500


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
