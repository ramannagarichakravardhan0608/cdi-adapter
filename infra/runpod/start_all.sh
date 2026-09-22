#!/usr/bin/env bash
# One-command, idempotent lifecycle for the pod or a fallback host (ARCHITECTURE §3.3, §10.1-§10.2;
# backlog E1-S3):
#   bootstrap_pod.sh -> start_mlserve.sh -> webapp (:8888) -> print the live URL.
set -euo pipefail
WS=${WS:-/workspace}
REPO=${REPO:-$WS/cdi}
HERE=$(cd "$(dirname "$0")" && pwd)
. "$HERE/lib.sh"

bash "$HERE/bootstrap_pod.sh"
bash "$HERE/start_mlserve.sh"

set -a; [ -f "$REPO/.env" ] && . "$REPO/.env"; set +a
PORT=${CDI_WEBAPP_PORT:-8888}
if ! curl -fsS "http://127.0.0.1:$PORT/healthz" | grep -q '"db"'; then
  ( cd "$REPO" && setsid nohup "$REPO/.venv/bin/python" -m cdi_adapter.webapp \
      >"$WS/logs/webapp.log" 2>&1 </dev/null & )
  # RunPod's proxy answers HTTP 200 with a fake 502 page for unbound ports (§10.4): check a
  # content marker, never just the status code. /login: the one public page (E11-S1).
  for _ in $(seq 90); do
    curl -fsS "http://127.0.0.1:$PORT/login" 2>/dev/null | grep -q "Agentic EMR generation" && break
    sleep 1
  done
fi
curl -fsS "http://127.0.0.1:$PORT/login" | grep -q "Agentic EMR generation" \
  || { echo "webapp did not come up; see $WS/logs/webapp.log"; exit 1; }
echo "LIVE URL: $(live_url "$PORT")"
# E11-S1: nobody can sign in until the first account exists
USERS=$( cd "$REPO" && "$REPO/.venv/bin/python" -c \
  "from cdi_adapter.db import session_scope; from sqlalchemy import text
with session_scope() as c: print(c.execute(text('SELECT count(*) FROM web_user WHERE active')).scalar_one())" \
  2>/dev/null || echo "?")
if [ "$USERS" = 0 ]; then
  echo "WARNING: no staff accounts yet - create the first admin (docs/auth.md):"
  echo "  $REPO/.venv/bin/python -m cdi_adapter.webapp.users add <name> --name \"<Full Name>\" --role admin"
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
