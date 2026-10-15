#!/usr/bin/env bash
# Before a PLANNED stop or restart of the pod (backlog ENT-S4; docs/RUNBOOK.md).
#
#   1. stop what writes: the balancer, the web app instances, the job workers, the listener
#      (documents still being read are not lost: their jobs are in the database and are taken
#      up again after the start - OUT-S3);
#   2. take a snapshot now: the database dump and every new scan, as cron does every 5 minutes;
#   3. write down the row counts of every table, the number of scans and of queue keys.
#
# After the restart, start_all.sh restores the dump and compares the counts before anything
# writes again. The model server, Postgres, Redis and MinIO are left running: stopping the pod
# stops them, and the snapshot needs Postgres and MinIO up.
#
# An ABRUPT stop skips all this: then the newest 5-minute snapshot is restored, and up to about
# five minutes of database rows are lost (the scans are not - they are on the persistent disk).
set -euo pipefail
WS=${WS:-/workspace}
REPO=${REPO:-$WS/cdi}
RUN=${CDI_RUN_DIR:-$WS/run}
HERE=$(cd "$(dirname "$0")" && pwd)
PY="$REPO/.venv/bin/python"
set -a; [ -f "$REPO/.env" ] && . "$REPO/.env"; set +a

echo "1. stopping the writers"
stopped=0
for pid_file in "$RUN"/balancer.pid "$RUN"/webapp-*.pid "$RUN"/worker-*.pid "$RUN"/gpu-*.pid \
                "$RUN"/listener*.pid; do
  [ -f "$pid_file" ] || continue
  pid=$(cat "$pid_file" 2>/dev/null || true)
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null || true
    stopped=$((stopped + 1))
  fi
done
# a worker finishes the step it is in; wait for them, then stop asking
for _ in $(seq 30); do
  alive=0
  for pid_file in "$RUN"/webapp-*.pid "$RUN"/worker-*.pid "$RUN"/gpu-*.pid; do
    [ -f "$pid_file" ] || continue
    pid=$(cat "$pid_file" 2>/dev/null || true)
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then alive=$((alive + 1)); fi
  done
  [ "$alive" = 0 ] && break
  sleep 2
done
echo "   $stopped process(es) asked to stop; $alive still running"
if [ "$alive" != 0 ]; then
  echo "   WARNING: a writer is still running - the counts below may differ from the snapshot"
fi

echo "2. snapshot"
mkdir -p "$WS/backup" "$WS/logs"
flock "$WS/backup/.snapshot.lock" bash "$HERE/snapshot.sh"

echo "3. row counts"
( cd "$REPO" && "$PY" -m cdi_adapter.dr counts --save "$WS/backup/counts.before.json" )
rm -f "$WS/backup/counts.checked.json"

echo "SAFE TO STOP. After the restart:  bash $REPO/infra/runpod/start_all.sh"
