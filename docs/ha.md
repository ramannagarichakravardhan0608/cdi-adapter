# HA: the data tier (backlog E10-S6) and the application tier (backlog E10-S9)

Before E10-S6 the only copy of the database was a `pg_dump` every 15 minutes (RPO 15 min), and the
scans lived on one MinIO process. Now:

| | What protects it | RPO | Commands |
|---|---|---|---|
| Postgres | a hot standby, streaming WAL through a replication slot | the replication lag, normally under 1 s (alert at 30 s) | `python -m cdi_adapter.ha pg …` |
| MinIO (scans, pages) | erasure coding over 4 drives (2 data + 2 parity shards): one host with 4 drives, or 4 hosts | 0: any one drive (or, across hosts, one host) can be lost, writes continue | `python -m cdi_adapter.ha minio …` |

Code: `src/cdi_adapter/ha/`. Alerts: `CdiPgReplicationLag`, `CdiPgStandbyDisconnected`,
`CdiMinioDegraded` (`docs/alerts.md`). Grafana: "Postgres standby" and "MinIO" panels.

## What one host can and cannot cover

The pod (or Colab) is one machine. A standby and an erasure-coded MinIO on it cover a **crashed
or damaged Postgres and a lost or corrupted MinIO drive**, and that is what the drills prove there.
They do **not** cover losing the whole host. For that, the standby and the MinIO nodes must run on
other hosts (the E1-S3 fallback host, or more India VMs).

- **MinIO won't do multi-node on loopback in a container.** At the pinned release
  (`cmd/endpoint.go`), when `/.dockerenv` exists, as on the pod and Colab, MinIO never treats a
  `127.0.0.1` address as a node. §12 keeps every service on loopback. So "several MinIO nodes on
  one pod" is impossible by design, and node loss needs real hosts on a private network.
- **The code is host-agnostic:** `CDI_MINIO_MODE=cluster` with `CDI_MINIO_ADDRESSES`.
- **Encryption between hosts:** Postgres replication and MinIO traffic between hosts must be
  encrypted (an SSH tunnel as in E1-S9, or TLS), because the pod exposes nothing publicly.

## Postgres: primary + hot standby

Turn it on in `.env` (the bootstrap does the rest on every boot):

```bash
CDI_PG_STANDBY_PORT=5433                         # standby on this host; its data: $PGDATA-standby
CDI_DATABASE_HOSTS=127.0.0.1:5432,127.0.0.1:5433 # the app connects to whichever is writable
```

- `pg setup-standby`:
  - creates the `cdi_repl` replication role (password: `cdi_replication_password` in the secrets
    store) and the slot `cdi_standby`, and grants the app role `pg_monitor` for the metrics;
  - builds the standby with `pg_basebackup -R` and starts it.

  The slot keeps WAL on the primary until the standby has it, so a standby that was away catches
  up instead of needing a rebuild. The replication password is kept in `.pgpass-replication`
  (0600, owned by `postgres`) next to the data folders, because the standby server reads it
  itself. Rerun `pg setup-standby` after rotating that password: once the standby is built, it
  only re-applies the password.
- `pg status`: role, `receiving`, `replay_lag_s`, `bytes_behind` and slots, for both servers.

### Failover (the primary is gone or broken)

```bash
python -m cdi_adapter.ha pg status        # 1. is the standby receiving? how far behind?
python -m cdi_adapter.ha pg failover      # 2. fence the primary, promote the standby
python -m cdi_adapter.ha pg status        # 3. the standby says role=primary
```

- **Fence first:** the old primary is stopped and gets `cdi.fenced` in its data folder, and
  `bootstrap_pod.sh` refuses to start a fenced folder. Two writable primaries (split brain) cannot
  come out of these commands. If the old primary's host is unreachable, the fence file cannot be
  written: **that host must not be started again** before `rejoin`.
- **The app needs nothing.** `CDI_DATABASE_HOSTS` + `target_session_attrs=read-write` make new
  connections go to the promoted server; `pool_pre_ping` drops connections to the dead one. Work in
  flight at the crash fails once and is retried like any other job failure.
- **What can be lost:** commits the standby had not received yet (the lag in step 1). The drill
  measures this.

### Rejoin (the old primary comes back as the new standby)

```bash
python -m cdi_adapter.ha pg rejoin        # old folder kept aside as <data>.old-<time>, rebuilt from the new primary
```

After a failover the roles are swapped: `failover --to primary` and `rejoin --old standby` do the
same in the other direction.

## MinIO: erasure coding

```bash
# one host, 4 drives (a lost or corrupted drive): best with 4 real disks mounted at d1..d4
CDI_MINIO_MODE=erasure
CDI_MINIO_ADDRESSES=127.0.0.1:9000        # the app's CDI_S3_ENDPOINT_URL stays http://127.0.0.1:9000
CDI_MINIO_DRIVES=4                        # $WS/minio-erasure/d1..d4 (CDI_MINIO_DIR)

# several hosts (a lost host), on each host, CDI_MINIO_NODE = its place in the list
CDI_MINIO_MODE=cluster
CDI_MINIO_ADDRESSES=10.0.0.1:9000,10.0.0.2:9000,10.0.0.3:9000,10.0.0.4:9000
CDI_MINIO_NODE=1
CDI_S3_ENDPOINT_URL=http://10.0.0.1:9000,http://10.0.0.2:9000,http://10.0.0.3:9000,http://10.0.0.4:9000
```

- The bootstrap starts this host's MinIO process (`minio start`; root credentials from the secrets
  store, in the environment only). It also publishes MinIO's drive gauges on loopback for the web
  app's collector (`cdi_minio_drives_offline`).
- With several hosts, the app's `ObjectStore` gets one client per host. A host that does not
  answer (or answers 503 without quorum) is skipped, and the next one takes the call.
- **Moving from the single-drive store** (once): start the erasure set on a spare port
  (`CDI_MINIO_ADDRESSES=127.0.0.1:9010 python -m cdi_adapter.ha minio start`), then run

  ```bash
  python -m cdi_adapter.ha minio migrate --from http://127.0.0.1:9000
  ```

  It copies every object and reads each copy back. Then stop the old store and start the erasure
  set on 9000.
- **A drive lost:** put an empty drive (or folder) at the same path. MinIO formats and heals it.
- **MooseFS on the pod:** all 4 folders on one MooseFS volume protect against a corrupted folder
  and bit rot, not a failed disk. Real disk loss needs separate disks or hosts.

## Drills (backlog E10-S6 "Tests")

```bash
python -m cdi_adapter.ha pg drill --bindir /usr/lib/postgresql/16/bin   # PG FAILOVER DRILL: PASS
python -m cdi_adapter.ha minio drill --binary $WS/tools/minio-<tag>     # MINIO DRIVE-LOSS DRILL: PASS
```

Both run on throwaway servers (their own folders and ports), never the app's data.

- **Postgres:**
  1. A writer commits a numbered row 25 times a second through the app's own connection code
     (`engine_url`).
  2. The primary is **crashed** (`pg_ctl stop -m immediate`), the standby is promoted, and the
     writer carries on by itself.
  3. The drill reports acknowledged commits lost (RPO) and time to the next commit (RTO).
  4. It checks the old primary was fenced, then rejoins it and checks that new rows reach it.
- **MinIO (one host):**
  1. Objects of 1 KB to 1.5 MB are written through the app's `ObjectStore` to an erasure set of
     4 drives.
  2. **Drive 1 is deleted while MinIO runs.**
  3. Every object must read back byte-identical, the set must stay writable, and new objects must
     be written.
  4. An empty replacement drive goes in, and MinIO must heal it with every object (including those
     written while it was gone). No object may be lost.
  5. A drill on a single-disk host (Colab) lifts MinIO's root-disk refusal with its
     `MINIO_ROOTDISK_THRESHOLD_SIZE` setting. The pod's `/workspace` is not the root disk.

  The **node-loss** drill (a whole MinIO host lost) needs MinIO on separate hosts: see above.

`tests/test_ha_integration.py` runs the Postgres drill with real PostgreSQL 16 servers, and
`tests/test_ha_unit.py` covers the rest.

## Application tier: several web app instances, durable jobs (backlog E10-S9)

Before E10-S9 there was one web app process, and each upload's job lived in its memory: killing
it lost every job it was running, and a second copy could not see the first one's jobs. Now:

```
RunPod proxy / E1-S9 tunnel --> Caddy balancer :8888 (infra/runpod/Caddyfile.lb)
    --> web app web1 127.0.0.1:8881, web2 127.0.0.1:8882, ...   stateless: any one serves any request
          upload: files -> MinIO uploads/<job>/<n>, job -> web_job, THEN 202, then dispatch
    --> Redis queue cdi-web --> CPU pool worker1, worker2 (Celery, threads pool)
          claim the job with a lease, run Phase 1 of each document, save each step to web_job
    --> Redis queue cdi-gpu --> GPU pool gpu1 (E9-S2): claims the job again and extracts
          the CPU pool gives its lease up at the phase boundary; nothing is OCR'd twice
    --> model gateways: one or more replicas per model version (CDI_MLSERVE_URL, comma separated)
```

| What dies | What happens | Lost |
|---|---|---|
| A web app instance | The balancer takes it out on the first failed connection (passive check) or the next `/readyz` check (every 2 s, 2 s timeout), whichever comes first. A request whose connection fails goes to another instance. | Only an upload that was still being sent to it: not acknowledged, so the counter sees an error and sends it again. **An acknowledged upload is already in MinIO + PostgreSQL.** |
| A job worker (either pool) | Its leases stop being renewed. After `CDI_JOB_LEASE_S` (30 s) another worker's sweep (every 10 s) claims each of its jobs and resumes it: finished documents stay, a document past extraction continues from there, one through Phase 1 continues at extraction (E9-S2), the others run again from the staged files. A CPU-pool sweep that finds a job with only extraction left sends it to `cdi-gpu` instead of running it. | Nothing. At most the one stage each document was in runs again. |
| A model gateway replica | The next call to it fails to connect; the call goes to the next replica, and the dead one is skipped for `CDI_MLSERVE_DOWN_S` (15 s). | Nothing: the document's call is answered by another replica. |
| The queue message (Redis restarted) | A job nobody claimed `CDI_JOB_UNCLAIMED_S` (120 s) after its upload is claimed by a sweep. | Nothing. |

Start, repair, look:

```bash
bash infra/runpod/start_apptier.sh           # starts what is down (start_all.sh calls it)
bash infra/runpod/start_apptier.sh status    # each instance, worker, and the balancer's upstreams
python scripts/app_ha_drill.py               # the kill-instance drill under load (below)
```

Settings (`.env.runpod`): `CDI_WEBAPP_INSTANCES` (2), `CDI_WEBAPP_BASE_PORT` (8881),
`CDI_JOB_EXECUTION=queue`, `CDI_WORKERS` (2) and `CDI_WORKER_CPU_CONCURRENCY` (0 = `CDI_JOB_MAX_WORKERS`)
for the CPU pool, `CDI_GPU_WORKERS` (1) and `CDI_WORKER_GPU_CONCURRENCY` (0 = the GPU extraction
slots, E2-S2) for the GPU pool (E9-S2; `CDI_WORKER_CONCURRENCY` still overrides the CPU pool's
concurrency), `CDI_JOB_LEASE_S` (30), `CDI_JOB_UNCLAIMED_S` (120), `CDI_JOB_MAX_ATTEMPTS` (3),
`CDI_MIGRATION_DRAIN_S` (120), `CDI_MIGRATION_HOLD_S` (900), `CDI_MIGRATION_STAGE_STALE_S` (600),
`CDI_MLSERVE_DOWN_S` (15). Several gateway replicas: list them in `CDI_MLSERVE_URL`, or give a
rollout side several URLs (`python -m cdi_adapter.modelops serve <version> --url
http://127.0.0.1:8077,http://127.0.0.1:8078`, E10-S5); every replica must serve the version.

How the pieces were chosen (from the architecture; owner delegated the choice):

- **Caddy as the balancer.** It is already the architecture's ingress (E1-S9), pinned at the same
  release (`CADDY_VERSION`, checksum-verified). Native process, no Docker (§3.3). The instances bind
  127.0.0.1 only; the balancer holds the one public port the single web app used to hold (§12).
- **Celery + Redis for the jobs** (the architecture's queue, §13 / §14 #8; the core of E9-S1),
  with the job itself in PostgreSQL. The queue message only says "run job X", so a lost or
  duplicated message changes nothing: the claim decides who runs a job.
- **A lease, not a lock held by a connection.** A lease survives a database failover (E10-S6); a
  runner that cannot renew it stops at its next step boundary and never finishes the job twice.
- **Ingest is one-at-a-time per file hash** (a transaction-scoped advisory lock), so a resumed
  job and a second upload of the same scan cannot render its pages at the same moment, and a
  document an interrupted ingest left without pages is rendered again.

Limits (what this block does not cover):

- **A frozen runner.** A process that is stopped (not killed) for longer than a lease, then
  continues, can finish the one stage it was in before it notices the take-over at its next step
  boundary. OCR and extraction replace their earlier output, so the document's results stay
  single, but its review item may have to be rebuilt. A killed or crashed process has no such
  window. Fencing every stage's own writes is not in this block.
- **The same upload sent twice** (the counter retries after a timeout that did get through)
  makes two jobs; the documents are shared (ingest dedupes by SHA-256). Idempotency keys are E9-S3.
- **Losing the whole host.** Every instance and worker runs on the pod; losing the pod is the
  data tier's "second host" (above) plus E1-S3.

### Drill (backlog E10-S9 "Tests": kill-instance drill under load)

`python scripts/app_ha_drill.py` (on the pod / fallback host, app tier up) uploads 12 synthetic
patients (the E1-S7 corpus) through the balancer over 20 s. Mid-burst it SIGKILLs web app instance
1, then job worker 1 while it runs jobs (`--kill-gateway`: also one model gateway replica). Then it
checks from PostgreSQL: **zero lost acknowledged uploads** (every 202 job finished, no document left
unprocessed), the killed instance **left rotation within the health-check window** (the
balancer's own log line, interval + timeout = 4 s), no document processed twice (one extraction row
and one open review item per document), and which jobs were taken over. Finally it restarts what it
killed and checks the instance is back in rotation. Report: `$WS/logs/ha/last-app-drill.json`.

Tests: `tests/test_apptier_unit.py` (lease, resume, take-over, the sweep, replica failover,
`/readyz`, the balancer config); `tests/test_apptier_integration.py` (the lease rules on
PostgreSQL; an upload MinIO cannot store is refused with 503 and leaves nothing; a runner killed
inside an extraction is taken over on real PostgreSQL + MinIO with no loss and no doubles; the
ingest re-render).

### A schema migration with the cluster up (backlog E9-S1)

A forward-incompatible migration — a renamed or dropped column, a new constraint — breaks the SQL
of the workers that are already running, and the cluster is not stopped for it: uploads keep
arriving and `web_job` keeps jobs no runner has finished. So a migration is applied **through the
gate**, never with bare `alembic upgrade`:

```bash
make schema-status                 # python -m cdi_adapter.migration status
make migrate-live                  # upgrade head, with the queue held
make migrate-rollback DOWN=0020    # the same, the other way
```

What `cdi_adapter.migration` does (migration 0021; the module docstring has the detail):

1. Takes a PostgreSQL advisory lock, so there is one migrator.
2. Closes `schema_gate`. From here no runner claims a **new** job — the claim and the orphan sweep
   in `webapp/jobstore.py` both refuse while it is closed — so the queue holds still instead of
   being run against a schema the code does not match. `expires_at` bounds the hold: a migrator
   that dies gates nothing for longer than `CDI_MIGRATION_HOLD_S`.
3. Waits (`CDI_MIGRATION_DRAIN_S`) for everything in flight to end: web jobs with a live lease,
   and pipeline stages still running (the folder-watch path holds no lease, so an open
   `pipeline_run` stands for a worker mid-document; one older than
   `CDI_MIGRATION_STAGE_STALE_S` is a dead worker, not a live one). **Jobs in flight are never
   interrupted** and keep their lease. If it does not drain, nothing is applied: the gate opens
   again and the schema is untouched (`--force` applies it anyway, for a cluster already stopped).
4. Runs Alembic, then opens the gate — also when the migration fails or is rolled back; it is
   never left closed. The waiting jobs are claimed by the next sweep, each exactly once, because
   the claim is still the only thing that decides who runs a job.

Each window writes a `migration_run` row (direction, revisions, what was in flight, whether it
drained, outcome), so what happened to the queue during a migration is on the record. A migrator
killed mid-window: `python -m cdi_adapter.migration open-gate` releases the queue at once, and the
hold would have expired by itself anyway.

Still the operator's job: writing migrations **expand/contract** (add, backfill, then drop in a
later release) is what keeps a rolling deploy possible at all; the gate makes the one
incompatible step safe, it does not make an incompatible schema compatible.

Tests: `tests/test_migration_integration.py` — a real forward-incompatible Alembic revision
applied and rolled back on PostgreSQL with three jobs queued and one in flight, then each queued
job runs exactly once; a migration with a job in flight is aborted and changes nothing; one
migrator at a time; a closed gate stops new claims but never takes a job from a live runner; an
expired hold gates nothing.

### One upload, one job; and what stops being retried (backlog E9-S3)

Two ways work used to be able to go wrong on a busy queue, both closed by migration 0022:

**A retried upload must not become a second job.** `§5 S1`'s SHA-256 ingest dedupe already shares
the *documents* of two identical uploads, but each POST still made its own job — so a counter whose
`POST /api/jobs` timed out and was sent again produced two jobs, two reviews and two FHIR bundles
for one visit. Every accepted upload now carries an **idempotency key** in
`web_job.idempotency_key`, under a unique index:

- the client may name its retry with an `Idempotency-Key` header (namespaced by the account, so
  two users cannot collide on the same string);
- with no header the key is **derived from the upload** — every file's SHA-256 and name, in the
  order sent, plus the actor and the patient context — so an unchanged retry lands on the job it
  already created without the client having to do anything;
- `jobstore.submit` looks the key up first: a hit returns the first job and stages, inserts and
  dispatches nothing. When both copies arrive at the same moment, both stage and the unique index
  decides — the loser drops its staged files and answers with the winner's job.

The same scan sent for a different patient, or by a different user, is still its own job.
`cdi_job_duplicate_uploads_total` counts the hits.

**Work that keeps failing must leave the queue.** A poison document (page 2 is not a PDF, a scan
the renderer cannot read) used to end as a bare `MaxRetriesExceededError` with nothing to look at,
and a job that kept killing its runner was given up in a log line only. Both now land in
**`dead_letter`** — one row per `(kind, ref)`, with the attempt count, the last error, the queue
and task, and the documents' own errors:

- a web job past `CDI_JOB_MAX_ATTEMPTS` is dead-lettered and finished, so no sweep picks it up
  again (`jobstore.PgJobStore.dead_letter`, `web_job.dead_lettered_at`);
- a pipeline stage that exhausts `MAX_RETRIES` records the document and then raises its own
  error, so the chain stops instead of carrying a broken document forward (`worker._retry`);
- `repo.dead_letters()` lists what is open and `repo.mark_replayed()` records that an operator
  sent one back through. `cdi_dead_letters_total{kind}` counts them.

**Priority lanes** ride on the same messages: `web_job.priority` (0 lowest … 9 highest, 5 normal)
is sent as the Celery message priority and is kept across the E9-S2 hand-off, so an urgent job
stays urgent on the GPU queue. Redis has no priority of its own — Celery emulates it with one list
per step (`cdi-web`, `cdi-web:1`, …), which is why `priority_steps`, `sep` and
`queue_order_strategy` are set in one place (`cdi_adapter.worker`) and must match on every sender
and worker. Nothing sets a lane other than normal yet, so the lanes change no behaviour today;
proving that a high lane is actually drained first needs a real broker and belongs with the pod
(E9-S6).

Tests: `tests/test_queueing_unit.py` — what the key is made of (same bytes → same key; changed
bytes, order, name, patient or user → a different one; a client key wins and cannot collide), the
lane settings every sender and worker shares, and a poison document retried within its budget but
dead-lettered on the attempt that exhausts it. `tests/test_queueing_integration.py` on real
PostgreSQL — the identical upload twice is one job that stages once and dispatches once, two
copies at once are still one job, a job past its attempts is dead-lettered and no longer swept,
a repeat failure updates its row instead of piling up, and the list/replay round trip.
`tests/test_apptier_integration.py` — the same through the real `/api/jobs` route with real MinIO.

### Drill: broker crash-durability (backlog E9-S8)

`python scripts/broker_crash_drill.py` (on the pod / fallback host, app tier up) answers the one
question E9-S1 left open: killed mid-batch, does the **Redis broker** lose a document or run one
twice? It runs the E9-S6 load shape (30 patients × 5–10 documents), SIGKILLs the broker at
randomised points strictly inside the burst — seeded, so a run can be replayed exactly — restarts
it after `--down-s`, optionally kills a job worker as well, then counts from PostgreSQL
(`cdi_adapter.durability`): documents **lost** (never reached a terminal state), **duplicated**
(a second extraction row or open review item — what E9-S3's idempotency keys do *not* already
catch), **given up** (dead-lettered), and how many jobs were **resumed**. The broker's own
persistence settings go in the report, because that is what the numbers must be read against.

```bash
python scripts/broker_crash_drill.py --runs 3 --kills 1 \
    --broker-restart "redis-server \$CDI_SECRETS_DIR/redis.conf --port 6379 --bind 127.0.0.1 --dir \$WS/redis --appendonly yes --daemonize yes --logfile \$WS/logs/redis.log"
CDI_BROKER_URL=amqp://guest:guest@127.0.0.1:5672// \
  python scripts/broker_crash_drill.py --label rabbitmq --broker-process beam.smp \
    --broker-restart "systemctl start rabbitmq-server"         # the same drill, other broker
```

Note what the numbers are read against: `bootstrap_pod.sh` starts Redis with **`--appendonly yes`**,
which is *not* Redis's out-of-the-box persistence. The drill records the live `CONFIG GET` in its
report, so a run states the configuration it measured; a run meant to answer E9-S8's "default
persistence" wording has to be done twice — as deployed (AOF on) and with AOF off — or the report
has to say plainly which one it is. RabbitMQ is not provisioned anywhere yet (no `APT_PINS` entry,
no compose service), so the swap is a configuration change in the code but still an install step
on the host.

Tolerance: **0 lost, 0 duplicated, 0 given up** over at least three kills; anything else exits 1
and is the trigger to swap the broker (`CDI_BROKER_URL`) and measure again. The swap is
configuration only — the queues, routing, lanes and task code are identical, and
`worker.broker_conf()` is the one place the two brokers differ (ARCHITECTURE §7.6). Report:
`$WS/logs/ha/broker-drill-<label>-<time>.json` and `last-broker-drill.json`; the verdict, with
the measured numbers, is written into ARCHITECTURE §7.6 — which is **open until the pod run**.

Tests: `tests/test_broker_unit.py` (the broker comes from the URL; Redis keeps the emulated lanes
while RabbitMQ gets real priority queues and publisher confirms; queues, routing and lanes do not
depend on the broker; an unsupported broker is refused; the kill schedule is random, repeatable
and always inside the burst) and `tests/test_durability_integration.py` on real PostgreSQL (what
counts as lost, what a redelivered document is instead, a dead letter reported on its own, a
document extracted twice, and the verdict against the tolerance).

### The derived patient summary (backlog E5-S2)

`patient_summary` (migration 0026) holds one row per patient — the current problem list, the
active medications and the allergies, as jsonb with their counts and the latest encounter date —
built **from the §4.1 read-model views**, so it inherits E5-S1's continuity: the active order of
each medicine, one line per problem.

It is **not a PostgreSQL materialized view**, and that is deliberate. The story asks for a summary
that is current "within the same job's live-reprojection window — no separate manual refresh
step", and a matview cannot do that: `REFRESH MATERIALIZED VIEW` rebuilds *every* patient under an
`AccessExclusiveLock` (one patient's fact landing would block every reader and every other job, at
30 concurrent patients), and `REFRESH ... CONCURRENTLY` cannot run inside a transaction block at
all. So the summary is a derived table refreshed **per patient, in the caller's transaction**:

- the S6 gate refreshes it right after it reconciles the patient (`validate/service.py`), so by the
  time the job projects FHIR the summary is already current;
- each Reviewer decision does the same (`webapp/review.py`);
- there is nothing to run by hand, and no staleness window.

A trigger on `clinical_fact` sets `stale_at` whenever a **governed** fact is written, changed or
removed, and the refresh clears it. Nothing depends on the trigger for correctness — the refresh is
in the same transaction — but it turns "a write path forgot to refresh" from silent drift into a
visible row:

```bash
python -m cdi_adapter.summary stale            # exits 1 and lists the patients if any drifted
python -m cdi_adapter.summary rebuild          # backfill after the migration, or repair
python -m cdi_adapter.summary show <patient>   # one patient's summary as the reader sees it
```

Tests: `tests/test_summary_integration.py` on real PostgreSQL — the summary is current when the
job ends with nothing calling a refresh; a later visit moves it (the superseded order is gone, the
problem is still one line); a Reviewer decision updates it; only governed facts reach it; a patient
with nothing governed has an empty summary rather than a missing one; the refresh is idempotent and
the lists are ordered so a reader diffing two summaries sees only real movement; a governed fact
written behind its back shows as stale and the refresh clears it; `rebuild` backfills.

### Settling a merge conflict (backlog E5-S3)

The S8 console decides one fact at a time, which is the wrong shape when the S6 gate holds *two*
facts over: a `value_mismatch` writes a `fact_conflict` (`CONTRADICTED`, `blocker`) and both facts
go `in_review`. Those conflicts were written and then invisible — nothing read the table. Now:

- **`GET /conflicts`** (`webapp/conflict_page.py`) — the merge console. Both facts **side by side,
  each with its own evidence**: its page image with the bbox drawn, the OCR span behind it, the
  value, the code, the confidence and the findings. Linked from the nav.
- **keep A / keep B / merge.** Keep confirms one fact and rejects the other
  (`is_current = false`, `clinical_status = 'entered-in-error'`), exactly as the S8 console would.
  Merge applies the reviewer's values to the fact they keep, which produces a new `corrected` fact
  that **supersedes** it (the same `apply_fact_correction` path as an S8 correction, so medication
  detail and catalogue re-matching behave identically), and rejects the other side. `base` says
  which fact the merge builds on. Merge without values is refused — that decision is keep A.
- **One decision, one record** (migration 0027). The conflict row itself carries `resolution`,
  `result_fact_id`, `resolved_by`, `resolved_at` and `note`, under a constraint that a resolved
  conflict must say how and by whom and an open one must say neither; and exactly **one**
  `audit_log` row is written against `fact_conflict`. Then E5-S1 reconciliation and the E5-S2
  summary run in the same transaction, so the patient's lists and summary move with the decision.
  `cdi_conflicts_resolved_total{resolution}` counts them.

Tests: `tests/test_conflicts_integration.py` on real PostgreSQL — a real gate-written conflict is
listed with both sides; the detail carries each side's own evidence; keep A and keep B set the two
facts and write a single audit row; merge creates a corrected fact superseding the chosen base and
can build on either side; merge without values is refused and writes nothing; a settled conflict
cannot be settled twice; the queue drops it; and the summary is left current.

### Clinical field grammars (backlog E15-S2)

E2-S3 locks the *document* schema, so the model cannot emit invalid JSON. It says nothing about
what goes **inside** a field: `dose: "1-7-1"` is schema-valid and clinically impossible. The four
prescription fields whose form is closed now have their own grammar
([`cdi_adapter/fieldgrammar.py`](../src/cdi_adapter/fieldgrammar.py)):

| domain | grammar |
|---|---|
| `strength` | `<number><unit>`, the unit from the project's own UCUM aliases |
| `dose` | `d-d-d`, each slot 0–4 in halves (`1-0-1`, `1-½-1`) — a **count**, which is why `1-7-1` is not in it |
| `duration` | `x<n>d` \| `x<n> days` \| `x<n>wk` \| `x<n>/52` |
| `frequency` | the closed set OD/BD/TDS/QID/HS/SOS/PRN, every spelling `terminology.parse` knows, **or a dose pattern** — a prescription here writes the frequency as `1-0-1`, and `freq_per_day` reads it that way |

**One definition, two renderings.** Each grammar is one expression tree rendered to XGrammar EBNF
*and* to a regex. The decoder is given the EBNF; the pipeline (which has no `xgrammar`) uses the
regex; `tests/test_fieldgrammar_unit.py` runs the **real XGrammar 0.1.25** over the whole fixture
corpus and asserts the two verdicts are identical, so they cannot drift apart silently.

**As a validator (live).** `rules.check_field_grammars` flags a reading an unconstrained engine
produced outside its grammar — finding `field-out-of-grammar`, blocker for the numeric fields
(E4-S3's strict ones) and for a frequency written as a broken dose pattern, warn otherwise. It
judges the engine's own strings (the extraction payload, and `medication_detail.frequency_code`,
which is kept verbatim); a structured `{value, unit}` has already been parsed and is left to the
numeric rules, and an absent field stays `check_medication`'s business.

**As constrained decoding (opt-in).** Any schema property may carry `"x-cdi-field": "<domain>"`
and `mlserve.grammar` swaps it for that field's exact rule, using the same marker mechanism E2-S3
already uses for the 0–1 confidence. The shipped schemas are **not annotated yet, deliberately**:
locking a free-text clinical field means the model can no longer write what is on the page when
the page is outside the grammar, and whether that trades well belongs to the E2-S4 gold set rather
than to an assumption. Until then a bad reading is *flagged*, not *impossible*. An unknown domain
in a schema raises `GrammarError` in CI rather than passing unconstrained.

What this found on day one: the pipeline stores dose patterns in the frequency field, which the
first run surfaced as flagged readings on a clean prescription — the grammar was corrected to the
pipeline's reality rather than the other way round.

### Is the picture good enough? And retaking one page (backlog UP-S2)

A person at the desk photographs a prescription. If the photo is unreadable they should be told
**at the desk, while the page is still in front of them** — not after a reviewer finds an empty
extraction. Three parts, all new ([`recognition/quality.py`](../src/cdi_adapter/recognition/quality.py), which IM-S1 then widened - see below):

**The verdict.** Three measurements on the page as uploaded: the **variance of the Laplacian**
(sharpness), the **ink fraction** (share of pixels darker than mid-grey) and the **shorter side**
in pixels. Measured on rendered pages: a sparse prescription scores 73, a dense one 2153, the same
page blurred by 9 px 1.6, blank paper 0.0 — so sharpness is the signal that decides, and the
defaults come from those numbers rather than from intuition. Mean luminance and its percentiles
are **recorded but never judged**: a document is mostly white paper, so a real page and a
washed-out one both measure ~255 and the 5th percentile is white on any sparse page. For the same
reason there is no "too bright" verdict — glare that hides the writing shows up as *nothing to
read*, which is what the person can act on. Verdicts: `unreadable`, `too_small`, `too_dark`,
`blank`, `blurred`, each with a plain sentence and the measurements that produced it.

**It is off by default.** `CDI_QUALITY_CHECK=false`, so no page is ever held and the API carries no
verdict — the pipeline behaves exactly as it did. Switching it on starts rejecting pages, and a
limit set too high rejects *good* ones, so the limits (`CDI_QUALITY_MIN_SHARPNESS`,
`CDI_QUALITY_MIN_SIDE_PX`, `CDI_QUALITY_MIN_INK_FRACTION`, `CDI_QUALITY_MAX_INK_FRACTION`) are
per-deployment and want tuning on the E2-S4 gold set first — that tuning is IM-S1's work.

**Retaking one page.** A held page gets `status = quality_hold` (not in `UNFINISHED`, so no sweep
re-runs it), its plain reason in `docs[i].quality`, and a **Retake this page** button. That posts
to `POST /api/jobs/{job}/pages/{n}/replace`: the new file is staged under the same key, that one
document is reset, the job reopens, and the runner's resume runs Phase 1 **for the replaced page
only** — every other page of the prescription keeps its work. Refused, with nothing changed, while
a runner still holds the job. The progress table also now names the stage in plain words
(received → checking the picture → reading → saving → done).

Tests: `tests/test_upload_quality_unit.py` — each fault named and told in words with no numbers in
the message; order of judgement (a thumbnail is called small, not blurry); brightness recorded but
never judged; a real rendered page passes the **default** limits while its blurred, darkened and
blanked copies are each caught; the check off by default changes nothing; and the replace-page
contract (empty file, page out of range, missing job, a held page being terminal).

### The result on screen, and as a file (backlog UP-S3, the contract of OUT-S2)

A person at the desk has to see what was read and be able to take it away. Both are served from
**one document** — the Output Contract's `result.v1` — so the screen and the file can never
disagree ([`cdi_adapter/result.py`](../src/cdi_adapter/result.py),
[`schemas/contract/result.v1.json`](../schemas/contract/result.v1.json)):

- **`GET /api/documents/{id}/result.json`** — the contract document: `schema_version`,
  `document_id`, `received_at`, `source`, `page_count`, `status`, then `patient`, `doctor`,
  `visit`, `lab_tests[]`, `diagnoses[]`, `review` and `absent`.
- **`GET …/result.json/download`** — the same bytes as a named file.
- **`GET /result/{document_id}`** — four read-only cards (Patient, Doctor, Lab tests, Status) with
  a Download JSON button. No inputs: corrections stay in `/review`, and the page says so. The
  upload screen links to it once a page has passed validate.

Two contract rules enforced in code **and** in the schema, so a future builder cannot drift:

1. **No value without its status.** Every clinical value is
   `{value, status, confidence, evidence{page,bbox,observation_ids}}`; `result.value()` is the only
   way one is constructed and the schema makes `status` required, with `accepted|needs_check` the
   only allowed values. The screen draws every value through one helper that prints the tag with it.
2. **`accepted` only with proof** — a governed gate state (`auto_accepted`,
   `clinician_confirmed`, `corrected`) **and** recorded evidence. A confident value nobody can
   point at on the page is `needs_check`, and says which of the two was missing.

**What is deliberately missing.** The contract marks many fields NEW — preparation items, test
context, `preparation_refs`, the clinic block, patient phone/address, stamp/signature, the engine
versions. Their tables and extractors are OUT-S1/EX-S1–S5 work and do not exist, so those keys are
**left out and listed in `absent`** rather than emitted empty: a blank must never read as "nothing
was written on the page". The screen shows that list once, under "not extracted yet".

The field names are the Output Contract's own, and that contract says they are provisional until
OUT-S2's open question — *which system reads the JSON* — is answered. `schema_version` is what a
consumer pins, and the bytes are sorted and stable so two builds of one document compare identical.

Tests: `tests/test_result_unit.py` (the status rules, the schema refusing a bare value, the four
cards, a read-only page, the screen reading the contract and not the tables) and
`tests/test_result_integration.py` on real PostgreSQL (a real prescription validates against
`result.v1.json`, every value carries a status and a reason when held, an accepted value can be
pointed at on the page, and two builds are byte-identical).

### Check every picture first (backlog IM-S1)

UP-S2 needed a verdict to show; IM-S1 is that verdict done properly, in
[`cdi_adapter/recognition/quality.py`](../src/cdi_adapter/recognition/quality.py). Each page is
looked at before anything reads it, and **every page's measurements are recorded, pass or fail**,
in `document_page.preproc.quality` together with the limits they were judged against - so the
limits can later be moved on evidence. Recording is always on (well under a second a page, CPU
only); *holding* a page is still behind `CDI_QUALITY_CHECK`, off by default.

| Measurement | What it is | Verdict |
|---|---|---|
| `extent_px` | the shorter side of the page | `too_small` |
| `ink_fraction` | share of pixels darker than mid-grey | `too_dark`, `blank` |
| `soft_ink_fraction` | share more than 30 grey levels below the paper tone | tells *smeared* from *blank* |
| `glare_fraction` | share burnt out to white - **photographs only** | `glare` |
| `sharpness` | variance of the Laplacian | `blurred` |
| `text_height_px` | median height of the written marks (v3) | `text_too_small` |
| `rotated_90_suspected` | lines run down the page | **flag only** - turning it is IM-S2 |

Every one of those was checked against a rendered prescription and adversarial copies of it, and
the measuring changed the design four times:

- **Sharpness cannot see tiny text.** Shrinking a page to a fifth *raises* the Laplacian variance
  (844 to 7471) while the letters fall from 19 px to 4 px - which is why v3 asks for text height.
- **Glare only exists on a photograph.** Paper photographs as grey (~210) and glare burns a patch
  to 255: the burnt-out share reads 0.000 / 0.028 / 0.174 as it grows. On a render or a clean scan
  the paper is *already* white and the same number is ~0.98, so glare is judged only when the
  paper tone is below `CDI_QUALITY_PAPER_WHITE`.
- **A blurred page is not a blank one.** A strong blur leaves no pixel darker than mid-grey, so by
  ink alone it looked empty and the person was told "no writing found". Soft ink separates them:
  3-5 % on a blurred page at every strength, 0.000 % on blank paper.
- **Blur corrupts text height.** A soft focus erodes strokes to specks, the median mark shrinks,
  and the person was told to move closer. So blur is judged first and text height only once the
  page is known to be sharp.

**Not covered here:** an upside-down page looks upright by the line-gap measure, and a partial
page is a question of page edges, not picture quality - both are IM-S2's, below; and AC3 - the
false-reject rate on the answer-key set - cannot be produced until that set exists (ENT-S3). The
limits (`CDI_QUALITY_MIN_SHARPNESS`, `_MIN_SIDE_PX`, `_MIN_INK_FRACTION`, `_MAX_INK_FRACTION`,
`_MIN_TEXT_PX`, `_MAX_GLARE_FRACTION`, `_PAPER_WHITE`) are starting points derived from those
measurements, not tuned values.

Tests: `tests/test_recognition_unit.py` - the v3 adversarial list (tiny text, rotated,
over-compressed, glare, partial page), the order of judgement, and the record written for every
page; `tests/test_recognition_integration.py` - the measurements really land in
`document_page.preproc` on PostgreSQL.

### Straighten and crop safely, original untouched (backlog IM-S2)

[`cdi_adapter/recognition/geometry.py`](../src/cdi_adapter/recognition/geometry.py) makes a page
upright when it is confident and leaves it exactly as it was when it is not. Everything it does
is geometry, written down as one 3x3 matrix in `document_page.preproc.transform`, so a box found
on the stored page can be carried back to the pixels of the upload (`to_original`,
`box_to_original`). The uploaded file itself is never written to: S1 keeps it at its own key and
stores the straightened copy beside it.

| Move | When | Function |
|---|---|---|
| perspective crop | the table shows round the paper and its four corners can be found | `find_page_quad`, `warp_perspective` |
| deskew | a page that fills the frame, tilted 0.15-15 degrees (S1's existing rule, moved here unchanged) | `deskew` |
| quarter turn | the writing runs down the page | `writing_axis`, `rotate90` |
| half turn | the page is upside down | `upside_down`, `rotate90` |

**Off by default.** `CDI_STRAIGHTEN=false` leaves S1 doing what it always did - the small-tilt
deskew alone - and only adds the recorded matrix. Switching it on enables the crop and the turns,
and holds a page that cannot be made upright (UP-S2's retake screen shows the reason).

What the measurements changed, each one now pinned by a test:

- **Which way is up is read from the margin, not from the letters.** Counting ascenders against
  descenders reads +0.59 on a typed prescription and **-0.27 on one written in capitals**, and
  +/-0.03 on the sample scans. The left margin held on every page: line starts spread 0-20 px,
  line ends 60-290 px, and the two swap when the page is upside down.
- **Sideways is read from word shapes, not from IM-S1's flag.** `rotated_90_suspected` missed all
  three sample scans turned on their side, and the half-turn check then spun one of them the wrong
  way. Joining letters into words and counting wide against tall separated all thirty pages tried
  (13-72 one way, 0-13 the other). The flag stays what IM-S1 made it: a record.
- **A cropped page is not deskewed as well.** On a flattened photograph the ink-rectangle estimate
  read a tilt of -4.8 degrees that was not there and turned level lines crooked. The paper's edges
  set the level; the deskew runs only on a page that fills the frame.

**When unsure, nothing is changed.** Two cases also ask for a retake, because the picture is known
to be wrong and cannot be put right: `edges_not_found` (the table is visible but there are not
four corners - a page running out of shot) and `orientation_unsure` (sideways, and no margin says
which way). A level page that merely *might* be upside down - every line the same length, or
centred - is left alone and not held.

**Not done, and said so:**

- The story's proof asks for before/after of **five real phone photographs**. There are none in
  the repo; the photographs here are simulated (grey paper on a dark table, tilted, angled, with
  noise) from rendered pages and the three sample scans. That is why it is off by default.
- The margin rule assumes writing that starts at the left. A right-to-left script, a centred
  letterhead-only page or a fully justified page gives no answer (left alone, never guessed).
- The page must be lighter than the surface it lies on. White paper on a white table has no
  edge to find; it is treated as a page that fills the frame.
- `render_pages` does not apply a JPEG's EXIF orientation. With straightening on, such a photo is
  put right from its content; with it off it is stored as the bytes say, as before.
- Evidence boxes are not yet rewritten through the matrix anywhere: the matrix and the two
  mapping functions exist and are proven, and the stages that draw boxes still work in
  stored-page coordinates, as they did.

Tests: `tests/test_geometry_unit.py` (95) - AC1 tilted photographs come out level, AC2/AC5 every
quarter and half turn undone exactly on the rendered page and all three sample scans, AC3 a box
on the straightened page lands within 6 px of the same ink after tilt + angle + turn, AC4 held
pages come back byte-identical with the identity matrix; plus off-by-default and the S1 record.

### Find text lines, label printed vs handwritten (backlog IM-S3)

[`cdi_adapter/recognition/regions.py`](../src/cdi_adapter/recognition/regions.py) finds each line
of writing on a page, labels it `printed`, `handwritten`, `mixed` or `uncertain`, and cuts it
from the page as its own crop with a position and a fingerprint. Anything not plainly one kind is
for **both** reader paths; nothing is dropped. The workbook lists this story as built in this
file; it was not in the repo or on any branch, so it was built here.

**In the pipeline.** On the print reader's pass, S3 (`ocr/service.py`) runs the line finder on
every page and records what it found with the OCR run (`pipeline_run.metrics.regions`: lines,
printed, handwritten, mixed, uncertain, and the most handwritten lines on any one page). That is
always on and changes nothing else; if the line finder fails, OCR goes on without it.

**Acting on the labels is `CDI_LINE_REGIONS`, off by default.** With it on:

- each observation is stored with the label of the line it lies on (`ocr_observation.region_kind`)
  instead of the reader's blanket "printed" - S5 (`resolution/facts.py`) already treats evidence
  as handwritten when an observation says so, and until now only the VLM pass ever said so;
- a page with at least `CDI_LINE_REGIONS_MIN_HANDWRITTEN` (3) handwritten lines gets the
  handwriting pass even when the classifier took the page for printed (`webapp/jobs.py`).

It is a switch because the story's own rule is that routing must first prove its worth by
ablation, and that needs the answer-key set. Still page-level: the handwriting reader is given
the page, not line crops - there is no line-crop reader in the tree to route single lines to.
The Celery chain (`worker.py`: classify before OCR) does not use the line counts; the web app's
job runner does.

**The story's two cues did not work, measured on real pages:**

| Cue | Printed | Handwritten |
|---|---|---|
| stroke-width variation (story) | 0.08 - 0.49 | 0.27 - 0.33 |
| print reader's confidence (story) | 0.92 - 1.00 | 0.80 - 1.00 |
| **strokes upright and level** (`axis_share`) | **0.41 - 0.61** | **0.20 - 0.38** |

RapidOCR read a handwritten "150" at 1.00 and "Metformin" at 0.99: its confidence is not
evidence of type, so it is used only to make a "printed" line *less* certain, never more. What
separates the two is that type's strokes stand upright and lie level and a hand's slant and curve.

**The two mistakes are not equal.** Handwriting sent to the print reader alone is the dangerous
one, so `printed` is said only of a line of eight letters or more whose letters also keep fixed
heights - a handwritten "TSH", "HS" and "1-0-1" stand as upright as type.

**Measured on two real photographed prescriptions** (not an answer-key set; the pictures are on
the developer's machine and are not in the repo):

| Page | Lines found | Right | To both readers | Wrong |
|---|---|---|---|---|
| neat hand, printed letterhead | 52 | 34 | 18 | 0 |
| hurried hand, shadow, Kannada letterhead | 45 | 5 | 38 | 1 (+1 form line, printed label with a written value, called handwritten) |
| the three printed sample scans | 31 | 24 | 7 | 0 |

The one wrong line is a printed Kannada address called handwritten: **print in a non-Latin
script is not recognised as print** (a curved script has no upright strokes). On the hard page
most lines go to both readers - safe, and no saving. 0.1-0.2 s a page on a CPU.

**The crop standard** (`CropStandard`): padding of 15 % of the line height and at least 4 px so
no stroke is cut; a crop shorter than 32 px is enlarged (cubic, at most 4x); the fingerprint is
of the pixels as they are on the page, before enlarging, in the same form as S3's `crop_hash`;
padding, scale and both sizes are recorded with each crop so reader errors can be sliced by size.

**Not done, and said so:**

- **AC3** - recall and label accuracy by document type on the answer-key set. The counter
  (`regions.score`, `score_by_type`) and the runner are built and tested:
  `python scripts/measure_regions.py --key answer_key.json` prints the table, and "sent to both"
  is counted apart from "right" so caution cannot pass for accuracy. The key itself (ENT-S3) and
  the 30-page table do not exist yet.
- **The crop-size-against-accuracy measurement and the routing ablation** (v3): both need readers
  run over the crops and an answer key.
- **Line crops are not stored as their own rows.** S3's observations carry the line's label and,
  as before, the fingerprint of the pixels they were read from; the per-line crop record
  (`Region.record()`: padding, scale, sizes) is built but has no column to live in yet.
- **No real handwriting is in the repo**, so the automated tests use a stand-in (printed words
  leaned over and made uneven). They pin the rule; the real-page numbers above are what measure it.

Tests: `tests/test_regions_unit.py` (55); two more in `tests/test_ocr_integration.py` prove the
recorded counts and the stored labels on PostgreSQL + MinIO (not run on the dev box: no MinIO). The two real-page rows were judged by eye from the boxed
copies `scripts/measure_regions.py --draw` writes, not from drawn boxes.

### One site for computer, tablet and phone

Every page is built from `webapp/theme.py` (one stylesheet, one header) and already declared a
device-width viewport, but had no small-screen rules: two-column layouts stayed two columns, wide
tables pushed the page sideways and the six header links ran off a phone. The rules now live once,
in `theme.RESPONSIVE`, appended to the existing stylesheet (nothing a desk screen used changed):

- side-by-side panels (every `.grid`, whatever columns it asks for inline) stack below 900 px;
- the header wraps, and on a phone the links are one row that scrolls sideways under the title;
- a wide table scrolls inside its card (any width) or inside itself (phone);
- buttons and fields are finger-sized (42 px) and fields use 16 px text so iOS does not zoom.

Checked in a browser at 320, 375, 768, 1024 and 1280 px on the upload, result, review, reviewer,
merge-conflict, patient-360, admin, sign-in and monitor pages: none scrolls sideways, and the
panels are one column at 320-768 and two at 1024 and up. The admin table used to overflow the
page even on a desk screen (1348 px wide in a 1280 px window); it now scrolls inside its card.
Checked on static renders of each page - layout only, not with live data on a real phone.
Tests: `tests/test_responsive_unit.py`.

