"""LS-S4 acceptance: read each image or PDF with the same OCR steps as the upload screen.

One group of tests per acceptance criterion, then the PDF renderer (pypdfium2).

What is real here: the watched folder (``tmp_path``, the ``local`` connector), the listener
loop, the listener's real reader (``pipeline_reader``), the page renderer and the picture
quality check. **What is not: the pipeline behind the job store.** These tests stop at the door
- they record what the listener hands to ``jobstore.submit`` and answer for the job. Saving a
document, OCR, the model and the extracted values need PostgreSQL, the document store and the
model, none of which run in a unit test; the gaps that leaves are written as expected failures.
"""

from __future__ import annotations

import inspect
import io
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import cv2
import numpy as np
import pytest
from PIL import Image, ImageDraw

from cdi_adapter.config import Settings
from cdi_adapter.ingest import pages, pdf
from cdi_adapter.listener import connectors as C
from cdi_adapter.listener import service as S
from cdi_adapter.recognition import quality
from cdi_adapter.webapp import jobs, jobstore
from tests.helpers import blank_pdf, make_pdf

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "cdi_adapter"
WHEN = datetime(2026, 10, 8, 9, 30, tzinfo=UTC)
RX_1 = ["CareFlow Polyclinic", "Name: Anjali Das   Age: 48 Y / F", "Tab Metformin 500 mg 1-0-1",
        "Tab Amlodipine 5 mg OD", "Review after 1 month"]  # fmt: skip
RX_2 = ["City Clinic", "Name: Ravi Kumar   Age: 61 Y / M", "Tab Atorvastatin 10 mg 0-0-1",
        "Tab Telmisartan 40 mg OD", "Review after 2 weeks"]  # fmt: skip


def _picture(lines: list[str], fmt: str) -> bytes:
    """A sharp photo-sized picture of a prescription, as PNG (a scan) or JPEG (a photo)."""
    page = pages.render_pages(make_pdf([lines], dpi=150), "application/pdf", dpi=150)[0]
    out = io.BytesIO()
    Image.open(io.BytesIO(page)).convert("RGB").save(out, format=fmt, quality=92)
    return out.getvalue()


def _blurred(lines: list[str]) -> bytes:
    arr = cv2.imdecode(np.frombuffer(_picture(lines, "PNG"), np.uint8), cv2.IMREAD_GRAYSCALE)
    return cv2.imencode(".png", cv2.GaussianBlur(arr, (41, 41), 0))[1].tobytes()


class Door:
    """Stands where the pipeline's job store is. It records exactly what the listener's real
    reader submits, and answers for each job the way the pipeline would at its first step: the
    **real** picture-quality check decides between "held" and "done"."""

    def __init__(self, monkeypatch) -> None:
        self.submitted: list[dict[str, Any]] = []
        self._jobs: dict[str, Any] = {}
        monkeypatch.setattr(jobstore, "submit", self.submit)
        monkeypatch.setattr(jobstore, "wait", lambda job_id, timeout: self._jobs[job_id])

    def submit(self, files: list[tuple[str, bytes, str | None]], **options: Any) -> Any:
        job_id = f"job-{len(self.submitted) + 1}"
        ((name, raw, mime),) = files
        held = quality.assess(raw, mime or "", settings=Settings(quality_check=True)).as_json()
        doc = SimpleNamespace(
            filename=name,
            status="quality_hold" if held else "done",
            quality=held,
            error=(held or {}).get("message"),
            doc_type=None,
        )
        self._jobs[job_id] = SimpleNamespace(id=job_id, state="review", docs=[doc])
        self.submitted.append({"files": files, "options": options, "pages": None if held
                               else len(pages.render_pages(raw, mime or ""))})  # fmt: skip
        return SimpleNamespace(id=job_id)


@pytest.fixture
def folder(tmp_path) -> Path:
    root = tmp_path / "File listener"
    root.mkdir()
    return root


def _listener(folder: Path) -> S.Listener:
    settings = Settings(listener_connector="local", listener_local_root=str(folder),
                        listener_inbox="")  # fmt: skip
    connector = C.get_connector(settings)
    connector.ensure_folders()
    reader = S.pipeline_reader(settings)
    # the real reader, without its database: readiness and the ledger are the loop's business
    return S.Listener(connector, S.Reader(read=reader.read), now=lambda: WHEN)


def _process(folder: Path) -> dict[str, Any]:
    listener = _listener(folder)
    listener.poll()
    return listener.poll()


def _names(folder: Path, sub: str = "") -> list[str]:
    return sorted(p.name for p in (folder / sub).iterdir() if p.is_file())


# ------------------------------------------------------------------ AC1
# GIVEN a 2-page PDF prescription is dropped WHEN it is processed
# THEN one document with 2 pages is saved and both pages are read


def test_ac1_a_two_page_pdf_goes_in_as_one_file_one_job(monkeypatch, folder) -> None:
    door, raw = Door(monkeypatch), make_pdf([RX_1, RX_2], dpi=150)
    (folder / "rx.pdf").write_bytes(raw)
    report = _process(folder)
    assert report["succeeded"] == ["rx.pdf"]
    (sent,) = door.submitted  # one job ...
    assert sent["files"] == [("rx.pdf", raw, "application/pdf")]  # ... of one file: one document
    assert sent["pages"] == 2  # ... whose two pages both become pictures to be read


def test_ac1_both_pages_become_pictures_at_the_dpi_setting() -> None:
    assert Settings.model_fields["page_dpi"].default == 200
    rendered = pages.render_pages(make_pdf([RX_1, RX_2], dpi=150), "application/pdf")
    assert len(rendered) == 2
    for png in rendered:
        with Image.open(io.BytesIO(png)) as im:
            assert im.size == (1654, 2339) and im.mode == "RGB"  # A4 at 200 dpi
    assert rendered[0] != rendered[1]  # two different pages, not the first one twice


def test_ac1_every_page_of_the_file_is_judged_not_just_the_first(monkeypatch, folder) -> None:
    """The second page is the blurred one: the file is held, so page 2 was looked at."""
    sharp = Image.open(io.BytesIO(_picture(RX_1, "PNG")))
    soft = Image.open(io.BytesIO(_blurred(RX_2))).convert("RGB")
    buf = io.BytesIO()
    sharp.save(buf, format="PDF", resolution=150.0, save_all=True, append_images=[soft])
    Door(monkeypatch)
    (folder / "rx.pdf").write_bytes(buf.getvalue())
    (failed,) = _process(folder)["failed"]
    assert failed["kind"] == "rescan"


def test_ac1_one_file_is_one_prescription_whatever_its_page_count(monkeypatch, folder) -> None:
    door = Door(monkeypatch)
    (folder / "three-pages.pdf").write_bytes(make_pdf([RX_1, RX_2, RX_1], dpi=150))
    (folder / "one-page.png").write_bytes(_picture(RX_2, "PNG"))
    _process(folder)
    assert [len(s["files"]) for s in door.submitted] == [1, 1]  # never split, never merged
    assert sorted(s["pages"] for s in door.submitted) == [1, 3]


@pytest.mark.xfail(strict=True, reason="LS-S4 AC1, 'one document with 2 pages is saved': saving "
                   "needs PostgreSQL and the document store, which are not running. No test "
                   "has stored a listener document and counted its pages.")  # fmt: skip
def test_ac1_the_saved_document_has_been_checked_on_a_real_database() -> None:
    assert (ROOT / "tests" / "test_listener_pipeline_integration.py").is_file()


# ------------------------------------------------------------------ AC2
# GIVEN a photo (JPEG) and a scan (PNG) of different prescriptions are dropped
# WHEN both finish THEN both went through the same steps and are saved the same way


def test_ac2_a_jpeg_and_a_png_are_both_read_through_the_same_door(monkeypatch, folder) -> None:
    door = Door(monkeypatch)
    photo, scan = _picture(RX_1, "JPEG"), _picture(RX_2, "PNG")
    (folder / "photo.jpg").write_bytes(photo)
    (folder / "scan.png").write_bytes(scan)
    report = _process(folder)
    assert sorted(report["succeeded"]) == ["photo.jpg", "scan.png"] and report["failed"] == []
    sent = {s["files"][0][0]: s for s in door.submitted}
    assert sent["photo.jpg"]["files"] == [("photo.jpg", photo, "image/jpeg")]
    assert sent["scan.png"]["files"] == [("scan.png", scan, "image/png")]
    assert sent["photo.jpg"]["options"] == sent["scan.png"]["options"]  # nothing differs
    assert _names(folder, "success") == ["photo.jpg", "scan.png"]


def test_ac2_the_runner_has_one_set_of_steps_whatever_the_file_type() -> None:
    """The steps are chosen by nothing about the file: the runner's code never looks at the
    picture format to decide what to run."""
    source = inspect.getsource(jobs)
    for branch in ('== "image/jpeg"', '== "image/png"', '.endswith(".jpg")', '.endswith(".png")'):
        assert branch not in source, branch
    steps = [m for m in ("quality.assess", "s.ingest(", "s.ocr(", "s.classify(", "s.extract(")
             if m in source]  # fmt: skip
    assert len(steps) == 5  # quality check, save, read, classify, fill the form: all there


def test_ac2_both_kinds_of_picture_become_the_same_kind_of_page() -> None:
    for fmt, mime in (("JPEG", "image/jpeg"), ("PNG", "image/png")):
        (page,) = pages.render_pages(_picture(RX_1, fmt), mime)
        with Image.open(io.BytesIO(page)) as im:
            assert im.format == "PNG" and im.mode == "RGB", fmt  # an image is used as it is


# ------------------------------------------------------------------ AC3
# GIVEN a blurred picture is dropped WHEN the quality check fails
# THEN the file goes to 'error' with the reason 'rescan' and is not retried


def test_ac3_the_blurred_picture_really_fails_the_quality_check() -> None:
    verdict = quality.assess(_blurred(RX_1), "image/png", settings=Settings(quality_check=True))
    assert verdict.ok is False and verdict.code == "blurred"
    assert quality.assess(_picture(RX_1, "PNG"), "image/png",
                          settings=Settings(quality_check=True)).ok  # fmt: skip


def test_ac3_it_goes_to_error_with_the_reason_rescan_and_is_not_retried(
    monkeypatch, folder
) -> None:
    Door(monkeypatch)
    (folder / "blurry.png").write_bytes(_blurred(RX_1))
    report = _process(folder)
    (failed,) = report["failed"]
    assert failed["file"] == "blurry.png" and failed["kind"] == "rescan"
    assert failed["reason"].startswith("rescan: image quality below threshold")
    assert failed["retry"] is False  # a data error
    assert _names(folder, "error") == ["blurry.png"] and report["succeeded"] == []


def test_ac3_the_note_says_rescan_why_and_that_it_will_not_be_tried_again(
    monkeypatch, folder
) -> None:
    Door(monkeypatch)
    (folder / "blurry.png").write_bytes(_blurred(RX_1))
    _process(folder)
    (note,) = _names(folder, "log")
    text = (folder / "log" / note).read_text(encoding="utf-8")
    assert "rescan: image quality below threshold" in text and "kind:    rescan" in text
    assert "blur" in text.lower()  # the particular reason is kept after it
    assert "data error" in text and "will not be tried again" in text and "job:" in text


def test_ac3_it_is_kept_as_refused_so_nothing_will_retry_it(monkeypatch, folder) -> None:
    Door(monkeypatch)
    (folder / "blurry.png").write_bytes(_blurred(RX_1))
    listener = _listener(folder)
    listener.poll()
    listener.poll()
    (row,) = listener.ledger._rows.values()  # type: ignore[attr-defined]
    assert row.state == "refused" and row.kind == "rescan"
    assert "rescan" in S.DATA_ERRORS
    for _ in range(3):  # and later looks at the folder do not touch it again
        assert listener.poll()["failed"] == []


def test_ac3_nothing_is_guessed_the_file_never_gets_past_the_quality_check() -> None:
    """In the runner the hold comes before the document is saved or read."""
    source = inspect.getsource(jobs)
    assert source.index("quality.assess(") < source.index("s.ingest(") < source.index("s.ocr(")


# ------------------------------------------------------------------ AC4
# GIVEN the same prescription is uploaded on the screen and dropped in the folder
# WHEN the two results are compared THEN the extracted values are identical


def test_ac4_the_folder_and_the_screen_hand_the_same_bytes_to_the_same_function(
    monkeypatch, folder
) -> None:
    door, raw = Door(monkeypatch), _picture(RX_1, "PNG")
    (folder / "rx.png").write_bytes(raw)
    _process(folder)
    (sent,) = door.submitted
    assert sent["files"] == [("rx.png", raw, "image/png")]  # the bytes as dropped, untouched
    assert sent["options"] == {"actor": "listener"}  # and no option the screen does not have
    web = (SRC / "webapp" / "app.py").read_text(encoding="utf-8")
    assert "jobstore.submit" in web  # the screen's upload goes through the same function


def test_ac4_the_only_thing_the_channel_changes_is_what_is_recorded_as_the_source() -> None:
    runner = inspect.getsource(jobs)
    uses = [ln.strip() for ln in runner.splitlines() if "job.channel" in ln or ".channel" in ln]
    assert uses == ['source = {} if job.channel == "upload" else {"source_channel": job.channel}']
    assert "res = s.ingest(raw, filename, mime, patient_id=job.patient_id, **source)" in runner
    assert jobstore.channel_of("listener") == "listener"
    assert jobstore.channel_of("dr.rao") == "upload" and jobstore.channel_of(None) == "upload"
    assert jobs.Job(id="j", docs=[]).channel == "upload"


def test_ac4_the_listener_has_no_reading_steps_of_its_own() -> None:
    """Nothing in the listener reads a picture: no OCR, no model, no extraction - so it cannot
    read one differently from the screen."""
    for name in ("service.py", "ledger.py", "connectors.py", "auth.py", "__main__.py"):
        source = (SRC / "listener" / name).read_text(encoding="utf-8")
        for step in ("ocr_document", "extract_document", "classify_document", "rapidocr",
                     "vlm_ocr", "cdi_adapter.mlserve", "get_client"):  # fmt: skip
            assert step not in source, (name, step)
    # LS-S10: it does knock on the model server's port before taking a file - and no more


@pytest.mark.xfail(strict=True, reason="LS-S4 AC4, 'the extracted values are identical': the "
                   "comparison itself - one file through the screen and through the folder, "
                   "then the two results side by side - needs PostgreSQL, the document store "
                   "and the model running. It has not been run.")  # fmt: skip
def test_ac4_the_two_results_have_been_compared_on_a_running_pipeline() -> None:
    assert (ROOT / "tests" / "test_listener_parity_integration.py").is_file()


# ------------------------------------------------------------------ AC5
# GIVEN a file called scan.pdf is not a real PDF WHEN it is opened
# THEN it goes to 'error' as a data error and is not retried

NOT_PDFS = {"a text file": b"this is my prescription, typed in notepad\n" * 20,
            "a PDF header and nothing else": b"%PDF-1.7\n" + b"\x00" * 200,
            "a Word document": b"PK\x03\x04" + b"\x00" * 200,
            "half a PDF": blank_pdf(2)[:150]}  # fmt: skip


@pytest.mark.parametrize("what", list(NOT_PDFS))
def test_ac5_a_scan_pdf_that_is_not_a_pdf_goes_to_error_as_a_data_error(
    what: str, monkeypatch, folder
) -> None:
    door = Door(monkeypatch)
    (folder / "scan.pdf").write_bytes(NOT_PDFS[what])
    report = _process(folder)
    (failed,) = report["failed"]
    assert failed["file"] == "scan.pdf" and failed["kind"] == "not_readable"
    assert "not a PDF that can be opened" in failed["reason"]
    assert failed["retry"] is False and door.submitted == []  # never reached the pipeline
    assert _names(folder, "error") == ["scan.pdf"]


def test_ac5_the_note_says_so_and_that_it_will_not_be_tried_again(monkeypatch, folder) -> None:
    Door(monkeypatch)
    (folder / "scan.pdf").write_bytes(NOT_PDFS["a text file"])
    listener = _listener(folder)
    listener.poll()
    listener.poll()
    (note,) = _names(folder, "log")
    text = (folder / "log" / note).read_text(encoding="utf-8")
    assert "not a PDF that can be opened" in text and "not_readable" in text
    assert "data error" in text and "will not be tried again" in text
    (row,) = listener.ledger._rows.values()  # type: ignore[attr-defined]
    assert row.state == "refused" and "not_readable" in S.DATA_ERRORS
    for _ in range(3):
        assert listener.poll()["failed"] == []  # not retried


def test_ac5_opening_it_raises_a_data_error_everywhere_not_only_in_the_listener() -> None:
    with pytest.raises(pages.NotReadable) as caught:
        pages.render_pages(NOT_PDFS["a text file"], "application/pdf")
    assert isinstance(caught.value, pages.DocumentRefused) and caught.value.kind == "not_readable"
    with pytest.raises(pdf.NotAPdf):
        pdf.renderer().page_count(NOT_PDFS["a Word document"])


def test_ac5_a_real_pdf_named_scan_pdf_is_read(monkeypatch, folder) -> None:
    door = Door(monkeypatch)
    (folder / "scan.pdf").write_bytes(make_pdf([RX_1], dpi=150))
    assert _process(folder)["succeeded"] == ["scan.pdf"] and len(door.submitted) == 1


# ------------------------------------------------------------------ the PDF renderer: pypdfium2


def test_the_pdf_renderer_is_pypdfium2() -> None:
    assert pdf.renderer().name == "pypdfium2" and isinstance(pdf.renderer(), pdf.PdfiumRenderer)


def test_page_count() -> None:
    for count in (1, 2, 7):
        assert pdf.renderer().page_count(blank_pdf(count)) == count
        assert len(pdf.renderer().render(blank_pdf(count), dpi=50)) == count


@pytest.mark.parametrize(("dpi", "size"), [(72, (595, 842)), (100, (827, 1170)),
                                           (200, (1653, 2339)), (300, (2480, 3509))])  # fmt: skip
def test_dpi(dpi: int, size: tuple[int, int]) -> None:
    """An A4 page (595 x 842 points) is ``points x dpi / 72`` pixels, rounded up to a whole
    pixel, and says so."""
    (png,) = pdf.renderer().render(blank_pdf(1, size=(595, 842)), dpi)
    with Image.open(io.BytesIO(png)) as im:
        assert im.size == size and im.mode == "RGB"
        assert round(im.info["dpi"][0]) == dpi and round(im.info["dpi"][1]) == dpi


def test_one_known_pdf() -> None:
    """A page with a black block in a known place comes back with the block in that place."""
    known = Image.new("L", (850, 1100), 255)  # US Letter at 100 dpi
    ImageDraw.Draw(known).rectangle([100, 200, 399, 499], fill=0)
    buf = io.BytesIO()
    known.convert("P").save(buf, format="PDF", resolution=100.0)  # stored without loss
    assert pdf.renderer().page_sizes(buf.getvalue()) == [(612.0, 792.0)]  # 8.5 x 11 inches
    (png,) = pdf.renderer().render(buf.getvalue(), dpi=200)
    got = np.array(Image.open(io.BytesIO(png)).convert("L"))
    assert got.shape == (2200, 1700)  # twice the size at twice the dpi
    assert got[402:998, 202:798].max() < 40  # the block, where it should be, black
    assert got[410:990, 210:790].mean() < 5
    outside = got.copy()
    outside[396:1004, 196:804] = 255
    assert outside.min() > 215  # and everything else is paper


def test_page_sizes_are_read_without_drawing_anything() -> None:
    assert pdf.renderer().page_sizes(blank_pdf(3, size=(300, 400))) == [(300.0, 400.0)] * 3


def test_a_pdf_is_read_as_pictures_never_from_its_text_layer() -> None:
    source = (SRC / "ingest" / "pdf.py").read_text(encoding="utf-8")
    for call in ("get_textpage", "get_text", "extract_text"):
        assert call not in source.split('"""', 2)[2], call


def test_rendering_from_several_threads_at_once_is_safe() -> None:
    from concurrent.futures import ThreadPoolExecutor

    raw = make_pdf([RX_1, RX_2], dpi=72)
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: pdf.renderer().render(raw, 72), range(18)))
    assert all(r == results[0] for r in results) and len(results[0]) == 2


def test_pymupdf_is_gone() -> None:
    """Licence (AGPL): there is no PyMuPDF option. Nothing imports it, nothing declares it."""
    for path in [*SRC.rglob("*.py"), *(ROOT / "scripts").glob("*.py"),
                 ROOT / "tests" / "helpers.py"]:  # fmt: skip
        text = path.read_text(encoding="utf-8")
        assert "import pymupdf" not in text and "import fitz" not in text, path.name
    project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"pymupdf' not in project and '"pypdfium2>=' in project


def test_the_pods_locked_requirements_have_been_updated() -> None:
    """Was an expected failure until the lock was updated by hand on 2026-10-09 (the Linux
    wheel's hash, taken from PyPI). The lock has still to be regenerated by its own script."""
    lock = (ROOT / "infra" / "runpod" / "requirements.lock").read_text(encoding="utf-8")
    assert "pypdfium2==" in lock and "pymupdf==" not in lock


def test_ingest_records_the_renderer_it_used() -> None:
    source = (SRC / "ingest" / "service.py").read_text(encoding="utf-8")
    # SW-S1: the name AND the version of what made the pages (the renderer for a PDF)
    assert "pymupdf" not in source and "pages.pdf.describe(mime)" in source
    assert "model_name=maker" in source and "model_version=maker_version" in source
    assert pdf.describe("application/pdf") == ("pypdfium2", pdf.renderer().version)


def test_handwriting_is_read_by_whatever_the_screen_uses_not_a_listener_copy() -> None:
    """The story lists TrOCR among the steps. TrOCR was built on 2026-10-09 (RD-S2) as a step
    of the shared pipeline - off until ``CDI_TWO_READERS`` is set - so the screen and the folder
    get it together, through the same door. The listener has no copy of its own."""
    found = sorted(f"{p.parent.name}/{p.name}" for p in SRC.rglob("*.py")
                   if "trocr" in p.read_text(encoding="utf-8").lower()
                   and p.parent.name in ("ocr", "recognition", "listener"))  # fmt: skip
    assert found and not [f for f in found if f.startswith("listener/")]
    assert "ocr/service.py" in found and "recognition/engines.py" in found
    assert Settings(_env_file=None).two_readers is False


# ------------------------------------------------------------------ AC1, AC2, AC4: the real runner
# A closer check (2026-10-08): the *real* job runner - the one the screen's uploads run on -
# with stand-in steps that record every call. The first step renders the pages for real. Still
# no database, document store or model: what is proven is which steps run, on what, in what
# order - not what the model reads.


class Steps:
    """The pipeline's steps as recorders. ``ingest`` really renders the file's pages."""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.pages: dict[str, int] = {}
        self.channels: dict[str, str] = {}
        self._lock = __import__("threading").Lock()

    def _log(self, *call: Any) -> None:
        with self._lock:
            self.calls.append(call)

    def stages(self) -> jobs.Stages:
        def ingest(raw: bytes, name: str, mime: str | None, patient_id: Any = None,
                   source_channel: str = "upload") -> Any:  # fmt: skip
            doc = f"doc-{name}"
            self.pages[doc] = len(pages.render_pages(raw, mime or pages.sniff_mime(raw, name)))
            self.channels[doc] = source_channel
            self._log("ingest", doc)
            return SimpleNamespace(document_id=doc)

        def extract(doc: str, patient_id: Any = None, expected_patient: Any = None) -> Any:
            self._log("extract", doc)
            return {"patient_id": patient_id or "patient-1", "facts": 3}

        return jobs.Stages(
            ingest=ingest, extract=extract,
            ocr=lambda doc, force_engine=None: self._log("ocr", doc, force_engine),
            classify=lambda doc: self._log("classify", doc) or {"doc_type": "prescription",
                                                               "is_handwritten": False},
            terminology=lambda doc: self._log("terminology", doc),
            validate=lambda doc: self._log("validate", doc),
            counts=lambda doc: {"facts": 3, "accepted": 2, "in_review": 1},
            merge_identity=lambda patient: self._log("merge", patient),
        )  # fmt: skip

    def of(self, doc: str) -> list[tuple[Any, ...]]:
        """The steps one document went through, in order, without its name."""
        return [(c[0], *c[2:]) for c in self.calls if len(c) > 1 and c[1] == doc]


def _run_on_the_real_runner(files: list[tuple[str, bytes, str | None]], channel: str,
                            steps: Steps | None = None) -> tuple[Any, Steps]:  # fmt: skip
    """Run one job to its end on the real runner, as the screen (``upload``) or the folder
    (``listener``) would have it run. The job is built the way ``jobstore.submit`` builds it."""
    steps = steps or Steps()
    runner = jobs.JobRunner(steps.stages(), max_workers=2)
    job = jobs.Job(id=f"job-{channel}-{len(steps.calls)}", channel=channel,
                   docs=[jobs.DocState(filename=f[0]) for f in files])  # fmt: skip
    runner._run(job, list(files))
    return job, steps


def test_ac1_on_the_runner_a_two_page_pdf_is_one_document_with_two_pages() -> None:
    raw = make_pdf([RX_1, RX_2], dpi=150)
    job, steps = _run_on_the_real_runner([("rx.pdf", raw, "application/pdf")], "listener")
    assert job.state == "review" and len(job.docs) == 1  # one document ...
    (doc,) = job.docs
    assert doc.status == "done" and doc.document_id == "doc-rx.pdf"
    assert steps.pages == {"doc-rx.pdf": 2}  # ... of two pages
    assert [c[0] for c in steps.calls].count("ingest") == 1  # saved once, not once per page


def test_ac1_on_the_runner_the_document_is_read_after_it_is_saved_whole() -> None:
    """The reading step is given the document - all its pages - not page one."""
    raw = make_pdf([RX_1, RX_2], dpi=150)
    job, steps = _run_on_the_real_runner([("rx.pdf", raw, "application/pdf")], "listener")
    assert steps.of("doc-rx.pdf") == [("ingest",), ("ocr", "rapidocr"), ("classify",),
                                      ("extract",), ("terminology",), ("validate",)]  # fmt: skip
    assert job.docs[0].steps_done[:3] == ["ingest", "ocr", "classify"]


def test_ac2_on_the_runner_a_jpeg_and_a_png_go_through_the_same_steps_in_the_same_order() -> None:
    files = [("photo.jpg", _picture(RX_1, "JPEG"), "image/jpeg"),
             ("scan.png", _picture(RX_2, "PNG"), "image/png")]  # fmt: skip
    steps = Steps()
    for one in files:  # the folder sends one file per job
        job, _ = _run_on_the_real_runner([one], "listener", steps)
        assert job.state == "review" and job.docs[0].status == "done"
    photo, scan = steps.of("doc-photo.jpg"), steps.of("doc-scan.png")
    assert photo == scan  # the same steps, the same order, the same settings
    assert [s[0] for s in photo] == ["ingest", "ocr", "classify", "extract", "terminology",
                                     "validate"]  # fmt: skip
    assert steps.pages == {"doc-photo.jpg": 1, "doc-scan.png": 1}
    assert set(steps.channels.values()) == {"listener"}  # and both recorded the same way


def test_ac4_on_the_runner_the_screen_and_the_folder_run_the_same_steps() -> None:
    raw = _picture(RX_1, "PNG")
    file = [("rx.png", raw, "image/png")]
    screen_job, screen = _run_on_the_real_runner(file, "upload")
    folder_job, folder = _run_on_the_real_runner(file, "listener")
    assert screen.of("doc-rx.png") == folder.of("doc-rx.png")  # step for step
    assert screen.pages == folder.pages
    assert (screen_job.state, screen_job.docs[0].status, screen_job.docs[0].doc_type) == (
        folder_job.state, folder_job.docs[0].status, folder_job.docs[0].doc_type)  # fmt: skip
    # the one difference: what is written down as where the file came from
    assert screen.channels == {"doc-rx.png": "upload"}
    assert folder.channels == {"doc-rx.png": "listener"}


def test_ac4_the_pages_handed_to_the_reading_steps_are_identical_pixel_for_pixel() -> None:
    """What the reading steps see is decided by the bytes alone: the same file gives the same
    page pictures however it arrived, so the same values can follow."""
    for raw, mime in ((make_pdf([RX_1, RX_2], dpi=150), "application/pdf"),
                      (_picture(RX_1, "JPEG"), "image/jpeg")):  # fmt: skip
        first, second = pages.render_pages(raw, mime), pages.render_pages(raw, mime)
        assert first == second
        assert [pages.normalize_image(p)[0] for p in first] == [
            pages.normalize_image(p)[0] for p in second]  # fmt: skip


def test_ac3_on_the_runner_the_blurred_picture_is_held_before_anything_is_saved_or_read() -> None:
    job, steps = _run_on_the_real_runner([("blurry.png", _blurred(RX_1), "image/png")], "listener")
    assert job.docs[0].status == "quality_hold" and steps.calls == []  # no step ran at all
    out = S.outcome_of(job)
    assert out.kind == "rescan" and (out.reason or "").startswith(S.RESCAN)
