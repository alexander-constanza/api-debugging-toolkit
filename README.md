# API Debugging Toolkit

**Live demo:** http://18.193.101.255:5000/health — running on a real AWS
EC2 instance (`t3.micro`, `eu-central-1`). Try `POST /orders` or any
`/simulate/*` endpoint below directly against it.

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

## Try it live

```bash
curl http://18.193.101.255:5000/health
curl -X POST http://18.193.101.255:5000/orders \
  -H "Content-Type: application/json" \
  -d '{"customer_name": "Alex", "item": "Widget", "quantity": 2}'
```

## Run it yourself

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

## Deployment

The live demo runs on a single AWS EC2 instance, provisioned like this:

1. Security group allowing SSH (22, restricted to a single IP) and the
   app port (5000, public — it's a demo link)
2. `t3.micro` instance (free-tier eligible), Ubuntu 22.04
3. Docker installed via `get.docker.com`
4. `git clone` this repo, then `docker compose up -d --build` — the exact
   same command as local dev, no separate "prod" config

No managed database (RDS) is used — Postgres runs as a container on the
same instance, to keep this a single free-tier resource rather than two
billable ones. See [RUNBOOK.md](RUNBOOK.md) for how to diagnose issues on
the running deployment, and
[infra-health-check](https://github.com/ubiquitousdrop/infra-health-check)
for the CLI used to verify the instance and endpoint are healthy:

```bash
infra-health ec2 i-0f88fbc1a72c890c7
infra-health http http://18.193.101.255:5000/health
```

## Design choices

- **Structured JSON logs** with a `request_id` on every line — see
  `app/logging_config.py`.
- **`X-Request-Id` response header** on every request, so a customer
  report can be tied directly to log lines.
- **SQLAlchemy** so the same code runs against SQLite locally/in tests and
  Postgres in Docker, controlled by `DATABASE_URL`.
- **Deliberate 400 vs 422 vs 500** distinctions — see the runbook for why
  that separation matters for triage speed.
- **Identical deploy and local commands** (`docker compose up --build`)
  — no drift between "how I tested it" and "how it actually runs."
