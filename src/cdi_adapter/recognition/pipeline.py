"""Where a reader's lines become evidence (backlog RD-S1; ARCHITECTURE §15.4).

Every line a reader returns is saved as its own row of ``ocr_observation``: the text exactly as
read, where on the page it is (box and outline), how sure the reader was, the order it is read
in, which engine and which version read it, and the fingerprint of the pixels it was read from.

The table is append-only. A row is never changed and never deleted - the database refuses both
(migration 0013) - so reading a page again adds new rows and leaves the earlier ones where they
were, pointed at the run that replaced them (``superseded_by``). ``ocr_block`` shows the rows
that are current.

``record_rapid_observations`` is the print reader's (RapidOCR, ``ocr/rapid.py``): its lines are
stored as ``printed`` unless a line was found to be something else (IM-S3).

``recognize_page`` is the handwriting side (RD-S2): every handwritten line crop goes to two
readers that never see each other's answer (``engines.py``), and both readings are saved, each
with the fingerprint of the one crop both were read from. A reader that is down or slow does
not hold the page: the line is saved with the one reading it has, marked for review.
"""

from __future__ import annotations

from collections.abc import Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as Late
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import Connection

from cdi_adapter import repo
from cdi_adapter.logging import get_logger
from cdi_adapter.ocr import rapid
from cdi_adapter.recognition import regions
from cdi_adapter.recognition.engines import EngineDown, LineEngine, Reading

log = get_logger(__name__)

# one page of a run: the page's row, the lines read on it, and the fingerprint of each line's crop
PageLines = tuple[dict[str, Any], list[rapid.OcrLine], list[str | None]]


def observation_rows(
    document_id: UUID | str,
    run_id: UUID | str,
    engine: str,
    per_page: Sequence[PageLines],
    meta: dict[str, Any],
    *,
    kinds: Sequence[Sequence[str | None]] | None = None,
) -> list[dict[str, Any]]:
    """One row per line, in reading order. ``meta`` is what every row of the run carries (the
    engine's version, its blanket ``region_kind``); ``kinds`` gives a line its own label where
    one was found, page by page, and leaves the blanket one where it is ``None``."""
    kinds = kinds if kinds is not None else [[None] * len(lines) for _, lines, _ in per_page]
    return [
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


def record_observations(
    conn: Connection,
    *,
    document_id: UUID | str,
    run_id: UUID | str,
    engine: str,
    per_page: Sequence[PageLines],
    meta: dict[str, Any],
    kinds: Sequence[Sequence[str | None]] | None = None,
) -> list[dict[str, Any]]:
    """Save a run's lines as evidence and return the rows that were written. It only ever
    inserts: the rows of earlier runs are not touched here."""
    rows = observation_rows(document_id, run_id, engine, per_page, meta, kinds=kinds)
    repo.insert_ocr_blocks(conn, rows)
    return rows


def record_rapid_observations(
    conn: Connection,
    *,
    document_id: UUID | str,
    run_id: UUID | str,
    per_page: Sequence[PageLines],
    meta: dict[str, Any],
    kinds: Sequence[Sequence[str | None]] | None = None,
) -> list[dict[str, Any]]:
    """Save the print reader's lines (RD-S1): every line with its text, position and
    confidence, as RapidOCR's own evidence."""
    return record_observations(
        conn,
        document_id=document_id,
        run_id=run_id,
        engine=rapid.ENGINE,
        per_page=per_page,
        meta=meta,
        kinds=kinds,
    )


# ---------------------------------------------------------------- two readers (RD-S2)


@dataclass
class LineReadings:
    """One handwritten line: its crop, and what each reader made of it."""

    region: int  # index of the line among the page's regions
    label: str  # handwritten, mixed or uncertain
    bbox: tuple[int, int, int, int]
    crop: regions.Crop
    readings: dict[str, Reading] = field(default_factory=dict)  # by reader
    missing: dict[str, str] = field(default_factory=dict)  # reader -> why it has no reading
    assessment: Any = None  # RD-S3: hierarchy.Assessment, once the line has been decided

    @property
    def review_reason(self) -> str | None:
        """Why a person must look at this line, or ``None`` when it may stand: both readers
        read it and (RD-S3) every check on it passed."""
        if self.missing:
            why = "; ".join(f"{name} {reason}" for name, reason in sorted(self.missing.items()))
            return f"{'one reading' if self.readings else 'no reading'}: {why}"
        if self.assessment is not None:
            return str(self.assessment.review_reason) if self.assessment.reasons else None
        return None


@dataclass
class PageReadings:
    lines: list[LineReadings] = field(default_factory=list)
    over_cap: int = 0  # handwritten lines past the per-page limit: not sent to either reader

    def counts(self) -> dict[str, int]:
        both = sum(1 for ln in self.lines if not ln.missing)
        one = sum(1 for ln in self.lines if ln.missing and ln.readings)
        return {"lines": len(self.lines), "both": both, "one_reading": one,
                "no_reading": len(self.lines) - both - one, "over_cap": self.over_cap}  # fmt: skip


def _collect(name: str, job: Future[Reading], timeout: float, line: LineReadings) -> None:
    try:
        reading = job.result(timeout=timeout)
    except Late:
        line.missing[name] = f"did not answer in {timeout:g} s"
    except EngineDown as exc:
        line.missing[name] = f"is down ({exc})"
    except Exception as exc:  # noqa: BLE001 - one reader's failure must not cost the line
        line.missing[name] = f"failed ({type(exc).__name__})"
    else:
        if reading.text:
            line.readings[name] = reading
        else:
            line.missing[name] = "read nothing"
    if name in line.missing:
        log.warning("line_reader_missing", reader=name, reason=line.missing[name])


def recognize_page(found: Sequence[regions.Region], readers: Sequence[LineEngine], *,
                   max_lines: int, timeout: float) -> PageReadings:  # fmt: skip
    """Give every handwritten line crop of a page to each reader, separately.

    The readers run side by side and each is handed the crop alone - there is no path by which
    one reading reaches another reader. Each is waited for ``timeout`` seconds per line and no
    longer: a reader that is down, fails, is late or reads nothing leaves the line with the
    other's reading and a ``review_reason``. Printed lines are not sent at all."""
    out = PageReadings()
    pool = ThreadPoolExecutor(max_workers=max(1, len(readers)), thread_name_prefix="line-reader")
    try:
        for i, r in enumerate(found):
            if "handwriting" not in r.readers or r.crop is None:
                continue
            if len(out.lines) >= max_lines:
                out.over_cap += 1
                continue
            line = LineReadings(i, r.label, tuple(int(v) for v in r.bbox), r.crop)  # type: ignore[arg-type]
            jobs = [(reader.name, pool.submit(reader.read, r.crop.png)) for reader in readers]
            for name, job in jobs:
                _collect(name, job, timeout, line)
            out.lines.append(line)
    finally:
        pool.shutdown(
            wait=False, cancel_futures=True
        )  # a late reader is left behind, not waited for
    return out


def decide_lines(
    page: PageReadings,
    *,
    again: Any = None,
    word_list: Any = None,
    second: Any = None,
    ground: Any = None,
) -> None:
    """RD-S3: decide each line both readers read (``hierarchy.assess_fact``). ``again`` reads
    a crop once more with other padding (self-consistency), ``second`` asks for a second
    opinion; either may be left out. A line one reader missed needs no deciding: it already
    goes to a person."""
    from cdi_adapter.recognition import hierarchy

    for line in page.lines:
        if line.missing:
            continue
        texts = {name: r.text for name, r in line.readings.items()}
        second_read = None
        if again is not None:
            try:
                second_read = again(line.crop.png)
            except Exception as exc:  # noqa: BLE001 - the check could not be made: say so
                log.warning("self_consistency_failed", error=type(exc).__name__)
        opinion = (lambda a, b, png=line.crop.png: second(png, a, b)) if second else None
        line.assessment = hierarchy.assess_fact(texts, crop_png=line.crop.png, ground=ground,
                                                word_list=word_list, again=second_read,
                                                second_opinion=opinion)  # fmt: skip


def record_decisions(conn: Connection, run_id: UUID | str, lines: Sequence[LineReadings]
                     ) -> int:  # fmt: skip
    """Save how each decided line was decided (FR5): one ``ocr_agreement`` row for the pair of
    readings - the state, the score, both confidences, and the whole record of the checks."""
    import json

    from sqlalchemy import text

    from cdi_adapter import agreement
    from cdi_adapter.recognition import disagreement
    from cdi_adapter.recognition.engines import QWEN, TROCR

    written = 0
    for line in lines:
        a = line.assessment
        if a is None or a.comparison is None:
            continue
        found = conn.execute(text(
            "SELECT engine, id FROM ocr_observation WHERE pipeline_run_id = :r "
            "AND crop_hash = :h AND engine IN (:t, :q)"),
            {"r": run_id, "h": line.crop.sha256, "t": TROCR, "q": QWEN}).all()  # fmt: skip
        ids: dict[str, Any] = {str(engine): oid for engine, oid in found}
        if TROCR not in ids or QWEN not in ids:
            continue
        agreement.record_agreement(
            conn, observation_id_a=ids[TROCR], observation_id_b=ids[QWEN],
            disagreement_state=a.comparison.state, agreement_score=a.comparison.score,
            algorithm_version=disagreement.ALGORITHM_VERSION,
            trocr_confidence=line.readings[TROCR].confidence,
            qwen_confidence=line.readings[QWEN].confidence,
            reason=json.dumps(a.as_json(), ensure_ascii=False))  # fmt: skip
        written += 1
    return written


def reading_rows(document_id: UUID | str, run_id: UUID | str, page_id: Any,
                 lines: Sequence[LineReadings], *, first_order: int
                 ) -> list[dict[str, Any]]:  # fmt: skip
    """One evidence row per reading: two for a line both readers read, each with the same
    ``crop_hash``; one, carrying the ``review_reason``, for a line only one of them read."""
    rows: list[dict[str, Any]] = []
    for line in lines:
        for name in sorted(line.readings):
            reading = line.readings[name]
            rows.append({
                "document_id": document_id, "page_id": page_id, "block_type": "line",
                "text": reading.text, "bbox": [int(v) for v in line.bbox], "polygon": None,
                "ocr_conf": reading.confidence, "reading_order": first_order + len(rows),
                "engine": name, "pipeline_run_id": run_id, "crop_hash": line.crop.sha256,
                "region_kind": line.label, "engine_version": reading.engine_version,
                "prompt_hash": reading.prompt_hash, "review_reason": line.review_reason,
            })  # fmt: skip
    return rows
