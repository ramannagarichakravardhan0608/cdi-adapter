"""Web-app job model (ARCHITECTURE §7.2): parallel, then serial, then human review.

* **Phase 1 (parallel)** — ``ThreadPoolExecutor(max(2, CDI_JOB_MAX_WORKERS))``: every document runs
  ``ingest -> OCR(rapidocr) -> classify -> [OCR(vlm) if handwritten]`` concurrently.
* **Phase 2** — ``extract -> terminology -> validate``. How many extractions run at once is
  :func:`extract_slots` (backlog E2-S2): **one with ``hf``**, which does one forward pass at a
  time, and up to ``vllm_max_num_seqs`` with ``vllm``, which batches concurrent requests. The
  slots are shared by every job on the GPU. When no patient was chosen, the first document runs
  alone and fixes the patient; the other documents then attach to it in parallel.
* The job then stops at ``state = review`` (it does **not** auto-project). A registry patient
  chosen on the form arms the mismatch guard: a document for someone else stops the job with
  ``state = mismatch`` and its facts purged.

Job state is in memory (``_jobs``); moving it onto the durable queue is backlog E9-S1.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from cdi_adapter import metrics, tracing
from cdi_adapter.config import Settings, get_settings
from cdi_adapter.logging import get_logger

log = get_logger(__name__)

STEPS = ("ingest", "ocr", "classify", "extract", "terminology", "validate")


def extract_slots(settings: Settings | None = None) -> int:
    """Phase-2 extractions allowed in flight at once, across all jobs (backlog E2-S2, §7.2).

    ``hf`` is always 1: it does one forward pass at a time, so more would only queue on the GPU
    (or OOM). ``vllm`` batches concurrent requests: ``CDI_EXTRACT_CONCURRENCY``, or when that is 0
    (auto) the engine's own ``vllm_max_num_seqs``. ``stub`` is 1 unless set explicitly.
    """
    s = settings or get_settings()
    if s.mlserve_backend == "hf":
        return 1
    if s.extract_concurrency:
        return s.extract_concurrency
    return s.vllm_max_num_seqs if s.mlserve_backend == "vllm" else 1


@dataclass
class Stages:
    """The pipeline stage functions (the real ones by default)."""

    ingest: Callable[..., Any]
    ocr: Callable[..., Any]
    classify: Callable[..., Any]
    extract: Callable[..., Any]
    terminology: Callable[..., Any]
    validate: Callable[..., Any]
    counts: Callable[[str], dict[str, int]]
    merge_identity: Callable[[Any], Any]
    # a validated document lands in the shared /reviewer workbasket (§4.5)
    enqueue: Callable[[str, str], Any] = lambda document_id, job_id: None
    # E2-S8: several documents' extraction in one batched model call, and the page / OCR-block
    # counts that decide whether a document is short enough to join a batch
    extract_batch: Callable[..., list[Any]] | None = None
    shape: Callable[[str], dict[str, int]] | None = None

    @classmethod
    def real(cls) -> Stages:
        from sqlalchemy import text

        from cdi_adapter import repo
        from cdi_adapter.classify.service import classify_document
        from cdi_adapter.db import session_scope
        from cdi_adapter.extract.service import extract_document, extract_documents
        from cdi_adapter.ingest.service import ingest_bytes
        from cdi_adapter.mpi.service import merge_identity_evidence
        from cdi_adapter.ocr.service import ocr_document
        from cdi_adapter.terminology.service import bind_document
        from cdi_adapter.validate.service import validate_document

        def counts(doc_id: str) -> dict[str, int]:
            with session_scope() as c:
                rows = c.execute(
                    text("SELECT review_state, count(*) FROM clinical_fact "
                         "WHERE :d = ANY(source_doc_ids) AND is_current GROUP BY review_state"),
                    {"d": doc_id},
                ).all()  # fmt: skip
            by = {r[0]: r[1] for r in rows}
            return {"facts": sum(by.values()),
                    "accepted": by.get("auto_accepted", 0) + by.get("clinician_confirmed", 0)
                    + by.get("corrected", 0),
                    "in_review": by.get("in_review", 0) + by.get("pending", 0)}  # fmt: skip

        def merge(pid):
            with session_scope() as c:
                return merge_identity_evidence(c, pid)

        def enqueue(document_id: str, job_id: str):
            from cdi_adapter.canon.repo import create_rv_item_for_document

            with session_scope() as c:
                return create_rv_item_for_document(c, document_id, job_id=job_id)

        def shape(doc_id: str) -> dict[str, int]:
            with session_scope() as c:
                return repo.document_shape(c, doc_id)

        return cls(ingest_bytes, ocr_document, classify_document, extract_document,
                   bind_document, validate_document, counts, merge, enqueue,
                   extract_batch=extract_documents, shape=shape)  # fmt: skip


@dataclass
class DocState:
    filename: str
    status: str = "pending"  # pending | running | done | error | mismatch | skipped
    step: str | None = None
    steps_done: list[str] = field(default_factory=list)
    document_id: str | None = None
    doc_type: str | None = None
    facts: int = 0
    accepted: int = 0
    in_review: int = 0
    error: str | None = None


@dataclass
class Job:
    id: str
    docs: list[DocState]
    state: str = "running"  # running | review | mismatch | error
    patient_id: str | None = None
    registry_patient: dict[str, Any] | None = None
    abha: str | None = None
    mismatch: dict[str, Any] | None = None
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    # documents past Phase 1 that have not started extraction yet (cdi_extract_waiting, E10-S1)
    extract_queued: int = field(default=0, repr=False)
    # upload time: the start of the 5-minute SLA window (ADR-0002, E10-S2 alert)
    started_at: float = field(default_factory=time.time, repr=False)
    # the job's trace (E10-S3): every stage of every document, the model calls and the FHIR
    # projection are spans of this one trace
    span: Any = field(default=None, repr=False)
    trace_id: str | None = None
    # set when the job's run has fully ended: a mismatch changes `state` while other documents
    # may still be extracting, so `state` alone does not mean "finished"
    finished: threading.Event = field(default_factory=threading.Event, repr=False)

    def as_json(self) -> dict[str, Any]:
        return {"job_id": self.id, "state": self.state, "patient_id": self.patient_id,
                "mismatch": self.mismatch, "trace_id": self.trace_id,
                "documents": [d.__dict__ for d in self.docs]}  # fmt: skip


class JobRunner:
    def __init__(self, stages: Stages | None = None, max_workers: int | None = None,
                 extract_concurrency: int | None = None, batch_max: int | None = None,
                 batch_max_blocks: int | None = None) -> None:  # fmt: skip
        self.stages = stages or Stages.real()
        # E2-S8: up to batch_max short documents of one doc_type share one model call (1 = off)
        self.batch_max = batch_max or get_settings().extract_batch_max
        self.batch_max_blocks = batch_max_blocks or get_settings().extract_batch_max_blocks
        n = max(2, max_workers or get_settings().job_max_workers)
        self._pool = ThreadPoolExecutor(max_workers=n, thread_name_prefix="job-p1")
        # E2-S2: with one slot this is exactly the old behaviour (one job at a time, serial
        # Phase 2); with k slots, k jobs run and k extractions share the GPU at once
        self.extract_slots = extract_concurrency or extract_slots()
        k = self.extract_slots
        self._job_pool = ThreadPoolExecutor(max_workers=k, thread_name_prefix="job")
        self._p2_pool = ThreadPoolExecutor(max_workers=k, thread_name_prefix="job-p2")
        self._slots = threading.BoundedSemaphore(k)
        self._waited: dict[int, float] = {}  # slot wait per thread (E10-S1 timing)
        self._jobs: dict[str, Job] = {}

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def create(self, files: list[tuple[str, bytes, str | None]], *,
               registry_patient: dict[str, Any] | None = None, patient_id: str | None = None,
               abha: str | None = None) -> Job:  # fmt: skip
        job = Job(id=uuid.uuid4().hex, docs=[DocState(filename=f[0]) for f in files],
                  patient_id=patient_id, registry_patient=registry_patient, abha=abha)  # fmt: skip
        job.span = tracing.start_job(job.id)
        job.trace_id = tracing.ids(job.span).get("trace_id")
        self._jobs[job.id] = job
        self._job_pool.submit(self._run, job, files)
        return job

    def wait(self, job_id: str, timeout: float = 600) -> Job:
        """Block until the job's run has fully ended (every document done, skipped or failed)."""
        job = self._jobs[job_id]
        if not job.finished.wait(timeout):
            raise TimeoutError(job_id)
        return job

    # ---------------------------------------------------------------- pipeline

    def _phase1(self, job: Job, i: int, raw: bytes, filename: str, mime: str | None) -> None:
        d, s = job.docs[i], self.stages
        d.status = "running"
        try:
            d.step = "ingest"
            with metrics.stage("ingest"):
                res = s.ingest(raw, filename, mime, patient_id=job.patient_id)
            d.document_id = str(res.document_id)
            d.steps_done.append("ingest")
            d.step = "ocr"
            with metrics.stage("ocr", d.document_id):
                s.ocr(d.document_id, force_engine="rapidocr")
            d.steps_done.append("ocr")
            d.step = "classify"
            with metrics.stage("classify", d.document_id):
                cls = s.classify(d.document_id)
            d.doc_type = cls.get("doc_type")
            if cls.get("is_handwritten") and get_settings().handwritten_uses_vlm:
                d.step = "ocr"
                with metrics.stage("ocr", d.document_id):  # the handwriting pass on the VLM
                    s.ocr(d.document_id, force_engine="vlm")
            d.steps_done.append("classify")
        except Exception as exc:  # one bad document never sinks the job
            d.status, d.error = "error", f"{type(exc).__name__}: {exc}"
            log.error("job_doc_failed", job=job.id, filename=filename, error=d.error)

    def _groups(self, docs: list[DocState]) -> list[list[DocState]]:
        """E2-S8: short documents (one page, at most ``batch_max_blocks`` OCR blocks) of the same
        doc_type go together, up to ``batch_max`` per group; every other document is alone.
        Same doc_type = same schema and a similar output length, so no sequence in the batch
        waits long on another. Order is kept within each doc_type."""
        s = self.stages
        if self.batch_max <= 1 or s.extract_batch is None or s.shape is None:
            return [[d] for d in docs]
        groups: list[list[DocState]] = []
        open_by_type: dict[str, list[DocState]] = {}
        for d in docs:
            try:
                sh = s.shape(str(d.document_id))
                fits = sh["pages"] == 1 and sh["blocks"] <= self.batch_max_blocks
                short = bool(d.doc_type) and fits
            except Exception as exc:  # unknown shape: extract it alone
                log.warning("job_shape_failed", document_id=d.document_id, error=str(exc))
                short = False
            if not short:
                groups.append([d])
                continue
            group = open_by_type.get(str(d.doc_type))
            if group is None or len(group) >= self.batch_max:
                group = open_by_type[str(d.doc_type)] = []
                groups.append(group)
            group.append(d)
        return groups

    def _phase2_group(self, job: Job, group: list[DocState]) -> None:
        if len(group) == 1:
            self._phase2(job, group[0])
            return
        with job.lock:
            stopped = job.state == "mismatch"
        if stopped:  # _dequeue takes job.lock itself, so it runs outside it
            for d in group:
                d.status, d.step = "skipped", None
            self._dequeue(job, len(group))
            return
        for d in group:
            d.step = "extract"
        s = self.stages
        extract_batch = s.extract_batch
        assert extract_batch is not None
        outs: list[Any]
        t0 = time.perf_counter()
        docs = [str(d.document_id) for d in group]
        try:  # one batched model call holds one GPU slot
            with tracing.span("stage.extract", {"cdi.stage": "extract",
                                                "cdi.document_ids": docs}):  # fmt: skip
                outs = self._with_slot(job, len(group), lambda: extract_batch(
                    [d.document_id for d in group], patient_id=job.patient_id,
                    expected_patient=job.registry_patient))  # fmt: skip
        except Exception as exc:
            outs = [exc] * len(group)
        took = time.perf_counter() - t0 - self._waited.get(threading.get_ident(), 0.0)
        for out in outs:  # every document of the batch waited for the whole call
            metrics.STAGE_SECONDS.labels("extract").observe(took)
            if isinstance(out, Exception):
                metrics.STAGE_FAILURES.labels("extract").inc()
        log.info("job_extract_batch", job=job.id, documents=len(group), doc_type=group[0].doc_type)
        for d, out in zip(group, outs, strict=True):
            self._after_extract(job, d, out)

    def _phase2(self, job: Job, d: DocState) -> None:
        with job.lock:
            stopped = job.state == "mismatch"  # another document of this job already stopped it
        if stopped:
            d.status, d.step = "skipped", None
            self._dequeue(job, 1)
            return
        d.step = "extract"
        out: Any
        try:  # the only GPU-bound step
            out = self._with_slot(job, 1, lambda: self._timed_extract(job, d))
        except Exception as exc:
            out = exc
        self._after_extract(job, d, out)

    def _timed_extract(self, job: Job, d: DocState) -> Any:
        with metrics.stage("extract", d.document_id):
            return self.stages.extract(d.document_id, patient_id=job.patient_id,
                                       expected_patient=job.registry_patient)  # fmt: skip

    def _with_slot(self, job: Job, docs: int, fn: Callable[[], Any]) -> Any:
        """Runs ``fn`` holding one GPU extraction slot. The ``docs`` documents leave
        ``cdi_extract_waiting`` (queued since Phase 1 ended) once the slot is held, and the call
        counts in ``cdi_extract_in_flight`` (E10-S1). The slot wait is remembered per thread so a
        batch's timing can leave it out."""
        t0 = time.perf_counter()
        self._slots.acquire()
        self._dequeue(job, docs)
        self._waited[threading.get_ident()] = time.perf_counter() - t0
        metrics.EXTRACT_IN_FLIGHT.inc()
        try:
            return fn()
        finally:
            metrics.EXTRACT_IN_FLIGHT.dec()
            self._slots.release()

    def _after_extract(self, job: Job, d: DocState, out: Any) -> None:
        """The rest of Phase 2 for one document, given its extraction result or exception."""
        from cdi_adapter.extract.service import IdentityMismatch

        s = self.stages
        try:
            if isinstance(out, Exception):
                raise out
            with job.lock:
                if out.get("patient_id") and job.patient_id is None:
                    job.patient_id = str(out["patient_id"])
            d.steps_done.append("extract")
            if out.get("skipped"):
                d.status, d.step = "skipped", None
                return
            d.step = "terminology"
            with metrics.stage("terminology", d.document_id):
                s.terminology(d.document_id)
            d.steps_done.append("terminology")
            d.step = "validate"
            with metrics.stage("validate", d.document_id):
                s.validate(d.document_id)
            d.steps_done.append("validate")
            s.enqueue(str(d.document_id), job.id)
            d.__dict__.update(s.counts(str(d.document_id)))
            d.status, d.step = "done", None
        except IdentityMismatch as exc:
            d.status, d.step = "mismatch", None
            with job.lock:
                job.state = "mismatch"
                job.mismatch = {"filename": d.filename,
                               "selected": (job.registry_patient or {}).get("name"),
                               "document_name": exc.found.name}  # fmt: skip
        except Exception as exc:
            d.status, d.error = "error", f"{type(exc).__name__}: {exc}"
            log.error("job_doc_failed", job=job.id, filename=d.filename, error=d.error)

    def _in_job(self, job: Job, fn: Callable[..., Any], *args: Any) -> Any:
        """Run ``fn`` in a pool thread inside the job's trace (threads do not inherit it)."""
        with tracing.attached(job.span):
            return fn(*args)

    def _run(self, job: Job, files: list[tuple[str, bytes, str | None]]) -> None:
        with tracing.attached(job.span):
            self._run_traced(job, files)

    def _run_traced(self, job: Job, files: list[tuple[str, bytes, str | None]]) -> None:
        try:
            futures = [self._pool.submit(self._in_job, job, self._phase1, job, i, raw, name, mime)
                       for i, (name, raw, mime) in enumerate(files)]  # fmt: skip
            for f in futures:
                f.result()
            ready = [d for d in job.docs if d.status == "running"]
            self._queue(job, len(ready))  # E10-S1 queue depth: waiting for extraction
            # no patient yet: documents go one by one until one of them fixes it, so every
            # document of the job lands on the same patient (§7.2)
            while ready and job.patient_id is None and job.state != "mismatch":
                self._phase2(job, ready.pop(0))
            # then the rest in parallel, bounded by the shared extraction slots (E2-S2); short
            # documents of one type may share a batched model call (E2-S8)
            groups = self._groups(ready)
            for f in [self._p2_pool.submit(self._in_job, job, self._phase2_group, job, g)
                      for g in groups]:  # fmt: skip
                f.result()
            if job.patient_id and job.state != "mismatch":
                self.stages.merge_identity(job.patient_id)
            if job.state == "running":
                job.state = "review"
        except Exception as exc:  # pragma: no cover - defensive
            job.state = "error"
            log.error("job_failed", job=job.id, error=str(exc))
        finally:
            self._dequeue(job, job.extract_queued)  # nothing of this job is left waiting
            metrics.JOB_SECONDS.observe(time.time() - job.started_at)
            if job.span is not None:
                job.span.set_attribute("cdi.job_state", job.state)
                job.span.end()
            job.finished.set()
        log.info("job_finished", job=job.id, state=job.state)

    @staticmethod
    def _queue(job: Job, n: int) -> None:
        with job.lock:
            job.extract_queued += n
        metrics.EXTRACT_WAITING.inc(n)

    @staticmethod
    def _dequeue(job: Job, n: int) -> None:
        with job.lock:
            n = min(n, job.extract_queued)
            job.extract_queued -= n
        metrics.EXTRACT_WAITING.dec(n)


_runner: JobRunner | None = None


def all_jobs() -> list[Job]:
    """Every job of this process, without starting a runner (read by ``/metrics``, E10-S1)."""
    return list(_runner._jobs.values()) if _runner is not None else []


def get_runner() -> JobRunner:
    global _runner
    if _runner is None:
        _runner = JobRunner()
    return _runner
