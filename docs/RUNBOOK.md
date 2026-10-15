# Runbook: start, stop, restart, restore (backlog ENT-S4)

One page for the person operating the pod. `WS` is `/workspace`, `REPO` is `/workspace/cdi`.
Every alert has its own steps in [alerts.md](alerts.md); backups and the restore drill are
explained in [dr.md](dr.md).

## What lives where

| Survives a restart (`/workspace`) | Rebuilt on every start (the overlay) |
|---|---|
| the scans and pages (`minio-data`), the backups (`backup/`), Redis (`redis/`), the model cache, the code and its environment, the logs | the operating-system packages, the Postgres data folder (restored from the newest dump), the crontab |

So a restart **never loses a scan**. It can lose database rows written after the newest dump —
at most about five minutes of them, and none at all after a planned stop.

## Start

```bash
bash /workspace/cdi/infra/runpod/start_all.sh
```

One command, safe to run again at any time. It installs what the overlay lost, restores the
database from the newest dump, applies migrations, installs the 5-minute backup job, starts the
model server, the web app and the workers, and prints:

- `restart check: … IDENTICAL` — the row counts equal those written down before the stop;
- `LIVE URL: …` — where the screen is;
- `HEALTH: {"status":"ok", …}` — every part by name.

## Stop (planned)

```bash
bash /workspace/cdi/infra/runpod/prepare_stop.sh
```

It stops what writes (balancer, web app, workers, listener), takes a snapshot, and writes down
the row counts. When it prints `SAFE TO STOP`, stop the pod. Documents that were being read are
not lost: their jobs are in the database and continue after the start.

## Restart

`prepare_stop.sh`, stop the pod, start the pod, `start_all.sh`. Read the three lines above.

| You see | It means | Do |
|---|---|---|
| `restart check: … IDENTICAL` | nothing was lost | nothing |
| `restart check: no counts recorded before the stop` | the stop was not prepared (abrupt) | check the time of the restored dump: `python -m cdi_adapter.dr status` — rows after it are gone |
| `RESTART CHECK FAILED` | a table has other counts than before | read `$WS/logs/restart_check.json`; if rows are missing, restore an older dump (below) and tell the clinic which uploads to send again |
| `HEALTH` not `ok` | a part is down | the part named `false`: see the table below |

## Health, part by part

`curl -s http://127.0.0.1:8888/healthz` (public; booleans only).

| Part | Key | If `false` |
|---|---|---|
| Database | `db` | `pg_lsclusters`; `tail $WS/logs/postgres*.log`; run `start_all.sh` again |
| Document store | `s3` | `tail $WS/logs/minio.log`; [CdiMinioDegraded](alerts.md#cdiminiodegraded) |
| Queue | `queue` | `redis-cli ping`; `tail $WS/logs/redis.log` |
| Model server | `mlserve` | `bash infra/runpod/start_mlserve.sh`; [CdiModelServerDown](alerts.md#cdimodelserverdown) |
| OCR host | `ocr_host` | only shown where one is configured; without it pages are read in-process |

Numbers for dashboards and alerts are on `/metrics` of each process (Prometheus format):
documents and results per hour (`cdi_results_total`), seconds per result (`cdi_result_seconds`),
late results (`cdi_results_late_total`), queue length (`cdi_extract_waiting`), GPU memory
(`cdi_gpu_memory_used_bytes`), failures (`cdi_stage_failures_total`), disk (`cdi_disk_free_ratio`),
backups (`cdi_backup_last_ok`).

## Restore

The newest dump is restored by `start_all.sh` by itself. To go back to an **older** one:

```bash
ls -1t /workspace/backup/cdi.*Z.dump                 # the last 10, newest first
bash /workspace/cdi/infra/runpod/prepare_stop.sh     # stop the writers
cp /workspace/backup/cdi.<STAMP>.dump /workspace/backup/cdi.dump
# stop and start the pod (or drop and re-create the database), then:
bash /workspace/cdi/infra/runpod/start_all.sh
```

The restore order is fixed and is not to be changed: Postgres from the dump, then migrations,
then the services. The scans need no restore — they never left the persistent disk; the copy in
`backup/objects/` is for a lost disk (see [dr.md](dr.md)).

Prove a restore works without touching the live data: `python -m cdi_adapter.dr drill`
(it runs every Sunday by itself and feeds [CdiRestoreDrillFailed](alerts.md#cdirestoredrillfailed)).

## Backups

Every 5 minutes (`crontab -l`): the database dump, checked that it can be listed, and every new
scan copied and hash-checked. `python -m cdi_adapter.dr status` shows the last run.
A failed run raises [CdiBackupFailed](alerts.md#cdibackupfailed) at the next check; no good
backup for 15 minutes raises [CdiBackupStale](alerts.md#cdibackupstale).

## When something is wrong

1. The banner on every page lists the alerts firing now; each links to its steps in
   [alerts.md](alerts.md).
2. Never lower a threshold, accept values or skip review to clear a queue.
3. A bad model version: roll it back with the model registry (`docs/model-rollout.md`), not by
   editing settings on the pod.
