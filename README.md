# API Debugging Toolkit

A small Flask API (backed by Postgres/SQLite via SQLAlchemy) built to
practice — and demonstrate — the core loop of a support/implementation
engineer: reproduce an issue, read structured logs, trace it to a request,
and know the difference between a client error, a validation error, and a
server failure.

It has two kinds of endpoints:

- **Real endpoints** (`/health`, `/orders`) that do actual work against a
  database, with proper validation and error handling.
- **Simulate endpoints** (`/simulate/timeout`, `/simulate/rate-limit`,
  `/simulate/error`) that deliberately reproduce common failure modes, so
  the toolkit doubles as a sandbox for practicing diagnosis.

See [RUNBOOK.md](RUNBOOK.md) for the triage guide — symptom → cause →
diagnostic steps — written the way I'd document a real on-call handoff.

## Run it

```bash
docker compose up --build
```

Then:
```bash
curl http://localhost:5000/health
curl -X POST http://localhost:5000/orders \
  -H "Content-Type: application/json" \
  -d '{"customer_name": "Alex", "item": "Widget", "quantity": 2}'
```

## Run it locally without Docker

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m app.main   # uses local SQLite by default
```

## Tests

```bash
pip install -r requirements-dev.txt
pytest -v
```

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Liveness + DB dependency check |
| POST | `/orders` | Create an order (validates input) |
| GET | `/orders/<id>` | Fetch an order by id |
| GET | `/simulate/timeout?seconds=N` | Simulate a slow dependency |
| GET | `/simulate/rate-limit` | Always returns 429 |
| GET | `/simulate/error` | Always raises, to test error logging |

## Design choices

- **Structured JSON logs** with a `request_id` on every line — see
  `app/logging_config.py`.
- **`X-Request-Id` response header** on every request, so a customer
  report can be tied directly to log lines.
- **SQLAlchemy** so the same code runs against SQLite locally/in tests and
  Postgres in Docker, controlled by `DATABASE_URL`.
- **Deliberate 400 vs 422 vs 500** distinctions — see the runbook for why
  that separation matters for triage speed.
