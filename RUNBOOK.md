# Runbook: API Debugging Toolkit

Operational reference for triaging issues in this service. Written the way
I'd document a real support handoff: symptom, likely cause, diagnostic
steps, resolution.

## 0. Accessing a deployment

The service runs as containers, so wherever it is deployed the first
commands are the same ones used locally. From the directory holding
`docker-compose.yml` on the host running it:

```bash
docker compose ps                    # are api and db both Up (and healthy)?
docker compose logs api --tail 100   # recent application logs
docker compose logs db --tail 50     # database container logs
```

Before logging in to the host at all, an outside-in check with
[infra-health-check](https://github.com/alexander-constanza/infra-health-check)
tells you whether the endpoint is responding:

```bash
infra-health http http://<host>:5000/health
```

If the outside-in check fails but the containers report healthy from the
inside, that split result is itself diagnostic: the problem is between the
client and the host (DNS, security group/firewall, port mapping), not in
the application.

## 1. First response to any report ("the API is down / slow / erroring")

1. Hit `GET /health`.
   - `200 {"status": "ok"}` → API and DB are both reachable. The issue is
     likely client-side, network, or a specific endpoint, so skip to the
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
- If `db` isn't healthy yet, wait. `depends_on: condition: service_healthy`
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

This is intentional input validation, not a bug, but it is the #1 source
of "the API is broken" tickets, so know the cases cold:

| Response | Cause | What to tell the customer |
|---|---|---|
| `400 invalid_json` | Body isn't valid JSON, isn't a JSON *object* (an array or bare scalar), or `Content-Type` is missing/wrong | Confirm they're sending `Content-Type: application/json` and that the body is a JSON object |
| `400 missing_fields` | One of `customer_name`, `item`, `quantity` is absent **or explicitly null** | Check the `fields` array in the response: it names exactly what's missing. An explicit `null` is reported here, not as a bad value, because it's the same client bug |
| `422 invalid_field` | `customer_name` or `item` isn't a non-empty string (a dict, a list, or whitespace only) | The response names the offending `field`. The JSON parsed, the value was the wrong shape |
| `422 invalid_quantity` | `quantity` isn't a positive integer (`true` counts as invalid, not as 1) | Distinguish from `400`: the JSON was valid, the *value* was semantically wrong |

Note that `{}` returns `missing_fields` listing all three fields, not a
generic `invalid_json`. Telling a client "your body was bad" when you
could tell them "you're missing `quantity`" wastes a whole round trip.

## 5. Getting 429 on requests

`GET /simulate/rate-limit` always returns this: use it to test client-side
retry/backoff logic. Response includes `retry_after_seconds`, and a
correctly behaving client should honor it before retrying.

## 6. Unhandled exceptions (500s)

Every unhandled exception is caught by the global error handler, logged
with full traceback under `unhandled_exception`, and returned to the
client as `{"error": "internal_server_error", "request_id": "..."}`.

**Diagnose:**
```bash
docker compose logs api | grep "<request_id>" | grep unhandled_exception
```
The `exc_info` field in that log line has the full Python traceback, and
that's your starting point for root cause, not the generic client-facing
message.

**Important:** a 500 here means the *server* failed. Client mistakes
(unknown path, wrong method, unparseable path parameter) are answered as
404 or 405 by a separate handler and never reach this one. If you see a
spike in `internal_server_error`, it is real, not routing noise. A
catch-all handler that swallows 404s is a failure mode in its own right:
it lies to the client and inflates the error-rate metric you page on.

## 7. Health check times out while another request is in flight

**Symptom:** `/health` hangs or times out, monitoring flaps, but the
service looks fine the moment you retry. Often it coincides with someone
hitting `/simulate/timeout?seconds=10` or a genuinely slow query.

**Cause:** gunicorn's default is a *single synchronous worker*. One
in-flight blocking request occupies the only worker there is, so every
other request, health checks included, waits behind it. The service isn't
down, it's starved. This is worth knowing because it looks exactly like
an outage from the outside while every container reports healthy.

**Diagnose:**
```bash
docker compose logs api | grep request_completed   # a long duration_ms overlapping the gap?
docker compose exec api ps aux | grep gunicorn     # how many worker processes?
```
If only one gunicorn worker process is running alongside the master, that
is the problem.

**Resolve:** run more than one worker, and give each some threads, so a
blocking request can't monopolize the process. This repo's Dockerfile does
that:

```
gunicorn --workers 2 --threads 4 --timeout 30 --bind 0.0.0.0:5000 app.main:create_app()
```

Worker count is usually sized from CPU count (`2 * cores + 1` is the
common starting point); threads help when the workload is I/O bound, as it
is here. `--timeout 30` ensures a genuinely stuck worker is recycled
rather than hanging forever.

## 8. Practicing a dependency outage

`POST /simulate/db-down {"down": true}` forces `check_db_connection` to
report failure, so `/health` returns `503 degraded` without touching the
real database. It's the fastest way to rehearse Section 2's drill, or to
verify that monitoring actually alerts on a degraded health check.

```bash
curl -X POST http://localhost:5000/simulate/db-down \
  -H "Content-Type: application/json" -d '{"down": true}'
curl -i http://localhost:5000/health    # 503, database: unreachable
curl -X POST http://localhost:5000/simulate/db-down \
  -H "Content-Type: application/json" -d '{"down": false}'
```

The flag is in-process, so it resets on restart and, with more than one
gunicorn worker, applies only to the worker that served the toggle
request. That's a useful accident: it demonstrates why per-process state
doesn't belong in a horizontally scaled service.

## Design notes (why it's built this way)

- **Every log line is JSON**, greppable and parseable by request_id rather
  than free-text prose that requires manual reading. Fields passed as
  `extra` become real top-level keys (`path`, `status`, `duration_ms`), so
  you can filter on them instead of regexing a message string.
- **`X-Request-Id` on every response**, the single most useful thing an
  API can give a support engineer: a thread to pull that ties a customer's
  report to exact log lines.
- **`/health` checks the DB, not just "process is alive"**, because a
  process that's up but can't reach its database is still an outage from
  the customer's point of view.
- **400 vs 422 vs 404 vs 500 are used deliberately**, not
  interchangeably. Malformed request, semantically invalid input, wrong
  address, and server-side failure are different problems with different
  fixes, and conflating them makes triage slower.
