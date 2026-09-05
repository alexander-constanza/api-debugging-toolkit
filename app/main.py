"""API Debugging Toolkit.

A small Flask service with a couple of "real" endpoints (orders backed by
a database) plus a set of /simulate/* endpoints that deliberately
reproduce common failure modes a support/FDE engineer has to diagnose:
timeouts, malformed input, rate limiting, dependency outages, and
unhandled exceptions.
"""
import logging
import math
import os
import time
import uuid

from flask import Flask, g, jsonify, request
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.exceptions import HTTPException

from app.db import Order, SessionLocal, check_db_connection, init_db
from app.logging_config import configure_logging
from app.validation import validate_order

configure_logging()
logger = logging.getLogger("api_debugging_toolkit")

app = Flask(__name__)

# In-process toggle behind /simulate/db-down. Lets an operator practice the
# "dependency is gone" drill without actually stopping Postgres, which would
# also take down anything else pointed at it.
_force_db_down = False


@app.before_request
def start_request():
    g.request_id = str(uuid.uuid4())
    g.start_time = time.time()
    logger.info(
        "request_started",
        extra={"request_id": g.request_id, "method": request.method, "path": request.path},
    )


@app.after_request
def log_response(response):
    duration_ms = round((time.time() - g.get("start_time", time.time())) * 1000, 2)
    logger.info(
        "request_completed",
        extra={
            "request_id": g.get("request_id", "unknown"),
            "path": request.path,
            "status": response.status_code,
            "duration_ms": duration_ms,
        },
    )
    response.headers["X-Request-Id"] = g.get("request_id", "unknown")
    return response


@app.route("/health", methods=["GET"])
def health():
    """Liveness + dependency check. The first thing a support engineer
    (or a monitoring probe) should hit when something looks broken."""
    db_ok = False if _force_db_down else check_db_connection()
    status = "ok" if db_ok else "degraded"
    code = 200 if db_ok else 503
    return jsonify(
        {"status": status, "dependencies": {"database": "ok" if db_ok else "unreachable"}}
    ), code


@app.route("/orders", methods=["POST"])
def create_order():
    data = request.get_json(silent=True)
    if data is None:
        return jsonify({"error": "invalid_json", "message": "Request body must be valid JSON"}), 400
    if not isinstance(data, dict):
        return jsonify(
            {"error": "invalid_json", "message": "Request body must be a JSON object"}
        ), 400

    cleaned, error = validate_order(data)
    if error is not None:
        body, status = error
        return jsonify(body), status

    session = SessionLocal()
    try:
        order = Order(**cleaned)
        session.add(order)
        session.commit()
        session.refresh(order)
        return jsonify(
            {
                "id": order.id,
                "customer_name": order.customer_name,
                "item": order.item,
                "quantity": order.quantity,
            }
        ), 201
    except SQLAlchemyError:
        session.rollback()
        logger.exception(
            "order_creation_failed", extra={"request_id": g.get("request_id", "unknown")}
        )
        return jsonify({"error": "database_error"}), 500
    finally:
        session.close()


@app.route("/orders/<int:order_id>", methods=["GET"])
def get_order(order_id: int):
    session = SessionLocal()
    try:
        order = session.get(Order, order_id)
        if order is None:
            return jsonify(
                {"error": "not_found", "message": f"No order with id {order_id}"}
            ), 404
        return jsonify(
            {
                "id": order.id,
                "customer_name": order.customer_name,
                "item": order.item,
                "quantity": order.quantity,
            }
        )
    finally:
        session.close()


@app.route("/simulate/timeout", methods=["GET"])
def simulate_timeout():
    """Mimics a slow downstream dependency. Useful for practicing how to
    diagnose latency issues (e.g. via X-Request-Id + logs + duration_ms)."""
    raw = request.args.get("seconds", "3")
    try:
        delay = float(raw)
    except ValueError:
        return jsonify(
            {
                "error": "invalid_parameter",
                "parameter": "seconds",
                "message": f"'{raw}' is not a number",
            }
        ), 400

    # NaN and infinity survive float() but break the clamp: NaN compares
    # false against everything, so min/max would pass it straight through
    # to time.sleep.
    if not math.isfinite(delay):
        return jsonify(
            {
                "error": "invalid_parameter",
                "parameter": "seconds",
                "message": f"'{raw}' is not a finite number",
            }
        ), 400

    delay = max(0.0, min(delay, 10.0))
    time.sleep(delay)
    return jsonify({"message": f"responded after {delay}s"})


@app.route("/simulate/rate-limit", methods=["GET"])
def simulate_rate_limit():
    return jsonify({"error": "rate_limited", "retry_after_seconds": 30}), 429


@app.route("/simulate/db-down", methods=["POST"])
def simulate_db_down():
    """Toggle a simulated database outage so /health reports degraded.

    POST {"down": true} to break it, {"down": false} to restore. This is
    the most realistic on-call drill the toolkit offers: it exercises the
    full path from "health check went red" to "which dependency is at
    fault", without touching the real database.
    """
    global _force_db_down
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or not isinstance(payload.get("down"), bool):
        return jsonify(
            {
                "error": "invalid_field",
                "field": "down",
                "message": "Request body must be a JSON object with a boolean 'down' field",
            }
        ), 422

    _force_db_down = payload["down"]
    return jsonify({"database_forced_down": _force_db_down})


@app.route("/simulate/error", methods=["GET"])
def simulate_error():
    """Deliberately raises to exercise the generic error handler + logging."""
    raise RuntimeError("Simulated unhandled exception for debugging practice")


@app.errorhandler(HTTPException)
def handle_http_exception(err: HTTPException):
    """Client-side errors (404, 405, 400 from routing) answered as what
    they are.

    Without this, the catch-all below would swallow every werkzeug
    HTTPException and turn a routine 404 into a 500: the client is told
    the server broke, and the server's own error-rate metric climbs on
    what was really just a typo'd URL.
    """
    return jsonify(
        {
            "error": err.name.lower().replace(" ", "_"),
            "message": err.description,
            "request_id": g.get("request_id", "unknown"),
        }
    ), err.code


@app.errorhandler(Exception)
def handle_uncaught(err: Exception):
    del err  # logged via exc_info; Flask requires the parameter
    logger.exception("unhandled_exception", extra={"request_id": g.get("request_id", "unknown")})
    return jsonify(
        {"error": "internal_server_error", "request_id": g.get("request_id", "unknown")}
    ), 500


def create_app() -> Flask:
    """Application factory: creates the schema, then returns the app.

    Kept explicit so that merely importing this module has no side
    effects. In anything beyond a demo the schema would be managed by
    alembic migrations rather than create_all at boot.
    """
    init_db()
    return app


if __name__ == "__main__":
    create_app()
    # Loopback by default for local dev. Containers need all interfaces, so
    # the Dockerfile's gunicorn command binds 0.0.0.0 explicitly; set
    # FLASK_RUN_HOST to do the same with this dev server.
    app.run(
        host=os.environ.get("FLASK_RUN_HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "5000")),
        debug=os.environ.get("FLASK_DEBUG") == "1",
    )
