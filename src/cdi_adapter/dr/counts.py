"""Row counts before a stop and after a restart (backlog ENT-S4 AC1).

"Nothing was lost" is a number: every table's row count, the number of stored scans and the
number of queue keys, written down by ``prepare_stop.sh`` once the writers are stopped, and
compared by ``start_all.sh`` the moment the database is back from its dump - before any
service writes to it again.

    python -m cdi_adapter.dr counts --save   /workspace/backup/counts.before.json
    python -m cdi_adapter.dr counts --compare /workspace/backup/counts.before.json

``compare`` exits 0 when every count is the same and 1 when one is not. A table that exists
only after (a migration added it) is reported, not counted as a loss; a table that is gone,
or holds fewer or more rows, is a difference.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

from sqlalchemy import text

TABLES = ("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' "
          "AND table_type = 'BASE TABLE' ORDER BY table_name")  # fmt: skip


def table_counts(conn: Any) -> dict[str, int]:
    """Every base table of the application's schema with its row count."""
    names = [r[0] for r in conn.execute(text(TABLES)).all()]
    return {name: int(conn.execute(text(f'SELECT count(*) FROM "{name}"')).scalar_one())
            for name in names}  # fmt: skip


def object_count(store: Any) -> int | None:
    """How many scans and pages the document store holds; None when it cannot be asked."""
    try:
        from cdi_adapter import storage

        return sum(1 for _ in storage.listing(store))
    except Exception:  # noqa: BLE001 - the counts of the database are still worth having
        return None


def queue_keys(redis_url: str) -> int | None:
    """How many keys the queue's Redis holds; None when it cannot be asked."""
    try:
        import redis

        size: Any = redis.Redis.from_url(redis_url, socket_timeout=3).dbsize()
        return int(size)
    except Exception:  # noqa: BLE001
        return None


def take(conn: Any, store: Any = None, redis_url: str | None = None, *,
         now: dt.datetime | None = None) -> dict[str, Any]:  # fmt: skip
    tables = table_counts(conn)
    return {"taken_at": (now or dt.datetime.now(dt.UTC)).isoformat(timespec="seconds"),
            "tables": tables, "rows": sum(tables.values()),
            "objects": object_count(store) if store is not None else None,
            "queue_keys": queue_keys(redis_url) if redis_url else None}  # fmt: skip


def compare(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """What differs between two takes. ``same`` is True only when every table that existed
    before holds exactly the rows it held, and the scans and queue keys are as many."""
    b, a = before.get("tables") or {}, after.get("tables") or {}
    differences = []
    for name in sorted(b):
        if name not in a:
            differences.append({"what": f"table {name}", "before": b[name], "after": None})
        elif a[name] != b[name]:
            differences.append({"what": f"table {name}", "before": b[name], "after": a[name]})
    for key, label in (("objects", "stored scans and pages"), ("queue_keys", "queue keys")):
        if before.get(key) is not None and after.get(key) is not None and (
                before[key] != after[key]):  # fmt: skip
            differences.append({"what": label, "before": before[key], "after": after[key]})
    not_compared = [label for key, label in (("objects", "stored scans and pages"),
                                             ("queue_keys", "queue keys"))
                    if before.get(key) is None or after.get(key) is None]  # fmt: skip
    return {"same": not differences, "differences": differences,
            "tables_compared": len(b), "rows_before": before.get("rows"),
            "rows_after": sum(a[n] for n in b if n in a),
            "new_tables": sorted(set(a) - set(b)), "not_compared": not_compared,
            "before_taken_at": before.get("taken_at"),
            "after_taken_at": after.get("taken_at")}  # fmt: skip


def save(path: Path, counts: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(counts, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def load(path: Path) -> dict[str, Any]:
    return dict(json.loads(path.read_text(encoding="utf-8")))
