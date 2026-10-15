#!/usr/bin/env bash
# One-command, idempotent lifecycle for the pod or a fallback host (ARCHITECTURE §3.3, §10.1-§10.2;
# backlog E1-S3):
#   bootstrap_pod.sh -> start_mlserve.sh -> start_apptier.sh (balancer :8888 -> web app instances
#   + job workers, backlog E10-S9) -> print the live URL.
set -euo pipefail
WS=${WS:-/workspace}
REPO=${REPO:-$WS/cdi}
HERE=$(cd "$(dirname "$0")" && pwd)
. "$HERE/lib.sh"

bash "$HERE/bootstrap_pod.sh"
# ENT-S4 AC1: the database is back from the dump and nothing writes to it yet - the one moment
# its row counts can be compared with what prepare_stop.sh recorded before the stop. A
# difference is said loudly and kept in the log; the services are started all the same,
# because a clinic without the screen is worse than a clinic told to check a count.
if [ -f "$WS/backup/counts.before.json" ]; then
  if ( cd "$REPO" && "$REPO/.venv/bin/python" -m cdi_adapter.dr counts \
         --compare "$WS/backup/counts.before.json" --report "$WS/logs/restart_check.json" ); then
    mv "$WS/backup/counts.before.json" "$WS/backup/counts.checked.json"
  else
    echo "RESTART CHECK FAILED: row counts differ from before the stop (see $WS/logs/restart_check.json, docs/RUNBOOK.md)"
  fi
else
  echo "restart check: no counts recorded before the stop (prepare_stop.sh was not run) - nothing to compare"
fi
# SW-S7: the swap gate. Nothing the last accepted build did not have - a package, a server, a
# model - is started unless its licence passes (or a decision is recorded). A blocked gate stops
# the deploy here, before a model is loaded; the champion's own licence and checksum are checked
# again by the model server when it starts (models/registry.json).
( cd "$REPO" && "$REPO/.venv/bin/python" scripts/licence_audit.py swap-gate ) \
  || { echo "SWAP GATE BLOCKED: this build adds a component whose licence is not cleared" \
            "(python scripts/licence_audit.py swap-gate)"; exit 1; }
bash "$HERE/start_mlserve.sh"

set -a; [ -f "$REPO/.env" ] && . "$REPO/.env"; set +a
PORT=${CDI_WEBAPP_PORT:-8888}
# E10-S9: web app instances + job workers behind the balancer on $PORT (docs/ha.md "Application
# tier"). Ready = the /login content marker through the balancer, never just the status code:
# RunPod's proxy answers HTTP 200 with a fake 502 page for unbound ports (§10.4).
bash "$HERE/start_apptier.sh"
curl -fsS "http://127.0.0.1:$PORT/login" | grep -q "Agentic EMR generation" \
  || { echo "the balancer on :$PORT is not serving; see $WS/logs/balancer.log"; exit 1; }
echo "LIVE URL: $(live_url "$PORT")"
# ENT-S4 AC1: every part, by name. The model server may still be loading its weights.
for _ in $(seq 60); do
  HEALTH=$(curl -fsS "http://127.0.0.1:$PORT/healthz" 2>/dev/null || curl -sS "http://127.0.0.1:$PORT/healthz" 2>/dev/null || true)
  case "$HEALTH" in *'"status":"ok"'*) break ;; esac
  sleep 5
done
echo "HEALTH: ${HEALTH:-no answer}"
case "$HEALTH" in *'"status":"ok"'*) ;; *) echo "WARNING: not every part is healthy yet (docs/RUNBOOK.md)" ;; esac
# E11-S1: nobody can sign in until the first account exists
USERS=$( cd "$REPO" && "$REPO/.venv/bin/python" -c \
  "from cdi_adapter.db import session_scope; from sqlalchemy import text
with session_scope() as c: print(c.execute(text('SELECT count(*) FROM web_user WHERE active')).scalar_one())" \
  2>/dev/null || echo "?")
if [ "$USERS" = 0 ]; then
  echo "WARNING: no staff accounts yet - create the first admin (docs/auth.md):"
  echo "  $REPO/.venv/bin/python -m cdi_adapter.webapp.users add <name> --name \"<Full Name>\" --role admin"
fi

# LS-S9: after every pod start the file listener's retry agent comes back by itself, and the
# listener too where CDI_START_LISTENER=1 (a pod that watches a folder). Their state is in the
# database and on the drive, so a restart loses no file: one that was being read is picked up
# again when its lease runs out.
if [ "${CDI_START_LISTENER:-0}" = 1 ]; then
  bash "$HERE/start_listener.sh" || echo "WARNING: the file listener did not start (docs/LISTENER.md)"
else
  bash "$HERE/start_listener.sh" recover || echo "WARNING: the listener's retry agent did not start"
fi

# the stable address staff use (E1-S9): this pod dials the ingress VM, whatever its own URL is
bash "$HERE/start_tunnel.sh"
if [ -n "${CDI_INGRESS_HOSTNAME:-}" ] && [ -n "${CDI_INGRESS_SSH:-}" ]; then
  COUNTER="https://$CDI_INGRESS_HOSTNAME"
  for _ in $(seq 30); do
    "$REPO/.venv/bin/python" "$REPO/scripts/check_ingress.py" "$COUNTER" >/dev/null 2>&1 && break
    sleep 2
  done
  if STATE=$("$REPO/.venv/bin/python" "$REPO/scripts/check_ingress.py" "$COUNTER"); then
    echo "COUNTER URL: $COUNTER   (address of record; the LIVE URL above is this pod only)"
  else
    echo "WARNING: COUNTER URL $COUNTER is not serving yet: $STATE (see $WS/logs/tunnel.log, docs/ingress.md)"
  fi
fi
if [ "$(host_kind)" = fallback ]; then
  # staff sign in (E11-S1), but the app binds 0.0.0.0: keep it off the open internet anyway
  echo "WARNING: fallback host - allow port $PORT from the clinic network / VPN only (docs/fallback-host.md)"
fi
