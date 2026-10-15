"""``python -m cdi_adapter.dr``: backups and the restore drill (backlog E10-S11; ``docs/dr.md``).

backup --dump FILE --taken-at ISO   after snapshot.sh's pg_dump: copy new MinIO objects, record
backup --failed MSG --taken-at ISO  a failed snapshot (the staleness alert keeps counting)
drill [--minio-bin B] [--pg-bin D]  restore everything into a clean environment, measure
status                              the last backup and the last drill
counts --save FILE                  every table's row count, the scans, the queue keys (ENT-S4)
counts --compare FILE [--report F]  the same now, against FILE: exit 1 when one differs
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from cdi_adapter.dr import backup as bk
from cdi_adapter.dr import drill as dr


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m cdi_adapter.dr", description="backups + DR drill")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backup")
    b.add_argument("--taken-at", required=True, help="when pg_dump started (ISO 8601, UTC)")
    g = b.add_mutually_exclusive_group(required=True)
    g.add_argument("--dump", help="the dump file name written in the backup folder")
    g.add_argument("--failed", help="what failed")
    d = sub.add_parser("drill")
    d.add_argument("--pg-bin", default=os.environ.get("CDI_PG_BIN", "/usr/lib/postgresql/16/bin"))
    d.add_argument("--minio-bin", default=os.environ.get("CDI_MINIO_BIN"))
    d.add_argument("--no-golden", action="store_true")
    d.add_argument(
        "--live",
        action="store_true",
        help="also count documents the live database has that the backup lacks",
    )
    sub.add_parser("status")
    c = sub.add_parser("counts")
    cg = c.add_mutually_exclusive_group(required=True)
    cg.add_argument("--save", help="write the counts taken now to this file")
    cg.add_argument("--compare", help="compare the counts taken now with this file")
    c.add_argument("--report", default="", help="with --compare: also write the comparison here")
    return ap


def run_counts(args: argparse.Namespace) -> int:  # pragma: no cover - needs the servers
    from cdi_adapter.config import get_settings
    from cdi_adapter.db import session_scope
    from cdi_adapter.dr import counts
    from cdi_adapter.storage import get_store

    with session_scope() as conn:
        now = counts.take(conn, get_store(), get_settings().redis_url)
    if args.save:
        counts.save(Path(args.save), now)
        print(f"counts saved: {len(now['tables'])} tables, {now['rows']} rows, "
              f"{now['objects']} scans, {now['queue_keys']} queue keys")  # fmt: skip
        return 0
    found = counts.compare(counts.load(Path(args.compare)), now)
    if args.report:
        counts.save(Path(args.report), found)
    for d in found["differences"]:
        print(f"  DIFFERENT  {d['what']}: {d['before']} before, {d['after']} after")
    for label in found["not_compared"]:
        print(f"  not compared: {label} (could not be counted)")
    verdict = "IDENTICAL" if found["same"] else "DIFFERENT"
    print(f"restart check: {found['tables_compared']} tables, {found['rows_before']} rows "
          f"before, {found['rows_after']} after -> {verdict}")  # fmt: skip
    return 0 if found["same"] else 1


def main(argv: Sequence[str] | None = None) -> int:  # pragma: no cover - needs the servers
    from cdi_adapter.config import get_settings

    args = build_parser().parse_args(argv)
    if args.cmd == "counts":
        return run_counts(args)
    s = get_settings()
    backup_dir, report_dir = Path(s.backup_dir), Path(s.dr_report_dir)
    if args.cmd == "backup":
        taken = dt.datetime.fromisoformat(args.taken_at.replace("Z", "+00:00"))
        if args.failed:
            bk.write_status(backup_dir, ok=False, taken_at=taken, error=args.failed)
            return 1
        try:
            from cdi_adapter.storage import get_store

            counts = bk.backup_objects(get_store(), backup_dir)
        except Exception as exc:
            bk.write_status(
                backup_dir,
                ok=False,
                taken_at=taken,
                dump=args.dump,
                error=f"objects: {type(exc).__name__}: {exc}",
            )
            raise
        st = bk.write_status(backup_dir, ok=True, taken_at=taken, dump=args.dump, objects=counts)
        print(f"objects backed up: {counts['objects']} ({counts['added']} new); {st['dump']}")
        return 0
    if args.cmd == "drill":
        rep = dr.run_drill(
            backup_dir,
            pg_bin=args.pg_bin,
            minio_bin=args.minio_bin,
            golden=not args.no_golden,
            report_dir=report_dir,
            live_url=s.database_url if args.live else None,
        )
        print(json.dumps(rep, indent=1, default=str))
        print("RESTORE DRILL:", "PASS" if rep.get("ok") else "FAIL")
        return 0 if rep.get("ok") else 1
    print(
        json.dumps(
            {"backup": bk.read_status(backup_dir), "drill": dr.read_last(report_dir)},
            indent=1,
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
