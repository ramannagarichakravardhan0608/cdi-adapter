# Data-tier HA: Postgres standby and erasure-coded MinIO (backlog E10-S6)

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
