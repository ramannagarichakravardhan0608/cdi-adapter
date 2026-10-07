"""IM-S2: straighten and crop the page safely, keeping the original untouched.

The acceptance, one test group each:

* AC1 - a page photographed 12 degrees tilted comes out with level lines, original unchanged;
* AC2 - a sideways page is turned upright and the turn is recorded;
* AC3 - a box found on the straightened page maps back onto the same ink in the original;
* AC4 - when the page edges cannot be found nothing is changed and the page is held;
* AC5 - an upside-down page is turned upright and the turn is recorded.

The pictures are built here: rendered prescriptions, the repo's three sample scans, and "phone
photographs" of both - grey paper on a dark table, tilted, at an angle, with sensor noise. They
are simulations; the five real phone photographs the story's PROVE IT asks for need the
answer-key set (ENT-S3), and that is why everything new here is off by default.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from cdi_adapter.config import Settings

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")

from cdi_adapter.ingest import pages  # noqa: E402
from cdi_adapter.recognition import geometry as g  # noqa: E402
from tests.helpers import make_pdf  # noqa: E402

SAMPLES = sorted((Path(__file__).resolve().parents[1] / "data" / "samples").glob("*.scan.png"))
RX = ["Dr. S. Rao MBBS MD", "Patient: Ravi Kumar  Age 54  M", "Dx: Type 2 diabetes, hypertension",
      "Rx", "Tab Metformin 500 mg BD x 30 days", "Tab Amlodipine 5 mg OD",
      "Cap Omeprazole 20 mg before food", "Syp Paracetamol 5 ml TDS",
      "Review after 2 weeks"]  # fmt: skip


# ------------------------------------------------------------------ the pictures


def _render(lines: list[str]) -> Any:
    png = pages.render_pages(make_pdf([lines]), "application/pdf")[0]
    return cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_GRAYSCALE)


@pytest.fixture(scope="module")
def rx() -> Any:
    """A typed prescription: lines of different lengths from one left margin."""
    return _render(RX)


@pytest.fixture(scope="module", params=["rendered", *[p.name for p in SAMPLES]])
def page(request, rx) -> Any:
    """Every page the story is proven on: the rendered prescription and each sample scan."""
    if request.param == "rendered":
        return rx
    return cv2.imread(str(next(p for p in SAMPLES if p.name == request.param)),
                      cv2.IMREAD_GRAYSCALE)  # fmt: skip


def _photo(arr: Any, tilt: float = 0.0, lean: float = 0.0) -> tuple[Any, Any]:
    """``arr`` as a phone would photograph it: grey paper lying on a dark table, turned ``tilt``
    degrees, the far edge narrowed by ``lean`` (the camera's angle), with sensor noise. Returns
    the photograph and the matrix that put the page into it."""
    h, w = arr.shape
    big_h, big_w = int(h * 1.25), int(w * 1.35)
    paper = (arr.astype(np.float32) / 255.0 * 212.0).astype(np.uint8)
    sw, sh = w * 0.7, h * 0.7
    c = np.array([[-sw / 2, -sh / 2], [sw / 2, -sh / 2], [sw / 2, sh / 2], [-sw / 2, sh / 2]],
                 np.float32)  # fmt: skip
    c[0, 0] += lean * sw
    c[1, 0] -= lean * sw
    a = np.deg2rad(tilt)
    rot = np.array([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]], np.float32)
    dst = (c @ rot.T + [big_w / 2, big_h / 2]).astype(np.float32)
    src = np.array([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]], np.float32)
    m = cv2.getPerspectiveTransform(src, dst)
    out = cv2.warpPerspective(paper, m, (big_w, big_h), flags=cv2.INTER_AREA, borderValue=60)
    noise = np.random.default_rng(3).normal(0, 3, out.shape)
    return np.clip(out.astype(np.float32) + noise, 0, 255).astype(np.uint8), m.astype(np.float64)


def _tilted(arr: Any, deg: float) -> Any:
    """A flat scan fed in crooked: white all round, the page turned ``deg`` degrees."""
    h, w = arr.shape
    m = cv2.getRotationMatrix2D((w / 2, h / 2), deg, 1.0)
    return cv2.warpAffine(arr, m, (w, h), borderValue=255)


def _level(gray: Any) -> float:
    """How far from level a flat scan's lines are, in degrees."""
    return abs(g.estimate_skew(gray))


def _gaps(gray: Any) -> float:
    """The share of rows between the first and last line that hold no ink at all. Level lines
    leave clean gaps between them; a tilt of a few degrees smears every gap shut."""
    from cdi_adapter.recognition import quality

    return quality._line_gaps(gray < 128)[0]


def _png(arr: Any) -> bytes:
    return cv2.imencode(".png", arr)[1].tobytes()


# ------------------------------------------------------------------ AC1: a tilted photo


@pytest.mark.parametrize("tilt", [12.0, -12.0, 5.0, 25.0])
def test_a_tilted_photo_comes_out_with_level_lines(page, tilt):
    photo, _ = _photo(page, tilt)
    square = g.straighten(_photo(page)[0])  # the same photograph taken straight
    out = g.straighten(photo)
    assert out.hold is None and out.steps == ("perspective",)
    assert _gaps(photo) < 0.5 * _gaps(square.image)  # crooked going in: the gaps are smeared
    assert _gaps(out.image) >= 0.9 * _gaps(square.image)  # level coming out: they are back
    assert out.turned_deg == 0  # tilted, not turned: it must not be spun as well


def test_a_cropped_page_is_not_deskewed_as_well(rx):
    """Measured: on a flattened photograph the ink-rectangle estimate reads a tilt that is not
    there and would turn level lines crooked. The paper's edges have already set the level."""
    out = g.straighten(_photo(rx, 9.0, lean=0.05)[0])
    assert out.steps == ("perspective",) and out.skew_deg == 0.0


def test_the_original_is_not_changed_by_straightening(rx):
    photo, _ = _photo(rx, 12.0)
    before = photo.copy()
    out = g.straighten(photo)
    assert out.image is not photo and np.array_equal(photo, before)
    assert out.source_size == (photo.shape[1], photo.shape[0])


@pytest.mark.parametrize("deg", [3.0, 12.0, -7.0])
def test_a_crooked_scan_is_deskewed_and_the_original_array_left_alone(rx, deg):
    scan = _tilted(rx, deg)
    before = scan.copy()
    out = g.straighten(scan)
    assert out.steps == ("deskew",) and out.skew_deg == pytest.approx(deg, abs=0.3)
    assert _level(out.image) < 0.3 and np.array_equal(scan, before)


def test_a_photo_taken_at_an_angle_is_flattened_to_the_page(rx):
    photo, _ = _photo(rx, 4.0, lean=0.08)
    out = g.straighten(photo)
    assert out.steps[0] == "perspective" and out.quad is not None
    w, h = out.size
    assert h / w == pytest.approx(rx.shape[0] / rx.shape[1], rel=0.03)  # a page again, not a wedge
    assert not g.background_visible(out.image)  # the table is cropped away


# ------------------------------------------------------------------ AC2 / AC5: turned pages


@pytest.mark.parametrize(("turns", "undo"), [(1, 270), (2, 180), (3, 90)])
def test_a_turned_page_is_made_upright_and_the_turn_recorded(page, turns, undo):
    turned, _ = g.rotate90(page, turns)
    out = g.straighten(turned)
    assert out.hold is None and out.turned_deg == undo
    assert f"rotate{undo}" in out.steps
    assert np.array_equal(out.image, page)  # exactly the upright page: a turn loses nothing
    assert out.record(enabled=True)["turned_deg"] == undo


@pytest.mark.parametrize("turns", [1, 2, 3])
def test_a_turned_photo_is_made_upright(page, turns):
    photo, _ = _photo(page, 6.0)
    out = g.straighten(g.rotate90(photo, turns)[0])
    assert out.hold is None and out.turned_deg == (360 - 90 * turns) % 360
    assert g.upside_down(out.image)[0] is False and g.writing_axis(out.image)[0] == "across"


def test_an_upright_page_is_not_turned(page):
    out = g.straighten(page)
    assert out.turned_deg == 0 and out.hold is None
    assert not [s for s in out.steps if s.startswith("rotate")]


def test_pages_of_one_prescription_turned_differently_are_each_put_right(rx):
    """v3: one upload, three pages, each lying a different way."""
    stack = [rx, g.rotate90(rx, 2)[0], g.rotate90(rx, 1)[0]]
    made = [g.straighten(p) for p in stack]
    assert [m.turned_deg for m in made] == [0, 180, 270]
    assert all(np.array_equal(m.image, rx) for m in made)


def test_the_quarter_turn_matrix_is_exact():
    img = np.arange(12, dtype=np.uint8).reshape(3, 4)
    for turns in (0, 1, 2, 3):
        out, m = g.rotate90(img, turns)
        for y in range(3):
            for x in range(4):
                ((nx, ny),) = g.to_straightened(m, [(x, y)])
                assert out[round(ny), round(nx)] == img[y, x], (turns, x, y)


# ------------------------------------------------------------------ which way is up


def test_the_margin_says_which_way_is_up(page):
    assert g.upside_down(page)[0] is False
    assert g.upside_down(g.rotate90(page, 2)[0])[0] is True


def test_capital_letters_do_not_fool_the_upside_down_check():
    """The reason the margin is used and letter shapes are not: capitals have no ascenders to
    count, and prescriptions are written in capitals."""
    caps = _render(["TAB PAN 40 OD", "CBC LFT RFT", "HB 12.4", "TSH 3.2 NORMAL RANGE",
                    "REVIEW 2 WEEKS", "NO SUGAR"])  # fmt: skip
    assert g.upside_down(caps)[0] is False
    assert g.straighten(g.rotate90(caps, 2)[0]).turned_deg == 180


def test_a_page_with_no_clear_margin_is_left_alone():
    """Every line the same length: nothing says which side the margin is on. Not turned, and -
    being level - not held either: a readable page must not be rejected on a guess."""
    same = _render([f"Line {i}: Tab Amoxicillin 500mg 1-0-1 x5 days" for i in range(12)])
    flipped = g.rotate90(same, 2)[0]
    assert g.upside_down(flipped)[0] is None
    out = g.straighten(flipped)
    assert out.steps == () and out.hold is None and np.array_equal(out.image, flipped)


def test_too_few_lines_is_not_an_answer(rx):
    two = _render(["Tab Metformin 500 mg", "OD"])
    assert g.upside_down(two)[0] is None


def test_word_shapes_say_which_way_the_writing_runs(page):
    assert g.writing_axis(page)[0] == "across"
    assert g.writing_axis(g.rotate90(page, 1)[0])[0] == "down"
    assert g.writing_axis(_tilted(page, 8.0))[0] == "across"  # a tilt is not a turn


def test_a_blank_page_says_nothing_and_is_left_alone():
    blank = np.full((1200, 900), 255, np.uint8)
    assert g.writing_axis(blank)[0] is None
    out = g.straighten(blank)
    assert out.steps == () and out.hold is None and np.array_equal(out.matrix, np.eye(3))


# ------------------------------------------------------------------ AC3: the way back


def _ink_box(gray: Any) -> tuple[float, float, float, float]:
    """The box round the first line of writing on a page."""
    ink = gray < 128
    a, b = g._lines(ink)[0]
    cols = np.flatnonzero(ink[a:b].any(axis=0))
    return float(cols[0]), float(a), float(cols[-1]), float(b - 1)


def _inside(gray: Any, corners: list[tuple[float, float]], ink_below: float) -> float:
    """The share of the pixels inside ``corners`` that are ink."""
    mask = np.zeros(gray.shape, np.uint8)
    cv2.fillConvexPoly(mask, np.round(np.array(corners)).astype(np.int32), 1)
    return float((gray[mask == 1] < ink_below).mean())


@pytest.mark.parametrize("turns", [0, 1, 2, 3])
def test_a_box_on_the_straightened_page_lands_on_the_same_ink_in_the_original(rx, turns):
    """The whole point of recording the matrix: tilt, angle and turn, then back."""
    photo, placed = _photo(rx, 9.0, lean=0.05)
    photo, turn = g.rotate90(photo, turns)
    out = g.straighten(photo)
    assert out.hold is None
    box = _ink_box(out.image)
    back = g.box_to_original(out.matrix, box)
    # where that same line really is in the photograph: the page's own box, carried forward
    x0, y0, x1, y1 = _ink_box(rx)
    truth = g.to_straightened(turn @ placed, [(x0, y0), (x1, y0), (x1, y1), (x0, y1)])
    off = max(np.hypot(bx - tx, by - ty) for (bx, by), (tx, ty) in zip(back, truth, strict=True))
    assert off < 6.0, f"the box came back {off:.1f} px from its ink"
    # and it is ink there: the mapped box is as full of writing as the box it came from
    x0, y0, x1, y1 = (int(v) for v in box)
    there = float((out.image[y0 : y1 + 1, x0 : x1 + 1] < 140).mean())
    assert _inside(photo, back, 140.0) == pytest.approx(there, abs=0.05)


def test_there_and_back_is_the_same_point(page):
    photo, _ = _photo(page, 11.0, lean=0.06)
    out = g.straighten(g.rotate90(photo, 3)[0])
    pts = [(10.0, 10.0), (out.size[0] - 10.0, 20.0), (out.size[0] / 2, out.size[1] / 2)]
    again = g.to_straightened(out.matrix, g.to_original(out.matrix, pts))
    assert np.allclose(again, pts, atol=1e-6)


def test_the_recorded_matrix_is_enough_to_map_back(rx):
    """What is stored is a plain list of numbers; it must do the job on its own."""
    out = g.straighten(g.rotate90(_photo(rx, 7.0)[0], 1)[0])
    stored = out.record(enabled=True)["matrix"]
    assert len(stored) == 3 and all(len(r) == 3 for r in stored)
    box = _ink_box(out.image)
    assert np.allclose(g.box_to_original(stored, box), g.box_to_original(out.matrix, box),
                       atol=1e-3)  # fmt: skip


def test_an_untouched_page_maps_to_itself(rx):
    out = g.straighten(rx)
    assert np.array_equal(out.matrix, np.eye(3))
    assert g.to_original(out.matrix, [(5.0, 7.0)]) == [(5.0, 7.0)]


# ------------------------------------------------------------------ AC4: when unsure, do nothing


def test_a_page_running_off_the_picture_is_held_and_not_changed(page):
    photo, _ = _photo(page)
    cut = np.ascontiguousarray(photo[:, : photo.shape[1] // 2])  # half the page is out of shot
    out = g.straighten(cut)
    assert out.hold == "edges_not_found"
    assert out.steps == () and np.array_equal(out.image, cut)
    assert np.array_equal(out.matrix, np.eye(3))


def test_a_page_that_fills_the_frame_needs_no_edges(page):
    """A scan or a render has no table round it: nothing to find, so nothing to hold."""
    assert not g.background_visible(page)
    assert g.straighten(page).hold is None


def test_no_corners_means_no_quad():
    table = np.full((1200, 900), 60, np.uint8)
    assert g.find_page_quad(table) is None
    cv2.circle(table, (450, 600), 380, 212, -1)  # bright, large, and not a page
    assert g.find_page_quad(table) is None


def test_a_sideways_page_with_no_clear_margin_is_held_and_not_changed():
    same = _render([f"Line {i}: Tab Amoxicillin 500mg 1-0-1 x5 days" for i in range(12)])
    side = g.rotate90(same, 1)[0]
    out = g.straighten(side)
    assert out.hold == "orientation_unsure"
    assert out.steps == () and np.array_equal(out.image, side)


def test_every_hold_has_plain_words_for_the_desk():
    for code, words in g.HOLD_REASONS.items():
        assert code.islower() and "take it again" in words


# ------------------------------------------------------------------ geometry only


def test_nothing_but_geometry_touches_the_letters(rx):
    """No sharpening, no filter: a turn moves pixels and changes none of them."""
    out = g.straighten(g.rotate90(rx, 2)[0])
    assert sorted(np.unique(out.image).tolist()) == sorted(np.unique(rx).tolist())


# ------------------------------------------------------------------ in S1, and off by default


def test_straightening_is_off_by_default():
    assert Settings().straighten is False


def test_with_it_off_s1_does_exactly_what_it_did(rx):
    """Sideways page, default settings: only the old deskew rule applies, so nothing is turned."""
    side = _png(g.rotate90(rx, 1)[0])
    _, pre = pages.normalize_image(side, straighten=False)
    assert pre["steps"] == ["grayscale", "denoise", "clahe"]
    assert pre["transform"]["enabled"] is False and pre["transform"]["turned_deg"] == 0


def test_the_old_deskew_now_records_its_transform(rx):
    _, pre = pages.normalize_image(_png(_tilted(rx, 5.0)), straighten=False)
    assert pre["steps"] == ["grayscale", "deskew", "denoise", "clahe"]
    assert pre["skew_deg"] == pytest.approx(5.0, abs=0.3)
    m = np.array(pre["transform"]["matrix"])
    assert m.shape == (3, 3) and not np.array_equal(m, np.eye(3))


def test_with_it_on_s1_stores_the_upright_page_and_the_way_back(rx):
    side = g.rotate90(rx, 1)[0]
    norm, pre = pages.normalize_image(_png(side), straighten=True)
    t = pre["transform"]
    assert "rotate270" in pre["steps"] and t["turned_deg"] == 270 and t["enabled"] is True
    assert t["source_size"] == [side.shape[1], side.shape[0]]
    assert list(pages.image_size(norm)) == t["size"] == [rx.shape[1], rx.shape[0]]
    # a corner of the stored page is a corner of the upload
    ((x, y),) = g.to_original(t["matrix"], [(0.0, 0.0)])
    assert (round(x), round(y)) in {(0, 0), (side.shape[1] - 1, 0), (0, side.shape[0] - 1),
                                    (side.shape[1] - 1, side.shape[0] - 1)}  # fmt: skip


def test_a_held_page_reaches_the_desk_only_when_switched_on(rx):
    cut = _photo(rx)[0]
    raw = _png(np.ascontiguousarray(cut[:, : cut.shape[1] // 2]))
    assert g.hold_for(raw, "image/png", settings=Settings(straighten=False)) is None
    held = g.hold_for(raw, "image/png", settings=Settings(straighten=True))
    assert held is not None and held["code"] == "edges_not_found" and held["page"] == 1
    assert held["message"] == g.HOLD_REASONS["edges_not_found"]


def test_a_good_page_is_never_held(page):
    assert g.hold_for(_png(page), "image/png", settings=Settings(straighten=True)) is None
