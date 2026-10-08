"""One row per file the listener has seen: the ``listener_file`` table (backlog LS-S3).

The row is what lets the listener pick up **only finished, new, allowed files**:

* **finished** - :meth:`observe` counts how many looks in a row a file's size and version tag
  have stayed the same (``stable_polls``); the loop takes a file only at
  ``CDI_LISTENER_STABLE_POLLS``;
* **new** - a file that went through is kept with the ``sha256`` of its bytes, and
  :meth:`processed` finds it again when the same bytes are dropped a second time;
* **one worker only** - :meth:`claim` is the lease: it succeeds for one worker and fails for
  every other until the lease runs out (a worker that died) or the same worker comes back;
* **ignored, in a place a person can see** - :meth:`ignore` keeps a row with the reason, and
  :meth:`ignored` lists them (``python -m cdi_adapter.listener ignored``; the health view,
  LS-S9, will read the same rows).

Two ledgers with the same behaviour: :class:`DbLedger` is the table (migration 0029), used by
the running listener; :class:`MemoryLedger` keeps the rows in the process, for a loop run
without a database (the tests, and nothing else).

Each row carries the drive it came from (``source_id``), which is what will let the retry agent
(LS-S7) leave another drive's rows alone.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from cdi_adapter.listener.connectors import RemoteFile

# seen: waiting in the inbox; processing: taken, leased; succeeded / failed: read, or not;
# refused: a data error, never retried; duplicate: bytes already read; ignored: never taken.
STATES = ("seen", "processing", "succeeded", "failed", "refused", "duplicate", "ignored")


@dataclass(frozen=True)
class Entry:
    """One file, as the ledger knows it."""

    id: str
    source_id: str
    file_id: str
    name: str
    size: int | None = None
    fingerprint: str | None = None
    stable_polls: int = 1
    state: str = "seen"
    kind: str | None = None
    reason: str | None = None
    sha256: str | None = None
    job_id: str | None = None
    duplicate_of: str | None = None
    lease_owner: str | None = None
    lease_until: datetime | None = None
    updated_at: datetime | None = None


def stamp_of(file: RemoteFile) -> str:
    """What "has not changed" is judged on: the drive's version tag when it gives one, else
    the date it was last written. (The size is compared beside it.)"""
    return file.fingerprint or (file.modified.isoformat() if file.modified else "")


class Ledger(Protocol):
    def observe(self, source_id: str, file: RemoteFile) -> Entry: ...
    def find(self, source_id: str, file_id: str) -> Entry | None: ...
    def claim(self, entry: Entry, worker: str, lease_seconds: float) -> bool: ...
    def moved(self, entry: Entry, file: RemoteFile) -> None: ...
    def finish(self, entry: Entry, state: str, *, file: RemoteFile | None = None,
               kind: str | None = None, reason: str | None = None, sha256: str | None = None,
               job_id: str | None = None, duplicate_of: str | None = None) -> None: ...  # fmt: skip
    def processed(self, sha256: str) -> Entry | None: ...
    def ignore(self, source_id: str, file: RemoteFile, reason: str) -> bool: ...
    def ignored(self, source_id: str | None = None, limit: int = 200) -> list[Entry]: ...


# ------------------------------------------------------------------ in the process


class MemoryLedger:
    """The ledger kept in memory. Same rules as the table; gone when the process ends."""

    def __init__(self, now: Any = None) -> None:
        self._rows: dict[str, Entry] = {}
        self._now = now or (lambda: datetime.now(tz=UTC))

    def _at(self, source_id: str, file_id: str) -> Entry | None:
        return next((e for e in self._rows.values()
                     if e.source_id == source_id and e.file_id == file_id), None)  # fmt: skip

    def _put(self, entry: Entry, **changes: Any) -> Entry:
        made = replace(entry, updated_at=self._now(), **changes)
        self._rows[made.id] = made
        return made

    def observe(self, source_id: str, file: RemoteFile) -> Entry:
        stamp, old = stamp_of(file), self._at(source_id, file.id)
        if old is None:
            return self._put(Entry(str(uuid.uuid4()), source_id, file.id, file.name, file.size,
                                   stamp))  # fmt: skip
        if old.state == "processing":  # a worker has it: looking at it changes nothing
            return old
        same = old.state == "seen" and old.size == file.size and old.fingerprint == stamp
        if same:
            return self._put(old, stable_polls=old.stable_polls + 1)
        # changed since the last look - or a file put back where one was already dealt with
        return self._put(old, name=file.name, size=file.size, fingerprint=stamp, stable_polls=1,
                         state="seen", kind=None, reason=None, sha256=None, job_id=None,
                         duplicate_of=None, lease_owner=None, lease_until=None)  # fmt: skip

    def find(self, source_id: str, file_id: str) -> Entry | None:
        return self._at(source_id, file_id)

    def claim(self, entry: Entry, worker: str, lease_seconds: float) -> bool:
        row, now = self._rows[entry.id], self._now()
        if row.state not in ("seen", "processing"):
            return False
        held = row.lease_until is not None and row.lease_until > now
        if held and row.lease_owner != worker:
            return False
        self._put(row, state="processing", lease_owner=worker,
                  lease_until=now + timedelta(seconds=lease_seconds))  # fmt: skip
        return True

    def moved(self, entry: Entry, file: RemoteFile) -> None:
        stale = self._at(entry.source_id, file.id)
        if stale is not None and stale.id != entry.id:
            del self._rows[stale.id]
        self._put(self._rows[entry.id], file_id=file.id, name=file.name)

    def finish(self, entry: Entry, state: str, *, file: RemoteFile | None = None,
               kind: str | None = None, reason: str | None = None, sha256: str | None = None,
               job_id: str | None = None, duplicate_of: str | None = None) -> None:  # fmt: skip
        if file is not None:
            self.moved(entry, file)
        self._put(self._rows[entry.id], state=state, kind=kind, reason=reason, sha256=sha256,
                  job_id=job_id, duplicate_of=duplicate_of, lease_owner=None,
                  lease_until=None)  # fmt: skip

    def processed(self, sha256: str) -> Entry | None:
        done = [e for e in self._rows.values() if e.sha256 == sha256 and e.state == "succeeded"]
        return min(done, key=lambda e: e.updated_at or self._now()) if done else None

    def ignore(self, source_id: str, file: RemoteFile, reason: str) -> bool:
        old = self._at(source_id, file.id)
        if old is not None and old.state == "ignored" and old.reason == reason:
            return False
        base = old or Entry(str(uuid.uuid4()), source_id, file.id, file.name)
        self._put(base, name=file.name, size=file.size, state="ignored", reason=reason,
                  kind="ignored")  # fmt: skip
        return True

    def ignored(self, source_id: str | None = None, limit: int = 200) -> list[Entry]:
        rows = [e for e in self._rows.values() if e.state == "ignored"
                and (source_id is None or e.source_id == source_id)]  # fmt: skip
        return sorted(rows, key=lambda e: e.updated_at or self._now(), reverse=True)[:limit]


# ------------------------------------------------------------------ the table

_COLUMNS = ("id, source_id, file_id, name, size, fingerprint, stable_polls, state, kind, reason, "
            "sha256, job_id, duplicate_of, lease_owner, lease_until, updated_at")  # fmt: skip


def _entry(row: Any) -> Entry:
    m = dict(row._mapping)
    for key in ("id", "duplicate_of"):  # uuids, kept as text like every other id here
        m[key] = str(m[key]) if m[key] is not None else None
    return Entry(**m)


class DbLedger:
    """The ``listener_file`` table. Every method is one short transaction of its own, so a
    row is on disk before the listener acts on it."""

    def ready(self) -> str | None:
        """``None`` when the table is there, else what to do about it."""
        from sqlalchemy import text

        from cdi_adapter.db import session_scope

        try:
            with session_scope() as conn:
                found = conn.execute(text("SELECT to_regclass('listener_file')")).scalar_one()
        except Exception as exc:  # noqa: BLE001 - any failure means "not now"
            return f"the database cannot be reached ({type(exc).__name__})"
        if found is None:
            return ("the listener_file table is missing: bring the database up to date "
                    "(python -m alembic upgrade head)")  # fmt: skip
        return None

    def _one(self, sql: str, **params: Any) -> Entry | None:
        from sqlalchemy import text

        from cdi_adapter.db import session_scope

        with session_scope() as conn:
            row = conn.execute(text(sql), params).first()
        return _entry(row) if row is not None else None

    def _run(self, sql: str, **params: Any) -> int:
        from sqlalchemy import text

        from cdi_adapter.db import session_scope

        with session_scope() as conn:
            return int(conn.execute(text(sql), params).rowcount)

    def observe(self, source_id: str, file: RemoteFile) -> Entry:
        # One statement: a new row, one more unchanged look, or - when the size or version tag
        # moved, or the row is of a file already dealt with - a fresh start at one.
        made = self._one(
            f"""
            INSERT INTO listener_file (source_id, file_id, name, size, fingerprint, modified_at)
            VALUES (:source, :file, :name, :size, :stamp, :modified)
            ON CONFLICT (source_id, file_id) DO UPDATE SET
                stable_polls = CASE WHEN listener_file.state = 'seen'
                                     AND listener_file.size IS NOT DISTINCT FROM EXCLUDED.size
                                     AND listener_file.fingerprint
                                         IS NOT DISTINCT FROM EXCLUDED.fingerprint
                                    THEN listener_file.stable_polls + 1 ELSE 1 END,
                kind = CASE WHEN listener_file.state = 'seen' THEN listener_file.kind END,
                reason = CASE WHEN listener_file.state = 'seen' THEN listener_file.reason END,
                sha256 = CASE WHEN listener_file.state = 'seen' THEN listener_file.sha256 END,
                job_id = CASE WHEN listener_file.state = 'seen' THEN listener_file.job_id END,
                duplicate_of = NULL, lease_owner = NULL, lease_until = NULL, state = 'seen',
                name = EXCLUDED.name, size = EXCLUDED.size, fingerprint = EXCLUDED.fingerprint,
                modified_at = EXCLUDED.modified_at, updated_at = now()
            WHERE listener_file.state <> 'processing'
            RETURNING {_COLUMNS}
            """,
            source=source_id, file=file.id, name=file.name, size=file.size,
            stamp=stamp_of(file), modified=file.modified)  # fmt: skip
        # no row back: a worker has the file (state 'processing'), and looking changes nothing
        made = made or self.find(source_id, file.id)
        assert made is not None
        return made

    def find(self, source_id: str, file_id: str) -> Entry | None:
        return self._one(f"SELECT {_COLUMNS} FROM listener_file "
                         "WHERE source_id = :source AND file_id = :file",
                         source=source_id, file=file_id)  # fmt: skip

    def claim(self, entry: Entry, worker: str, lease_seconds: float) -> bool:
        # The lease. Two workers run this at the same moment; the row lock lets one through.
        return self._run(
            """
            UPDATE listener_file
               SET state = 'processing', lease_owner = :worker,
                   lease_until = now() + make_interval(secs => :seconds), updated_at = now()
             WHERE id = :id AND state IN ('seen', 'processing')
               AND (lease_until IS NULL OR lease_until < now() OR lease_owner = :worker)
            """, id=entry.id, worker=worker, seconds=float(lease_seconds)) == 1  # fmt: skip

    def moved(self, entry: Entry, file: RemoteFile) -> None:
        self._run("DELETE FROM listener_file WHERE source_id = :source AND file_id = :file "
                  "AND id <> :id", source=entry.source_id, file=file.id, id=entry.id)  # fmt: skip
        self._run("UPDATE listener_file SET file_id = :file, name = :name, updated_at = now() "
                  "WHERE id = :id", file=file.id, name=file.name, id=entry.id)  # fmt: skip

    def finish(self, entry: Entry, state: str, *, file: RemoteFile | None = None,
               kind: str | None = None, reason: str | None = None, sha256: str | None = None,
               job_id: str | None = None, duplicate_of: str | None = None) -> None:  # fmt: skip
        if file is not None:
            self.moved(entry, file)
        self._run(
            """
            UPDATE listener_file
               SET state = :state, kind = :kind, reason = :reason, sha256 = :sha256,
                   job_id = :job, duplicate_of = :dup, lease_owner = NULL, lease_until = NULL,
                   updated_at = now()
             WHERE id = :id
            """, id=entry.id, state=state, kind=kind, reason=reason, sha256=sha256, job=job_id,
            dup=duplicate_of)  # fmt: skip

    def processed(self, sha256: str) -> Entry | None:
        return self._one(f"SELECT {_COLUMNS} FROM listener_file WHERE sha256 = :sha "
                         "AND state = 'succeeded' ORDER BY updated_at LIMIT 1",
                         sha=sha256)  # fmt: skip

    def ignore(self, source_id: str, file: RemoteFile, reason: str) -> bool:
        # Written once per file and reason: a file that sits in the folder is not re-recorded
        # on every look.
        return self._run(
            """
            INSERT INTO listener_file (source_id, file_id, name, size, state, kind, reason)
            VALUES (:source, :file, :name, :size, 'ignored', 'ignored', :reason)
            ON CONFLICT (source_id, file_id) DO UPDATE SET
                state = 'ignored', kind = 'ignored', reason = EXCLUDED.reason,
                name = EXCLUDED.name, size = EXCLUDED.size, lease_owner = NULL,
                lease_until = NULL, updated_at = now()
            WHERE listener_file.state <> 'ignored'
               OR listener_file.reason IS DISTINCT FROM EXCLUDED.reason
            """, source=source_id, file=file.id, name=file.name, size=file.size,
            reason=reason) == 1  # fmt: skip

    def ignored(self, source_id: str | None = None, limit: int = 200) -> list[Entry]:
        from sqlalchemy import text

        from cdi_adapter.db import session_scope

        with session_scope() as conn:
            rows = conn.execute(text(
                f"SELECT {_COLUMNS} FROM listener_file WHERE state = 'ignored' "
                "AND (CAST(:source AS text) IS NULL OR source_id = :source) "
                "ORDER BY updated_at DESC LIMIT :limit"),
                {"source": source_id, "limit": limit}).all()  # fmt: skip
        return [_entry(r) for r in rows]
