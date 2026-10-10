"""RD-S1 acceptance: read printed text with RapidOCR.

One group of tests per acceptance criterion, then the functional and non-functional
requirements. The print reader is the real one (RapidOCR on ONNX Runtime, on this machine's
CPU), reading a synthetic printed page made for the test - no real document is used. The
database is not: what is written is caught where it is handed to the database, and the rules the
database itself enforces (rows are never changed or deleted) are read from the migration. They
are exercised for real by ``tests/test_evidence_schema_integration.py``, which needs PostgreSQL.
"""

from __future__ import annotations

import inspect
import io
import re
from importlib import metadata
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from cdi_adapter import repo
from cdi_adapter.config import Settings, get_settings
from cdi_adapter.ingest import pages
from cdi_adapter.ocr import rapid, vlm_ocr
from cdi_adapter.ocr import service as ocr
from cdi_adapter.ocrhost import __main__ as host_main
from cdi_adapter.ocrhost.app import create_app
from cdi_adapter.recognition import pipeline, regions
from tests.helpers import make_pdf

pytest.importorskip("rapidocr_onnxruntime")

ROOT = Path(__file__).resolve().parents[1]

PRINTED = ["City Care Clinic", "Dr. A. Rao, MBBS, MD (General Medicine)", "Reg. No. 12345",
           "Patient: Test Patient   Age: 44 Y / M", "Date: 09/10/2026", "Complete Blood Count",
           "Haemoglobin 13.5 g/dL", "Total WBC 7800 /cumm", "Platelet count 2.5 lakh/cumm",
           "Serum Creatinine 0.9 mg/dL"]  # fmt: skip


@pytest.fixture(scope="module")
def page_png() -> bytes:
    """A printed page: a clinic header, the doctor's printed name, test names."""
    return pages.render_pages(make_pdf([PRINTED], dpi=150), "application/pdf", dpi=150)[0]


@pytest.fixture(scope="module")
def read(page_png) -> list[rapid.OcrLine]:
    return rapid.read_here(page_png, min_conf=get_settings().ocr_min_conf)


def _letters(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


class Saved:
    """Stands where the database is: keeps every row handed to it, and never loses one."""

    def __init__(self, monkeypatch) -> None:
        self.rows: list[dict[str, Any]] = []
        self.calls = 0
        monkeypatch.setattr(repo, "insert_ocr_blocks", self._insert)

    def _insert(self, conn: Any, rows: list[dict[str, Any]]) -> None:
        self.calls += 1
        self.rows += [dict(r) for r in rows]


def _record(read, page_png, run_id: str) -> list[dict[str, Any]]:
    per_page = [({"id": "page-1"}, read, ocr.crop_hashes(page_png, read))]
    meta = ocr._observation_meta(rapid.ENGINE, get_settings())
    return pipeline.record_rapid_observations(None, document_id="doc-1", run_id=run_id,  # type: ignore[arg-type]
                                              per_page=per_page, meta=meta)  # fmt: skip


# ------------------------------------------------------------------ AC1
# GIVEN a printed page WHEN it is read THEN every printed line is saved with text, position and
# confidence


def test_ac1_every_printed_line_of_the_page_is_read(read) -> None:
    assert len(read) == len(PRINTED)
    for line, printed in zip(read, PRINTED, strict=True):  # in reading order, top to bottom
        assert _letters(line.text) == _letters(printed)


def test_ac1_each_line_has_its_position_on_the_page(read, page_png) -> None:
    with Image.open(io.BytesIO(page_png)) as im:
        width, height = im.size
    for line in read:
        x0, y0, x1, y1 = line.bbox
        assert 0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height
        assert line.polygon is not None and len(line.polygon) == 4  # the outline, four corners
    tops = [line.bbox[1] for line in read]
    assert tops == sorted(tops) and [line.reading_order for line in read] == list(range(len(read)))


def test_ac1_each_line_has_a_confidence(read) -> None:
    assert all(0 < line.conf <= 1 for line in read)
    assert all(line.conf >= get_settings().ocr_min_conf for line in read)


def test_ac1_every_line_is_saved_with_text_position_and_confidence(
    read, page_png, monkeypatch
) -> None:
    saved = Saved(monkeypatch)
    rows = _record(read, page_png, "run-1")
    assert saved.rows == rows and len(rows) == len(read)  # one row a line, none left out
    for row, line in zip(rows, read, strict=True):
        assert row["text"] == line.text  # exactly as read
        assert row["bbox"] == line.bbox and row["polygon"] == line.polygon
        assert row["ocr_conf"] == line.conf
        assert row["reading_order"] == line.reading_order
        assert row["engine"] == "rapidocr" and row["region_kind"] == "printed"
        assert row["engine_version"].startswith("rapidocr-onnxruntime ")
        assert row["document_id"] == "doc-1" and row["page_id"] == "page-1"
        assert row["pipeline_run_id"] == "run-1"
        assert re.fullmatch(r"[0-9a-f]{64}", row["crop_hash"])  # the pixels it was read from


def test_ac1_the_table_has_a_column_for_each() -> None:
    insert = inspect.getsource(repo.insert_ocr_blocks)
    for column in ("raw_text", "bbox", "polygon", "ocr_conf", "reading_order", "engine",
                   "engine_version", "crop_hash"):  # fmt: skip
        assert column in insert, column
    assert "INSERT INTO ocr_observation" in insert


def test_ac1_the_pipeline_saves_the_print_readers_lines_through_this() -> None:
    stage = inspect.getsource(ocr.ocr_document)
    assert "lines = rapid.run_rapidocr(png, min_conf=settings.ocr_min_conf)" in stage
    assert "evidence.record_rapid_observations(" in stage
    assert ocr.evidence is pipeline


def test_ac1_the_print_reader_runs_first_on_every_page() -> None:
    """The plain words of the story: it runs first, on every page, whatever comes after."""
    jobs = (ROOT / "src" / "cdi_adapter" / "webapp" / "jobs.py").read_text(encoding="utf-8")
    first = jobs.index('force_engine="rapidocr"')
    assert first < jobs.index('force_engine="vlm"')
    assert Settings(_env_file=None).ocr_engine == "rapidocr"


def test_ac1_a_line_the_reader_is_not_sure_of_is_a_setting_not_a_silent_loss() -> None:
    """ISSUE, recorded as a fact: a line read with a confidence under ``CDI_OCR_MIN_CONF``
    (0.30) is not saved. RapidOCR's own cut-off is higher, so none was seen dropped by it."""
    quad = [[0, 0], [10, 0], [10, 5], [0, 5]]
    kept = rapid.lines_from_result([[quad, "sure", 0.95], [quad, "unsure", 0.29]], min_conf=0.30)
    assert [ln.text for ln in kept] == ["sure"]
    assert Settings(_env_file=None).ocr_min_conf == 0.30
    assert len(rapid.lines_from_result([[quad, "unsure", 0.29]], min_conf=0.0)) == 1


# ------------------------------------------------------------------ AC2
# GIVEN the same page is read twice WHEN the evidence is checked THEN the earlier lines are
# still there (nothing deleted)


def test_ac2_reading_the_page_twice_keeps_the_first_readings_lines(
    read, page_png, monkeypatch
) -> None:
    saved = Saved(monkeypatch)
    first = _record(read, page_png, "run-1")
    second = _record(rapid.read_here(page_png), page_png, "run-2")
    assert saved.calls == 2 and len(saved.rows) == len(first) + len(second)
    kept = [r for r in saved.rows if r["pipeline_run_id"] == "run-1"]
    assert kept == first  # every earlier line, unchanged
    assert [r["text"] for r in second] == [r["text"] for r in first]  # and the page read the same


def test_ac2_saving_only_ever_adds() -> None:
    source = inspect.getsource(pipeline)
    code = "\n".join(line for line in source.split('"""')[2::2])  # without the docstrings
    assert "repo.insert_ocr_blocks(conn, rows)" in code
    assert not re.search(r"\b(UPDATE|DELETE|TRUNCATE)\b", code)
    assert not re.search(r"\b(UPDATE|DELETE)\b", inspect.getsource(repo.insert_ocr_blocks))


def test_ac2_a_second_reading_points_the_first_at_it_and_changes_nothing_else() -> None:
    supersede = inspect.getsource(repo.supersede_ocr_observations)
    assert "UPDATE ocr_observation SET superseded_by = :r, superseded_at = now()" in supersede
    assert "DELETE" not in supersede and "raw_text" not in supersede
    stage = inspect.getsource(ocr.ocr_document)
    assert "repo.supersede_ocr_observations(conn, document_id, run_id)" in stage


def test_ac2_the_database_itself_refuses_to_change_or_delete_a_line() -> None:
    migration = (ROOT / "db" / "alembic" / "versions" / "0013_evidence_schema.py").read_text(
        encoding="utf-8"
    )
    assert "CREATE TRIGGER ocr_observation_append_only BEFORE UPDATE OR DELETE" in migration
    assert "is raw evidence: it is never deleted" in migration
    assert "is raw evidence: it is never updated" in migration
    assert "ocr_observation_no_truncate" in migration  # nor emptied in one go


# ------------------------------------------------------------------ FR3
# Printed lines are not sent to the handwriting readers


def _crop() -> SimpleNamespace:
    return SimpleNamespace(png=b"crop", sha256="c" * 64, record=lambda: {"width": 300})


def test_fr3_a_printed_line_is_not_sent_to_the_handwriting_reader(monkeypatch) -> None:
    sent: list[bytes] = []
    monkeypatch.setattr(vlm_ocr, "read_line_crop", lambda png, client: sent.append(png) or "x")
    monkeypatch.setattr(regions, "meets_standard", lambda crop: True)
    printed = regions.Region(bbox=(0, 0, 400, 30), label="printed", reading_order=0,
                             crop=_crop())  # fmt: skip
    written = regions.Region(bbox=(0, 60, 400, 90), label="handwritten", reading_order=1,
                             crop=_crop())  # fmt: skip
    lines = [rapid.OcrLine(text="City Care Clinic", bbox=[0, 0, 400, 30], conf=0.99),
             rapid.OcrLine(text="scrawl", bbox=[0, 60, 400, 90], conf=0.41)]  # fmt: skip
    reads = ocr.read_lines(lines, [printed, written], client=None, max_lines=60)  # type: ignore[arg-type]
    assert len(sent) == 1 and reads.sent == 1  # the handwritten line only
    assert [label for _, label, _ in reads.read] == ["handwritten"]
    assert 0 not in reads.replaced  # the printed line keeps the print reader's reading
    assert regions.READERS["printed"] == ("print",)


@pytest.mark.xfail(strict=True, reason="RD-S1 FR3: reading line by line is built (IM-S3) but off "
                   "by default - it is one model call per handwritten line and its cost has not "
                   "been measured. With the default settings a page the classifier calls "
                   "handwritten is sent whole to the handwriting reader, its printed lines "
                   "included.")  # fmt: skip
def test_fr3_it_holds_with_the_settings_as_shipped() -> None:
    shipped = Settings(_env_file=None)
    assert shipped.line_regions and shipped.line_readers


# ------------------------------------------------------------------ the OCR host
# Technical requirement: ocrhost/app.py, POST /ocr/rapid on port 8079


@pytest.fixture(scope="module")
def host() -> TestClient:
    return TestClient(create_app())


def test_host_reads_a_page_and_returns_every_line_with_position_and_confidence(
    host, page_png, read
) -> None:
    response = host.post("/ocr/rapid", content=page_png)
    assert response.status_code == 200
    body = response.json()
    assert body["engine"] == "rapidocr" and body["engine_version"].startswith("rapidocr-")
    assert body["seconds"] > 0
    assert [(ln["text"], ln["bbox"], ln["conf"], ln["reading_order"]) for ln in body["lines"]] == [
        (ln.text, ln.bbox, ln.conf, ln.reading_order) for ln in read
    ]
    assert all(len(ln["polygon"]) == 4 for ln in body["lines"])


def test_host_says_what_it_is(host) -> None:
    health = host.get("/healthz").json()
    assert health["status"] == "ok" and health["engine"] == "rapidocr"
    assert health["device"] == "cpu"


def test_host_refuses_what_it_cannot_read_in_plain_words(host, monkeypatch) -> None:
    assert host.post("/ocr/rapid", content=b"").status_code == 400
    assert host.post("/ocr/rapid", content=b"not a picture").status_code == 422
    assert host.post("/ocr/rapid", content=b"x", params={"min_conf": 2}).status_code == 422
    monkeypatch.setenv("CDI_OCR_HOST_MAX_BYTES", "10")
    get_settings.cache_clear()
    try:
        assert host.post("/ocr/rapid", content=b"x" * 11).status_code == 413
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()


def test_host_listens_on_port_8079_on_this_machine_only() -> None:
    assert Settings(_env_file=None).ocr_host_port == 8079
    source = inspect.getsource(host_main)
    assert 'host="127.0.0.1"' in source and "port=get_settings().ocr_host_port" in source


def test_the_pipeline_reads_through_the_host_when_it_is_told_where_it_is(
    host, page_png, read, monkeypatch
) -> None:
    asked: list[str] = []

    def post(url: str, **kw: Any) -> Any:
        asked.append(url)
        return host.post("/ocr/rapid", content=kw["content"], params=kw.get("params"))

    import httpx

    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setenv("CDI_OCR_HOST_URL", "http://127.0.0.1:8079/")
    get_settings.cache_clear()
    try:
        through = rapid.run_rapidocr(page_png)
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
    assert asked == ["http://127.0.0.1:8079/ocr/rapid"]
    assert [(ln.text, ln.bbox, ln.conf, ln.polygon, ln.reading_order) for ln in through] == [
        (ln.text, ln.bbox, ln.conf, ln.polygon, ln.reading_order) for ln in read
    ]
    assert Settings(_env_file=None).ocr_host_url == ""  # as shipped: read in-process


# ------------------------------------------------------------------ non-functional


def test_nfr_cpu_only_no_gpu_memory() -> None:
    import onnxruntime

    engine = rapid.get_engine()
    sessions = [inner
                for part in ("text_det", "text_cls", "text_rec")
                for holder in vars(getattr(engine, part)).values()
                if hasattr(inner := getattr(holder, "session", None), "get_providers")]  # fmt: skip
    assert len(sessions) == 3  # the three models: find the lines, turn them, read them
    assert {p for s in sessions for p in s.get_providers()} == {"CPUExecutionProvider"}
    lock = (ROOT / "infra" / "runpod" / "requirements.lock").read_text(encoding="utf-8")
    assert "onnxruntime==" in lock and "onnxruntime-gpu" not in lock  # the pod's build too
    assert onnxruntime.__name__ == "onnxruntime"


def test_nfr_the_readers_code_is_apache_2() -> None:
    """The package says so itself. The licence of the model files inside it is not stated in
    the package: the story's own check (2026-10-03) is what that rests on."""
    assert metadata.metadata("rapidocr-onnxruntime")["License"] == "Apache-2.0"


def test_nfr_a_page_is_read_in_seconds_on_a_cpu(page_png) -> None:
    """Not the story's measurement (26 lines in 5.9 s on its sample page, first call): that
    page is not in the repository. This only holds the reader to the same order of time."""
    import time

    started = time.perf_counter()
    lines = rapid.read_here(page_png)
    assert len(lines) == len(PRINTED) and time.perf_counter() - started < 30
