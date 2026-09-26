"""Celery pipeline for the folder-watch / ingest-API path (ARCHITECTURE §3.1, TECH_STACK §5).

Broker + result backend: Redis (``CDI_REDIS_URL``). Queue ``cdi``. Each stage is a retryable
task; the chain is the five tasks TECH_STACK §5 names: ``ingest_file -> classify_document ->
ocr_document -> extract_document -> bind_document``. This path classifies **before** OCR (the
classifier falls back to a one-off RapidOCR hint, §5 S2), then OCR runs with the engine the
classification picks.

    celery -A cdi_adapter.worker.celery_app worker -Q cdi

It also runs the web app's jobs (backlog E10-S9, the core of E9-S1): task ``cdi.web_job`` on queue
``cdi-web``, started by ``infra/runpod/start_worker.sh`` as a *threads* pool, so all of a worker's
jobs share one runner and its GPU extraction slots (E2-S2)::

    celery -A cdi_adapter.worker.celery_app worker -Q cdi-web --pool threads --concurrency <n>
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from celery import Celery, chain, signals

from cdi_adapter.config import get_settings
from cdi_adapter.logging import get_logger

log = get_logger(__name__)

_s = get_settings()
celery_app = Celery("cdi", broker=_s.redis_url, backend=_s.redis_url)
celery_app.conf.update(
    task_default_queue="cdi",
    task_acks_late=True,  # a crashed worker's task is redelivered, not lost
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_routes={"cdi.web_job": {"queue": "cdi-web"}},
)

MAX_RETRIES = 3
RETRY_BACKOFF = 10  # seconds, doubled per attempt


def _retry(task, exc: Exception) -> Any:
    countdown = RETRY_BACKOFF * (2**task.request.retries)
    raise task.retry(exc=exc, countdown=countdown, max_retries=MAX_RETRIES)


@celery_app.task(bind=True, name="cdi.ingest_file", max_retries=MAX_RETRIES)
def ingest_file(self, path: str, source_channel: str = "folder") -> str:
    from cdi_adapter.ingest.service import ingest_bytes

    p = Path(path)
    try:
        res = ingest_bytes(p.read_bytes(), p.name, source_channel=source_channel)
    except FileNotFoundError:
        raise
    except Exception as exc:  # noqa: BLE001
        _retry(self, exc)
    log.info("folder_ingested", path=path, document_id=str(res.document_id),
             deduplicated=res.deduplicated)  # fmt: skip
    return str(res.document_id)


@celery_app.task(bind=True, name="cdi.classify_document", max_retries=MAX_RETRIES)
def classify_document(self, document_id: str) -> str:
    from cdi_adapter.classify.service import classify_document as run

    try:
        run(document_id)
    except Exception as exc:  # noqa: BLE001
        _retry(self, exc)
    return document_id


@celery_app.task(bind=True, name="cdi.ocr_document", max_retries=MAX_RETRIES)
def ocr_document(self, document_id: str) -> str:
    from cdi_adapter.ocr.service import ocr_document as run

    try:
        run(document_id)  # engine chosen from the classification
    except Exception as exc:  # noqa: BLE001
        _retry(self, exc)
    return document_id


@celery_app.task(bind=True, name="cdi.extract_document", max_retries=MAX_RETRIES)
def extract_document(self, document_id: str) -> str:
    from cdi_adapter.extract.service import extract_document as run

    try:
        run(document_id)
    except Exception as exc:  # noqa: BLE001
        _retry(self, exc)
    return document_id


@celery_app.task(bind=True, name="cdi.bind_document", max_retries=MAX_RETRIES)
def bind_document(self, document_id: str) -> str:
    from cdi_adapter.terminology.service import bind_document as run

    try:
        run(document_id)
    except Exception as exc:  # noqa: BLE001
        _retry(self, exc)
    return document_id


def pipeline_after_ingest():
    """The stages that follow ingest, as a Celery chain signature."""
    return chain(classify_document.s(), ocr_document.s(), extract_document.s(),
                 bind_document.s())  # fmt: skip


def submit_file(path: str | Path, source_channel: str = "folder"):
    """Queue one file through the whole pipeline."""
    return chain(ingest_file.s(str(path), source_channel), pipeline_after_ingest()).apply_async()


def submit_document(document_id: str):
    """Queue an already-ingested document (the ingest API) through the rest of the pipeline."""
    return chain(classify_document.s(document_id), ocr_document.s(), extract_document.s(),
                 bind_document.s()).apply_async()  # fmt: skip


# ------------------------------------------------------------------ web-app jobs (E10-S9)


@celery_app.task(name="cdi.web_job", acks_late=True, ignore_result=True)
def web_job(job_id: str) -> str:
    """Run (or resume) one stored web-app job to its end. The message only says "run this job":
    the job itself is the ``web_job`` row plus its staged uploads, and the claim decides who runs
    it, so a duplicated or redelivered message is a no-op and a lost one is covered by the sweep."""
    from cdi_adapter.webapp.jobs import get_runner

    return get_runner().run_stored(job_id)


@signals.worker_init.connect
def _start_web_runner(sender: Any = None, **_: Any) -> None:
    """A worker that consumes ``cdi-web`` starts its runner with the lease heartbeat and the
    orphan sweep at once (not on its first task), plus its tracing and ``/metrics`` port."""
    queues = getattr(getattr(getattr(sender, "app", None), "amqp", None), "queues", None)
    if queues is None or "cdi-web" not in (queues.consume_from or {}):
        return
    from cdi_adapter import tracing
    from cdi_adapter.logging import configure
    from cdi_adapter.webapp.jobs import get_runner

    configure()
    tracing.setup_from_settings(tracing.WORKER)
    port = get_settings().worker_metrics_port
    if port:
        from prometheus_client import start_http_server

        start_http_server(port, addr="127.0.0.1")
    runner = get_runner()
    log.info("web_runner_started", runner=runner.owner, slots=runner.extract_slots)
