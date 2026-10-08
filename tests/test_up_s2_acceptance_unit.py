"""UP-S2 acceptance: tell the person at once if a picture is not good enough, and let them retake
just that page.

One group of tests per acceptance criterion, written on 2026-10-08 to record what the code does
today. **No application code was changed for this file.**

How they run: the real picture check (``recognition/quality.py``) and the real job runner
(``webapp/jobs.py``) with the check switched on, on real pictures built here - a rendered
prescription and a blurred copy of it. The six pipeline stages are recorders, so what is proved
is *which pages reach which stage*, not what the stages do. A retake is made the way
``jobstore.replace_page`` makes it (a fresh page in the same place, the job reopened); the
database half of that call is covered by the integration tests and was not run here.

AC5 was found unbuilt by this file on 2026-10-08 (its tests were strict expected failures) and
was built the same day; every test here now has to pass.
"""

from __future__ import annotations

import inspect
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")

from cdi_adapter.config import Settings, get_settings  # noqa: E402
from cdi_adapter.ingest import pages  # noqa: E402
from cdi_adapter.recognition import quality  # noqa: E402
from cdi_adapter.webapp import jobstore  # noqa: E402
from cdi_adapter.webapp.jobs import UNFINISHED, DocState, JobRunner, Stages  # noqa: E402
from cdi_adapter.webapp.page import BODY, SCRIPT  # noqa: E402
from tests.helpers import make_pdf  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
STEPS = ["ingest", "ocr", "classify", "extract", "terminology", "validate"]


# ------------------------------------------------------------------ pictures and a pipeline


def _page(tag: str) -> bytes:
    lines = [f"Line {i}: Tab Amoxicillin 500mg 1-0-1 x5 days {tag}" for i in range(12)]
    return pages.render_pages(make_pdf([lines]), "application/pdf")[0]


def _blur(png: bytes) -> bytes:
    arr = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_GRAYSCALE)
    return cv2.imencode(".png", cv2.GaussianBlur(arr, (25, 25), 0))[1].tobytes()


@pytest.fixture(scope="module")
def good() -> bytes:
    return _page("one")


@pytest.fixture(scope="module")
def good2() -> bytes:
    return _page("two")


@pytest.fixture(scope="module")
def blurry(good) -> bytes:
    return _blur(good)


@pytest.fixture
def check_on(monkeypatch):
    """The picture check switched on, as on the test instance (``CDI_QUALITY_CHECK=true``)."""
    monkeypatch.setenv("CDI_QUALITY_CHECK", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class Pipeline:
    """The six stages as recorders: which file reached which stage."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def stages(self) -> Stages:
        def ingest(raw: bytes, name: str, mime: Any, patient_id: Any = None) -> Any:
            self.calls.append(("ingest", name))
            return SimpleNamespace(document_id=f"doc-{name}")

        def step(stage: str) -> Any:
            return lambda doc, *a, **k: self.calls.append((stage, doc[4:]))

        def classify(doc: str) -> dict[str, Any]:
            self.calls.append(("classify", doc[4:]))
            return {"doc_type": "prescription", "is_handwritten": False}

        def extract(doc: str, patient_id: Any = None, expected_patient: Any = None) -> Any:
            self.calls.append(("extract", doc[4:]))
            return {"patient_id": patient_id or "patient-1", "facts": 3}

        return Stages(ingest=ingest, ocr=step("ocr"), classify=classify, extract=extract,
                      terminology=step("terminology"), validate=step("validate"),
                      counts=lambda d: {"facts": 3, "accepted": 3, "in_review": 0},
                      merge_identity=lambda p: None)  # fmt: skip

    def reached(self, name: str) -> list[str]:
        return [stage for stage, who in self.calls if who == name]


def _run(files: list[tuple[str, bytes, str | None]]) -> tuple[Any, Pipeline]:
    pipe = Pipeline()
    runner = JobRunner(pipe.stages(), max_workers=4)
    return runner.wait(runner.create(files).id, timeout=60), pipe


# ------------------------------------------------------------------ AC1
# GIVEN a blurry page is sent WHEN the quality check runs
# THEN within 5 s the screen says 'too blurry, please retake' for that page


def test_ac1_a_blurry_page_is_held_and_named_blurred(check_on, good, blurry) -> None:
    job, _ = _run([("page1.png", good, "image/png"), ("page2.png", blurry, "image/png")])
    held = job.docs[1]
    assert held.status == "quality_hold" and held.quality["code"] == "blurred"
    assert job.docs[0].status == "done" and job.docs[0].quality is None  # only that page


def test_ac1_the_person_is_told_it_is_too_blurry_and_to_take_it_again(check_on, blurry) -> None:
    """The words on the screen. The AC quotes 'too blurry, please retake'; the page says the same
    thing in a fuller sentence and puts a "Retake this page" button beside it."""
    job, _ = _run([("page1.png", blurry, "image/png")])
    said = job.docs[0].quality["message"]
    assert "too blurry" in said and "take it again" in said
    assert job.docs[0].error == said  # what the progress row shows
    assert "(d.quality || {}).message || d.error" in SCRIPT
    assert "Retake this page" in SCRIPT and "picture not good enough" in SCRIPT


def test_ac1_the_message_is_on_that_pages_row(check_on) -> None:
    start = SCRIPT.index("j.documents.map((d, i) => {")
    row = SCRIPT[start : SCRIPT.index("}).join('');", start)]
    assert "const held = d.status === 'quality_hold';" in row
    assert 'data-retake="${i}"' in row  # the button knows which page it belongs to


def test_ac1_the_check_itself_takes_well_under_five_seconds(check_on) -> None:
    """A full-size phone frame (1920 x 1080), blurred."""
    frame = np.full((1080, 1920), 255, np.uint8)
    for y in range(80, 1000, 60):
        cv2.putText(frame, "Tab Metformin 500 mg 1-0-1 x 30 days", (80, y),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.4, 0, 3)  # fmt: skip
    raw = cv2.imencode(".png", cv2.GaussianBlur(frame, (31, 31), 0))[1].tobytes()
    started = time.perf_counter()
    verdict = quality.assess(raw, "image/png")
    took = time.perf_counter() - started
    assert verdict.code == "blurred" and took < 5.0, f"the check took {took:.2f} s"


def test_ac1_a_held_job_comes_back_within_five_seconds_of_being_sent(check_on, blurry) -> None:
    """Send to verdict, through the real runner (the stages after the check are instant here; on
    a busy server the wait in the queue comes on top and is not measured by this test)."""
    started = time.perf_counter()
    job, _ = _run([("page1.png", blurry, "image/png")])
    assert job.docs[0].status == "quality_hold" and time.perf_counter() - started < 5.0


def test_ac1_the_screen_asks_again_every_second_and_a_half() -> None:
    assert "if (j.state === 'running'){ setTimeout(() => poll(id), 1500); return; }" in SCRIPT


def test_ac1_the_check_comes_before_any_reading(check_on) -> None:
    from cdi_adapter.webapp import jobs

    phase1 = inspect.getsource(jobs.JobRunner._phase1)
    assert phase1.index("quality.assess(") < phase1.index("s.ingest(")


def test_ac1_the_check_runs_by_default(blurry) -> None:
    """On by default since 2026-10-08 (IM-S1 AC2): with no setting at all, a blurry page is held."""
    assert Settings().quality_check is True
    get_settings.cache_clear()
    job, _ = _run([("page1.png", blurry, "image/png")])
    assert job.docs[0].status == "quality_hold" and job.docs[0].quality["code"] == "blurred"


def test_ac1_it_can_be_switched_off_and_then_nothing_is_held(monkeypatch, blurry) -> None:
    monkeypatch.setenv("CDI_QUALITY_CHECK", "false")
    get_settings.cache_clear()
    try:
        job, _ = _run([("page1.png", blurry, "image/png")])
    finally:
        get_settings.cache_clear()
    assert job.docs[0].status == "done" and job.docs[0].quality is None


# ------------------------------------------------------------------ AC2
# GIVEN the person retakes that page WHEN the new picture is sent
# THEN only that page is re-checked and the others stay as they were


def _retake(runner: JobRunner, job: Any, index: int, name: str, raw: bytes) -> Any:
    """What ``jobstore.replace_page`` does to the job, without the database: a fresh page in the
    same place, the job reopened, and the runner sent round again with that one file."""
    files: list[tuple[str, bytes, str | None] | None] = [None] * len(job.docs)
    files[index] = (name, raw, "image/png")
    job.docs[index] = DocState(filename=name)
    job.state = "running"
    job.finished.clear()
    runner._run(job, files)
    return job


def test_ac2_only_the_retaken_page_is_read_again(check_on, good, good2, blurry) -> None:
    pipe = Pipeline()
    runner = JobRunner(pipe.stages(), max_workers=4)
    job = runner.wait(
        runner.create([("page1.png", good, "image/png"), ("page2.png", blurry, "image/png")]).id,
        timeout=60,
    )
    assert [d.status for d in job.docs] == ["done", "quality_hold"]
    before = dict(vars(job.docs[0]))
    first_run = list(pipe.calls)

    _retake(runner, job, 1, "page2-retake.png", good2)

    again = pipe.calls[len(first_run):]  # fmt: skip
    assert {who for _, who in again} == {"page2-retake.png"}  # nothing of page 1 ran again
    assert [stage for stage, _ in again] == STEPS  # and the new page went the whole way
    assert vars(job.docs[0]) == before  # page 1 exactly as it was
    assert [d.status for d in job.docs] == ["done", "done"] and job.state == "review"


def test_ac2_a_retake_that_is_still_blurry_is_held_again_and_only_it(
    check_on, good, blurry
) -> None:
    pipe = Pipeline()
    runner = JobRunner(pipe.stages(), max_workers=4)
    job = runner.wait(
        runner.create([("page1.png", good, "image/png"), ("page2.png", blurry, "image/png")]).id,
        timeout=60,
    )
    seen = len(pipe.calls)
    _retake(runner, job, 1, "page2-again.png", blurry)
    assert pipe.calls[seen:] == []  # held before any stage: nothing ran at all
    assert [d.status for d in job.docs] == ["done", "quality_hold"]
    assert job.docs[1].filename == "page2-again.png"


def test_ac2_the_runner_starts_over_only_pages_that_are_not_finished(check_on) -> None:
    """The rule the retake relies on: a finished page and a held page are both left alone; only
    the fresh page in the retaken place is run."""
    runner = JobRunner(Pipeline().stages())
    job = SimpleNamespace(docs=[DocState(filename="a", status="done", steps_done=list(STEPS)),
                                DocState(filename="b", status="quality_hold"),
                                DocState(filename="c-retake")])  # fmt: skip
    assert runner._resume(job) == [2]
    assert "quality_hold" not in UNFINISHED and "done" not in UNFINISHED


def test_ac2_the_retake_rewrites_that_one_page_and_nothing_else() -> None:
    """``jobstore.replace_page``: one file slot and one document, addressed by the page's own
    index, under the same storage key. (Its SQL is run by the integration tests.)"""
    source = inspect.getsource(jobstore.replace_page)
    assert "key = staging_key(job_id, index)" in source
    assert "files = jsonb_set(files, :path" in source and "docs = jsonb_set(docs, :path" in source
    assert '"path": "{" + str(index) + "}"' in source
    assert "fresh = DocState(filename=name)" in source


def test_ac2_the_screen_sends_the_retake_for_that_page_only() -> None:
    assert "retakePage = +b.dataset.retake + 1" in SCRIPT
    assert (
        "fetch(`/api/jobs/${jobId}/pages/${retakePage}/replace`, {method:'POST', body:fd})"
        in SCRIPT
    )
    assert "id=retakefile" in BODY


def test_ac2_a_page_that_is_not_in_the_job_cannot_be_retaken() -> None:
    class _Store:
        objects = None

        def load(self, job_id: str) -> Any:
            return SimpleNamespace(docs=[DocState(filename="a")])

    with pytest.raises(jobstore.PageNotReplaced, match="pages 1-1"):
        jobstore.replace_page("j", 3, "x.png", b"x", "image/png", actor="desk", st=_Store())  # type: ignore[arg-type]
    with pytest.raises(jobstore.PageNotReplaced, match="empty"):
        jobstore.replace_page("j", 0, "x.png", b"", "image/png", actor="desk", st=_Store())  # type: ignore[arg-type]


# ------------------------------------------------------------------ AC3
# GIVEN all pages are good WHEN the job runs THEN no warning is shown and the result appears


def test_ac3_good_pages_are_not_held_and_carry_no_warning(check_on, good, good2) -> None:
    job, pipe = _run([("page1.png", good, "image/png"), ("page2.png", good2, "image/png")])
    assert [d.status for d in job.docs] == ["done", "done"] and job.state == "review"
    assert all(d.quality is None and d.error is None for d in job.docs)
    assert all(d.steps_done == STEPS for d in job.docs)
    assert pipe.reached("page1.png") == STEPS and pipe.reached("page2.png") == STEPS


def test_ac3_the_api_carries_no_verdict_for_a_good_page(check_on, good) -> None:
    job, _ = _run([("page1.png", good, "image/png")])
    doc = job.as_json()["documents"][0]
    assert doc["status"] == "done" and not doc.get("quality")
    assert quality.assess(good, "image/png").as_json() is None


def test_ac3_the_warning_is_drawn_only_for_a_held_page_and_the_result_is_offered() -> None:
    assert "const note = held" in SCRIPT and ": '';" in SCRIPT  # no note unless held
    assert "see result" in SCRIPT and "d.steps_done.includes('validate')" in SCRIPT
    assert 'href="/result/job/${id}"' in SCRIPT  # several pages: the one result of the upload


# ------------------------------------------------------------------ AC4
# GIVEN a page is held WHEN the job finishes THEN no values are saved from the held page


def test_ac4_a_held_page_reaches_no_stage_at_all(check_on, good, blurry) -> None:
    job, pipe = _run([("page1.png", good, "image/png"), ("page2.png", blurry, "image/png")])
    assert pipe.reached("page2.png") == []  # not ingested, not read, nothing extracted
    assert pipe.reached("page1.png") == STEPS
    assert job.state == "review"  # the job itself finishes


def test_ac4_a_held_page_has_no_document_and_no_facts(check_on, good, blurry) -> None:
    job, _ = _run([("page1.png", good, "image/png"), ("page2.png", blurry, "image/png")])
    held = job.docs[1]
    assert held.document_id is None and held.steps_done == []
    assert (held.facts, held.accepted, held.in_review) == (0, 0, 0)
    assert job.docs[0].document_id == "doc-page1.png" and job.docs[0].facts == 3


def test_ac4_a_job_of_only_held_pages_saves_nothing(check_on, blurry) -> None:
    job, pipe = _run([("a.png", blurry, "image/png"), ("b.png", _blur(_page("b")), "image/png")])
    assert pipe.calls == [] and [d.status for d in job.docs] == ["quality_hold"] * 2


def test_ac4_the_upload_result_shows_the_held_page_as_held_with_no_values(monkeypatch) -> None:
    """The one result of the upload (``result.build_job``): the held page is listed with its
    reason and contributes no value."""
    from cdi_adapter import result

    class _Rows:
        def mappings(self) -> Any:
            return self

        def first(self) -> Any:
            return {
                "id": "a" * 32,
                "state": "review",
                "created_at": None,
                "docs": [
                    {
                        "filename": "page2.png",
                        "status": "quality_hold",
                        "document_id": None,
                        "quality": {
                            "code": "blurred",
                            "message": "This photo is too blurry to read.",
                        },
                    }
                ],
            }

    out = result.build_job(SimpleNamespace(execute=lambda *a, **k: _Rows()), "a" * 32)
    assert out["pages"][0]["status"] == "held_for_rescan"
    assert out["documents"] == [] and out["lab_tests"] == [] and out["diagnoses"] == []
    assert out["status"] == "held_for_rescan"


# ------------------------------------------------------------------ AC5
# GIVEN many pages are held for 'too small' WHEN the weekly report is read
# THEN the retake rate and the top reasons are shown


def _mentions(*words: str) -> list[str]:
    hits = []
    for folder in ("src", "scripts", "infra"):
        for path in (ROOT / folder).rglob("*"):
            if path.suffix in (".py", ".sh", ".sql", ".yml", ".yaml", ".json") and path.is_file():
                text = path.read_text(encoding="utf-8", errors="ignore").lower()
                if all(w in text for w in words):
                    hits.append(str(path.relative_to(ROOT)))
    return hits


def test_ac5_what_a_report_would_be_built_from_is_recorded_today(check_on, good) -> None:
    """What exists: each held page keeps its reason code and what was measured, and each retake
    is written to the audit log by page number."""
    small = cv2.imencode(".png", np.full((300, 240), 255, np.uint8))[1].tobytes()
    job, _ = _run([("tiny.png", small, "image/png")])
    kept = job.as_json()["documents"][0]["quality"]
    assert kept["code"] == "too_small" and "extent_px" in kept["measures"]
    assert '"replaced_page": index + 1' in inspect.getsource(jobstore.replace_page)


def test_ac5_there_is_a_report_that_gives_the_retake_rate() -> None:
    assert _mentions("retake_rate") or _mentions("retake rate")


def test_ac5_the_report_counts_the_top_reasons_pages_were_held() -> None:
    assert _mentions("top_reasons") or _mentions("top reasons")


def test_ac5_the_report_can_be_read_through_a_route_or_a_command() -> None:
    from cdi_adapter.webapp.app import app

    routes = [p for p in app.openapi()["paths"] if "report" in p or "quality" in p]
    commands = [
        p.name
        for p in (ROOT / "scripts").glob("*")
        if "report" in p.name.lower()
        and "quality" in p.read_text(encoding="utf-8", errors="ignore").lower()
    ]
    assert routes or commands


# ------------------------------------------------------------------ AC5: the report itself


def _report(pages: int, uploads: int, reasons: list[tuple[str, int]], retaken: int) -> Any:
    from datetime import UTC, datetime, timedelta

    from cdi_adapter.webapp import quality_report

    until = datetime(2026, 10, 8, tzinfo=UTC)
    return quality_report.summarise(pages, uploads, reasons, retaken, days=7,
                                    since=until - timedelta(days=7), until=until)  # fmt: skip


def test_ac5_many_pages_held_for_too_small_show_as_the_top_reason_and_a_rate() -> None:
    """The AC's own case: a week in which many pages were held for 'too small'."""
    out = _report(pages=180, uploads=90, reasons=[("blurred", 9), ("too_small", 30),
                                                  ("too_dark", 3)], retaken=20)  # fmt: skip
    assert out["pictures_sent"] == 200  # 180 pages and the 20 retakes that replaced some
    assert out["held"] == 42 and out["retake_rate"] == 0.21  # 42 of 200 had to be retaken
    top = out["top_reasons"]
    assert [(r["code"], r["held"]) for r in top] == [("too_small", 30), ("blurred", 9),
                                                     ("too_dark", 3)]  # fmt: skip
    assert top[0]["reason"] == "too small" and top[0]["share_of_held"] == round(30 / 42, 4)
    assert out["retaken"] == 20 and out["retaken_share_of_held"] == round(20 / 42, 4)
    assert out["period"]["days"] == 7 and out["uploads"] == 90


def test_ac5_a_week_with_nothing_held_and_a_week_with_nothing_sent() -> None:
    quiet = _report(pages=50, uploads=40, reasons=[], retaken=0)
    assert quiet["retake_rate"] == 0.0 and quiet["top_reasons"] == []
    assert quiet["retaken_share_of_held"] is None
    empty = _report(pages=0, uploads=0, reasons=[], retaken=0)
    assert empty["retake_rate"] is None and empty["pictures_sent"] == 0  # no rate of nothing


def test_ac5_equal_reasons_are_listed_in_a_fixed_order() -> None:
    out = _report(10, 5, [("too_dark", 2), ("blurred", 2)], 0)
    assert [r["code"] for r in out["top_reasons"]] == ["blurred", "too_dark"]


def test_ac5_the_report_reads_as_plain_text() -> None:
    from cdi_adapter.webapp import quality_report

    said = quality_report.as_text(_report(180, 90, [("too_small", 30), ("blurred", 12)], 20))
    assert "retake rate        21.0 %" in said and "Top reasons" in said
    assert said.index("too small") < said.index("blurred")
    assert "(no picture was held)" in quality_report.as_text(_report(5, 5, [], 0))


# a job store that accepts every save: enough for the runner to treat the job as stored
_STORE = SimpleNamespace(save=lambda *a, **k: True, finish=lambda *a, **k: None)


class _Conn:
    """A connection that answers the report's three questions and keeps what it was asked."""

    def __init__(self) -> None:
        self.asked: list[tuple[str, dict[str, Any]]] = []

    def execute(self, statement: Any, params: dict[str, Any] | None = None) -> Any:
        sql = " ".join(str(statement).split())
        self.asked.append((sql, dict(params or {})))
        if "FROM web_job" in sql:
            return SimpleNamespace(one=lambda: (180, 90))
        if "quality_hold" in sql:
            return SimpleNamespace(all=lambda: [("too_small", 30), ("blurred", 12)])
        return SimpleNamespace(scalar_one=lambda: 20)


def test_ac5_the_report_counts_the_last_seven_days_from_the_audit_log() -> None:
    from datetime import UTC, datetime

    from cdi_adapter.webapp import quality_report

    conn, now = _Conn(), datetime(2026, 10, 8, 12, tzinfo=UTC)
    out = quality_report.weekly(conn, now=now)  # type: ignore[arg-type]
    assert out["held"] == 42 and out["retake_rate"] == 0.21 and out["retaken"] == 20
    assert out["top_reasons"][0]["code"] == "too_small"
    jobs_sql, holds_sql, retakes_sql = (sql for sql, _ in conn.asked)
    assert "sum(jsonb_array_length(docs))" in jobs_sql
    assert "detail ? 'quality_hold'" in holds_sql and "GROUP BY 1" in holds_sql
    assert "detail ? 'replaced_page'" in retakes_sql
    for _, params in conn.asked:  # every count is over the same seven days
        assert (params["until"] - params["since"]).days == 7 and params["until"] == now


def test_ac5_each_hold_is_written_down_when_it_happens(monkeypatch, check_on, blurry) -> None:
    """The runner records a hold the moment it holds: page number and reason, nothing else."""
    from contextlib import contextmanager

    from cdi_adapter import db
    from cdi_adapter.webapp import quality_report

    written: list[dict[str, Any]] = []

    @contextmanager
    def scope() -> Any:
        yield "conn"

    monkeypatch.setattr(db, "session_scope", scope)
    monkeypatch.setattr(quality_report.repo, "write_audit",
                        lambda conn, **row: written.append(row))  # fmt: skip
    runner = JobRunner(Pipeline().stages(), max_workers=2)
    runner.store = _STORE  # type: ignore[assignment]
    job = runner.wait(
        runner.create(
            [("ok.png", _page("ok"), "image/png"), ("blurry.png", blurry, "image/png")]
        ).id,
        timeout=60,
    )
    assert job.docs[1].status == "quality_hold"
    assert written == [{"actor": runner.owner, "action": "update", "entity": "web_job",
                        "entity_id": job.id,
                        "detail": {"quality_hold": {"page": 2, "code": "blurred"}}}]  # fmt: skip


def test_ac5_a_report_row_that_cannot_be_written_never_costs_the_hold(monkeypatch, check_on,
                                                                      blurry) -> None:  # fmt: skip
    from cdi_adapter import db

    def broken() -> Any:
        raise RuntimeError("database away")

    monkeypatch.setattr(db, "session_scope", broken)
    runner = JobRunner(Pipeline().stages(), max_workers=2)
    runner.store = _STORE  # type: ignore[assignment]
    job = runner.wait(runner.create([("blurry.png", blurry, "image/png")]).id, timeout=60)
    assert job.docs[0].status == "quality_hold" and job.docs[0].quality["code"] == "blurred"


def test_ac5_the_report_carries_counts_only() -> None:
    """No file name, no patient, no measurement: nothing that says whose picture it was."""
    from cdi_adapter.webapp import quality_report

    source = inspect.getsource(quality_report)
    assert 'detail={HOLD: {"page": page, "code": code}}' in source
    out = _report(10, 5, [("blurred", 2)], 1)
    assert set(out) == {"period", "uploads", "pictures_sent", "held", "retake_rate", "retaken",
                        "retaken_share_of_held", "top_reasons"}  # fmt: skip


def test_ac5_the_report_has_a_route_and_a_command_and_a_week_is_the_default() -> None:
    from cdi_adapter.webapp import auth, quality_report
    from cdi_adapter.webapp.app import app, quality_report_route

    assert "/api/reports/quality" in app.openapi()["paths"]
    assert auth.POLICY[("GET", "/api/reports/quality")] == {"reviewer", "admin"}
    assert inspect.signature(quality_report_route).parameters["days"].default == 7
    assert inspect.signature(quality_report.weekly).parameters["days"].default == 7
    assert (ROOT / "scripts" / "quality_report.py").is_file()


@pytest.mark.parametrize("days", [0, -1, 367])
def test_ac5_a_silly_number_of_days_is_refused(days: int) -> None:
    from fastapi import HTTPException

    from cdi_adapter.webapp.app import quality_report_route

    with pytest.raises(HTTPException) as refused:
        quality_report_route(days=days)
    assert refused.value.status_code == 422
