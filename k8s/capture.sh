#!/usr/bin/env bash
# Base: kubectl's own rollout subcommands. Verified 2026-09-07.
#
# Captures one rollout and one rollback into k8s/evidence/ as numbered text files.
# Run it ON THE EC2 HOST, from the repo root, after the stack is up and healthy.
#
#   ./k8s/capture.sh ghcr.io/alexander-constanza/api-debugging-toolkit:sha-abc1234
#
# The argument is the NEW image (the v2 of the exercise). The script reads the
# current image out of the cluster first, so the rollback target is recorded rather
# than assumed.
#
# What it deliberately does not capture: `kubectl get secret -o yaml`, and anything
# else that would put the database password in a file that gets committed. The last
# step fails the whole run if a password reached the evidence anyway.

set -euo pipefail

NS=toolkit
DEPLOY=deployment/api-debugging-toolkit
CONTAINER=api
URL="${URL:-http://127.0.0.1}"          # Traefik on the node, port 80
EVID="${EVID:-k8s/evidence}"

NEW_IMAGE="${1:-}"
if [ -z "$NEW_IMAGE" ]; then
  echo "usage: $0 <new image ref, for example ghcr.io/OWNER/api-debugging-toolkit:sha-abc1234>" >&2
  exit 2
fi

# k3s installs kubectl as `k3s kubectl`. Use whichever exists.
if command -v kubectl >/dev/null 2>&1; then
  K="kubectl"
else
  K="sudo k3s kubectl"
fi

mkdir -p "$EVID"

# run <file> <heading> <command...>
run() {
  local file="$EVID/$1"; shift
  local heading="$1"; shift
  {
    echo "### $heading"
    echo "# host: $(hostname), utc: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "\$ $*"
    echo
  } | tee -a "$file"
  "$@" 2>&1 | tee -a "$file"
  echo | tee -a "$file"
}

echo "Capturing into $EVID"

# ---------------------------------------------------------------- 1. the cluster
run 00-cluster.txt "Cluster and node" $K version
run 00-cluster.txt "Nodes" $K get nodes -o wide
run 00-cluster.txt "Storage class shipped by k3s" $K get storageclass

# ------------------------------------------------------- 2. the state before v2
run 01-before.txt "Everything in the namespace" $K -n "$NS" get all
run 01-before.txt "Ingress" $K -n "$NS" get ingress -o wide
run 01-before.txt "Persistent volume claim" $K -n "$NS" get pvc
run 01-before.txt "Pods, with their node" $K -n "$NS" get pods -o wide

OLD_IMAGE="$($K -n "$NS" get "$DEPLOY" -o jsonpath="{.spec.template.spec.containers[?(@.name=='$CONTAINER')].image}")"
{
  echo "### The image running before the rollout"
  echo "$OLD_IMAGE"
  echo
  echo "### The image being rolled out"
  echo "$NEW_IMAGE"
  echo
} | tee -a "$EVID/01-before.txt"

run 02-health-before.txt "Health through the Ingress, before the rollout" \
  curl -si --max-time 5 "$URL/health"

# --------------------------------------- 3. availability poll across the rollout
POLL="$EVID/05-availability-during-rollout.txt"
: > "$POLL"
(
  while :; do
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 "$URL/health" || echo 000)"
    printf '%s %s\n' "$(date -u +%H:%M:%S)" "$code" >> "$POLL"
    sleep 0.2
  done
) &
POLLER=$!
# shellcheck disable=SC2064
trap "kill $POLLER 2>/dev/null || true" EXIT

# ------------------------------------------------------------------ 4. rollout
run 03-rollout.txt "Roll forward to the new image" \
  $K -n "$NS" set image "$DEPLOY" "$CONTAINER=$NEW_IMAGE"
run 03-rollout.txt "Rollout status (this is the line worth pasting)" \
  $K -n "$NS" rollout status "$DEPLOY" --timeout=180s
run 03-rollout.txt "Revision history" \
  $K -n "$NS" rollout history "$DEPLOY"
run 03-rollout.txt "Pods after the rollout" \
  $K -n "$NS" get pods -o wide
run 03-rollout.txt "Health through the Ingress, on the new image" \
  curl -si --max-time 5 "$URL/health"

# ----------------------------------------------------------------- 5. rollback
run 04-rollback.txt "Undo, back to $OLD_IMAGE" \
  $K -n "$NS" rollout undo "$DEPLOY"
run 04-rollback.txt "Rollout status after the undo" \
  $K -n "$NS" rollout status "$DEPLOY" --timeout=180s
run 04-rollback.txt "Revision history after the undo" \
  $K -n "$NS" rollout history "$DEPLOY"
run 04-rollback.txt "The image now running" \
  $K -n "$NS" get "$DEPLOY" -o jsonpath="{.spec.template.spec.containers[?(@.name=='$CONTAINER')].image}{'\n'}"
run 04-rollback.txt "Pods after the rollback" \
  $K -n "$NS" get pods -o wide
run 04-rollback.txt "Health through the Ingress, after the rollback" \
  curl -si --max-time 5 "$URL/health"

# ------------------------------------------------- 6. what the poll saw, summarised
kill "$POLLER" 2>/dev/null || true
trap - EXIT
sleep 0.5
{
  echo
  echo "### Summary: HTTP status codes seen at the Ingress across the rollout and the rollback"
  echo "# one probe every 0.2s. 000 means the request never completed."
  awk '{print $2}' "$POLL" | sort | uniq -c | sort -rn
  echo
  echo "# total probes: $(wc -l < "$POLL")"
} | tee -a "$POLL"

# ------------------------------------------- 7. the Tailscale route, still there
# Kept as evidence that k3s's CNI and iptables rules did not take the subnet route
# away. Do NOT paste this one into the public README: it names tailnet peers and
# their 100.x addresses.
run 06-tailscale-not-for-readme.txt "Tailscale status after the rollout" \
  tailscale status
run 06-tailscale-not-for-readme.txt "Advertised and accepted routes" \
  sh -c 'tailscale status --json | grep -i -A3 "AdvertisedRoutes\|PrimaryRoutes" || true'

# ------------------------------------------------------- 8. no password escaped
PW="$($K -n "$NS" get secret toolkit-db -o jsonpath='{.data.POSTGRES_PASSWORD}' | base64 -d)"
if [ -n "$PW" ] && grep -rqF -- "$PW" "$EVID"; then
  echo "STOP: the database password appears in $EVID. Do not commit. Find it with:" >&2
  echo "  grep -rlF '<the password>' $EVID" >&2
  exit 1
fi
unset PW

echo
echo "Done. Evidence in $EVID:"
ls -la "$EVID"
echo
echo "Paste into the README: the rollout status line from 03-rollout.txt, the undo"
echo "and status lines from 04-rollback.txt, and the code summary at the end of"
echo "05-availability-during-rollout.txt. Leave 06-tailscale-not-for-readme.txt out."
