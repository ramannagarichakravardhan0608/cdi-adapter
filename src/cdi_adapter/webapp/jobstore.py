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

from botocore.exceptions import ClientError
from sqlalchemy import text

from cdi_adapter import repo, tracing
from cdi_adapter.config import get_settings
from cdi_adapter.db import session_scope
from cdi_adapter.logging import get_logger
from cdi_adapter.storage import ObjectStore, get_store
from cdi_adapter.webapp.jobs import DocState, Job

log = get_logger(__name__)

WEB_QUEUE = "cdi-web"
WEB_TASK = "cdi.web_job"
# names this process's leases: unique per process start, readable in web_job.lease_owner
OWNER = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"
_JOB_ID = frozenset("0123456789abcdef")


class NotAccepted(Exception):
    """The upload could not be made durable, so it was not acknowledged."""


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
              mismatch=row["mismatch"], trace_id=row["trace_id"], span_id=row["span_id"],
              attempts=row["attempts"])  # fmt: skip
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

    def insert(self, job: Job, files: list[dict[str, Any]], *, actor: str,
               accepted_by: str) -> None:  # fmt: skip
        with session_scope() as c:
            c.execute(text(
                "INSERT INTO web_job (id, state, patient_id, registry_patient, abha, files, docs, "
                "trace_id, span_id, created_by, accepted_by) VALUES (:i, 'running', :p, "
                "CAST(:r AS jsonb), :a, CAST(:f AS jsonb), CAST(:d AS jsonb), :t, :s, :u, :b)"),
                {"i": job.id, "p": job.patient_id,
                 "r": json.dumps(job.registry_patient, default=str)
                 if job.registry_patient is not None else None,
                 "a": job.abha, "f": json.dumps(files), "d": _docs(job), "t": job.trace_id,
                 "s": job.span_id, "u": actor, "b": accepted_by})  # fmt: skip
            repo.write_audit(
                c,
                actor=actor,
                action="create",
                entity="web_job",
                entity_id=job.id,
                detail={"documents": len(files), "accepted_by": accepted_by},
            )

    def load(self, job_id: str) -> Job | None:
        if not _valid_id(job_id):
            return None
        with session_scope() as c:
            row = c.execute(text("SELECT * FROM web_job WHERE id = :i"),
                            {"i": job_id}).mappings().first()  # fmt: skip
        return _job(row) if row else None

    # ------------------------------------------------------------ the runner's side

    def claim(self, job_id: str, owner: str, lease_s: float) -> Job | None:
        with session_scope() as c:
            row = c.execute(text(
                "UPDATE web_job SET lease_owner = :o, "
                "lease_until = now() + make_interval(secs => :l), attempts = attempts + 1, "
                "started_at = coalesce(started_at, now()), updated_at = now() "
                "WHERE id = :i AND finished_at IS NULL "
                "AND (lease_until IS NULL OR lease_until < now()) RETURNING *"),
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
        """Give the lease up now (the run could not start), so a sweep retries the job."""
        with session_scope() as c:
            c.execute(text("UPDATE web_job SET lease_until = now() - interval '1 second' "
                           "WHERE id = :i AND lease_owner = :o AND finished_at IS NULL"),
                      {"i": job_id, "o": owner})  # fmt: skip

    def files(self, job: Job, indices: list[int]) -> dict[int, tuple[str, bytes, str | None]]:
        with session_scope() as c:
            meta: Any = c.execute(text("SELECT files FROM web_job WHERE id = :i"),
                             {"i": job.id}).scalar_one()  # fmt: skip
        out: dict[int, tuple[str, bytes, str | None]] = {}
        for i in indices:
            f = meta[i]
            try:
                raw = self.objects.get_bytes(f["key"])
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
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
                "ORDER BY created_at LIMIT :n"),
                {"u": unclaimed_s, "n": limit}).scalars())  # fmt: skip

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


def submit(files: list[tuple[str, bytes, str | None]], *, actor: str,
           registry_patient: dict[str, Any] | None = None, patient_id: str | None = None,
           abha: str | None = None) -> Job:  # fmt: skip
    """Make the upload durable, then dispatch it. Raises :class:`NotAccepted` (nothing kept)
    when the files or the row could not be written: the caller must not acknowledge."""
    job = Job(id=uuid.uuid4().hex, docs=[DocState(filename=f[0]) for f in files],
              patient_id=patient_id, registry_patient=registry_patient, abha=abha)  # fmt: skip
    st = store()
    staged: list[str] = []
    try:
        with tracing.span("job.accept", {"cdi.job_id": job.id,
                                         "cdi.documents": len(files)}) as sp:  # fmt: skip
            meta = []
            for i, (name, raw, mime) in enumerate(files):
                key = staging_key(job.id, i)
                st.objects.put_bytes(key, raw, mime or "application/octet-stream")
                staged.append(key)
                meta.append({"filename": name, "mime": mime, "key": key, "bytes": len(raw),
                             "sha256": hashlib.sha256(raw).hexdigest()})  # fmt: skip
            ids = tracing.ids(sp)
            job.trace_id, job.span_id = ids.get("trace_id"), ids.get("span_id")
            st.insert(job, meta, actor=actor, accepted_by=instance_name())
    except Exception as exc:
        log.error("job_not_accepted", job=job.id, error=f"{type(exc).__name__}: {exc}")
        for key in staged:
            try:
                st.objects.delete(key)
            except Exception:  # noqa: BLE001 - the store is likely what failed
                pass
        raise NotAccepted(str(exc)) from exc
    dispatch(job.id)
    log.info("job_accepted", job=job.id, documents=len(files), instance=instance_name())
    return job


def dispatch(job_id: str) -> None:
    """Hand an accepted job to a runner. Never fails the upload: the job is already durable, and
    one that no runner claims is picked up by a sweep after ``CDI_JOB_UNCLAIMED_S``."""
    try:
        if get_settings().job_execution == "queue":
            from cdi_adapter.worker import celery_app

            celery_app.send_task(WEB_TASK, args=[job_id], queue=WEB_QUEUE)
        else:
            from cdi_adapter.webapp.jobs import get_runner

            get_runner().submit_stored(job_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("job_dispatch_failed", job=job_id, error=str(exc))


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
