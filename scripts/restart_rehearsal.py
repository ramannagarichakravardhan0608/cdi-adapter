"""ENT-S4 AC1: rehearse the DATABASE half of a restart, and keep the log.

    python scripts/restart_rehearsal.py --admin-url postgresql://cdi@127.0.0.1:5432/postgres \\
        --pg-bin /usr/lib/postgresql/16/bin --out docs/evidence/ENT-S4

It works in a scratch database it makes itself (``cdi_rehearsal_<time>``) and drops at the
end; it never opens another database. With the same commands the pod uses:

1. a database at the newest migration, with made-up rows in it (no real patient data);
2. BEFORE: every table's row count (``cdi_adapter.dr.counts`` - what prepare_stop.sh records);
3. the snapshot: ``pg_dump -Fc``, and ``pg_restore --list`` to see it can be read (snapshot.sh);
4. the stop: the database is DROPPED - what a pod restart does to the overlay it lives on;
5. the start: an empty database, ``pg_restore --exit-on-error --single-transaction --clean
   --if-exists --no-owner``, then ``alembic upgrade head`` (bootstrap_pod.sh, in that order);
6. AFTER: the counts again, compared with BEFORE;
7. the check of the check: one row is removed, and the comparison must say DIFFERENT.

What it does NOT rehearse: the pod scripts themselves (prepare_stop.sh, start_all.sh), the
scans in the document store, the queue, and the services coming back healthy. Those need a pod.
Exit code 0 when the counts are identical and the removed row is noticed.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]


def _run(cmd: list[str], env: dict[str, str]) -> None:
    done = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if done.returncode != 0:
        raise RuntimeError(f"{Path(cmd[0]).name} failed: {done.stderr.strip()[:400]}")


def _seed(url: str, documents: int) -> None:
    """Made-up rows in several tables, through the application's own writers where there is
    one. The names are obviously not real people."""
    os.environ["CDI_DATABASE_URL"] = url
    from cdi_adapter import db, repo
    from cdi_adapter.config import get_settings

    get_settings.cache_clear()
    db.reset_engine()
    with db.session_scope() as c:
        for i in range(documents):
            sha = uuid.uuid4().hex + uuid.uuid4().hex
            doc = repo.insert_source_document(
                c,
                sha256=sha,
                filename=f"rehearsal-{i:03d}.png",
                mime_type="image/png",
                byte_size=1000 + i,
                storage_key=f"rehearsal/{sha}",
            )
            repo.insert_document_page(
                c,
                document_id=doc,
                page_no=1,
                storage_key=f"p/{sha}/1.png",
                thumb_key="t",
                width=1654,
                height=2339,
                dpi=200,
                preproc={},
            )
            patient = c.execute(
                text("INSERT INTO patient_identity (name) VALUES (:n) RETURNING id"),
                {"n": f"Rehearsal Person {i:03d}"},
            ).scalar_one()
            for j in range(3):
                c.execute(
                    text(
                        "INSERT INTO clinical_fact (patient_id, fact_type, local_text, "
                        "value_kind, value_text, review_state, source_doc_ids) VALUES "
                        "(:p, 'lab_order', :t, 'text', NULL, 'pending', "
                        "CAST(:d AS uuid[]))"
                    ),
                    {"p": patient, "t": f"Made-up test {j}", "d": [str(doc)]},
                )
            repo.write_audit(
                c,
                actor="rehearsal",
                action="create",
                entity="source_document",
                entity_id=doc,
                detail={"rehearsal": True},
            )


def _counts(url: str) -> dict[str, Any]:
    from cdi_adapter import db
    from cdi_adapter.config import get_settings
    from cdi_adapter.dr import counts

    os.environ["CDI_DATABASE_URL"] = url
    get_settings.cache_clear()
    db.reset_engine()
    with db.session_scope() as c:
        return counts.take(c)


def _migrate(url: str) -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "db" / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")


def rehearse(admin_url: str, pg_bin: Path, documents: int = 25) -> dict[str, Any]:
    from cdi_adapter import db
    from cdi_adapter.dr import counts

    name = "cdi_rehearsal_" + dt.datetime.now(dt.UTC).strftime("%Y%m%d%H%M%S")
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    target = make_url(admin_url).set(database=name)
    url = target.render_as_string(hide_password=False)
    plain = target.set(drivername="postgresql").render_as_string(hide_password=False)
    env = {**os.environ, "PGPASSWORD": target.password or ""}
    exe = ".exe" if os.name == "nt" else ""
    log: dict[str, Any] = {"database": name, "documents_seeded": documents, "steps": []}

    def step(what: str) -> None:
        log["steps"].append(
            {"at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"), "step": what}
        )

    with tempfile.TemporaryDirectory() as tmp:
        dump = Path(tmp) / "cdi.dump"
        try:
            with admin.connect() as c:
                c.execute(text(f'CREATE DATABASE "{name}"'))
            _migrate(url)
            _seed(url, documents)
            step("a database at the newest migration, with made-up rows")
            before = _counts(url)
            step(f"BEFORE: {len(before['tables'])} tables, {before['rows']} rows")
            db.reset_engine()
            _run([str(pg_bin / f"pg_dump{exe}"), "-Fc", "-d", plain, "-f", str(dump)], env)
            _run([str(pg_bin / f"pg_restore{exe}"), "--list", str(dump)], env)
            step(f"snapshot: pg_dump -Fc ({dump.stat().st_size} bytes), listed by pg_restore")
            with admin.connect() as c:
                c.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
                c.execute(text(f'CREATE DATABASE "{name}"'))
            step("stop and start: the database dropped, an empty one made")
            _run(
                [
                    str(pg_bin / f"pg_restore{exe}"),
                    "--exit-on-error",
                    "--single-transaction",
                    "--clean",
                    "--if-exists",
                    "--no-owner",
                    "-d",
                    plain,
                    str(dump),
                ],
                env,
            )
            _migrate(url)
            step("restore: pg_restore, then alembic upgrade head (the pod's order)")
            after = _counts(url)
            found = counts.compare(before, after)
            step(
                f"AFTER: {found['rows_after']} rows -> "
                f"{'IDENTICAL' if found['same'] else 'DIFFERENT'}"
            )
            with db.session_scope() as c:  # the check of the check
                c.execute(text("DELETE FROM audit_log WHERE id = (SELECT max(id) FROM audit_log)"))
            noticed = counts.compare(before, _counts(url))
            step(
                "one row removed on purpose -> "
                + ("noticed" if not noticed["same"] else "NOT NOTICED")
            )
            log.update(
                before=before,
                after=after,
                comparison=found,
                a_removed_row_is_noticed=not noticed["same"],
                removed_row_difference=noticed["differences"],
                same=found["same"],
                passed=found["same"] and not noticed["same"],
            )
        finally:
            db.reset_engine()
            with admin.connect() as c:
                c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
            admin.dispose()
    return log


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--admin-url", required=True, help="a URL of the 'postgres' database")
    ap.add_argument("--pg-bin", required=True, help="the folder of pg_dump and pg_restore")
    ap.add_argument("--documents", type=int, default=25)
    ap.add_argument("--out", default="")
    ap.add_argument("--where", default="", help="where this was run (goes into the log)")
    args = ap.parse_args(argv)
    log = rehearse(args.admin_url, Path(args.pg_bin), args.documents)
    log["where"] = args.where
    log["scope"] = (
        "the database half of a restart only: not the pod scripts, the document "
        "store, the queue or the services"
    )
    for s in log["steps"]:
        print(f"  {s['at']}  {s['step']}")
    print(f"restart rehearsal (database): {'PASS' if log['passed'] else 'FAIL'}")
    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "restart_rehearsal_database.json").write_text(
            json.dumps(log, indent=1, sort_keys=True) + "\n", encoding="utf-8"
        )
    return 0 if log["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
