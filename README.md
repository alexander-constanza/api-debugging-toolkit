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

### Publishing the demo with Tailscale Funnel

The live-demo URL at the top of this README is served by Tailscale Funnel on the EC2 host: Funnel terminates TLS for the public `<machine>.<tailnet>.ts.net` name and proxies to Traefik, which serves the Ingress. Neither port 80 nor 443 is open in the instance's security group, so Funnel is the only route in from the internet.

**Funnel publishes `/health` and nothing else.** Every other path gets Tailscale's own plain-text `404 page not found` without reaching the application:

```bash
tailscale funnel status
# https://<machine>.<tailnet>.ts.net (Funnel on)
# |-- /health proxy http://127.0.0.1:80/health
```

That narrowing is deliberate, and it is the only thing doing the work. `k8s/ingress.yaml` has no host or path rule and `app/main.py` has no auth, so anything Funnel publishes is public and unauthenticated - and this service ships `/simulate/*` endpoints whose whole purpose is to break it:

- `POST /simulate/db-down {"down": true}` forces `/health` to 503. Readiness is `/health`, so the single replica leaves the Service after about 30 seconds, and Traefik can then no longer route the `{"down": false}` request that would undo it. Liveness is `/simulate/timeout?seconds=0`, which keeps answering 200, so the pod is never restarted. Recovery needs `kubectl -n toolkit rollout restart deploy/api-debugging-toolkit` from inside the cluster. The flag is per-process and gunicorn runs two workers, so it takes a few POSTs to be deterministic rather than one.
- `GET /simulate/timeout?seconds=10` holds a worker thread for ten seconds. Capacity is `--workers 2 --threads 4`, so about eight concurrent requests stall everything behind them, and sustained, that fails the liveness probe into a restart loop.
- `POST /orders` is an unauthenticated write. `app/validation.py` sets no maximum length and the columns in `app/db.py` are unbounded `String`, so anything posted is stored and readable back at `/orders/<id>`.

Publishing only `/health` takes all three off the internet without touching the application, so the drills above still work in full over the tailnet and from inside the cluster.

How it is set, and how to change it:

```bash
tailscale funnel status                                                    # what is published now

# publish only /health (the current configuration)
tailscale funnel --bg --yes --set-path=/health http://127.0.0.1:80/health
tailscale funnel --https=443 --set-path=/ off

tailscale funnel --bg 80                                                   # widen back to the whole origin
tailscale funnel reset                                                     # withdraw the demo entirely
```

Add the narrow path before removing the wide one, as above, so the published URL never has a gap.

Verify without calling the endpoints that break the service:

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://<machine>.<tailnet>.ts.net/health        # expect 200
curl -s -o /dev/null -w '%{http_code}\n' https://<machine>.<tailnet>.ts.net/orders/999999 # expect 404 from Funnel
```

`GET /orders/999999` is the right probe: it is idempotent and harmless whichever layer answers it, and Funnel's plain-text `404 page not found` is visibly different from the application's JSON 404, so the response tells you which layer replied. Do not use `/simulate/*` to test the block - that is the thing being protected against.

One trap when checking from a machine that is on the tailnet: MagicDNS resolves `<machine>.<tailnet>.ts.net` to the tailnet address, so the request goes over the tailnet instead of through Funnel and does not test the public path at all. Check from off the tailnet.

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


The `"build"` key in the `/health` body is the marker used to make the two images distinguishable in a `curl` during the rollout above. It is kept rather than reverted, so the deployed image and the repository agree: the live demo's `/health` carries `"build": "2"`. The tag pinned in `k8s/api.yaml` is the image actually rolled out; later commits that do not touch `app/` leave it accurate.

### What this is, and what it is not

One node, one Postgres, one weekend. This is a learning exercise and a portfolio piece, not a production service, and the list below is the honest version of what that means rather than a list of things that were finished. It runs a single replica: two on one node buy no fault tolerance anyway, since the node can take both with it, and the `/simulate/db-down` drill is only deterministic with one.

Deploying it also surfaced something Compose hid: `/simulate/db-down` sets a module-level flag inside one gunicorn worker, so behind a Service with more than one replica the toggle reaches one worker out of several and `/health` then answers inconsistently. Scale to one replica to run that drill, or move the flag to shared state.

Reachability: Traefik serves the Ingress on the node's port 80, which is not open to the internet in the instance's security group, so the Ingress is reachable over the tailnet and published to the internet by Tailscale Funnel at the live-demo URL above. Nothing here creates an AWS load balancer or an EBS volume, so it adds no cost beyond the instance itself.

Known, and not fixed:

- **The application itself is unauthenticated.** Funnel publishes only `/health`, so `/simulate/*` and `POST /orders` cannot be reached from the internet - but nothing in the app or the Ingress enforces that. The protection is at the edge and one command deep: widen Funnel again, or reach the Ingress over the tailnet, and every route is open to whoever gets there. `k8s/ingress.yaml` still has no host or path rule.
- **`POST /orders` accepts unauthenticated writes with no size limit**, and the columns are unbounded. There is no rate limiting anywhere (`/simulate/rate-limit` only returns a canned 429; it limits nothing).
- **`create_all` runs at application boot** rather than as a migration job. Design choices below already calls this out as the shortcut alembic would replace.
- **Postgres runs in-cluster on a `local-path` volume.** That is a portfolio deployment, not a way to run a database: the provisioner does not enforce the PVC's 2Gi request, so it writes against the node's root disk.
- **The manifests' comments are the design notes.** They were written against the cluster as it stood on 2026-09-07 and are not re-verified on every change, so treat them as intent rather than as current fact.

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
