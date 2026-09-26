#!/usr/bin/env bash
# Application tier (backlog E10-S9; ARCHITECTURE §3.3, §12, §13; docs/ha.md "Application tier"):
#
#   Caddy balancer on :$CDI_WEBAPP_PORT (the one public port, as the single web app had)
#     -> $CDI_WEBAPP_INSTANCES web app instances on 127.0.0.1:$CDI_WEBAPP_BASE_PORT, +1, ...
#   $CDI_WORKERS Celery workers on queue cdi-web (CDI_JOB_EXECUTION=queue) run the jobs
#
# Native processes, no Docker; Caddy is the pinned release (versions.env), checksum-verified.
# Idempotent: starts what is down, leaves what is up, reloads the balancer's config. Called by
# start_all.sh; run it alone to bring back a killed instance or worker.
#
#   bash infra/runpod/start_apptier.sh           # start / repair
#   bash infra/runpod/start_apptier.sh status    # one line per process
set -euo pipefail
WS=${WS:-/workspace}
REPO=${REPO:-$WS/cdi}
HERE=$(cd "$(dirname "$0")" && pwd)
set -a; [ -f "$REPO/.env" ] && . "$REPO/.env"; set +a
. "$HERE/versions.env"
PY=$REPO/.venv/bin/python
RUN=$WS/run
LOGS=$WS/logs
TOOLS=$WS/tools
mkdir -p "$RUN" "$LOGS" "$TOOLS" "$WS/monitoring/targets"

PORT=${CDI_WEBAPP_PORT:-8888}
N=${CDI_WEBAPP_INSTANCES:-2}
BASE=${CDI_WEBAPP_BASE_PORT:-8881}
MBASE=${CDI_METRICS_PORT:-9464}
EXEC=${CDI_JOB_EXECUTION:-inprocess}
W=${CDI_WORKERS:-2}
WMBASE=${CDI_WORKER_METRICS_PORT:-9470}
ADMIN=${CDI_LB_ADMIN_PORT:-2020}
BIND=${CDI_LB_BIND:-0.0.0.0}
CADDY=$TOOLS/caddy-$CADDY_VERSION/caddy

alive() { [ -f "$1" ] && kill -0 "$(cat "$1")" 2>/dev/null; }
ready() { curl -fsS -m 3 "http://127.0.0.1:$1/readyz" 2>/dev/null | grep -q '"ready":true'; }
# a target list for Prometheus (file_sd): every instance's / worker's loopback /metrics port
targets() {  # targets <file> <port>...
  local f=$1; shift
  local list; list=$(printf '"127.0.0.1:%s",' "$@"); list=${list%,}
  printf '[{"targets": [%s]}]\n' "$list" > "$f.tmp" && mv "$f.tmp" "$f"
}

if [ "${1:-}" = status ]; then
  for i in $(seq "$N"); do
    p=$((BASE + i - 1))
    if ready "$p"; then echo "web$i  127.0.0.1:$p  ready"; else echo "web$i  127.0.0.1:$p  NOT READY"; fi
  done
  if [ "$EXEC" = queue ]; then
    for i in $(seq "$W"); do
      if alive "$RUN/worker-$i.pid"; then echo "worker$i  pid $(cat "$RUN/worker-$i.pid")  up"; else echo "worker$i  DOWN"; fi
    done
  fi
  if curl -fsS -m 3 "http://127.0.0.1:$ADMIN/reverse_proxy/upstreams" 2>/dev/null; then echo; else echo "balancer DOWN"; fi
  exit 0
fi

# ---------------------------------------------------------------- Caddy (pinned + sha512)
if [ ! -x "$CADDY" ]; then
  arc=$TOOLS/caddy_${CADDY_VERSION}_linux_amd64.tar.gz
  curl -fsSL -o "$arc.partial" \
    "https://github.com/caddyserver/caddy/releases/download/v${CADDY_VERSION}/caddy_${CADDY_VERSION}_linux_amd64.tar.gz"
  echo "$CADDY_SHA512  $arc.partial" | sha512sum -c --quiet - \
    || { rm -f "$arc.partial"; echo "sha512 mismatch for caddy $CADDY_VERSION"; exit 1; }
  mkdir -p "$(dirname "$CADDY")"
  tar -xzf "$arc.partial" -C "$(dirname "$CADDY")" caddy
  rm -f "$arc.partial"
fi
"$CADDY" version | grep -q "^v$CADDY_VERSION " || { echo "caddy is not $CADDY_VERSION"; exit 1; }

# ---------------------------------------------------------------- web app instances
ups=() mports=()
cd "$REPO"
for i in $(seq "$N"); do
  p=$((BASE + i - 1)) mp=$((MBASE + i - 1))
  ups+=("127.0.0.1:$p") mports+=("$mp")
  if ! alive "$RUN/webapp-$i.pid" || ! ready "$p"; then
    [ -f "$RUN/webapp-$i.pid" ] && kill "$(cat "$RUN/webapp-$i.pid")" 2>/dev/null || true
    # the command itself in the background (not a `cd && ...` list): a caller reading this
    # script's output (a notebook cell, a pipe) must still see EOF
    CDI_WEBAPP_HOST=127.0.0.1 CDI_WEBAPP_PORT=$p CDI_METRICS_PORT=$mp CDI_INSTANCE_NAME=web$i \
      setsid nohup "$PY" -m cdi_adapter.webapp >>"$LOGS/webapp-$i.log" 2>&1 </dev/null &
    echo $! > "$RUN/webapp-$i.pid"
  fi
done
for i in $(seq "$N"); do
  p=$((BASE + i - 1))
  for _ in $(seq 90); do ready "$p" && break; sleep 1; done
  ready "$p" || { echo "web$i did not become ready; see $LOGS/webapp-$i.log"; exit 1; }
done
targets "$WS/monitoring/targets/cdi-webapp.json" "${mports[@]}"

# ---------------------------------------------------------------- job workers (queue mode)
if [ "$EXEC" = queue ]; then
  C=${CDI_WORKER_CONCURRENCY:-0}
  if [ "$C" = 0 ]; then  # as many jobs at once as there are GPU extraction slots (E2-S2)
    C=$("$PY" -c "from cdi_adapter.webapp.jobs import extract_slots; print(extract_slots())")
  fi
  wports=()
  for i in $(seq "$W"); do
    wp=$((WMBASE + i - 1)); wports+=("$wp")
    alive "$RUN/worker-$i.pid" && continue
    CDI_WORKER_METRICS_PORT=$wp setsid nohup "$PY" -m celery -A cdi_adapter.worker.celery_app \
      worker -Q cdi-web --pool threads --concurrency "$C" -n "web$i@%h" --loglevel INFO \
      --without-gossip --without-mingle >>"$LOGS/worker-$i.log" 2>&1 </dev/null &
    echo $! > "$RUN/worker-$i.pid"
  done
  for i in $(seq "$W"); do
    for _ in $(seq 60); do grep -q "web_runner_started" "$LOGS/worker-$i.log" 2>/dev/null && break; sleep 1; done
    alive "$RUN/worker-$i.pid" || { echo "worker$i did not start; see $LOGS/worker-$i.log"; exit 1; }
  done
  targets "$WS/monitoring/targets/cdi-worker.json" "${wports[@]}"
fi

# ---------------------------------------------------------------- the balancer
export CDI_WEBAPP_PORT=$PORT CDI_LB_BIND=$BIND CDI_LB_ADMIN_PORT=$ADMIN \
  CDI_LB_LOG=$LOGS/balancer.log CDI_WEBAPP_UPSTREAMS="${ups[*]}"
"$CADDY" validate --adapter caddyfile --config "$HERE/Caddyfile.lb" >/dev/null
if curl -fsS -m 3 "http://127.0.0.1:$ADMIN/config/" >/dev/null 2>&1; then
  "$CADDY" reload --adapter caddyfile --config "$HERE/Caddyfile.lb" --address "127.0.0.1:$ADMIN"
else
  if curl -fsS -m 3 "http://127.0.0.1:$PORT/login" >/dev/null 2>&1; then
    echo "port $PORT is held by another process (a single web app from before E10-S9?):"
    echo "  stop it once:  pkill -f 'python -m cdi_adapter.webapp'  then run this again"
    exit 1
  fi
  setsid nohup "$CADDY" run --adapter caddyfile --config "$HERE/Caddyfile.lb" \
    >>"$LOGS/balancer.out" 2>&1 </dev/null &
  echo $! > "$RUN/balancer.pid"
fi
for _ in $(seq 30); do
  curl -fsS "http://127.0.0.1:$PORT/login" 2>/dev/null | grep -q "Agentic EMR generation" && break
  sleep 1
done
curl -fsS "http://127.0.0.1:$PORT/login" | grep -q "Agentic EMR generation" \
  || { echo "balancer on :$PORT is not serving; see $LOGS/balancer.log"; exit 1; }
echo "app tier up: balancer :$PORT -> ${ups[*]} (execution=$EXEC, workers=$([ "$EXEC" = queue ] && echo "$W" || echo 0))"
