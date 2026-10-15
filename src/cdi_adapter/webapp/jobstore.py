"""Durable web-app jobs (backlog E10-S9, with the core of E9-S1; ARCHITECTURE §7.2, §13).

What makes the web app stateless, so that several instances can run behind the balancer and any
one of them can be killed:

* **Accept** (:func:`submit`, any instance): every uploaded file is written to the object store
  under ``uploads/<job>/<n>``, then the job row goes into ``web_job``; only then is the upload
  acknowledged (202). A failure before that refuses the upload (503), and nothing half-accepted
  is left behind. Then the job is dispatched: onto the Celery queue ``cdi-web``
  (``CDI_JOB_EXECUTION=queue``, the pod) or to this instance's own runner (``inprocess``).
* **Read** (:func:`load`, any instance): the upload page's polling, the facts, Generate EMR and
  FHIR routes all read the row, never one process's memory.
* **Run** (``JobRunner.run_stored``): claims the row with a lease and keeps it current. A dispatch
  that is lost (Redis restarted before a worker took it) or a runner that dies is covered by the
  sweep in every runner (:meth:`PgJobStore.orphans`): the job is claimed again and resumed.
* **Hold** (``cdi_adapter.migration``, backlog E9-S1): while a schema migration holds the gate,
  :meth:`PgJobStore.claim` and :meth:`PgJobStore.orphans` refuse - the queue waits instead of
  running against a schema this code does not match - and the claim is still the only thing that
  decides who runs a job, so afterwards every waiting job runs exactly once.

So an acknowledged upload is always in PostgreSQL + MinIO, and its job always ends, whichever
process is killed. The staged files are removed once the job has finished (the original of
every document is kept by S1 ingest under ``documents/``).
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import socket
import time
import uuid
from types import SimpleNamespace
from typing import Any

from sqlalchemy import text

from cdi_adapter import metrics, repo, storage, tracing
from cdi_adapter.config import get_settings
from cdi_adapter.db import session_scope
from cdi_adapter.logging import get_logger
from cdi_adapter.storage import ObjectStore, get_store
from cdi_adapter.webapp.jobs import DocState, Job

log = get_logger(__name__)

WEB_QUEUE = "cdi-web"
WEB_TASK = "cdi.web_job"
# E9-S2: the GPU pool's queue. A job whose Phase 1 is done goes back on the queue - the same task,
# addressed to the other pool - and the worker consuming cdi-gpu claims it and extracts.
GPU_QUEUE = "cdi-gpu"
# E9-S1: while a schema migration holds the gate (cdi_adapter.migration, migration 0021) no runner
# takes a NEW job - neither by its queue message nor by the sweep - so the queue holds still
# instead of being run against a schema this code does not match. Jobs already claimed keep their
# lease and finish; an expired hold (a migrator that died) gates nothing.
GATE_OPEN = ("NOT EXISTS (SELECT 1 FROM schema_gate WHERE closed "
             "AND (expires_at IS NULL OR expires_at > now()))")  # fmt: skip
# E9-S3 priority lanes: the message priority a job is dispatched with (0 lowest … 9 highest).
# Everything is NORMAL unless the caller says otherwise, so the lanes change nothing by default.
MIN_PRIORITY, NORMAL_PRIORITY, MAX_PRIORITY = 0, 5, 9
# names this process's leases: unique per process start, readable in web_job.lease_owner
OWNER = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"
_JOB_ID = frozenset("0123456789abcdef")


class NotAccepted(Exception):
    """The upload could not be made durable, so it was not acknowledged."""


class Duplicate(Exception):
    """This upload's idempotency key is already another job's (E9-S3)."""


def staging_key(job_id: str, index: int) -> str:
    return f"uploads/{job_id}/{index:02d}"


def instance_name() -> str:
    s = get_settings()
    return s.instance_name or f"{socket.gethostname()}:{s.webapp_port}"


def _valid_id(job_id: str) -> bool:
    return len(job_id) == 32 and set(job_id) <= _JOB_ID


def _docs(job: Job) -> str:
    return json.dumps([dataclasses.asdict(d) for d in job.docs])


def _job(row: Any) -> Job:
    job = Job(id=row["id"], docs=[DocState(**d) for d in row["docs"]], state=row["state"],
              patient_id=str(row["patient_id"]) if row["patient_id"] else None,
              registry_patient=row["registry_patient"], abha=row["abha"],
              mobile=row.get("mobile"), token_no=row.get("token_no"),
              mismatch=row["mismatch"], trace_id=row["trace_id"], span_id=row["span_id"],
              attempts=row["attempts"], channel=channel_of(row.get("created_by")))  # fmt: skip
    job.started_at = row["created_at"].timestamp()  # the upload: the SLA window starts here
    if row["finished_at"] is not None:
        job.finished.set()
    return job


class PgJobStore:
    """``web_job`` + the staged uploads: the ``JobStore`` of every production runner."""

    def __init__(self, objects: ObjectStore | None = None) -> None:
        self._objects = objects

    @property
    def objects(self) -> ObjectStore:
        return self._objects or get_store()

    # ------------------------------------------------------------ accept / read

    def insert(self, job: Job, files: list[dict[str, Any]], *, actor: str, accepted_by: str,
               key: str | None = None, priority: int = NORMAL_PRIORITY) -> None:  # fmt: skip
        """Write the job row. Raises :class:`Duplicate` when ``key`` is already another job's
        idempotency key (E9-S3): the caller reads that job instead of keeping this one."""
        with session_scope() as c:
            row = c.execute(text(
                "INSERT INTO web_job (id, state, patient_id, registry_patient, abha, files, docs, "
                "trace_id, span_id, created_by, accepted_by, idempotency_key, priority, "
                "mobile, token_no) VALUES (:i, 'running', :p, "
                "CAST(:r AS jsonb), :a, CAST(:f AS jsonb), CAST(:d AS jsonb), :t, :s, :u, :b, "
                ":k, :pr, :m, :tk) ON CONFLICT DO NOTHING RETURNING id"),
                {"i": job.id, "p": job.patient_id,
                 "r": json.dumps(job.registry_patient, default=str)
                 if job.registry_patient is not None else None,
                 "a": job.abha, "f": json.dumps(files), "d": _docs(job), "t": job.trace_id,
                 "s": job.span_id, "u": actor, "b": accepted_by, "k": key,
                 "pr": priority, "m": job.mobile, "tk": job.token_no}).first()  # fmt: skip
            if row is None:  # the unique index on idempotency_key held: this upload is a repeat
                raise Duplicate(key or job.id)
            detail = {"documents": len(files), "accepted_by": accepted_by,
                      "idempotency_key": key, "priority": priority}  # fmt: skip
            repo.write_audit(c, actor=actor, action="create", entity="web_job",
                             entity_id=job.id, detail=detail)  # fmt: skip

    def by_key(self, key: str) -> Job | None:
        """The job this idempotency key already accepted, if any (E9-S3)."""
        with session_scope() as c:
            row = c.execute(text("SELECT * FROM web_job WHERE idempotency_key = :k"),
                            {"k": key}).mappings().first()  # fmt: skip
        return _job(row) if row else None

    def cleared(self, job: Job) -> bool:
        """True when this job ran to its end and none of its documents is in front of a
        reviewer any more - the review was rejected or cleared away, or there never was one. The
        same upload sent again is then a new run, not a repeat (owner, 2026-10-10: a document
        tested once could not be tested again). A job still running, or one that failed, is
        never "cleared": a retried POST still gets the first job back (E9-S3)."""
        if job.state != "review" or not job.docs:
            return False
        from cdi_adapter.canon.repo import has_live_item

        with session_scope() as c:
            return not has_live_item(c, [str(d.document_id) for d in job.docs if d.document_id])

    def load(self, job_id: str) -> Job | None:
        if not _valid_id(job_id):
            return None
        with session_scope() as c:
            row = c.execute(text("SELECT * FROM web_job WHERE id = :i"),
                            {"i": job_id}).mappings().first()  # fmt: skip
        return _job(row) if row else None

    # ------------------------------------------------------------ the runner's side

    def claim(self, job_id: str, owner: str, lease_s: float, *,
              attempt: bool = True) -> Job | None:  # fmt: skip
        """``attempt=False`` (E9-S2): the GPU pool taking the job over at the phase boundary is
        the one run carrying on, so it does not spend one of ``job_max_attempts``."""
        n = "attempts + 1" if attempt else "attempts"
        with session_scope() as c:
            row = c.execute(text(
                "UPDATE web_job SET lease_owner = :o, "
                f"lease_until = now() + make_interval(secs => :l), attempts = {n}, "
                "started_at = coalesce(started_at, now()), updated_at = now() "
                "WHERE id = :i AND finished_at IS NULL "
                f"AND (lease_until IS NULL OR lease_until < now()) AND {GATE_OPEN} "
                "RETURNING *"),
                {"i": job_id, "o": owner, "l": lease_s}).mappings().first()  # fmt: skip
        return _job(row) if row else None

    def save(self, job: Job, owner: str) -> bool:
        with session_scope() as c:
            n = c.execute(text(
                "UPDATE web_job SET state = :s, patient_id = :p, mismatch = CAST(:m AS jsonb), "
                "docs = CAST(:d AS jsonb), updated_at = now() "
                "WHERE id = :i AND lease_owner = :o AND finished_at IS NULL"),
                {"i": job.id, "o": owner, "s": job.state, "p": job.patient_id,
                 "m": json.dumps(job.mismatch) if job.mismatch else None,
                 "d": _docs(job)}).rowcount  # fmt: skip
        return n == 1

    def finish(self, job: Job, owner: str) -> None:
        state = "error" if job.state == "running" else job.state
        with session_scope() as c:
            row = c.execute(text(
                "UPDATE web_job SET state = :s, patient_id = :p, mismatch = CAST(:m AS jsonb), "
                "docs = CAST(:d AS jsonb), finished_at = now(), lease_owner = NULL, "
                "lease_until = NULL, updated_at = now() "
                "WHERE id = :i AND lease_owner = :o AND finished_at IS NULL RETURNING files"),
                {"i": job.id, "o": owner, "s": state, "p": job.patient_id,
                 "m": json.dumps(job.mismatch) if job.mismatch else None,
                 "d": _docs(job)}).first()  # fmt: skip
        if row is None:
            log.warning("job_finish_not_owner", job=job.id, runner=owner)
            return
        for f in row[0]:  # the staged copies; S1 keeps each document's original
            try:
                self.objects.delete(f["key"])
            except Exception as exc:  # noqa: BLE001 - a leftover staged file harms nothing
                log.warning("job_staged_delete_failed", job=job.id, key=f["key"], error=str(exc))

    def release(self, job_id: str, owner: str) -> None:
        """Give the lease up now (the run could not start, or the job is handed to the GPU
        pool), so another runner can claim it. The owner is cleared with it: a lease that still
        named this runner was renewed by its next heartbeat, and the job - on the GPU queue,
        claimable by nobody - sat there for good (found under load, LS-S10)."""
        with session_scope() as c:
            c.execute(text("UPDATE web_job SET lease_until = now() - interval '1 second', "
                           "lease_owner = NULL "
                           "WHERE id = :i AND lease_owner = :o AND finished_at IS NULL"),
                      {"i": job_id, "o": owner})  # fmt: skip

    def files(self, job: Job, indices: list[int]) -> dict[int, tuple[str, bytes, str | None]]:
        with session_scope() as c:
            meta = c.execute(text("SELECT files FROM web_job WHERE id = :i"),
                             {"i": job.id}).scalar_one()  # fmt: skip
        out: dict[int, tuple[str, bytes, str | None]] = {}
        for i in indices:
            f = meta[i]
            try:
                raw = self.objects.get_bytes(f["key"])
            except Exception as exc:
                # SW-S1: "that key is gone" in whichever store is in use, asked of the store
                if storage.missing(exc):
                    log.error("job_staged_missing", job=job.id, key=f["key"])
                    continue
                raise
            if hashlib.sha256(raw).hexdigest() != f["sha256"]:
                log.error("job_staged_corrupt", job=job.id, key=f["key"])
                continue
            out[i] = (f["filename"], raw, f["mime"])
        return out

    def heartbeat(self, owner: str, lease_s: float) -> int:
        with session_scope() as c:
            return int(c.execute(text(
                "UPDATE web_job SET lease_until = now() + make_interval(secs => :l) "
                "WHERE lease_owner = :o AND finished_at IS NULL"),
                {"o": owner, "l": lease_s}).rowcount)  # fmt: skip

    def orphans(self, unclaimed_s: float, limit: int) -> list[str]:
        with session_scope() as c:
            return list(c.execute(text(
                "SELECT id FROM web_job WHERE finished_at IS NULL AND (lease_until < now() OR "
                "(lease_until IS NULL AND created_at < now() - make_interval(secs => :u))) "
                f"AND {GATE_OPEN} ORDER BY created_at LIMIT :n"),
                {"u": unclaimed_s, "n": limit}).scalars())  # fmt: skip

    # ------------------------------------------------------------ dead letters (E9-S3)

    def dead_letter(self, job: Job, *, reason: str, actor: str = "runner") -> None:
        """Take a job that kept failing out of the queue (migration 0022): a ``dead_letter`` row
        with its attempt count and last error, and ``dead_lettered_at`` on the job itself. The row
        is written whether or not this runner still holds the lease - the job is being given up,
        not saved - and a failure to record it never stops the job from being finished."""
        try:
            docs = [{"filename": d.filename, "status": d.status, "error": d.error}
                    for d in job.docs]  # fmt: skip
            detail = {"documents": docs, "patient_id": job.patient_id}
            with session_scope() as c:
                repo.write_dead_letter(c, kind="web_job", ref=job.id, attempts=job.attempts,
                                       error=reason, queue=WEB_QUEUE, task=WEB_TASK,
                                       detail=detail)  # fmt: skip
                c.execute(text("UPDATE web_job SET dead_lettered_at = now(), updated_at = now() "
                               "WHERE id = :i"), {"i": job.id})  # fmt: skip
                repo.write_audit(c, actor=actor, action="update", entity="web_job",
                                 entity_id=job.id,
                                 detail={"dead_letter": reason})  # fmt: skip
            metrics.DEAD_LETTERS.labels("web_job").inc()
            log.error("job_dead_lettered", job=job.id, attempts=job.attempts, reason=reason)
        except Exception as exc:  # noqa: BLE001 - the job is given up either way; the log has it
            log.error("job_dead_letter_failed", job=job.id, error=f"{type(exc).__name__}: {exc}")

    # ------------------------------------------------------------ metrics (E10-S1)

    def metric_rows(self) -> list[Any]:
        """Unfinished jobs, and jobs of the last day: their state, upload time and whether
        their lease ran out (no runner holds them)."""
        with session_scope() as c:
            rows = c.execute(
                text(
                    "SELECT state, extract(epoch FROM created_at) AS t, "
                    "(finished_at IS NULL AND lease_until < now()) AS orphaned FROM web_job "
                    "WHERE finished_at IS NULL OR created_at > now() - interval '1 day'"
                )
            ).all()
        return [SimpleNamespace(state=r[0], started_at=float(r[1]), orphaned=bool(r[2]))
                for r in rows]  # fmt: skip


def store() -> PgJobStore:
    return PgJobStore()


def load(job_id: str) -> Job | None:
    return store().load(job_id)


LISTENER_ACTOR = "listener"
MAX_AGAIN = 200  # how many times one upload can be cleared and sent again: only an upper bound


def channel_of(actor: str | None) -> str:
    """The source channel of a job, from who submitted it: the file listener's jobs are
    recorded as ``listener``, everything else as an ``upload`` from the screen (LS-S4)."""
    return "listener" if actor == LISTENER_ACTOR else "upload"


def idempotency_key(files: list[tuple[str, bytes, str | None]], *, actor: str,
                    patient_id: str | None = None, abha: str | None = None,
                    given: str | None = None, mobile: str | None = None,
                    token_no: str | None = None) -> str:  # fmt: skip
    """The key that decides whether this upload has already been accepted (backlog E9-S3).

    ``given`` is the client's ``Idempotency-Key`` header, namespaced by the actor so two accounts
    cannot collide on the same string. Without one, the key is derived from the upload itself -
    every file's SHA-256 and name, in the order sent, plus the actor and the patient context - so
    a counter whose POST timed out and was retried with the same bytes gets the first job back.
    Two *genuinely* different uploads never share a key; the same scan sent for two patients, or
    by two users, does not either.
    """
    if given:
        return "hdr:" + hashlib.sha256(f"{actor}\x00{given}".encode()).hexdigest()
    h = hashlib.sha256()
    h.update(f"{actor}\x00{patient_id or ''}\x00{abha or ''}\x00{len(files)}\x00".encode())
    if mobile or token_no:  # only when given, so an upload without them keeps the key it had
        h.update(f"{mobile or ''}\x00{token_no or ''}\x00".encode())
    for name, raw, _ in files:
        h.update(hashlib.sha256(raw).digest())
        h.update(f"\x00{name}\x00".encode())
    return "body:" + h.hexdigest()


def submit(files: list[tuple[str, bytes, str | None]], *, actor: str,
           registry_patient: dict[str, Any] | None = None, patient_id: str | None = None,
           abha: str | None = None, key: str | None = None, given_key: str | None = None,
           priority: int = NORMAL_PRIORITY, st: PgJobStore | None = None,
           mobile: str | None = None, token_no: str | None = None) -> Job:  # fmt: skip
    """Make the upload durable, then dispatch it. Raises :class:`NotAccepted` (nothing kept)
    when the files or the row could not be written: the caller must not acknowledge.

    Accepted **once** (E9-S3): the upload's idempotency key is looked up first, and if it is
    already a job's, that job is returned and nothing new is staged, dispatched or inserted. When
    two identical copies arrive at the same moment both stage, then the unique index decides: the
    loser drops its staged files and reads the winner's job. So a retried POST always answers with
    one job id, and the queue only ever holds one run of it.
    """
    st = st or store()
    key = key or idempotency_key(files, actor=actor, patient_id=patient_id, abha=abha,
                                 given=given_key, mobile=mobile, token_no=token_no)  # fmt: skip
    first = st.by_key(key)
    # a run whose review was cleared away does not hold the key: the upload runs again, under a
    # key made from that run - so a repeat of THIS upload is, once more, answered with one job
    cleared = getattr(st, "cleared", None)
    for _ in range(MAX_AGAIN):
        if first is None or cleared is None or not cleared(first):
            break
        log.info("job_upload_again", earlier=first.id, actor=actor)
        key = "again:" + hashlib.sha256(f"{key}\x00{first.id}".encode()).hexdigest()
        first = st.by_key(key)
    if first is not None:
        log.info("job_duplicate_upload", job=first.id, actor=actor, idempotency_key=key)
        metrics.JOB_DUPLICATES.inc()
        return first
    job = Job(id=uuid.uuid4().hex, docs=[DocState(filename=f[0]) for f in files],
              patient_id=patient_id, registry_patient=registry_patient, abha=abha,
              channel=channel_of(actor), mobile=mobile, token_no=token_no)  # fmt: skip
    staged: list[str] = []
    try:
        with tracing.span("job.accept", {"cdi.job_id": job.id,
                                         "cdi.documents": len(files)}) as sp:  # fmt: skip
            meta = []
            for i, (name, raw, mime) in enumerate(files):
                skey = staging_key(job.id, i)
                st.objects.put_bytes(skey, raw, mime or "application/octet-stream")
                staged.append(skey)
                meta.append({"filename": name, "mime": mime, "key": skey, "bytes": len(raw),
                             "sha256": hashlib.sha256(raw).hexdigest()})  # fmt: skip
            ids = tracing.ids(sp)
            job.trace_id, job.span_id = ids.get("trace_id"), ids.get("span_id")
            st.insert(job, meta, actor=actor, accepted_by=instance_name(), key=key,
                      priority=priority)  # fmt: skip
    except Duplicate:  # the other copy of this upload won the insert; this one leaves no trace
        _drop_staged(st, staged, job.id)
        first = st.by_key(key)
        if first is None:  # pragma: no cover - the winner's row cannot vanish between the two
            raise NotAccepted(f"idempotency key {key} taken by a job that is gone") from None
        log.info("job_duplicate_upload", job=first.id, actor=actor, idempotency_key=key,
                 raced=True)  # fmt: skip
        metrics.JOB_DUPLICATES.inc()
        return first
    except Exception as exc:
        log.error("job_not_accepted", job=job.id, error=f"{type(exc).__name__}: {exc}")
        _drop_staged(st, staged, job.id)
        raise NotAccepted(str(exc)) from exc
    dispatch(job.id)
    log.info("job_accepted", job=job.id, documents=len(files), instance=instance_name())
    return job


def _drop_staged(st: PgJobStore, staged: list[str], job_id: str) -> None:
    """Remove the staged copies of an upload that was not accepted (or was a duplicate)."""
    for key in staged:
        try:
            st.objects.delete(key)
        except Exception:  # noqa: BLE001 - the store is likely what failed
            log.warning("job_staged_delete_failed", job=job_id, key=key)


def priority_of(job_id: str) -> int:
    """The lane this job was accepted on (E9-S3); ``NORMAL_PRIORITY`` when it is unknown."""
    try:
        with session_scope() as c:
            p = c.execute(text("SELECT priority FROM web_job WHERE id = :i"),
                          {"i": job_id}).scalar()  # fmt: skip
        return NORMAL_PRIORITY if p is None else int(p)
    except Exception:  # noqa: BLE001 - never let the lane lookup fail a dispatch
        return NORMAL_PRIORITY


def dispatch(job_id: str, priority: int | None = None) -> None:
    """Hand an accepted job to a runner. Never fails the upload: the job is already durable, and
    one that no runner claims is picked up by a sweep after ``CDI_JOB_UNCLAIMED_S``. The job's
    lane rides along as the message priority (E9-S3)."""
    try:
        if get_settings().job_execution == "queue":
            from cdi_adapter.worker import celery_app

            celery_app.send_task(WEB_TASK, args=[job_id], queue=WEB_QUEUE,
                                 priority=priority_of(job_id) if priority is None
                                 else priority)  # fmt: skip
        else:
            from cdi_adapter.webapp.jobs import get_runner

            get_runner().submit_stored(job_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("job_dispatch_failed", job=job_id, error=str(exc))


def dispatch_extract(job_id: str) -> None:
    """Hand a job whose Phase 1 is done to the GPU pool (backlog E9-S2). ``handoff`` tells the
    runner there that this is the same run carrying on, not a retry of a failed one. Raises: the
    caller (``JobRunner._hand_over``) logs it, and the job is an orphan a sweep sends on again.
    It keeps the job's lane (E9-S3), so an urgent job stays urgent on the GPU queue too."""
    from cdi_adapter.worker import celery_app

    celery_app.send_task(WEB_TASK, args=[job_id], kwargs={"handoff": True}, queue=GPU_QUEUE,
                         priority=priority_of(job_id))  # fmt: skip


def wait(job_id: str, timeout: float = 600, poll: float = 0.5) -> Job:
    """Poll until the stored job has finished (tests, the HA drill)."""
    deadline = time.monotonic() + timeout
    while True:
        job = load(job_id)
        if job is not None and job.finished.is_set():
            return job
        if time.monotonic() > deadline:
            raise TimeoutError(job_id)
        time.sleep(poll)


class PageNotReplaced(Exception):
    """The page could not be replaced, and nothing about the job was changed (UP-S2)."""


def replace_page(job_id: str, index: int, name: str, raw: bytes, mime: str | None, *,
                 actor: str, st: PgJobStore | None = None) -> Job:  # fmt: skip
    """Put a retaken page in place of one page of an existing job (backlog UP-S2).

    The person at the desk is told a page is unreadable and retakes **that page**; the other pages
    of the prescription keep their work. So: the new file is staged under the same key, the job's
    file list and that one document are rewritten, the job is reopened and dispatched - and the
    runner's resume runs Phase 1 for the replaced page only, because every other document is
    already finished (``JobRunner._resume``).

    Refused (with nothing changed) when the job or the page does not exist, or while a runner
    still holds the job: replacing a page under a live run would race its own writes.
    """
    if not raw:
        raise PageNotReplaced("the replacement page is empty")
    st = st or store()
    job = st.load(job_id)
    if job is None:
        raise PageNotReplaced("job not found")
    if not 0 <= index < len(job.docs):
        raise PageNotReplaced(f"this job has pages 1-{len(job.docs)}")
    key = staging_key(job_id, index)
    meta = {"filename": name, "mime": mime, "key": key, "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest()}  # fmt: skip
    fresh = DocState(filename=name)
    st.objects.put_bytes(key, raw, mime or "application/octet-stream")
    with session_scope() as c:
        row = c.execute(text(
            "UPDATE web_job SET files = jsonb_set(files, :path, CAST(:meta AS jsonb)), "
            "  docs = jsonb_set(docs, :path, CAST(:doc AS jsonb)), state = 'running', "
            "  finished_at = NULL, lease_owner = NULL, lease_until = NULL, updated_at = now() "
            "WHERE id = :i AND (finished_at IS NOT NULL OR lease_until IS NULL "
            "  OR lease_until < now()) RETURNING id"),
            {"i": job_id, "path": "{" + str(index) + "}", "meta": json.dumps(meta),
             "doc": json.dumps(dataclasses.asdict(fresh))}).first()  # fmt: skip
        if row is None:
            raise PageNotReplaced("the job is still being processed; try again when it has ended")
        repo.write_audit(c, actor=actor, action="update", entity="web_job", entity_id=job_id,
                         detail={"replaced_page": index + 1, "filename": name,
                                 "bytes": len(raw)})  # fmt: skip
    log.info("job_page_replaced", job=job_id, page=index + 1, filename=name, actor=actor)
    dispatch(job_id)
    out = st.load(job_id)
    if out is None:  # pragma: no cover - it was just updated
        raise PageNotReplaced("job not found")
    return out
