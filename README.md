# API Debugging Toolkit

[![tests](https://github.com/alexander-constanza/api-debugging-toolkit/actions/workflows/test.yml/badge.svg)](https://github.com/alexander-constanza/api-debugging-toolkit/actions/workflows/test.yml)

<!-- Live demo: TODO, add the public URL here once the deployment is back up. -->

A small Flask API (backed by Postgres/SQLite via SQLAlchemy) built to
practice, and demonstrate, the core loop of a support/implementation
engineer: reproduce an issue, read structured logs, trace it to a request,
and know the difference between a client error, a validation error, and a
server failure.

It has two kinds of endpoints:

- **Real endpoints** (`/health`, `/orders`) that do actual work against a
  database, with proper validation and error handling.
- **Simulate endpoints** (`/simulate/timeout`, `/simulate/rate-limit`,
  `/simulate/db-down`, `/simulate/error`) that deliberately reproduce
  common failure modes, so the toolkit doubles as a sandbox for
  practicing diagnosis.

See [RUNBOOK.md](RUNBOOK.md) for the triage guide (symptom, cause,
diagnostic steps) written the way I'd document a real on-call handoff.

## Quickstart

```bash
docker compose up --build
```

That starts Postgres and the API together, and is the same command used
to run it anywhere else: no separate "prod" config to drift out of sync.

Then try it:

```bash
# Healthy service, with its database dependency checked
curl http://localhost:5000/health

# Create an order
curl -X POST http://localhost:5000/orders \
  -H "Content-Type: application/json" \
  -d '{"customer_name": "Alex", "item": "Widget", "quantity": 2}'

# Fetch it back
curl http://localhost:5000/orders/1
```

And practice the failure modes:

```bash
# Validation: names exactly which fields are missing
curl -X POST http://localhost:5000/orders \
  -H "Content-Type: application/json" -d '{}'

# Semantically invalid value: 422, not 400
curl -X POST http://localhost:5000/orders \
  -H "Content-Type: application/json" \
  -d '{"customer_name": "Alex", "item": "Widget", "quantity": 0}'

# A client error stays a client error: 404, not 500
curl -i http://localhost:5000/nope

# Slow dependency
curl "http://localhost:5000/simulate/timeout?seconds=5"

# Force the database dependency down, then watch /health go red
curl -X POST http://localhost:5000/simulate/db-down \
  -H "Content-Type: application/json" -d '{"down": true}'
curl -i http://localhost:5000/health   # 503 degraded
curl -X POST http://localhost:5000/simulate/db-down \
  -H "Content-Type: application/json" -d '{"down": false}'
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
| POST | `/simulate/db-down` | Toggle a simulated DB outage (`{"down": true}`) |
| GET | `/simulate/error` | Always raises, to test error logging |

## Deployment

The service is deployed as containers, built from this repo with the same
`docker compose up -d --build` used for local development. Postgres runs
as a container alongside the API rather than as a managed database, which
keeps the whole thing a single resource to stand up or tear down.

See [RUNBOOK.md](RUNBOOK.md) for how to diagnose issues on a running
deployment, and
[infra-health-check](https://github.com/alexander-constanza/infra-health-check)
for the CLI used to verify an instance and endpoint are healthy:

```bash
infra-health http http://<host>:5000/health
```

## Design choices

- **Structured JSON logs** with a `request_id` on every line, and with
  fields like `path`, `status` and `duration_ms` as real top-level JSON
  keys rather than text baked into a message string. That is the
  difference between `grep`-ing and actually filtering. See
  `app/logging_config.py`.
- **`X-Request-Id` response header** on every request, so a customer
  report can be tied directly to log lines.
- **SQLAlchemy** so the same code runs against SQLite locally/in tests and
  Postgres in Docker, controlled by `DATABASE_URL`.
- **Deliberate 400 vs 422 vs 404 vs 500** distinctions. Client errors are
  answered as client errors: a typo'd URL returns 404, not a 500 that
  pollutes the server's error-rate metric. See the runbook for why that
  separation matters for triage speed.
- **Validation lives in `app/validation.py`**, separate from the route, so
  the rules are readable and testable on their own.
- **Schema created explicitly by an app factory**, not as an import side
  effect, so importing the module doesn't silently create a database.
  Anything beyond a demo would use alembic migrations instead of
  `create_all` at boot.
- **Identical deploy and local commands** (`docker compose up --build`),
  so there's no drift between "how I tested it" and "how it actually
  runs."

## How this was built

Written with AI assistance (Claude), the same way I work day to day: I set the
design and the constraints, the model drafted, and I reviewed, tested and
deployed it. The design choices above are mine and I can walk through any of
them. Everything described here is covered by the test suite.
