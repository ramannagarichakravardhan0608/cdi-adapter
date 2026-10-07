"""S3 OCR service: ``ocr_document`` (ARCHITECTURE §5 S3).

Runs **before** classify: the orchestrator first calls ``ocr_document(doc, force_engine=
"rapidocr")`` so the S2 heuristic reads real OCR text; if S2 then marks the page handwritten it
calls ``ocr_document(doc, force_engine="vlm")``, so the VLM transcription supersedes the RapidOCR
pass. Without ``force_engine`` the engine is chosen from the classification
(``is_handwritten AND CDI_HANDWRITTEN_USES_VLM`` -> VLM).

Every run appends raw observations (``ocr_observation``, backlog E4-S7): the document's earlier
observations are never deleted, they are pointed at the new run (``superseded_by``), so
``ocr_block`` - the current observations - shows exactly what the old delete-and-insert did.
Each observation records the SHA-256 of the pixels it was read from (the page image cropped at
its bbox), the region kind, the engine version and, for the VLM, the prompt's hash. The engines
themselves (``rapid.py``, ``vlm_ocr.py``) are unchanged: only where their output is kept changed.

**Lines, printed or handwritten (backlog IM-S3).** On the print reader's pass every page is also
looked at by ``recognition/regions.py``, which finds its lines and labels each one. What it found
is always recorded with the run (``metrics.regions``: how many lines of each kind). Acting on it
is ``CDI_LINE_REGIONS``, off by default: with it on, each observation is stored with the label of
the line it lies on rather than the reader's blanket "printed" - which is what S5 reads to treat
evidence as handwritten - and ``webapp/jobs.py`` sends a page with handwritten lines to the
handwriting pass. Finding the lines can never fail a page: if it breaks, OCR goes on without it.
"""

from __future__ import annotations

import hashlib
import io
from functools import cache
from importlib import metadata
from typing import Any
from uuid import UUID

from PIL import Image

from cdi_adapter import repo
from cdi_adapter.config import Settings, get_settings
from cdi_adapter.db import session_scope
from cdi_adapter.logging import get_logger
from cdi_adapter.ml.client import MLClient, get_client
from cdi_adapter.ocr import rapid, vlm_ocr
from cdi_adapter.recognition import regions
from cdi_adapter.storage import ObjectStore, get_store

log = get_logger(__name__)

ENGINES = ("rapidocr", "vlm")
_EARLY = ("received", "pages_rendered")  # statuses OCR may advance to 'ocr_done'


@cache
def _rapid_version() -> str:
    try:
        return "rapidocr-onnxruntime " + metadata.version("rapidocr-onnxruntime")
    except metadata.PackageNotFoundError:
        return "rapidocr-onnxruntime"


def crop_hashes(png: bytes, lines: list[rapid.OcrLine]) -> list[str | None]:
    """SHA-256 of the pixels each line was read from: the page image cropped at its bbox (the
    size is hashed with the pixels, so two crops of different shape never collide)."""
    with Image.open(io.BytesIO(png)) as im:
        page = im.convert("RGB")
    out: list[str | None] = []
    for ln in lines:
        x0, y0, x1, y1 = (int(v) for v in ln.bbox)
        box = (max(0, x0), max(0, y0), min(page.width, x1), min(page.height, y1))
        if box[2] <= box[0] or box[3] <= box[1]:
            out.append(None)  # a box outside the page: no pixels to point at
            continue
        crop = page.crop(box)
        h = hashlib.sha256(f"{crop.width}x{crop.height}:".encode())
        h.update(crop.tobytes())
        out.append(h.hexdigest())
    return out


def _observation_meta(engine: str, settings: Settings) -> dict[str, Any]:
    if engine == "vlm":  # used for pages the classifier calls handwritten, read as one region
        return {
            "region_kind": "handwritten",
            "engine_version": settings.vlm_model_id,
            "prompt_hash": hashlib.sha256(vlm_ocr.TRANSCRIBE_PROMPT.encode()).hexdigest(),
        }
    return {"region_kind": "printed", "engine_version": _rapid_version(), "prompt_hash": None}


def page_regions(png: bytes, lines: list[rapid.OcrLine]) -> list[regions.Region]:
    """The lines of one page, labelled (IM-S3) - or none at all if that fails: this is a second
    opinion on the page, and a page must never be lost for want of it."""
    try:
        return regions.detect_regions(png, ocr=lines, crops=False)
    except Exception as exc:  # noqa: BLE001 - see the docstring
        log.warning("line_regions_failed", error=f"{type(exc).__name__}: {exc}")
        return []


def region_kinds(lines: list[rapid.OcrLine], found: list[regions.Region]) -> list[str | None]:
    """For each line the print reader read, the label of the line of writing it lies on: the
    region covering most of it, if that is at least a third of it. ``None`` where no region
    does - the reader's own kind stands."""
    out: list[str | None] = []
    for ln in lines:
        x0, y0, x1, y1 = (float(v) for v in ln.bbox)
        area = max((x1 - x0) * (y1 - y0), 1.0)
        best, share = None, 1 / 3
        for r in found:
            a0, b0, a1, b1 = r.bbox
            over = max(0.0, min(x1, a1) - max(x0, a0)) * max(0.0, min(y1, b1) - max(y0, b0))
            if over / area >= share:
                best, share = r.label, over / area
        out.append(best)
    return out


def _region_counts(found: list[list[regions.Region]], enforced: bool) -> dict[str, Any]:
    """What the run records: lines of each kind over the document, and the most handwritten
    lines on any one page (what the handwriting pass is decided on)."""
    totals = {"lines": 0, **dict.fromkeys(regions.LABELS, 0)}
    most = 0
    for page in found:
        s = regions.summary(page)
        for k in totals:
            totals[k] += s[k]
        most = max(most, s["handwritten"])
    return {**totals, "max_handwritten_on_a_page": most, "enforced": enforced}


def choose_engine(
    force_engine: str | None, classification: dict[str, Any] | None, settings: Settings
) -> str:
    if force_engine is not None:
        if force_engine not in ENGINES:
            raise ValueError(f"force_engine must be one of {ENGINES}")
        return force_engine
    if classification and classification.get("is_handwritten") and settings.handwritten_uses_vlm:
        return "vlm"
    return settings.ocr_engine


def ocr_document(
    document_id: UUID | str,
    *,
    force_engine: str | None = None,
    client: MLClient | None = None,
    store: ObjectStore | None = None,
    actor: str = "system",
) -> dict[str, Any]:
    settings = get_settings()
    store = store or get_store()
    with session_scope() as conn:
        doc = repo.get_document(conn, document_id)
        if doc is None:
            raise LookupError(f"document {document_id} not found")
        pages = repo.get_pages(conn, document_id)
        engine = choose_engine(force_engine, repo.get_latest_classification(conn, document_id),
                               settings)  # fmt: skip
        model = settings.vlm_model_id if engine == "vlm" else "rapidocr-onnxruntime"
        run_id = repo.start_run(conn, document_id=document_id, stage="ocr", model_name=model)

    try:
        per_page: list[tuple[dict[str, Any], list[rapid.OcrLine], list[str | None]]] = []
        found: list[list[regions.Region]] = []  # IM-S3: the print pass only (see module note)
        kinds: list[list[str | None]] = []
        vlm = (client or get_client()) if engine == "vlm" else None
        for page in pages:
            png = store.get_bytes(page["storage_key"])
            if vlm is not None:
                lines = vlm_ocr.run_vlm_transcription(png, page["width"], page["height"], vlm)
                kinds.append([None] * len(lines))
            else:
                lines = rapid.run_rapidocr(png, min_conf=settings.ocr_min_conf)
                found.append(page_regions(png, lines))
                on = settings.line_regions
                kinds.append(region_kinds(lines, found[-1]) if on else [None] * len(lines))
            per_page.append((page, lines, crop_hashes(png, lines)))

        meta = _observation_meta(engine, settings)
        with session_scope() as conn:
            superseded = repo.supersede_ocr_observations(conn, document_id, run_id)
            rows = [
                {
                    "document_id": document_id,
                    "page_id": page["id"],
                    "block_type": ln.block_type,
                    "text": ln.text,
                    "bbox": ln.bbox,
                    "polygon": ln.polygon,
                    "ocr_conf": ln.conf,
                    "reading_order": ln.reading_order,
                    "engine": engine,
                    "pipeline_run_id": run_id,
                    "crop_hash": crop,
                    **meta,
                    **({"region_kind": kind} if kind else {}),
                }
                for (page, lines, crops), page_kinds in zip(per_page, kinds, strict=True)
                for ln, crop, kind in zip(lines, crops, page_kinds, strict=True)
            ]
            repo.insert_ocr_blocks(conn, rows)
            confs = [r["ocr_conf"] for r in rows]
            metrics = {
                "engine": engine,
                "pages": len(per_page),
                "blocks": len(rows),
                "superseded": superseded,
                "mean_conf": round(sum(confs) / len(confs), 4) if confs else None,
            }
            if engine != "vlm":
                metrics["regions"] = _region_counts(found, settings.line_regions)
            if doc["status"] in _EARLY:
                repo.set_document_status(conn, document_id, "ocr_done")
            repo.finish_run(conn, run_id, metrics=metrics)
            repo.write_audit(conn, actor=actor, action="update", entity="source_document",
                             entity_id=document_id, detail={"stage": "ocr", **metrics})  # fmt: skip
    except Exception as exc:
        with session_scope() as conn:
            repo.fail_run(conn, run_id, error=f"{type(exc).__name__}: {exc}")
            repo.set_document_status(conn, document_id, "error",
                                     error={"stage": "ocr", "message": str(exc)})  # fmt: skip
        log.error("ocr_failed", document_id=str(document_id), error=str(exc))
        raise
    log.info("ocr_done", document_id=str(document_id), **metrics)
    return metrics
