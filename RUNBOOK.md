# Runbook — API Debugging Toolkit

Operational reference for triaging issues in this service. Written the way
I'd document a real support handoff: symptom → likely cause → diagnostic
steps → resolution.

## 0. Accessing the live deployment

The demo runs on AWS EC2 (`i-0f88fbc1a72c890c7`, `t3.micro`,
`eu-central-1`), public IP `18.193.101.255`.

```bash
# SSH in (key restricted to the operator's IP in the security group)
ssh -i ~/.ssh/api-toolkit-key.pem ubuntu@18.193.101.255

# Once connected, the compose commands below work exactly as they do locally
cd api-debugging-toolkit && sudo docker compose ps
```

Before SSHing in, a quick outside-in check with
[infra-health-check](https://github.com/ubiquitousdrop/infra-health-check)
tells you whether the instance itself is up and whether the app is
responding, without needing to log in at all:

```bash
infra-health ec2 i-0f88fbc1a72c890c7           # is the instance running?
infra-health http http://18.193.101.255:5000/health   # is the app responding?
```

If the EC2 check fails but the instance shows as running in the AWS
console, or vice versa, that split result is itself diagnostic — see
which one disagrees and start there.

## 1. First response to any report ("the API is down / slow / erroring")

1. Hit `GET /health`.
   - `200 {"status": "ok"}` → API and DB are both reachable. The issue is
     likely client-side, network, or a specific endpoint — skip to the
     relevant section below.
   - `503 {"status": "degraded"}` → database is unreachable. Go to
     **Section 2**.
   - No response / connection refused → the process itself is down. Check
     container status: `docker compose ps`, then `docker compose logs api`.
2. Every response carries an `X-Request-Id` header. Ask the reporter for
   it (or the request payload/timestamp) so you can grep logs precisely
   instead of scanning everything.

## 2. Database unreachable (`/health` returns 503)

**Likely causes:** DB container not started, wrong `DATABASE_URL`, DB
still initializing, network/firewall between API and DB containers.

**Diagnose:**
```bash
docker compose ps                 # is the db service Up and (healthy)?
docker compose logs db --tail 50  # crash-looping? auth failures?
docker compose exec db pg_isready -U toolkit
```

**Resolve:**
- If `db` isn't healthy yet, wait — `depends_on: condition: service_healthy`
  should prevent `api` from starting before Postgres is ready, but if you
  bypassed that (e.g. ran `app/main.py` directly against a Postgres URL
  before it was up), restart the api service after the DB reports healthy.
- If `DATABASE_URL` is wrong, verify it matches `docker-compose.yml`
  (`postgresql+psycopg2://toolkit:toolkit@db:5432/toolkit` inside the
  compose network; `localhost` only works from the host machine).

## 3. Slow responses / suspected timeout

**Reproduce locally:** `GET /simulate/timeout?seconds=5`

**Diagnose in logs:** every request logs a `request_completed` line with
`duration_ms`. Grep by the `request_id` from the client's report:
```bash
docker compose logs api | grep "<request_id>"
```
Compare `duration_ms` against your SLA. If it's consistently high on real
endpoints (not just `/simulate/timeout`), check:
- DB query time (is the `orders` table missing an index the query needs?)
- Whether the container is CPU/memory constrained: `docker stats`

## 4. Client getting 400/422 on `POST /orders`

This is intentional input validation, not a bug — but it's the #1 source
of "the API is broken" tickets, so know the three cases cold:

| Response | Cause | What to tell the customer |
|---|---|---|
| `400 invalid_json` | Body isn't valid JSON, or `Content-Type` header missing/wrong | Confirm they're sending `Content-Type: application/json` and the body parses |
| `400 missing_fields` | One of `customer_name`, `item`, `quantity` absent | Check the `fields` array in the response — it names exactly what's missing |
| `422 invalid_quantity` | `quantity` isn't a positive integer | Distinguish from `400`: the JSON was valid, the *value* was semantically wrong |

## 5. Getting 429 on requests

`GET /simulate/rate-limit` always returns this — use it to test client-side
retry/backoff logic. Response includes `retry_after_seconds`; a correctly
behaving client should honor it before retrying.

## 6. Unhandled exceptions (500s)

Every unhandled exception is caught by the global error handler, logged
with full traceback under `unhandled_exception`, and returned to the
client as `{"error": "internal_server_error", "request_id": "..."}`.

**Diagnose:**
```bash
docker compose logs api | grep "<request_id>" | grep unhandled_exception
```
The `exc_info` field in that log line has the full Python traceback —
that's your starting point for root cause, not the generic client-facing
message.

## Design notes (why it's built this way)

- **Every log line is JSON** — greppable/parseable by request_id, not
  free-text prose that requires manual reading.
- **`X-Request-Id` on every response** — the single most useful thing an
  API can give a support engineer: a thread to pull that ties a customer's
  report to exact log lines.
- **`/health` checks the DB, not just "process is alive"** — a process
  that's up but can't reach its database is still an outage from the
  customer's point of view.
- **400 vs 422 vs 500 are used deliberately**, not interchangeably —
  malformed request, semantically invalid input, and server-side failure
  are different problems with different fixes, and conflating them makes
  triage slower.
