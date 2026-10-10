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

* **what happened to it, in order** - :meth:`note` appends to the row's ``history``: the batch
  a file ran in (LS-S5: every file of a batch carries the batch's id) and, when it finishes,
  how many seconds passed from the drop to the result.

Two ledgers with the same behaviour: :class:`DbLedger` is the table (migration 0029), used by
the running listener; :class:`MemoryLedger` keeps the rows in the process, for a loop run
without a database (the tests, and nothing else).

Each row carries the drive it came from (``source_id``), which is what will let the retry agent
(LS-S7) leave another drive's rows alone.
"""

from __future__ import annotations

import functools
import json
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from cdi_adapter.listener.connectors import RemoteFile

# seen: waiting in the inbox; processing: taken, leased; succeeded / failed: read, or not;
# refused: a data error, never retried; duplicate: bytes already read; ignored: never taken;
# quarantined: failed as often as it may (LS-S6) - a person must look.
STATES = ("seen", "processing", "succeeded", "failed", "refused", "duplicate", "ignored",
          "quarantined")  # fmt: skip
# the history events that count as a failed run of a file
FAILED_RUNS = ("failed", "quarantined")


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
    first_seen_at: datetime | None = None  # when the listener first saw it: "the drop"
    # LS-S8: the stored document this file became (its result), the name it was dropped under
    # (``name`` follows the file as it moves and may gain a ".1"), and the folder as a person
    # would say it
    document_id: str | None = None
    original_name: str | None = None
    folder: str | None = None
    history: tuple[dict[str, Any], ...] = ()  # what happened to it, oldest first


def stamp_of(file: RemoteFile) -> str:
    """What "has not changed" is judged on: the drive's version tag when it gives one, else
    the date it was last written. (The size is compared beside it.)"""
    return file.fingerprint or (file.modified.isoformat() if file.modified else "")


class Ledger(Protocol):
    def observe(self, source_id: str, file: RemoteFile, folder: str | None = None) -> Entry: ...
    def find(self, source_id: str, file_id: str) -> Entry | None: ...
    def claim(self, entry: Entry, worker: str, lease_seconds: float) -> bool: ...
    def moved(self, entry: Entry, file: RemoteFile) -> None: ...
    def finish(self, entry: Entry, state: str, *, file: RemoteFile | None = None,
               kind: str | None = None, reason: str | None = None, sha256: str | None = None,
               job_id: str | None = None, duplicate_of: str | None = None,
               document_id: str | None = None) -> None: ...  # fmt: skip
    def processed(self, sha256: str) -> Entry | None: ...
    def failures(self, entry: Entry, sha256: str | None) -> int: ...
    def failed(self, source_id: str) -> list[Entry]: ...
    def heartbeat(self, source_id: str, worker: str, *, ok: bool, waiting: int = 0,
                  oldest_waiting_at: datetime | None = None,
                  reason: str | None = None) -> None: ...  # fmt: skip
    def health(self, source_id: str | None = None,
               stale_seconds: float = 300.0) -> list[dict[str, Any]]: ...  # fmt: skip
    def now(self) -> datetime: ...
    def note(self, entry: Entry, event: dict[str, Any]) -> None: ...
    def ignore(self, source_id: str, file: RemoteFile, reason: str) -> bool: ...
    def ignored(self, source_id: str | None = None, limit: int = 200) -> list[Entry]: ...


# ------------------------------------------------------------------ health (LS-S9)


def health_row(beat: dict[str, Any], counts: dict[str, int], ignored: list[dict[str, str]],
               now: datetime, stale_seconds: float) -> dict[str, Any]:  # fmt: skip
    """One watched folder's health, as the view shows it: when it last looked and last looked
    *successfully*, what is waiting and for how long, and what has gone to error and
    quarantine. Built the same way from the table and from the in-memory ledger."""
    last_ok, oldest = beat.get("last_ok_at"), beat.get("oldest_waiting_at")
    since_ok = (now - last_ok).total_seconds() if last_ok else None
    waiting = int(beat.get("waiting") or 0)
    return {
        "source_id": beat["source_id"],
        "drive": beat["source_id"].split(":", 1)[0],
        "worker": beat.get("worker"),
        "last_poll_at": beat["last_poll_at"].isoformat() if beat.get("last_poll_at") else None,
        "last_good_poll_at": last_ok.isoformat() if last_ok else None,
        "seconds_since_good_poll": round(since_ok, 1) if since_ok is not None else None,
        "healthy": since_ok is not None and since_ok <= stale_seconds,
        "waiting": waiting,
        "oldest_waiting_seconds": round((now - oldest).total_seconds(), 1)
        if oldest and waiting else None,
        "processing": counts.get("processing", 0),
        "error": counts.get("failed", 0) + counts.get("refused", 0),
        "quarantine": counts.get("quarantined", 0),
        "reason": beat.get("reason"),
        "ignored": ignored,
    }  # fmt: skip


# ------------------------------------------------------------------ in the process


def _locked(method: Callable[..., Any]) -> Callable[..., Any]:
    """One caller at a time: the files of a batch are read on several threads (LS-S5)."""

    @functools.wraps(method)
    def inner(self: Any, *args: Any, **kwargs: Any) -> Any:
        with self._lock:
            return method(self, *args, **kwargs)

    return inner


class MemoryLedger:
    """The ledger kept in memory. Same rules as the table; gone when the process ends."""

    def __init__(self, now: Any = None) -> None:
        self._rows: dict[str, Entry] = {}
        self._now = now or (lambda: datetime.now(tz=UTC))
        self._lock = threading.RLock()
        self._beats: dict[str, dict[str, Any]] = {}

    def _at(self, source_id: str, file_id: str) -> Entry | None:
        return next((e for e in self._rows.values()
                     if e.source_id == source_id and e.file_id == file_id), None)  # fmt: skip

    def _put(self, entry: Entry, **changes: Any) -> Entry:
        made = replace(entry, updated_at=self._now(), **changes)
        self._rows[made.id] = made
        return made

    @_locked
    def observe(self, source_id: str, file: RemoteFile, folder: str | None = None) -> Entry:
        stamp, old = stamp_of(file), self._at(source_id, file.id)
        if old is None:
            return self._put(Entry(str(uuid.uuid4()), source_id, file.id, file.name, file.size,
                                   stamp, first_seen_at=self._now(), original_name=file.name,
                                   folder=folder))  # fmt: skip
        if old.state == "processing":  # a worker has it: looking at it changes nothing
            return old
        same = old.state == "seen" and old.size == file.size and old.fingerprint == stamp
        if same:
            return self._put(old, stable_polls=old.stable_polls + 1)
        # changed since the last look - or a file put back where one was already dealt with
        return self._put(old, name=file.name, size=file.size, fingerprint=stamp, stable_polls=1,
                         state="seen", kind=None, reason=None, sha256=None, job_id=None,
                         duplicate_of=None, lease_owner=None, lease_until=None,
                         first_seen_at=old.first_seen_at if old.state == "seen"
                         else self._now(),
                         original_name=old.original_name if old.state == "seen" else file.name,
                         document_id=None, folder=folder or old.folder)  # fmt: skip

    @_locked
    def find(self, source_id: str, file_id: str) -> Entry | None:
        return self._at(source_id, file_id)

    @_locked
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

    @_locked
    def moved(self, entry: Entry, file: RemoteFile) -> None:
        stale = self._at(entry.source_id, file.id)
        if stale is not None and stale.id != entry.id:
            del self._rows[stale.id]
        self._put(self._rows[entry.id], file_id=file.id, name=file.name)

    @_locked
    def finish(self, entry: Entry, state: str, *, file: RemoteFile | None = None,
               kind: str | None = None, reason: str | None = None, sha256: str | None = None,
               job_id: str | None = None, duplicate_of: str | None = None,
               document_id: str | None = None) -> None:  # fmt: skip
        if file is not None:
            self.moved(entry, file)
        row = self._rows[entry.id]
        self._put(row, state=state, kind=kind, reason=reason, sha256=sha256,
                  job_id=job_id, duplicate_of=duplicate_of, lease_owner=None,
                  lease_until=None, document_id=document_id or row.document_id)  # fmt: skip

    @_locked
    def processed(self, sha256: str) -> Entry | None:
        done = [e for e in self._rows.values() if e.sha256 == sha256 and e.state == "succeeded"]
        return min(done, key=lambda e: e.updated_at or self._now()) if done else None

    def now(self) -> datetime:
        """The ledger's own clock. Times that are compared with each other (when a file was
        first seen, when it finished, when it may be retried) are all read from it."""
        at: datetime = self._now()
        return at

    @_locked
    def heartbeat(self, source_id: str, worker: str, *, ok: bool, waiting: int = 0,
                  oldest_waiting_at: datetime | None = None,
                  reason: str | None = None) -> None:  # fmt: skip
        now, old = self._now(), self._beats.get(source_id, {})
        self._beats[source_id] = {
            "source_id": source_id, "worker": worker, "last_poll_at": now,
            "last_ok_at": now if ok else old.get("last_ok_at"),
            "waiting": waiting if ok else old.get("waiting", 0),
            "oldest_waiting_at": oldest_waiting_at if ok else old.get("oldest_waiting_at"),
            "reason": None if ok else reason}  # fmt: skip

    @_locked
    def health(self, source_id: str | None = None,
               stale_seconds: float = 300.0) -> list[dict[str, Any]]:  # fmt: skip
        out = []
        for source, beat in sorted(self._beats.items()):
            if source_id is not None and source != source_id:
                continue
            rows = [r for r in self._rows.values() if r.source_id == source]
            counts: dict[str, int] = {}
            for r in rows:
                counts[r.state] = counts.get(r.state, 0) + 1
            ignored = [{"file": r.name, "reason": r.reason or ""}
                       for r in self.ignored(source, limit=50)]  # fmt: skip
            out.append(health_row(beat, counts, ignored, self._now(), stale_seconds))
        return out

    @_locked
    def failed(self, source_id: str) -> list[Entry]:
        rows = [r for r in self._rows.values() if r.source_id == source_id and r.state == "failed"]
        return sorted(rows, key=lambda r: r.updated_at or self._now())

    @_locked
    def failures(self, entry: Entry, sha256: str | None) -> int:
        rows = [r for r in self._rows.values()
                if r.id == entry.id or (sha256 and r.sha256 == sha256)]  # fmt: skip
        return sum(1 for r in rows for e in r.history if e.get("event") in FAILED_RUNS)

    @_locked
    def note(self, entry: Entry, event: dict[str, Any]) -> None:
        row = self._rows[entry.id]
        self._put(row, history=(*row.history, dict(event)))

    @_locked
    def ignore(self, source_id: str, file: RemoteFile, reason: str) -> bool:
        old = self._at(source_id, file.id)
        if old is not None and old.state == "ignored" and old.reason == reason:
            return False
        base = old or Entry(str(uuid.uuid4()), source_id, file.id, file.name,
                            first_seen_at=self._now())  # fmt: skip
        self._put(base, name=file.name, size=file.size, state="ignored", reason=reason,
                  kind="ignored")  # fmt: skip
        return True

    @_locked
    def ignored(self, source_id: str | None = None, limit: int = 200) -> list[Entry]:
        rows = [e for e in self._rows.values() if e.state == "ignored"
                and (source_id is None or e.source_id == source_id)]  # fmt: skip
        return sorted(rows, key=lambda e: e.updated_at or self._now(), reverse=True)[:limit]


# ------------------------------------------------------------------ the table

_COLUMNS = ("id, source_id, file_id, name, size, fingerprint, stable_polls, state, kind, reason, "
            "sha256, job_id, duplicate_of, lease_owner, lease_until, updated_at, "
            "first_seen_at, history, document_id, original_name, folder")  # fmt: skip


def _entry(row: Any) -> Entry:
    m = dict(row._mapping)
    for key in ("id", "duplicate_of", "document_id"):  # uuids, kept as text like every other id
        m[key] = str(m[key]) if m[key] is not None else None
    m["history"] = tuple(m.get("history") or ())
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

    def observe(self, source_id: str, file: RemoteFile, folder: str | None = None) -> Entry:
        # One statement: a new row, one more unchanged look, or - when the size or version tag
        # moved, or the row is of a file already dealt with - a fresh start at one.
        made = self._one(
            f"""
            INSERT INTO listener_file (source_id, file_id, name, size, fingerprint, modified_at,
                                       original_name, folder)
            VALUES (:source, :file, :name, :size, :stamp, :modified, :name, :folder)
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
                -- "the drop" is when the file was first seen; a file put back where one was
                -- already dealt with is a new drop
                first_seen_at = CASE WHEN listener_file.state = 'seen'
                                     THEN listener_file.first_seen_at ELSE now() END,
                original_name = CASE WHEN listener_file.state = 'seen'
                                     THEN listener_file.original_name ELSE EXCLUDED.name END,
                document_id = CASE WHEN listener_file.state = 'seen'
                                   THEN listener_file.document_id END,
                folder = COALESCE(EXCLUDED.folder, listener_file.folder),
                duplicate_of = NULL, lease_owner = NULL, lease_until = NULL, state = 'seen',
                name = EXCLUDED.name, size = EXCLUDED.size, fingerprint = EXCLUDED.fingerprint,
                modified_at = EXCLUDED.modified_at, updated_at = now()
            WHERE listener_file.state <> 'processing'
            RETURNING {_COLUMNS}
            """,
            source=source_id, file=file.id, name=file.name, size=file.size,
            stamp=stamp_of(file), modified=file.modified, folder=folder)  # fmt: skip
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
               job_id: str | None = None, duplicate_of: str | None = None,
               document_id: str | None = None) -> None:  # fmt: skip
        if file is not None:
            self.moved(entry, file)
        self._run(
            """
            UPDATE listener_file
               SET state = :state, kind = :kind, reason = :reason, sha256 = :sha256,
                   job_id = :job, duplicate_of = :dup, lease_owner = NULL, lease_until = NULL,
                   document_id = COALESCE(CAST(:doc AS uuid), document_id), updated_at = now()
             WHERE id = :id
            """, id=entry.id, state=state, kind=kind, reason=reason, sha256=sha256, job=job_id,
            dup=duplicate_of, doc=document_id)  # fmt: skip

    def processed(self, sha256: str) -> Entry | None:
        return self._one(f"SELECT {_COLUMNS} FROM listener_file WHERE sha256 = :sha "
                         "AND state = 'succeeded' ORDER BY updated_at LIMIT 1",
                         sha=sha256)  # fmt: skip

    def now(self) -> datetime:
        """The database's clock: the one ``first_seen_at`` and ``updated_at`` are written by,
        whatever the clock of the machine the listener runs on says."""
        from sqlalchemy import text

        from cdi_adapter.db import session_scope

        with session_scope() as conn:
            at: datetime = conn.execute(text("SELECT now()")).scalar_one()
        return at

    def heartbeat(self, source_id: str, worker: str, *, ok: bool, waiting: int = 0,
                  oldest_waiting_at: datetime | None = None,
                  reason: str | None = None) -> None:  # fmt: skip
        """The listener looked at its folder. A look that failed keeps the last good look's
        time and counts, and says why."""
        self._run(
            """
            INSERT INTO listener_heartbeat (source_id, worker, last_poll_at, last_ok_at,
                                            waiting, oldest_waiting_at, reason)
            VALUES (:source, :worker, now(), CASE WHEN :ok THEN now() END, :waiting,
                    :oldest, CASE WHEN :ok THEN NULL ELSE :reason END)
            ON CONFLICT (source_id) DO UPDATE SET
                worker = EXCLUDED.worker, last_poll_at = now(),
                last_ok_at = CASE WHEN :ok THEN now() ELSE listener_heartbeat.last_ok_at END,
                waiting = CASE WHEN :ok THEN EXCLUDED.waiting
                               ELSE listener_heartbeat.waiting END,
                oldest_waiting_at = CASE WHEN :ok THEN EXCLUDED.oldest_waiting_at
                                         ELSE listener_heartbeat.oldest_waiting_at END,
                reason = EXCLUDED.reason
            """, source=source_id, worker=worker, ok=ok, waiting=waiting,
            oldest=oldest_waiting_at, reason=reason)  # fmt: skip

    def health(self, source_id: str | None = None,
               stale_seconds: float = 300.0) -> list[dict[str, Any]]:  # fmt: skip
        """Every watched folder's health (or one's), read from the tables."""
        from sqlalchemy import text

        from cdi_adapter.db import session_scope

        with session_scope() as conn:
            now = conn.execute(text("SELECT now()")).scalar_one()
            beats = conn.execute(text(
                "SELECT source_id, worker, last_poll_at, last_ok_at, waiting, oldest_waiting_at, "
                "reason FROM listener_heartbeat WHERE CAST(:s AS text) IS NULL OR source_id = :s "
                "ORDER BY source_id"), {"s": source_id}).mappings().all()  # fmt: skip
            out = []
            for beat in beats:
                counts = {r[0]: int(r[1]) for r in conn.execute(text(
                    "SELECT state, count(*) FROM listener_file WHERE source_id = :s "
                    "GROUP BY state"), {"s": beat["source_id"]}).all()}  # fmt: skip
                ignored = [{"file": r[0], "reason": r[1] or ""} for r in conn.execute(text(
                    "SELECT name, reason FROM listener_file WHERE source_id = :s "
                    "AND state = 'ignored' ORDER BY updated_at DESC LIMIT 50"),
                    {"s": beat["source_id"]}).all()]  # fmt: skip
                out.append(health_row(dict(beat), counts, ignored, now, stale_seconds))
        return out

    def failed(self, source_id: str) -> list[Entry]:
        """The files of this drive whose last run failed and may be run again."""
        from sqlalchemy import text

        from cdi_adapter.db import session_scope

        with session_scope() as conn:
            rows = conn.execute(
                text(
                    f"SELECT {_COLUMNS} FROM listener_file WHERE source_id = :source "
                    "AND state = 'failed' ORDER BY updated_at"
                ),
                {"source": source_id},
            ).all()
        return [_entry(r) for r in rows]

    def failures(self, entry: Entry, sha256: str | None) -> int:
        """How many runs of this file have failed so far: on its own row, and on any other
        row holding the same bytes (a file put back into the inbox is a new row on a drive
        that names files by their path)."""
        from sqlalchemy import text

        from cdi_adapter.db import session_scope

        with session_scope() as conn:
            return int(conn.execute(text(
                "SELECT count(*) FROM listener_file f, jsonb_array_elements(f.history) e "
                "WHERE (f.id = :id OR (CAST(:sha AS text) IS NOT NULL AND f.sha256 = :sha)) "
                "AND e->>'event' IN ('failed', 'quarantined')"),
                {"id": entry.id, "sha": sha256}).scalar_one())  # fmt: skip

    def note(self, entry: Entry, event: dict[str, Any]) -> None:
        self._run("UPDATE listener_file SET history = history || CAST(:event AS jsonb), "
                  "updated_at = now() WHERE id = :id",
                  id=entry.id, event=json.dumps([event], default=str))  # fmt: skip

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
