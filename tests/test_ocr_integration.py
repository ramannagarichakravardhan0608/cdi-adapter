"""E0-S5 integration: ingest -> OCR against real PostgreSQL 16 + real MinIO (no substitutes)."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from cdi_adapter import repo
from cdi_adapter.db import session_scope
from cdi_adapter.ingest.service import ingest_bytes
from cdi_adapter.ocr.service import ocr_document
from tests.helpers import make_pdf

pytestmark = pytest.mark.integration


def test_ocr_writes_ordered_blocks_with_bboxes_and_is_idempotent(app_env):
    pdf = make_pdf([["LABORATORY REPORT", "HbA1c 7.8 % 4.0-5.6", "Creatinine 1.1 mg/dL"]])
    res = ingest_bytes(pdf, "lab-ocr.pdf")
    first = ocr_document(res.document_id, force_engine="rapidocr")
    again = ocr_document(res.document_id, force_engine="rapidocr")
    with session_scope() as c:
        blocks = repo.get_ocr_blocks(c, res.document_id)
        doc = repo.get_document(c, res.document_id)
        runs = [r for r in repo.get_runs(c, res.document_id) if r["stage"] == "ocr"]
    assert first["blocks"] == again["blocks"] == len(blocks) >= 3  # re-run replaced, not doubled
    assert "hba1c" in " ".join(b["text"] for b in blocks).lower().replace(" ", "")
    assert all(len(b["bbox"]) == 4 and b["engine"] == "rapidocr" for b in blocks)
    assert [b["reading_order"] for b in blocks] == list(range(len(blocks)))
    assert doc["status"] == "ocr_done"
    assert [r["status"] for r in runs] == ["ok", "ok"]


def test_vlm_pass_supersedes_rapidocr_blocks(app_env):
    res = ingest_bytes(make_pdf([["handwritten-ish note", "Rx Tab Dolo 650"]]), "hw.pdf")
    ocr_document(res.document_id, force_engine="rapidocr")

    class Transcriber:
        def generate(self, prompt, image_png=None, *, max_tokens=1024, json_schema=None):
            return "Rx\nTab Dolo 650 SOS"

    ocr_document(res.document_id, force_engine="vlm", client=Transcriber())
    with session_scope() as c:
        blocks = repo.get_ocr_blocks(c, res.document_id)
    assert {b["engine"] for b in blocks} == {"vlm"}
    assert [b["text"] for b in blocks] == ["Rx", "Tab Dolo 650 SOS"]
    assert all(b["ocr_conf"] == pytest.approx(0.55) for b in blocks)


def test_every_ocr_run_appends_raw_observations(app_env):
    """E4-S7: the RapidOCR observations are superseded by the VLM pass, never deleted, and each
    observation records the pixels it was read from and the engine that read them."""
    res = ingest_bytes(make_pdf([["handwritten-ish note", "Rx Tab Dolo 650"]]), "hw-e4s7.pdf")
    first = ocr_document(res.document_id, force_engine="rapidocr")

    class Transcriber:
        def generate(self, prompt, image_png=None, *, max_tokens=1024, json_schema=None):
            return "Rx\nTab Dolo 650 SOS"

    second = ocr_document(res.document_id, force_engine="vlm", client=Transcriber())
    with session_scope() as c:
        obs = c.execute(
            text("SELECT engine, engine_version, region_kind, crop_hash, prompt_hash, "
                 "  superseded_by FROM ocr_observation WHERE document_id = :d"),
            {"d": res.document_id},
        ).mappings().all()  # fmt: skip
    old = [o for o in obs if o["engine"] == "rapidocr"]
    new = [o for o in obs if o["engine"] == "vlm"]
    assert len(old) == first["blocks"] >= 2 and len(new) == second["blocks"] == 2
    assert second["superseded"] == len(old)
    assert all(o["superseded_by"] is not None for o in old)  # a pointer, not a delete
    assert all(o["superseded_by"] is None for o in new)
    assert all(o["region_kind"] == "printed" and o["prompt_hash"] is None for o in old)
    assert all(o["region_kind"] == "handwritten" and len(o["prompt_hash"]) == 64 for o in new)
    assert all(len(o["crop_hash"]) == 64 and o["engine_version"] for o in obs)


def test_the_print_pass_records_the_lines_it_found_and_changes_nothing_by_default(app_env):
    """IM-S3 in S3: the counts are always recorded; with ``CDI_LINE_REGIONS`` off (the default)
    every observation is still stored as the reader's kind, exactly as before."""
    pdf = make_pdf([["LABORATORY REPORT FOR THE PATIENT", "Haemoglobin 13.5 g/dL reference",
                     "Creatinine 1.1 mg/dL within range"]])  # fmt: skip
    res = ingest_bytes(pdf, "lab-regions.pdf")
    metrics = ocr_document(res.document_id, force_engine="rapidocr")
    found = metrics["regions"]
    assert found["enforced"] is False and found["lines"] >= 3
    assert found["lines"] == sum(found[k] for k in ("printed", "handwritten", "mixed",
                                                    "uncertain"))  # fmt: skip
    assert found["handwritten"] == 0  # a rendered page is type
    with session_scope() as c:
        kinds = c.execute(text("SELECT DISTINCT region_kind FROM ocr_observation "
                               "WHERE document_id = :d"),
                          {"d": res.document_id}).scalars().all()  # fmt: skip
        run = [r for r in repo.get_runs(c, res.document_id) if r["stage"] == "ocr"][-1]
    assert kinds == ["printed"]
    assert run["metrics"]["regions"] == found  # kept with the run


def test_with_line_regions_on_each_observation_carries_its_lines_label(app_env, monkeypatch):
    from cdi_adapter.config import get_settings

    monkeypatch.setenv("CDI_LINE_REGIONS", "true")
    get_settings.cache_clear()
    try:
        pdf = make_pdf([["LABORATORY REPORT FOR THE PATIENT", "Haemoglobin 13.5 g/dL reference",
                         "Creatinine 1.1 mg/dL within range"]])  # fmt: skip
        res = ingest_bytes(pdf, "lab-regions-on.pdf")
        metrics = ocr_document(res.document_id, force_engine="rapidocr")
        with session_scope() as c:
            kinds = set(c.execute(text("SELECT region_kind FROM ocr_observation "
                                       "WHERE document_id = :d"),
                                  {"d": res.document_id}).scalars())  # fmt: skip
    finally:
        get_settings.cache_clear()
    assert metrics["regions"]["enforced"] is True
    assert kinds <= {"printed", "uncertain"} and "printed" in kinds  # never "handwritten" here
