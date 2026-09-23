#!/usr/bin/env bash
# Start the model gateway on localhost:8077 (ARCHITECTURE §3.4). The model lazy-loads on the first
# request (~35 s); weights are cached in /workspace/hf-cache.
set -euo pipefail
WS=${WS:-/workspace}
REPO=${REPO:-$WS/cdi}
set -a; [ -f "$REPO/.env" ] && . "$REPO/.env"; set +a
PORT=${CDI_MLSERVE_PORT:-8077}
export HF_HOME=${HF_HOME:-$WS/hf-cache}

if curl -fsS "http://127.0.0.1:$PORT/healthz" | grep -q '"status":"ok"'; then
  echo "mlserve already running"; exit 0
fi
# the vllm backend runs from its own venv (backlog E2-S1: vLLM on torch 2.8 needs transformers < 5)
PY=$REPO/.venv/bin/python
if [ "${CDI_MLSERVE_BACKEND:-stub}" = "vllm" ]; then PY=$REPO/.venv-vllm/bin/python; fi
# Background the command itself, not a `cd && ...` list: a backgrounded list runs in a subshell that
# keeps this script's stdout open for the server's lifetime, so a caller reading our output (a
# notebook cell, `$(...)`, a pipe) never sees EOF and hangs.
cd "$REPO"
setsid nohup "$PY" -m cdi_adapter.mlserve >"$WS/logs/mlserve.log" 2>&1 </dev/null &
for _ in $(seq 60); do
  curl -fsS "http://127.0.0.1:$PORT/healthz" 2>/dev/null | grep -q '"status":"ok"' && { echo "mlserve up"; exit 0; }
  sleep 1
done
echo "mlserve did not start; see $WS/logs/mlserve.log"; exit 1
