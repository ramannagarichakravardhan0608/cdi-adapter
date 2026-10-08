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

**Line by line (IM-S3 AC2 / AC4).** With ``CDI_LINE_READERS`` on as well, each line goes to the
reader its label names (``regions.READERS``), instead of the page going to one reader:

* a **printed** line keeps the print reader's reading;
* a **handwritten** line is cut from the page to the crop standard and read by the handwriting
  reader, and the print reader's reading of it is left out;
* an **uncertain** or **mixed** line is read by **both** - nothing is dropped, and S5 sees two
  engines on it.

Every crop handed to the handwriting reader meets the crop standard, and its size, padding and
enlargement are recorded with the run (``metrics.line_crops``) so reader errors can be sliced
by crop size. A line the handwriting reader fails on, or one past the per-page cap, keeps its
print reading. Off by default: it is one model call per such line.
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass, field
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


def page_regions(png: bytes, lines: list[rapid.OcrLine], *,
                 crops: bool = False) -> list[regions.Region]:  # fmt: skip
    """The lines of one page, labelled (IM-S3) - or none at all if that fails: this is a second
    opinion on the page, and a page must never be lost for want of it. ``crops`` also cuts each
    line to the crop standard, which is only needed when lines are read one by one."""
    try:
        return regions.detect_regions(png, ocr=lines, crops=crops)
    except Exception as exc:  # noqa: BLE001 - see the docstring
        log.warning("line_regions_failed", error=f"{type(exc).__name__}: {exc}")
        return []


def region_index(lines: list[rapid.OcrLine], found: list[regions.Region]) -> list[int | None]:
    """For each line the print reader read, which line of writing it lies on: the region
    covering most of it, if that is at least a third of it. ``None`` where no region does."""
    out: list[int | None] = []
    for ln in lines:
        x0, y0, x1, y1 = (float(v) for v in ln.bbox)
        area = max((x1 - x0) * (y1 - y0), 1.0)
        best, share = None, 1 / 3
        for i, r in enumerate(found):
            a0, b0, a1, b1 = r.bbox
            over = max(0.0, min(x1, a1) - max(x0, a0)) * max(0.0, min(y1, b1) - max(y0, b0))
            if over / area >= share:
                best, share = i, over / area
        out.append(best)
    return out


def region_kinds(lines: list[rapid.OcrLine], found: list[regions.Region]) -> list[str | None]:
    """For each line the print reader read, the label of the line of writing it lies on.
    ``None`` where it lies on none - the reader's own kind stands."""
    return [None if i is None else found[i].label for i in region_index(lines, found)]


@dataclass
class LineReads:
    """What reading a page line by line produced (IM-S3 AC2 / AC4)."""

    read: list[tuple[rapid.OcrLine, str, str]] = field(default_factory=list)
    """The handwriting reader's lines: the reading, its line's label, its crop's fingerprint."""
    replaced: set[int] = field(default_factory=set)
    """Print-reader lines (by index) left out: they lie on a line that is plainly handwritten
    and that the handwriting reader did read."""
    crops: list[dict[str, Any]] = field(default_factory=list)
    """One record per crop handed to the handwriting reader: its size, padding, enlargement."""
    sent: int = 0
    failed: int = 0
    over_cap: int = 0

    def counts(self) -> dict[str, int]:
        return {"sent": self.sent, "read": len(self.read), "failed": self.failed,
                "over_cap": self.over_cap, "print_lines_replaced": len(self.replaced)}  # fmt: skip


def read_lines(lines: list[rapid.OcrLine], found: list[regions.Region], client: MLClient, *,
               max_lines: int) -> LineReads:  # fmt: skip
    """Send each line to the reader its label names. Printed lines need nothing: the print
    reader has read the page. Every other line is handed to the handwriting reader as a crop.

    Nothing is dropped. A handwritten line's print reading is left out only once the handwriting
    reader has actually returned something for it; an uncertain or mixed line keeps both
    readings; a line the handwriting reader fails on, returns nothing for, or that lies past
    ``max_lines``, keeps the print reader's reading as it was."""
    out = LineReads()
    on_region = region_index(lines, found)
    for i, r in enumerate(found):
        if "handwriting" not in r.readers or r.crop is None:
            continue
        if out.sent >= max_lines:
            out.over_cap += 1
            continue
        out.sent += 1
        try:
            text = vlm_ocr.read_line_crop(r.crop.png, client)
        except Exception as exc:  # noqa: BLE001 - one line's failure must not cost the page
            log.warning("line_read_failed", label=r.label, error=f"{type(exc).__name__}: {exc}")
            out.failed += 1
            text = ""
        out.crops.append({**r.crop.record(), "label": r.label, "read": bool(text),
                          "meets_standard": regions.meets_standard(r.crop)})  # fmt: skip
        if not text:
            continue
        line = rapid.OcrLine(text=text, bbox=[int(v) for v in r.bbox], conf=vlm_ocr.VLM_OCR_CONF,
                             block_type="line")  # fmt: skip
        out.read.append((line, r.label, r.crop.sha256))
        if (
            "print" not in r.readers
        ):  # plainly handwritten: this reading takes the print one's place
            out.replaced |= {n for n, where in enumerate(on_region) if where == i}
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
        # IM-S3 AC2: read line by line - only on the print pass, and only with both switches on
        by_line = engine != "vlm" and settings.line_regions and settings.line_readers
        line_client = (client or get_client()) if by_line else None
        line_reads: list[tuple[dict[str, Any], LineReads]] = []
        for page in pages:
            png = store.get_bytes(page["storage_key"])
            if vlm is not None:
                lines = vlm_ocr.run_vlm_transcription(png, page["width"], page["height"], vlm)
                kinds.append([None] * len(lines))
            else:
                lines = rapid.run_rapidocr(png, min_conf=settings.ocr_min_conf)
                found.append(page_regions(png, lines, crops=by_line))
                on = settings.line_regions
                kinds.append(region_kinds(lines, found[-1]) if on else [None] * len(lines))
                if line_client is not None:
                    reads = read_lines(lines, found[-1], line_client,
                                       max_lines=settings.line_readers_max_per_page)  # fmt: skip
                    line_reads.append((page, reads))
                    # the print reader's reading of a plainly handwritten line is left out
                    keep = [n for n in range(len(lines)) if n not in reads.replaced]
                    lines = [lines[n] for n in keep]
                    kinds[-1] = [kinds[-1][n] for n in keep]
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
            # IM-S3 AC2: the handwriting reader's lines, each stored as its own observation
            # with the line's label and the fingerprint of the crop it was read from
            line_meta = _observation_meta("vlm", settings)
            line_meta["prompt_hash"] = hashlib.sha256(vlm_ocr.LINE_PROMPT.encode()).hexdigest()
            for page, reads in line_reads:
                taken = sum(1 for r in rows if r["page_id"] == page["id"])
                rows += [{"document_id": document_id, "page_id": page["id"],
                          "block_type": ln.block_type, "text": ln.text, "bbox": ln.bbox,
                          "polygon": None, "ocr_conf": ln.conf, "reading_order": taken + n,
                          "engine": "vlm", "pipeline_run_id": run_id, "crop_hash": crop_hash,
                          **line_meta, "region_kind": label}
                         for n, (ln, label, crop_hash) in enumerate(reads.read)]  # fmt: skip
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
            if by_line:
                totals: dict[str, int] = {}
                for _, reads in line_reads:
                    for k, v in reads.counts().items():
                        totals[k] = totals.get(k, 0) + v
                metrics["line_reads"] = totals
                # IM-S3 AC4: the size of every crop a reader was given, kept with the run
                metrics["line_crops"] = [
                    {**c, "page_no": page["page_no"]}
                    for page, reads in line_reads
                    for c in reads.crops
                ]
            if doc["status"] in _EARLY:
                repo.set_document_status(conn, document_id, "ocr_done")
            repo.finish_run(conn, run_id, metrics=metrics)
            brief = {k: v for k, v in metrics.items() if k != "line_crops"}  # the audit row
            repo.write_audit(conn, actor=actor, action="update", entity="source_document",
                             entity_id=document_id, detail={"stage": "ocr", **brief})  # fmt: skip
    except Exception as exc:
        with session_scope() as conn:
            repo.fail_run(conn, run_id, error=f"{type(exc).__name__}: {exc}")
            repo.set_document_status(conn, document_id, "error",
                                     error={"stage": "ocr", "message": str(exc)})  # fmt: skip
        log.error("ocr_failed", document_id=str(document_id), error=str(exc))
        raise
    log.info(
        "ocr_done",
        document_id=str(document_id),
        **{k: v for k, v in metrics.items() if k != "line_crops"},
    )
    return metrics
