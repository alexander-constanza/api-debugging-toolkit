# API Debugging Toolkit

[![tests](https://github.com/alexander-constanza/api-debugging-toolkit/actions/workflows/test.yml/badge.svg)](https://github.com/alexander-constanza/api-debugging-toolkit/actions/workflows/test.yml)

**Live demo:** https://ec2-api.tail422656.ts.net/health, served by the k3s Ingress through Traefik and published with Tailscale Funnel.

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

## Running on Kubernetes (k3s)

The same service, deployed to a single-node k3s cluster on the EC2 host that already runs it under Compose. The manifests are in `k8s/` and the image is built and pushed to GHCR by `.github/workflows/publish.yml`, tagged by commit SHA so that a rollout and a rollback both refer to a specific build rather than to a moving `latest`.

| File | What it is |
|---|---|
| `k8s/namespace.yaml` | Namespace `toolkit`, so the whole exercise deletes in one command |
| `k8s/postgres.yaml` | Postgres 16 as a single replica with `strategy: Recreate`, a 2Gi PersistentVolumeClaim on k3s's `local-path` class, and a `pg_isready` readiness probe |
| `k8s/api.yaml` | The API Deployment: an init container that waits for Postgres, startup, liveness and readiness probes, resource requests, and the database URL read from a Secret |
| `k8s/service.yaml` | ClusterIP Service on port 5000 |
| `k8s/ingress.yaml` | Traefik Ingress, the cluster's single entry point |
| `k8s/capture.sh` | Records the rollout and the rollback into `k8s/evidence/` |

The Secret is deliberately not in the repository. It is created once on the host:

```bash
kubectl apply -f k8s/namespace.yaml
PGPASS="$(openssl rand -hex 16)"
kubectl -n toolkit create secret generic toolkit-db \
  --from-literal=POSTGRES_USER=toolkit \
  --from-literal=POSTGRES_DB=toolkit \
  --from-literal=POSTGRES_PASSWORD="$PGPASS" \
  --from-literal=DATABASE_URL="postgresql+psycopg2://toolkit:${PGPASS}@postgres:5432/toolkit"
unset PGPASS

kubectl apply -f k8s/
kubectl -n toolkit rollout status deployment/api-debugging-toolkit
```

The namespace is applied on its own line first because `kubectl apply -f k8s/` reads the directory in alphabetical order, and `api.yaml` would otherwise be created before the namespace it lives in exists.

### The probes, and why liveness does not use `/health`

`/health` checks the database and returns **503** when it cannot reach it. That is exactly right for a readiness probe: a pod whose database is gone should stop receiving traffic, and Kubernetes takes it out of the Service endpoints until it recovers by itself. It is exactly wrong for a liveness probe: restarting the API cannot fix Postgres, so a liveness probe on `/health` turns a database blip into a restart loop, and `/simulate/db-down`, this repo's own headline drill, would deliberately trigger it.

So the three probes point at different things:

- **startup** and **liveness** hit `/simulate/timeout?seconds=0`, the only endpoint that returns 200 without touching Postgres. Because the container runs gunicorn with two workers and four threads each, that also proves a worker thread is actually free, which a plain TCP check would not.
- **readiness** hits `/health`, so a degraded dependency removes the pod from the Service rather than killing it.

One consequence worth stating: when the database really is down, every pod goes unready and Traefik answers 503 at the edge, so the JSON `"status": "degraded"` body is visible from inside the cluster rather than through the Ingress.

### Rollout and rollback

Run on 2026-09-08, output verbatim from `k8s/evidence/`:

```text
$ kubectl -n toolkit set image deployment/api-debugging-toolkit api=ghcr.io/alexander-constanza/api-debugging-toolkit:sha-3b34c93
deployment.apps/api-debugging-toolkit image updated
$ kubectl -n toolkit rollout status deployment/api-debugging-toolkit --timeout=180s
Waiting for deployment "api-debugging-toolkit" rollout to finish: 1 old replicas are pending termination...
Waiting for deployment "api-debugging-toolkit" rollout to finish: 1 old replicas are pending termination...
deployment "api-debugging-toolkit" successfully rolled out
$ kubectl -n toolkit rollout history deployment/api-debugging-toolkit
$ kubectl -n toolkit get pods -o wide
$ curl -si --max-time 5 http://127.0.0.1/health
```

```text
$ kubectl -n toolkit rollout undo deployment/api-debugging-toolkit
deployment.apps/api-debugging-toolkit rolled back
$ kubectl -n toolkit rollout status deployment/api-debugging-toolkit --timeout=180s
deployment "api-debugging-toolkit" successfully rolled out
$ kubectl -n toolkit rollout history deployment/api-debugging-toolkit
REVISION  CHANGE-CAUSE
2         <none>
3         <none>
$ kubectl -n toolkit get deployment/api-debugging-toolkit -o jsonpath={.spec.template.spec.containers[?(@.name=='api')].image}{'\n'}
$ kubectl -n toolkit get pods -o wide
$ curl -si --max-time 5 http://127.0.0.1/health
```

`k8s/capture.sh` polls `/health` through the Ingress every 200 ms for the whole of both operations. With `maxUnavailable: 0` and `maxSurge: 1`, a new pod becomes ready before an old one is terminated, so no request should be lost:

```text
### Summary: HTTP status codes seen at the Ingress across the rollout and the rollback
# one probe every 0.2s. 000 means the request never completed.
    154 200
      2 502
      1 one
      1 Summary:
      1 000000
      1 

# total probes: 167
```


The `"build"` key in the `/health` body is the marker used to make the two images distinguishable in a `curl` during the rollout above. It is kept rather than reverted, so the deployed image and the repository agree.

### What this is, and what it is not

One node, one Postgres, one weekend. Two replicas buy no fault tolerance here, since the node can take both with it; they are there so that "the rollout did not drop a request" is something the evidence can support rather than a claim. Postgres runs in the cluster on a local-path volume, which is a portfolio deployment and not a way to run a database. `create_all` still runs at application boot rather than as a migration job, which the Design choices section above already calls out as the shortcut alembic would replace.

Deploying it also surfaced something Compose hid: `/simulate/db-down` sets a module-level flag inside one gunicorn worker, so behind a Service with more than one replica the toggle reaches one worker out of several and `/health` then answers inconsistently. Scale to one replica to run that drill, or move the flag to shared state.

Reachability: Traefik serves the Ingress on the node's port 80, which is not open to the internet in the instance's security group, so the Ingress is reachable over the tailnet and published to the internet by Tailscale Funnel at the live-demo URL above. Nothing here creates an AWS load balancer or an EBS volume, so it adds no cost beyond the instance itself.

To remove: `kubectl delete namespace toolkit` for the workload, or `/usr/local/bin/k3s-uninstall.sh` for the whole cluster. The Compose stack is unaffected either way.

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
