"""CareFlow web app on :8888 (ARCHITECTURE §7, §7.1).

| Method / path                            | Purpose                                              |
|------------------------------------------|------------------------------------------------------|
| GET  /                                   | upload page                                          |
| GET  /healthz                            | {db, s3, mlserve}                                    |
| GET  /readyz                             | can THIS instance take uploads (the E10-S9 balancer) |
| POST /api/jobs                           | files[] (<=10) + abha? + patient_ref? -> 202 |
| GET  /api/jobs/{id}                      | job + patient + per-document status                  |
| GET  /api/jobs/{id}/facts                | facts grouped by document, page url + bbox           |
| POST /api/jobs/{id}/generate             | apply keep/drop/corrections, then re-project         |
| GET  /api/jobs/{id}/fhir[/download]      | project_patient, re-projected live                   |
| GET  /api/patients/{id}/fhir             | bundles for an existing patient                      |
| GET  /api/documents/{id}/bundle          | single-document bundle (non-persisting)              |
| GET  /api/documents/{id}/evidence        | doc_type + OCR block count + facts table             |
| GET  /api/documents/{id}/pages/{n}       | the normalised page PNG                              |
| GET  /api/documents/{id}/original        | the scan exactly as uploaded                         |
| GET  /api/registry/lookup?q= · POST /api/registry | patient registry                           |

plus the S8 routes (``/review``, ``/api/review/*``, ``/api/facts/*/review``), the ``/reviewer``
workbasket/worklist routes and the ``/admin`` inspector (§4.5).

Stateless (backlog E10-S9): jobs are in PostgreSQL (``webapp.jobstore``), sessions too (E11-S1), so
several instances run behind the balancer (``infra/runpod/Caddyfile.lb``) and any of them serves
any request; an upload is acknowledged only once it is durable.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import text

from cdi_adapter import repo, result, tracing
from cdi_adapter.config import get_settings
from cdi_adapter.db import session_scope
from cdi_adapter.fhir.service import project_document, project_patient
from cdi_adapter.ingest.pages import UnsupportedDocument, sniff_mime
from cdi_adapter.logging import configure, get_logger
from cdi_adapter.mpi import registry
from cdi_adapter.mpi.service import patient_summary
from cdi_adapter.storage import get_store
from cdi_adapter.webapp import (
    admin,
    auth,
    conflict_page,
    conflicts,
    jobstore,
    monitor,
    quality_report,
    result_page,
    review,
    review_page,
    reviewer,
)
from cdi_adapter.webapp.page import upload_page

log = get_logger(__name__)
MAX_FILES = 10
# UP-S1 AC3: what the person at the desk is told when a file is not a picture, a PDF or a scan.
# The same words on the screen (webapp/page.py) and from the server, which is the one that
# cannot be got round.
NOT_A_SCAN = "Please add a photo, PDF or scan"
# UP-S1 AC6: an ABHA number is 14 digits, usually written 91-1234-5678-9012.
BAD_ABHA = ("The ABHA number must be 14 digits, like 91-1234-5678-9012. "
            "Correct it, or leave the box empty.")  # fmt: skip


def _must_be_a_scan(name: str | None, raw: bytes) -> None:
    """Refuse a file that is not a PDF or a supported picture (UP-S1 AC3) - before a job exists,
    in plain words. Judged as S1 will judge it: by its first bytes, then by its extension."""
    try:
        mime = sniff_mime(raw, name)
    except UnsupportedDocument as exc:
        raise HTTPException(415, NOT_A_SCAN) from exc
    # LS-S3: an upload is untrusted too. One with more pages than the limit, or a picture too
    # large to open safely, is refused whole and in plain words - never read up to the limit.
    from cdi_adapter.ingest.pages import DocumentRefused, inspect

    try:
        inspect(raw, mime)
    except DocumentRefused as exc:
        raise HTTPException(422, str(exc)) from exc
    except UnsupportedDocument:
        pass  # cannot be opened at all: the picture check says so, in its own words


def clean_abha(abha: str | None) -> str | None:
    """The ABHA number as typed, or ``None`` for an empty box; anything that is not 14 digits
    (spaces and hyphens between them allowed) is refused in plain words (UP-S1 AC6)."""
    typed = (abha or "").strip()
    if not typed:
        return None
    digits = typed.replace("-", "").replace(" ", "")
    if not (digits.isascii() and digits.isdigit() and len(digits) == 14):
        raise HTTPException(422, BAD_ABHA)
    return typed


# every route passes auth.authorize first: sign-in + role, per auth.POLICY (backlog E11-S1)
# (FastAPI's /docs, /redoc and /openapi.json would bypass it, so they are switched off)
# Only the explicit CDI spans are exported; implicit HTTP tracing can capture request data.
app = FastAPI(
    telemetry={"tracing": False},
    title="CareFlow CDI-Adapter",
    dependencies=[Depends(auth.authorize)],
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.include_router(auth.router)
app.include_router(review.router)
app.include_router(review_page.router)
app.include_router(conflicts.router)  # E5-S3
app.include_router(conflict_page.router)  # E5-S3
app.include_router(result_page.router)  # UP-S3
app.include_router(reviewer.router)
app.include_router(admin.router)
app.include_router(monitor.router)


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return upload_page()


@app.get("/healthz")
def healthz() -> JSONResponse:
    checks: dict[str, Any] = {}
    try:
        with session_scope() as c:
            c.execute(text("SELECT 1"))
        checks["db"] = True
    except Exception:  # noqa: BLE001
        checks["db"] = False
    checks["s3"] = get_store().healthy()
    s = get_settings()
    if s.mlserve_backend == "stub":
        checks["mlserve"] = True
    else:
        from cdi_adapter.ml.client import HttpMLClient

        try:
            checks["mlserve"] = HttpMLClient(timeout=3).healthz().get("status") == "ok"
        except Exception:  # noqa: BLE001
            checks["mlserve"] = False
    ok = all(checks.values())
    return JSONResponse({"status": "ok" if ok else "degraded", **checks},
                        status_code=200 if ok else 503)  # fmt: skip


@app.get("/readyz")
def readyz() -> JSONResponse:
    """Whether this instance can take an upload now (E10-S9): the database and the object store
    it makes an upload durable in, and the queue it dispatches to. Not the model gateway: an
    upload is accepted while it is down, and waits. The balancer takes an instance out of
    rotation when this is not 200 (or not answered)."""
    checks: dict[str, Any] = {}
    try:
        with session_scope() as c:
            c.execute(text("SELECT 1"))
        checks["db"] = True
    except Exception:  # noqa: BLE001
        checks["db"] = False
    checks["s3"] = get_store().healthy()
    if get_settings().job_execution == "queue":
        try:
            import redis

            checks["queue"] = bool(redis.Redis.from_url(get_settings().redis_url,
                                                        socket_timeout=1).ping())  # fmt: skip
        except Exception:  # noqa: BLE001
            checks["queue"] = False
    ok = all(checks.values())
    return JSONResponse({"ready": ok, "instance": jobstore.instance_name(), **checks},
                        status_code=200 if ok else 503)  # fmt: skip


# ------------------------------------------------------------------ jobs


@app.post("/api/jobs", status_code=202)
async def create_job(request: Request, files: list[UploadFile] = File(...),
                     abha: str | None = Form(None),
                     patient_ref: str | None = Form(None)) -> dict[str, Any]:  # fmt: skip
    if not files or len(files) > MAX_FILES:
        raise HTTPException(422, f"upload 1 to {MAX_FILES} files")
    payload = [(f.filename or "upload", await f.read(), f.content_type) for f in files]
    if any(not raw for _, raw, _ in payload):
        raise HTTPException(422, "empty file")
    for name, raw, _ in payload:  # AC3: one file that is not a scan refuses the whole upload
        _must_be_a_scan(name, raw)
    abha = clean_abha(abha)  # AC6
    reg, patient_id = None, None
    if patient_ref:
        with session_scope() as c:
            reg = registry.lookup(c, patient_ref)
            if reg is None:
                raise HTTPException(404, "no registered patient matches patient_ref")
            patient_id = str(registry.ensure_identity(c, reg))
    # E9-S3: the same upload sent twice (a counter retrying a POST that timed out) is one job.
    # The client may name the retry with an Idempotency-Key header; without one the key is
    # derived from the bytes, so an unchanged retry still lands on the job it already created.
    key = jobstore.idempotency_key(payload, actor=auth.actor(request).username,
                                   patient_id=patient_id, abha=(abha or None),
                                   given=request.headers.get("Idempotency-Key"))  # fmt: skip
    try:  # 202 only once the files and the job are durable (E10-S9)
        job = await run_in_threadpool(jobstore.submit, payload, actor=auth.actor(request).username,
                                      registry_patient=reg, patient_id=patient_id,
                                      abha=(abha or None), key=key)  # fmt: skip
    except jobstore.NotAccepted as exc:
        raise HTTPException(503, "the upload could not be stored and was NOT accepted; "
                                 "send it again") from exc  # fmt: skip
    return {"job_id": job.id, "trace_id": job.trace_id}


def _job(job_id: str):
    job = jobstore.load(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return job


@app.post("/api/jobs/{job_id}/pages/{page_no}/replace", status_code=202)
async def replace_page(job_id: str, page_no: int, request: Request,
                       file: UploadFile = File(...)) -> dict[str, Any]:  # fmt: skip
    """UP-S2: a retaken page takes the place of page ``page_no`` (1-based) of this job.

    Only that page is read again; every other page of the prescription keeps its work. The job
    reopens and the runner's resume picks the replaced page up.
    """
    raw = await file.read()
    if not raw:
        raise HTTPException(422, "empty file")
    _must_be_a_scan(file.filename, raw)  # a retaken page obeys the same rule as a first one
    try:
        job = await run_in_threadpool(
            jobstore.replace_page, job_id, page_no - 1, file.filename or "retake",
            raw, file.content_type, actor=auth.actor(request).username,
        )  # fmt: skip
    except jobstore.PageNotReplaced as exc:
        detail = str(exc)
        raise HTTPException(404 if detail == "job not found" else 409, detail) from exc
    return {"job_id": job.id, "page": page_no, "state": job.state}


@app.get("/api/documents/{document_id}/result.json")
def document_result(document_id: UUID) -> Response:
    """UP-S3 / OUT-S2: the prescription's result as the Output Contract document.

    The same bytes the Download button saves, and the same document the result screen renders -
    the screen is fed by this, not by the internal tables, so the two can never disagree.
    """
    with session_scope() as c:
        try:
            body = result.as_json(c, document_id)
        except LookupError as exc:
            raise HTTPException(404, "document not found") from exc
    return Response(body, media_type="application/json")


@app.get("/api/documents/{document_id}/result.json/download")
def download_result(document_id: UUID) -> Response:
    """The same document, as a named file for the person at the desk."""
    out = document_result(document_id)
    name = f"result-{document_id}.json"
    return Response(out.body, media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})  # fmt: skip


RESULT_REPORT = "result_report"  # audit_log.entity of a "this result is wrong" report


class WrongResult(BaseModel):
    """What the person says is wrong with a result (UP-S3 AC5)."""

    note: str = Field(min_length=1, max_length=1000)
    value: str | None = Field(default=None, max_length=200)  # the value they were looking at


@app.post("/api/documents/{document_id}/result/report", status_code=201)
def report_wrong_result(document_id: UUID, body: WrongResult,
                        request: Request) -> dict[str, Any]:  # fmt: skip
    """UP-S3 AC5: "Report a wrong result". Saved with the document id, who reported it and when,
    in the append-only audit log - so a report can never be edited or lost - and listed in the
    evaluation backlog. It changes nothing in the result: corrections are the review console's."""
    note = " ".join(body.note.split())
    if len(note) < 3:
        raise HTTPException(422, "Say what is wrong, in a few words.")
    with session_scope() as c:
        _doc(c, document_id)  # 404 for a document that does not exist
        report_id = repo.write_audit(
            c, actor=auth.actor(request).username, action="create", entity=RESULT_REPORT,
            entity_id=document_id,
            detail={"note": note, "value": (body.value or "").strip() or None,
                    "schema_version": result.SCHEMA_VERSION})  # fmt: skip
    log.info("result_reported_wrong", document_id=str(document_id), report_id=report_id)
    return {"report_id": report_id, "document_id": str(document_id)}


@app.get("/api/evaluation/backlog")
def evaluation_backlog(limit: int = 100) -> dict[str, Any]:
    """The evaluation backlog: every result reported wrong, newest first, for whoever works
    out why the reading went wrong."""
    if not 1 <= limit <= 500:
        raise HTTPException(422, "limit must be between 1 and 500")
    with session_scope() as c:
        total = c.execute(text("SELECT count(*) FROM audit_log WHERE entity = :e"),
                          {"e": RESULT_REPORT}).scalar_one()  # fmt: skip
        rows = c.execute(
            text("SELECT a.id, a.at, a.actor, a.entity_id AS document_id, "
                 "  a.detail ->> 'note' AS note, a.detail ->> 'value' AS value, d.filename "
                 "FROM audit_log a LEFT JOIN source_document d ON d.id::text = a.entity_id "
                 "WHERE a.entity = :e ORDER BY a.at DESC, a.id DESC LIMIT :n"),
            {"e": RESULT_REPORT, "n": limit}).mappings().all()  # fmt: skip
    reports = [{"report_id": r["id"], "reported_at": r["at"].isoformat() if r["at"] else None,
                "reported_by": r["actor"], "document_id": r["document_id"],
                "file_name": r["filename"], "note": r["note"], "value": r["value"]}
               for r in rows]  # fmt: skip
    return {"count": int(total), "reports": reports}


@app.get("/api/jobs/{job_id}/result.json")
def job_result(job_id: str) -> Response:
    """UP-S1 AC2: one result for one upload - every page sent together, as a single document
    (``result.build_job``). The screen at ``/result/job/<id>`` and the Download button are both
    fed by this."""
    with session_scope() as c:
        try:
            body = result.job_as_json(c, job_id)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
    return Response(body, media_type="application/json")


@app.get("/api/jobs/{job_id}/result.json/download")
def download_job_result(job_id: str) -> Response:
    out = job_result(job_id)
    return Response(out.body, media_type="application/json",
                    headers={"Content-Disposition":
                             f'attachment; filename="result-upload-{job_id}.json"'})  # fmt: skip


@app.get("/api/reports/quality")
def quality_report_route(days: int = 7) -> dict[str, Any]:
    """UP-S2 AC5: the weekly picture-quality report - the retake rate and the top reasons
    pictures were held. Counts only. ``days`` is how far back (1 to 366; a week by default)."""
    if not 1 <= days <= 366:
        raise HTTPException(422, "days must be between 1 and 366")
    with session_scope() as c:
        return quality_report.weekly(c, days=days)


@app.get("/api/activity")
def activity() -> dict[str, Any]:
    """How much is going on right now: uploads ("jobs") by state, and how many of their pages
    are still being read. An upload is one press of Generate EMR, with 1 to 10 pages; ``review``
    means it has been read and is waiting for a reviewer. Counts only - no names, no content."""
    with session_scope() as c:
        rows = c.execute(text("SELECT state, count(*) FROM web_job GROUP BY state")).all()
        pages = c.execute(
            text(
                "SELECT count(*) FROM web_job j, jsonb_array_elements(j.docs) d "
                "WHERE j.state = 'running' AND d->>'status' IN ('pending', 'running')"
            )
        ).scalar_one()
    states: dict[str, int] = {str(state): int(n) for state, n in rows}
    jobs = {s: states.get(s, 0) for s in ("running", "review", "mismatch", "error")}
    return {"jobs": jobs, "jobs_running": jobs["running"], "pages_being_read": int(pages)}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    job = _job(job_id)
    out = job.as_json()
    if job.patient_id and job.state != "mismatch":
        with session_scope() as c:
            out["patient"] = patient_summary(c, job.patient_id)
    else:
        out["patient"] = None  # a mismatch never surfaces any identity
        out["patient_id"] = None
    return out


@app.get("/api/jobs/{job_id}/facts")
def job_facts(job_id: str) -> dict[str, Any]:
    job = _job(job_id)
    docs = []
    with session_scope() as c:
        for d in job.docs:
            if not d.document_id:
                continue
            rows = []
            for f in repo.get_facts_for_document(c, d.document_id):
                if not f["is_current"]:
                    continue
                prov = repo.get_provenance(c, f["id"])
                p = prov[0] if prov else {}
                page_no = c.execute(text("SELECT page_no FROM document_page WHERE id = :i"),
                                    {"i": p.get("page_id")}).scalar_one_or_none()  # fmt: skip
                rows.append({k: f[k] for k in ("id", "fact_type", "local_text", "value_num",
                                               "value_unit_ucum", "value_text", "code",
                                               "code_display", "review_state",
                                               "confidence_overall")}
                            | {"bbox": p.get("bbox_union"),
                               "page_image_url": f"/api/documents/{d.document_id}/pages/{page_no}"
                               if page_no else None})  # fmt: skip
            docs.append({"document_id": d.document_id, "filename": d.filename,
                         "doc_type": d.doc_type, "facts": rows})  # fmt: skip
    return {"job_id": job.id, "documents": docs}


class Edit(BaseModel):
    fact_id: UUID
    action: Literal["keep", "drop"]
    corrections: dict[str, Any] | None = None


class GenerateBody(BaseModel):
    edits: list[Edit] = Field(default_factory=list)
    # no reviewer field (E7-S1): the edits are recorded under the signed-in user


@app.post("/api/jobs/{job_id}/generate")
def generate(job_id: str, body: GenerateBody, request: Request) -> dict[str, Any]:
    reviewer = auth.actor(request).username
    job = _job(job_id)
    if job.state != "review":
        raise HTTPException(409, f"job is {job.state}")
    job_docs = {d.document_id for d in job.docs if d.document_id}
    with session_scope() as c:
        for e in body.edits:
            owner = c.execute(text("SELECT source_doc_ids FROM clinical_fact WHERE id = :i"),
                              {"i": str(e.fact_id)}).scalar_one_or_none()  # fmt: skip
            if owner is None or not {str(x) for x in owner} & job_docs:
                raise HTTPException(404, f"fact {e.fact_id} is not in this job")
            action: Literal["accept", "correct", "reject"] = (
                ("correct" if e.corrections else "accept") if e.action == "keep" else "reject"
            )
            decision = review.ReviewDecision(action=action, corrections=e.corrections)
            try:
                review.decide(c, e.fact_id, decision, reviewer)
            except review.ReviewError as err:
                raise HTTPException(err.status, err.detail) from err
    return _fhir(job)


def _fhir(job) -> dict[str, Any]:
    """The FHIR record of THIS job: the patient's bundles, kept to the documents uploaded in the
    job (the patient may have documents from earlier visits; those are not this upload's)."""
    if not job.patient_id:
        raise HTTPException(409, "job has no patient yet")
    # a later request, but part of the job's trace (E10-S3): the bundle ends the document's path
    with tracing.span("fhir.project", {"cdi.job_id": job.id}, parent=job.trace_parent()):
        out = project_patient(job.patient_id)
    return job_bundles(out, [d.document_id for d in job.docs if d.document_id])


def job_bundles(projection: dict[str, Any], document_ids: list[str]) -> dict[str, Any]:
    mine = {str(d) for d in document_ids}
    bundles = [b for b in projection["bundles"] if str(b["document_id"]) in mine]
    return {**projection, "artifact_count": len(bundles), "bundles": bundles}


def download_bundle(bundles: list[dict[str, Any]], job_id: str, timestamp: str) -> dict[str, Any]:
    """What the download file holds: FHIR only. One document -> its document Bundle as is;
    several -> a ``collection`` Bundle with one entry per document Bundle. The review state
    (status, issues, held facts) stays in ``/api/jobs/{id}/fhir``: it is not FHIR."""
    docs = [b["bundle"] for b in bundles]
    if len(docs) == 1:
        return docs[0]
    return {"resourceType": "Bundle", "id": str(uuid5(NAMESPACE_URL, f"cdi:job:{job_id}")),
            "type": "collection", "timestamp": timestamp,
            "entry": [{"fullUrl": f"urn:uuid:{d['id']}", "resource": d} for d in docs]}  # fmt: skip


@app.get("/api/jobs/{job_id}/fhir")
def job_fhir(job_id: str) -> dict[str, Any]:
    return _fhir(_job(job_id))


@app.get("/api/jobs/{job_id}/fhir/download")
def job_fhir_download(job_id: str) -> Response:
    data = _fhir(_job(job_id))
    if not data["bundles"]:
        raise HTTPException(409, "no FHIR record for this job yet")
    now = datetime.now(UTC).isoformat(timespec="seconds")
    body = download_bundle(data["bundles"], job_id, now)
    disposition = f'attachment; filename="fhir_{job_id}.json"'
    return Response(json.dumps(body, default=str, indent=2), media_type="application/fhir+json",
                    headers={"Content-Disposition": disposition})  # fmt: skip


@app.get("/api/patients/{patient_id}/fhir")
def patient_fhir(patient_id: UUID) -> dict[str, Any]:
    with session_scope() as c:
        if not c.execute(text("SELECT 1 FROM patient_identity WHERE id = :p"),
                         {"p": str(patient_id)}).first():  # fmt: skip
            raise HTTPException(404, "patient not found")
    return project_patient(patient_id)


# ------------------------------------------------------------------ documents


def _doc(c, document_id: UUID) -> dict[str, Any]:
    d = repo.get_document(c, document_id)
    if d is None:
        raise HTTPException(404, "document not found")
    return d


@app.get("/api/documents/{document_id}/bundle")
def document_bundle(document_id: UUID) -> dict[str, Any]:
    with session_scope() as c:
        _doc(c, document_id)
    return project_document(document_id, persist=False)


@app.get("/api/documents/{document_id}/evidence")
def document_evidence(document_id: UUID) -> dict[str, Any]:
    with session_scope() as c:
        d = _doc(c, document_id)
        cls = repo.get_latest_classification(c, document_id)
        n_blocks = c.execute(text("SELECT count(*) FROM ocr_block WHERE document_id = :d"),
                             {"d": str(document_id)}).scalar_one()  # fmt: skip
        facts = [{k: f[k] for k in ("id", "fact_type", "local_text", "code", "code_status",
                                    "value_num", "value_unit_ucum", "review_state",
                                    "confidence_overall")}
                 for f in repo.get_facts_for_document(c, document_id)]  # fmt: skip
    return {"document_id": str(document_id), "filename": d["filename"],
                  "status": d["status"], "doc_type": cls["doc_type"] if cls else None,
                  "ocr_blocks": n_blocks, "facts": facts}  # fmt: skip


@app.get("/api/documents/{document_id}/pages/{page_no}")
def document_page(document_id: UUID, page_no: int) -> Response:
    with session_scope() as c:
        _doc(c, document_id)
        key = c.execute(text("SELECT storage_key FROM document_page WHERE document_id = :d "
                             "AND page_no = :n"), {"d": str(document_id), "n": page_no}
                        ).scalar_one_or_none()  # fmt: skip
    if key is None:
        raise HTTPException(404, "page not found")
    return Response(get_store().get_bytes(key), media_type="image/png")


@app.get("/api/documents/{document_id}/original")
def document_original(document_id: UUID) -> Response:
    """The scan exactly as uploaded (§5 S1 key ``documents/<sha[:2]>/<sha>/original.<ext>``): the
    evidence a reviewer checks a fact against, beyond the normalised page images."""
    with session_scope() as c:
        d = _doc(c, document_id)
    return Response(get_store().get_bytes(d["storage_key"]),
                    media_type=d["mime_type"] or "application/octet-stream")  # fmt: skip


# ------------------------------------------------------------------ registry


@app.get("/api/registry/lookup")
def registry_lookup(q: str) -> dict[str, Any]:
    with session_scope() as c:
        row = registry.lookup(c, q)
    if row is None:
        raise HTTPException(404, "no registered patient matches")
    return {k: (str(v) if k == "dob" and v else v) for k, v in row.items()
                  if k not in ("created_at", "updated_at")}  # fmt: skip


class RegistryBody(BaseModel):
    patient_id: str = Field(min_length=3, max_length=40)
    name: str | None = None
    mobile: str | None = Field(default=None, pattern=r"^[0-9+\- ]{10,16}$")
    dob: str | None = None
    gender: Literal["M", "F", "O", "U"] | None = None
    address: str | None = None
    abha_id: str | None = Field(default=None, pattern=r"^[0-9\- ]{14,17}$")


@app.post("/api/registry")
def registry_upsert(body: RegistryBody) -> dict[str, Any]:
    with session_scope() as c:
        exists = c.execute(text("SELECT 1 FROM patient_identity WHERE mpi_id = :m"),
                           {"m": body.patient_id}).first()  # fmt: skip
        if not exists:
            raise HTTPException(404, "unknown CareFlow id")
        try:
            row = registry.upsert_registry(c, body.patient_id,
                                           body.model_dump(exclude={"patient_id"}))  # fmt: skip
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
    return {k: str(v) if v is not None and k in ("dob", "created_at", "updated_at") else v
                  for k, v in row.items()}  # fmt: skip


def start_metrics(port: int | None = None) -> None:
    """Prometheus ``/metrics`` for the web app (backlog E10-S1) on its own port, bound to
    127.0.0.1: the staff-facing app and the ingress never serve it (§12)."""
    from prometheus_client import start_http_server

    from cdi_adapter import metrics

    # from web_job (E10-S9): every instance reports the same numbers; dashboards and alerts
    # take max() across instances
    metrics.register_once("jobs", metrics.JobsCollector(lambda: jobstore.store().metric_rows()))
    metrics.register_once("review_backlog", metrics.ReviewBacklogCollector())
    metrics.register_once("pg_replication", metrics.PgReplicationCollector())  # E10-S6
    metrics.register_once("minio_nodes", metrics.MinioNodesCollector())
    metrics.register_once("minio_drives", metrics.MinioDrivesCollector())
    metrics.register_once("dr", metrics.DrCollector())  # E10-S11
    port = get_settings().metrics_port if port is None else port
    if port:
        start_http_server(port, addr="127.0.0.1")


def main() -> None:
    import uvicorn

    configure()
    tracing.setup_from_settings(tracing.WEBAPP)
    start_metrics()
    s = get_settings()
    if s.job_execution == "inprocess":  # this instance runs jobs: start its lease heartbeat and
        from cdi_adapter.webapp.jobs import get_runner  # sweep now, not on the first upload

        get_runner()
    log.info("webapp_starting", instance=jobstore.instance_name(), execution=s.job_execution)
    uvicorn.run(app, host=s.webapp_host, port=s.webapp_port, timeout_graceful_shutdown=30)
