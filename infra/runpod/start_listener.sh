#!/usr/bin/env bash
# The file listener and its retry agent (backlog LS, docs/LISTENER.md): two processes that share
# the listener_file table.
#   listener          - takes each new file from the watched folder and has it read (LS-S1..S6)
#   listener-recover  - puts a failed file back in the inbox when its wait is over (LS-S7)
# Idempotent: a process that is already running is left alone. Called by start_all.sh on every
# pod start (LS-S9): the retry agent always, the listener only when CDI_START_LISTENER=1. The
# drive and folder come from the CDI_LISTENER_* settings in .env.
#
#   bash infra/runpod/start_listener.sh            # start both
#   bash infra/runpod/start_listener.sh recover    # start the retry agent only
#   bash infra/runpod/start_listener.sh stop       # stop both, gently
#   bash infra/runpod/start_listener.sh status
set -euo pipefail
WS=${WS:-/workspace}
REPO=${REPO:-$WS/cdi}
RUN=$WS/run
LOGS=$WS/logs
mkdir -p "$RUN" "$LOGS"
set -a; [ -f "$REPO/.env" ] && . "$REPO/.env"; set +a
PY="$REPO/.venv/bin/python"

running() { [ -f "$RUN/$1.pid" ] && kill -0 "$(cat "$RUN/$1.pid")" 2>/dev/null; }

# Stop gently (LS-S9): SIGTERM asks the process to finish its running batch and exit. It is given
# the length of a lease to do so; only then is it killed - and the file it held is recovered by
# the lease, like any file whose worker died.
stop() {
  for name in listener listener-recover; do
    if running "$name"; then
      pid=$(cat "$RUN/$name.pid")
      kill -TERM "$pid"
      for _ in $(seq "${CDI_LISTENER_LEASE_SECONDS:-900}"); do
        kill -0 "$pid" 2>/dev/null || break
        sleep 1
      done
      if kill -0 "$pid" 2>/dev/null; then kill -KILL "$pid"; echo "$name killed (it did not stop)";
      else echo "$name stopped"; fi
    fi
    rm -f "$RUN/$name.pid"
  done
}

status() {
  for name in listener listener-recover; do
    if running "$name"; then echo "$name  pid $(cat "$RUN/$name.pid")  up"; else echo "$name  DOWN"; fi
  done
}

start() {  # start <name> <log> <command...>
  local name=$1 logfile=$2; shift 2
  if running "$name"; then echo "$name already running (pid $(cat "$RUN/$name.pid"))"; return; fi
  # no "setsid -f": that forks, and $! would be the id of a process that has already gone -
  # status then says DOWN and stop stops nothing (found on the pod, LS-S10)
  # Nor "cd && ... &": that puts a shell in the background with python under it, and the id
  # written down is the shell's - a stop then ends the shell and leaves the listener running.
  ( cd "$REPO" || exit 1
    PYTHONUNBUFFERED=1 setsid nohup "$PY" -m cdi_adapter.listener "$@" \
      >> "$LOGS/$logfile" 2>&1 </dev/null &
    echo $! > "$RUN/$name.pid" )
  sleep 1
  echo "$name started (pid $(cat "$RUN/$name.pid")), log $LOGS/$logfile"
}

case "${1:-start}" in
  stop) stop; exit 0 ;;
  status) status; exit 0 ;;
  recover)
    start listener-recover listener-recover.log recover   # python -m cdi_adapter.listener recover
    exit 0 ;;
esac

# the folder must be ready before anything watches it: sign-in, the six folders
( cd "$REPO" && "$PY" -m cdi_adapter.listener check ) || {
  echo "the listener's folder is not ready (see above); nothing was started"; exit 1; }
start listener listener.log run                           # python -m cdi_adapter.listener run
start listener-recover listener-recover.log recover       # python -m cdi_adapter.listener recover
