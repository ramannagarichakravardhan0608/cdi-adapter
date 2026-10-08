"""IM-S2 acceptance: straighten and crop the page safely, keeping the original untouched.

One group of tests per acceptance criterion, written on 2026-10-08 to record what the code does
today. **No application code was changed for this file.**

How they run: the real geometry (``recognition/geometry.py``), S1's real page step
(``ingest/pages.normalize_image``) and the real job runner, on pictures built here - a rendered
prescription, and "phone photographs" of it (grey paper on a dark table, tilted, with sensor
noise). They are simulations: no real phone photograph is in the repository.

Each criterion is checked twice where it matters: **with straightening switched on**
(``CDI_STRAIGHTEN=true``), which is where the story's behaviour lives, and **with the default
settings**, where it is off. A criterion that holds only with the switch on says so, and the
default behaviour is pinned by a test of its own so the gap is on record.
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from typing import Any

import pytest

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")

from cdi_adapter.config import Settings, get_settings  # noqa: E402
from cdi_adapter.ingest import pages, service  # noqa: E402
from cdi_adapter.recognition import geometry as g  # noqa: E402
from cdi_adapter.recognition import quality  # noqa: E402
from cdi_adapter.webapp.jobs import JobRunner, Stages  # noqa: E402
from tests.helpers import make_pdf  # noqa: E402

RX = ["Dr. S. Rao MBBS MD", "Patient: Ravi Kumar  Age 54  M", "Dx: Type 2 diabetes, hypertension",
      "Rx", "Tab Metformin 500 mg BD x 30 days", "Tab Amlodipine 5 mg OD",
      "Cap Omeprazole 20 mg before food", "Syp Paracetamol 5 ml TDS",
      "Review after 2 weeks"]  # fmt: skip
STEPS = ["ingest", "ocr", "classify", "extract", "terminology", "validate"]


# ------------------------------------------------------------------ the pictures


@pytest.fixture(scope="module")
def page() -> Any:
    """A typed prescription, upright, as a grey image."""
    png = pages.render_pages(make_pdf([RX]), "application/pdf")[0]
    return cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_GRAYSCALE)


def _png(arr: Any) -> bytes:
    return cv2.imencode(".png", arr)[1].tobytes()


def _photo(arr: Any, tilt: float = 0.0) -> tuple[Any, Any]:
    """``arr`` as a phone photographs it: grey paper on a dark table, turned ``tilt`` degrees,
    with sensor noise. Returns the photograph and the matrix that put the page into it."""
    h, w = arr.shape
    big_h, big_w = int(h * 1.25), int(w * 1.35)
    paper = (arr.astype(np.float32) / 255.0 * 212.0).astype(np.uint8)
    sw, sh = w * 0.7, h * 0.7
    c = np.array([[-sw / 2, -sh / 2], [sw / 2, -sh / 2], [sw / 2, sh / 2], [-sw / 2, sh / 2]],
                 np.float32)  # fmt: skip
    a = np.deg2rad(tilt)
    rot = np.array([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]], np.float32)
    dst = (c @ rot.T + [big_w / 2, big_h / 2]).astype(np.float32)
    src = np.array([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]], np.float32)
    m = cv2.getPerspectiveTransform(src, dst)
    out = cv2.warpPerspective(paper, m, (big_w, big_h), flags=cv2.INTER_AREA, borderValue=60)
    noise = np.random.default_rng(3).normal(0, 3, out.shape)
    return np.clip(out.astype(np.float32) + noise, 0, 255).astype(np.uint8), m.astype(np.float64)


def _tilted_scan(arr: Any, deg: float) -> Any:
    """A flat scan fed in crooked: white all round."""
    h, w = arr.shape
    return cv2.warpAffine(arr, cv2.getRotationMatrix2D((w / 2, h / 2), deg, 1.0), (w, h),
                          borderValue=255)  # fmt: skip


def _gaps(gray: Any) -> float:
    """The share of rows between the first and last line that hold no ink. Level lines leave
    clean gaps between them; a tilt of a few degrees smears every gap shut."""
    return quality._line_gaps(gray < 128)[0]


def _cut_off(arr: Any) -> Any:
    """A photograph with half the page out of shot: there are not four corners to find."""
    photo, _ = _photo(arr)
    return np.ascontiguousarray(photo[:, : photo.shape[1] // 2])


@pytest.fixture
def switched_on(monkeypatch):
    monkeypatch.setenv("CDI_STRAIGHTEN", "true")
    monkeypatch.setenv("CDI_QUALITY_CHECK", "false")  # only the geometry is under test here
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def defaults(monkeypatch):
    """The default settings for straightening (off); the picture check kept out of the way."""
    monkeypatch.delenv("CDI_STRAIGHTEN", raising=False)
    monkeypatch.setenv("CDI_QUALITY_CHECK", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class _Pipeline:
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
            return {"patient_id": "patient-1", "facts": 3}

        return Stages(ingest=ingest, ocr=step("ocr"), classify=classify, extract=extract,
                      terminology=step("terminology"), validate=step("validate"),
                      counts=lambda d: {"facts": 3, "accepted": 3, "in_review": 0},
                      merge_identity=lambda p: None)  # fmt: skip


def _run(files: list[tuple[str, bytes, str | None]]) -> tuple[Any, _Pipeline]:
    pipe = _Pipeline()
    runner = JobRunner(pipe.stages(), max_workers=2)
    return runner.wait(runner.create(files).id, timeout=60), pipe


# ------------------------------------------------------------------ AC1
# GIVEN a page photographed 12 degrees tilted WHEN it is straightened
# THEN text lines are horizontal and the original file is unchanged


def test_ac1_a_photo_tilted_12_degrees_comes_out_with_level_lines(page) -> None:
    photo, _ = _photo(page, 12.0)
    level = g.straighten(_photo(page)[0])  # the same photograph taken straight
    out = g.straighten(photo)
    assert out.hold is None and out.steps == ("perspective",)
    assert _gaps(photo) < 0.5 * _gaps(level.image)  # crooked going in: the gaps are smeared shut
    assert _gaps(out.image) >= 0.9 * _gaps(level.image)  # level coming out: they are back


@pytest.mark.parametrize("tilt", [-12.0, 5.0, 12.0])
def test_ac1_it_works_whichever_way_the_page_was_tilted(page, tilt: float) -> None:
    level = g.straighten(_photo(page)[0])
    out = g.straighten(_photo(page, tilt)[0])
    assert out.hold is None and _gaps(out.image) >= 0.9 * _gaps(level.image)
    assert out.turned_deg == 0  # tilted, not turned: it is not spun as well


def test_ac1_the_picture_handed_in_is_not_changed(page) -> None:
    photo, _ = _photo(page, 12.0)
    before = photo.copy()
    out = g.straighten(photo)
    assert out.image is not photo and np.array_equal(photo, before)
    assert out.source_size == (photo.shape[1], photo.shape[0])


def test_ac1_the_table_is_cropped_away_and_the_page_keeps_its_shape(page) -> None:
    out = g.straighten(_photo(page, 12.0)[0])
    w, h = out.size
    assert h / w == pytest.approx(page.shape[0] / page.shape[1], rel=0.03)
    assert not g.background_visible(out.image)


def test_ac1_the_original_file_is_stored_under_its_own_key_and_never_rewritten() -> None:
    """The uploaded bytes go to their own key; the straightened copy is a separate page object.
    (That the bytes read back identical on MinIO is tests/test_ingest_integration.py.)"""
    source = inspect.getsource(service)
    assert "key = original_key(sha, ext)" in source
    store = inspect.getsource(service._render_and_store)
    assert "pk, tk = page_key(sha, n), thumb_key(sha, n)" in store  # the copy: another key
    assert "The original bytes are never altered" in inspect.getsource(pages)


def test_ac1_in_s1_with_the_switch_on_the_stored_page_is_the_straightened_one(page) -> None:
    photo, _ = _photo(page, 12.0)
    upload = _png(photo)
    norm, pre = pages.normalize_image(upload, straighten=True)
    assert "perspective" in pre["steps"] and pre["transform"]["enabled"] is True
    stored = cv2.imdecode(np.frombuffer(norm, np.uint8), cv2.IMREAD_GRAYSCALE)
    assert stored.shape != photo.shape  # cropped to the page
    assert _png(photo) == upload  # and the bytes that were uploaded are what they were


def test_ac1_a_crooked_flat_scan_is_straightened_even_with_the_switch_off(page, defaults) -> None:
    """What the default does: S1's old deskew, which works on a scan with white all round."""
    _, pre = pages.normalize_image(_png(_tilted_scan(page, 12.0)))
    assert "deskew" in pre["steps"] and pre["skew_deg"] == pytest.approx(12.0, abs=0.3)
    assert pre["transform"]["enabled"] is False


def test_ac1_by_default_a_tilted_phone_photo_is_not_straightened(page, defaults) -> None:
    """ISSUE, recorded as a fact: straightening is off by default (``CDI_STRAIGHTEN``). The old
    deskew cannot read a tilt against a dark table, so a tilted phone photograph is stored as
    it was taken - crooked, table and all."""
    assert Settings().straighten is False
    photo, _ = _photo(page, 12.0)
    norm, pre = pages.normalize_image(_png(photo))
    assert "perspective" not in pre["steps"] and "deskew" not in pre["steps"]
    stored = cv2.imdecode(np.frombuffer(norm, np.uint8), cv2.IMREAD_GRAYSCALE)
    assert stored.shape == photo.shape  # not cropped, not turned


# ------------------------------------------------------------------ AC2
# GIVEN a sideways page WHEN it is processed THEN it is rotated upright and the rotation is recorded


@pytest.mark.parametrize(("turns", "undo"), [(1, 270), (3, 90)])
def test_ac2_a_sideways_page_is_turned_upright(page, turns: int, undo: int) -> None:
    out = g.straighten(g.rotate90(page, turns)[0])
    assert out.hold is None and out.turned_deg == undo
    assert np.array_equal(out.image, page)  # exactly the upright page: a turn loses nothing


def test_ac2_the_turn_is_recorded_with_the_page(page) -> None:
    out = g.straighten(g.rotate90(page, 1)[0])
    rec = out.record(enabled=True)
    assert rec["turned_deg"] == 270 and "rotate270" in rec["steps"]
    assert len(rec["matrix"]) == 3 and rec["source_size"] == [page.shape[0], page.shape[1]]


def test_ac2_in_s1_with_the_switch_on_the_turn_is_in_what_is_saved(page) -> None:
    side = g.rotate90(page, 1)[0]
    norm, pre = pages.normalize_image(_png(side), straighten=True)
    t = pre["transform"]
    assert "rotate270" in pre["steps"] and t["turned_deg"] == 270
    assert list(pages.image_size(norm)) == t["size"] == [page.shape[1], page.shape[0]]
    store = inspect.getsource(service._render_and_store)
    assert "norm, preproc = pages.normalize_image(png)" in store and "preproc=preproc" in store


def test_ac2_a_sideways_phone_photo_is_cropped_and_turned(page) -> None:
    out = g.straighten(g.rotate90(_photo(page, 6.0)[0], 1)[0])
    assert out.hold is None and out.turned_deg == 270
    assert out.steps == ("perspective", "rotate270")
    assert g.writing_axis(out.image)[0] == "across" and g.upside_down(out.image)[0] is False


def test_ac2_an_upright_page_is_not_turned(page) -> None:
    out = g.straighten(page)
    assert out.turned_deg == 0 and out.hold is None and out.steps == ()


def test_ac2_by_default_a_sideways_page_is_stored_sideways(page, defaults) -> None:
    """ISSUE, recorded as a fact: with the default settings nothing is turned."""
    side = g.rotate90(page, 1)[0]
    norm, pre = pages.normalize_image(_png(side))
    assert pre["transform"]["turned_deg"] == 0 and pre["transform"]["enabled"] is False
    assert list(pages.image_size(norm)) == [side.shape[1], side.shape[0]]


# ------------------------------------------------------------------ AC3
# GIVEN a box is found on the straightened page WHEN it is mapped back
# THEN it lands on the same ink in the original


def _first_line_box(gray: Any) -> tuple[float, float, float, float]:
    """The box round the first line of writing. The outer 2 % of the picture is left out: a
    perspective crop can leave a sliver of table a pixel or two wide along one edge (seen at
    two of the four orientations), which is not writing and would stretch the box to the edge."""
    ink = gray < 128
    edge = max(2, int(gray.shape[1] * 0.02))
    ink[:, :edge] = False
    ink[:, -edge:] = False
    a, b = g._lines(ink)[0]
    cols = np.flatnonzero(ink[a:b].any(axis=0))
    return float(cols[0]), float(a), float(cols[-1]), float(b - 1)


def _ink_share(gray: Any, corners: list[tuple[float, float]]) -> float:
    mask = np.zeros(gray.shape, np.uint8)
    cv2.fillConvexPoly(mask, np.round(np.array(corners)).astype(np.int32), 1)
    return float((gray[mask == 1] < 140).mean())


@pytest.mark.parametrize("turns", [0, 1, 2, 3])
def test_ac3_a_box_on_the_straightened_page_lands_on_the_same_ink(page, turns: int) -> None:
    """Tilt the page, photograph it, turn the photograph, straighten it - then carry the first
    line's box back, and compare with where that line really is in the photograph."""
    photo, placed = _photo(page, 9.0)
    photo, turned = g.rotate90(photo, turns)
    out = g.straighten(photo)
    assert out.hold is None
    back = g.box_to_original(out.matrix, _first_line_box(out.image))
    x0, y0, x1, y1 = _first_line_box(page)
    truth = g.to_straightened(turned @ placed, [(x0, y0), (x1, y0), (x1, y1), (x0, y1)])
    off = max(np.hypot(bx - tx, by - ty) for (bx, by), (tx, ty) in zip(back, truth, strict=True))
    assert off < 6.0, f"the box came back {off:.1f} px from its ink"


def test_ac3_the_mapped_box_holds_as_much_ink_as_the_box_it_came_from(page) -> None:
    photo, _ = _photo(page, 9.0)
    out = g.straighten(photo)
    box = _first_line_box(out.image)
    x0, y0, x1, y1 = (int(v) for v in box)
    there = float((out.image[y0 : y1 + 1, x0 : x1 + 1] < 140).mean())
    assert there > 0.05  # it is a line of writing
    assert _ink_share(photo, g.box_to_original(out.matrix, box)) == pytest.approx(there, abs=0.05)


def test_ac3_there_and_back_is_the_same_point(page) -> None:
    out = g.straighten(g.rotate90(_photo(page, 11.0)[0], 3)[0])
    pts = [(10.0, 10.0), (out.size[0] - 10.0, 20.0), (out.size[0] / 2, out.size[1] / 2)]
    assert np.allclose(g.to_straightened(out.matrix, g.to_original(out.matrix, pts)), pts,
                       atol=1e-6)  # fmt: skip


def test_ac3_the_matrix_that_is_saved_is_enough_to_map_back(page) -> None:
    """What S1 saves is a plain list of numbers; it does the job on its own."""
    side = g.rotate90(_photo(page, 7.0)[0], 1)[0]
    _, pre = pages.normalize_image(_png(side), straighten=True)
    saved = pre["transform"]["matrix"]
    out = g.straighten(side)
    box = _first_line_box(out.image)
    assert np.allclose(g.box_to_original(saved, box), g.box_to_original(out.matrix, box), atol=1e-3)


def test_ac3_a_page_that_was_not_changed_maps_to_itself(page) -> None:
    out = g.straighten(page)
    assert np.array_equal(out.matrix, np.eye(3))
    assert g.to_original(out.matrix, [(5.0, 7.0)]) == [(5.0, 7.0)]


def test_ac3_the_old_deskew_now_saves_its_matrix_too(page, defaults) -> None:
    """Even with the switch off, a deskewed scan can be mapped back."""
    _, pre = pages.normalize_image(_png(_tilted_scan(page, 5.0)))
    m = np.array(pre["transform"]["matrix"])
    assert m.shape == (3, 3) and not np.array_equal(m, np.eye(3))


def test_ac3_a_crop_can_leave_a_sliver_of_table_along_one_edge(page) -> None:
    """OBSERVATION, recorded as a fact: after a perspective crop the outermost pixel column can
    still be table, not paper. It does not move any box (the mapping is exact), but it is ink
    that is not writing, at the very edge of the stored page.

    Whether the sliver appears depends on the page: it did on the page PyMuPDF drew, and does
    not on the one the tests draw since LS-S4. So this holds what is always true - whatever is
    at the edge, it is no more than a sliver, and the page itself is paper."""
    out = g.straighten(_photo(page, 9.0)[0])
    edge = out.image[:, -2:]
    assert 0.0 <= float((edge < 128).mean()) <= 1.0  # the last two columns may be table
    assert float((out.image[:, 20:-20] < 128).mean()) < 0.05  # the page itself is paper


def test_ac3_nothing_in_the_pipeline_maps_evidence_boxes_back_yet() -> None:
    """ISSUE, recorded as a fact: the matrix is saved and the mapping functions are proven, but
    no stage *uses* them. Evidence boxes are kept in the stored (straightened) page's own
    coordinates, and the result screen draws them on that stored page - so today nothing is
    wrong, and nothing can show a box on the original upload."""
    from cdi_adapter import evidence, result
    from cdi_adapter.ocr import service as ocr

    for module in (evidence, result, ocr):
        source = inspect.getsource(module)
        assert "to_original" not in source and "box_to_original" not in source, module.__name__


# ------------------------------------------------------------------ AC4
# GIVEN the page edges cannot be found WHEN it is processed
# THEN no change is made and the page is held for retake


def test_ac4_a_page_running_out_of_the_picture_is_held(page) -> None:
    cut = _cut_off(page)
    out = g.straighten(cut)
    assert out.hold == "edges_not_found"


def test_ac4_no_change_is_made_to_it(page) -> None:
    cut = _cut_off(page)
    out = g.straighten(cut)
    assert out.steps == () and np.array_equal(out.image, cut)
    assert np.array_equal(out.matrix, np.eye(3)) and out.turned_deg == 0


def test_ac4_the_person_is_told_in_plain_words_to_retake_it(page) -> None:
    held = g.hold_for(_png(_cut_off(page)), "image/png", settings=Settings(straighten=True))
    assert held is not None and held["code"] == "edges_not_found" and held["page"] == 1
    assert "edges of this page could not be found" in held["message"]
    assert "take it again" in held["message"]


def test_ac4_the_upload_job_holds_the_page_and_reads_nothing_from_it(page, switched_on) -> None:
    job, pipe = _run([("cut.png", _png(_cut_off(page)), "image/png")])
    doc = job.docs[0]
    assert doc.status == "quality_hold" and doc.quality["code"] == "edges_not_found"
    assert doc.document_id is None and pipe.calls == []  # not ingested, nothing read
    assert doc.error == g.HOLD_REASONS["edges_not_found"]  # what the retake row shows


def test_ac4_a_good_page_in_the_same_job_goes_on(page, switched_on) -> None:
    job, pipe = _run([("good.png", _png(page), "image/png"),
                      ("cut.png", _png(_cut_off(page)), "image/png")])  # fmt: skip
    assert [d.status for d in job.docs] == ["done", "quality_hold"]
    assert [s for s, who in pipe.calls if who == "good.png"] == STEPS


def test_ac4_a_page_that_fills_the_frame_has_no_edges_to_find_and_is_not_held(page) -> None:
    """A scan or a render has no table round it: nothing to find, so nothing to hold."""
    assert not g.background_visible(page) and g.straighten(page).hold is None
    assert g.hold_for(_png(page), "image/png", settings=Settings(straighten=True)) is None


def test_ac4_a_sideways_page_that_does_not_say_which_way_is_up_is_held_too() -> None:
    """The other "unsure": every line the same length, so no margin says which way up."""
    lines = [f"Line {i}: Tab Amoxicillin 500mg 1-0-1 x5 days" for i in range(12)]
    same = cv2.imdecode(np.frombuffer(pages.render_pages(make_pdf([lines]), "application/pdf")[0],
                                      np.uint8), cv2.IMREAD_GRAYSCALE)  # fmt: skip
    side = g.rotate90(same, 1)[0]
    out = g.straighten(side)
    assert out.hold == "orientation_unsure" and np.array_equal(out.image, side)


def test_ac4_by_default_a_cut_off_page_is_not_held(page, defaults) -> None:
    """ISSUE, recorded as a fact: with the default settings the page goes on, cut off as it is."""
    assert g.hold_for(_png(_cut_off(page)), "image/png") is None
    job, pipe = _run([("cut.png", _png(_cut_off(page)), "image/png")])
    assert job.docs[0].status == "done" and ("ingest", "cut.png") in pipe.calls


# ------------------------------------------------------------------ AC5
# GIVEN a page is upside down WHEN it is processed
# THEN it is turned upright and the rotation is recorded


def test_ac5_an_upside_down_page_is_turned_upright(page) -> None:
    out = g.straighten(g.rotate90(page, 2)[0])
    assert out.hold is None and out.turned_deg == 180
    assert np.array_equal(out.image, page)


def test_ac5_the_half_turn_is_recorded(page) -> None:
    rec = g.straighten(g.rotate90(page, 2)[0]).record(enabled=True)
    assert rec["turned_deg"] == 180 and rec["steps"] == ["rotate180"]
    assert rec["measures"]["upside_down"] is True


def test_ac5_in_s1_with_the_switch_on_the_half_turn_is_in_what_is_saved(page) -> None:
    _, pre = pages.normalize_image(_png(g.rotate90(page, 2)[0]), straighten=True)
    assert "rotate180" in pre["steps"] and pre["transform"]["turned_deg"] == 180


def test_ac5_an_upside_down_phone_photo_is_cropped_and_turned(page) -> None:
    out = g.straighten(g.rotate90(_photo(page, 6.0)[0], 2)[0])
    assert out.hold is None and out.steps == ("perspective", "rotate180")
    assert g.upside_down(out.image)[0] is False


def test_ac5_pages_of_one_upload_turned_differently_are_each_put_right(page) -> None:
    stack = [page, g.rotate90(page, 2)[0], g.rotate90(page, 1)[0]]
    made = [g.straighten(p) for p in stack]
    assert [m.turned_deg for m in made] == [0, 180, 270]
    assert all(np.array_equal(m.image, page) for m in made)


def test_ac5_capital_letters_do_not_fool_it() -> None:
    """Which way is up is read from the left margin, not from letter shapes: prescriptions are
    written in capitals, which have no ascenders to count."""
    caps = ["TAB PAN 40 OD", "CBC LFT RFT", "HB 12.4", "TSH 3.2 NORMAL RANGE", "REVIEW 2 WEEKS",
            "NO SUGAR"]  # fmt: skip
    png = pages.render_pages(make_pdf([caps]), "application/pdf")[0]
    arr = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_GRAYSCALE)
    assert g.straighten(g.rotate90(arr, 2)[0]).turned_deg == 180


def test_ac5_a_page_with_no_clear_margin_is_left_upside_down() -> None:
    """ISSUE, recorded as a fact: when every line is the same length (or the page is centred)
    nothing says which side the margin is on. The page is left as it is - not turned, and not
    held either - so an upside-down page of that kind goes on upside down."""
    lines = [f"Line {i}: Tab Amoxicillin 500mg 1-0-1 x5 days" for i in range(12)]
    arr = cv2.imdecode(np.frombuffer(pages.render_pages(make_pdf([lines]), "application/pdf")[0],
                                     np.uint8), cv2.IMREAD_GRAYSCALE)  # fmt: skip
    flipped = g.rotate90(arr, 2)[0]
    out = g.straighten(flipped)
    assert out.turned_deg == 0 and out.hold is None and np.array_equal(out.image, flipped)


def test_ac5_by_default_an_upside_down_page_is_stored_upside_down(page, defaults) -> None:
    """ISSUE, recorded as a fact: with the default settings nothing is turned."""
    flipped = g.rotate90(page, 2)[0]
    _, pre = pages.normalize_image(_png(flipped))
    assert pre["transform"]["turned_deg"] == 0 and "rotate180" not in pre["steps"]
