"""IM-S3: find the text lines and say which are printed and which are handwritten.

What these tests can and cannot show. The repo holds printed pages (renders and three sample
scans) and **no real handwriting**, and OpenCV's "script" fonts are no substitute - the module
labels them printed, rightly: they are type. So handwriting here is a *stand-in* built from the
two things the label is decided on: each word of a printed page leaned over (slant) and resized
and nudged off the line (uneven). That pins the rule; it does not measure it. The measurement on
real photographed prescriptions is in ``docs/ha.md`` and is repeated with
``scripts/measure_regions.py``; line recall and label accuracy by document type (AC3) need the
answer-key set, and :func:`regions.score` - tested below - is what will count them.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")

from cdi_adapter.ingest import pages  # noqa: E402
from cdi_adapter.recognition import regions as R  # noqa: E402
from tests.helpers import make_pdf  # noqa: E402

SAMPLES = sorted((Path(__file__).resolve().parents[1] / "data" / "samples").glob("*.scan.png"))
HEAD = ["City Care Hospitals and Diagnostics", "Consultant Physician and Diabetologist",
        "Plot No 12 Road No 3 Banjara Hills Hyderabad"]  # fmt: skip
BODY = ["Tab Metformin 500 mg twice daily after food", "Tab Amlodipine 5 mg once daily",
        "Cap Omeprazole 20 mg before breakfast", "Syp Paracetamol 5 ml three times",
        "Review after two weeks with reports"]  # fmt: skip


# ------------------------------------------------------------------ the pictures


def _render(lines: list[str]) -> Any:
    png = pages.render_pages(make_pdf([lines]), "application/pdf")[0]
    return cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_GRAYSCALE)


def _by_hand(gray: Any, below: int = 0, seed: int = 1, slant: float = 0.5) -> Any:
    """The stand-in for handwriting: every word below row ``below`` leaned over, resized and
    nudged off its baseline. Words above it are left as the type they are."""
    rng = np.random.default_rng(seed)
    out = gray.copy()
    marks, letter = R._marks(R.ink_mask(gray))
    out[below:] = 255
    words = cv2.dilate(marks, np.ones((1, max(3, int(letter * 0.8)) | 1), np.uint8))
    pad = int(letter)
    for x, y, w, h, _ in cv2.connectedComponentsWithStats(words, connectivity=8)[2][1:]:
        if y < below:
            continue
        y0, x0 = max(0, y - pad), max(0, x - pad)
        word = gray[y0 : y + h + pad, x0 : x + w + pad]
        hh, ww = word.shape
        s, size = slant * rng.uniform(0.8, 1.3), rng.uniform(0.8, 1.3)
        shift = (1 - size) * hh * 0.5 + rng.uniform(-0.25, 0.25) * letter
        m = np.array([[1, -s, s * hh], [0, size, shift]], dtype=np.float32)
        leaned = cv2.warpAffine(word, m, (ww + int(s * hh) + 2, hh), flags=cv2.INTER_CUBIC,
                                borderValue=255)  # fmt: skip
        target = out[y0 : y0 + hh, x0 : x0 + leaned.shape[1]]
        np.minimum(target, leaned[:, : target.shape[1]], out=target)
    return out


@pytest.fixture(scope="module")
def printed() -> Any:
    return _render(HEAD + BODY)


@pytest.fixture(scope="module")
def letterhead_and_hand(printed) -> tuple[Any, int]:
    """AC1's page: a printed header, a handwritten body. Returns it with the row between them."""
    lines = R.detect_lines(printed)
    split = (lines[len(HEAD) - 1][3] + lines[len(HEAD)][1]) // 2
    return _by_hand(printed, below=split), split


def _png(arr: Any) -> bytes:
    return cv2.imencode(".png", arr)[1].tobytes()


# ------------------------------------------------------------------ finding the lines


def test_every_line_of_a_printed_page_is_found_in_reading_order(printed):
    lines = R.detect_lines(printed)
    assert len(lines) == len(HEAD) + len(BODY)
    tops = [b[1] for b in lines]
    assert tops == sorted(tops)
    assert all(x1 > x0 and y1 > y0 for x0, y0, x1, y1 in lines)


@pytest.mark.parametrize("scan", SAMPLES, ids=lambda p: p.name)
def test_lines_are_found_on_the_sample_scans(scan):
    gray = cv2.imread(str(scan), cv2.IMREAD_GRAYSCALE)
    lines = R.detect_lines(gray)
    assert 8 <= len(lines) <= 16, len(lines)
    ink = R.ink_mask(gray)
    held = sum(int(ink[y0:y1, x0:x1].sum()) for x0, y0, x1, y1 in lines)
    assert held >= 0.85 * int(R._marks(ink)[0].sum())  # the lines hold nearly all the writing


def test_a_blank_page_has_no_lines():
    assert R.detect_lines(np.full((1200, 900), 255, np.uint8)) == []
    assert R.detect_regions(_png(np.full((1200, 900), 255, np.uint8))) == []


def test_two_columns_side_by_side_are_two_lines(printed):
    """A prescription's corner ("BP / PR / Wt") sits beside the patient's name, not after it."""
    page = np.full_like(printed, 255)
    x0, y0, x1, y1 = R.detect_lines(printed)[3]
    word = printed[y0:y1, x0 : x0 + (x1 - x0) // 3]
    page[300 : 300 + word.shape[0], 100 : 100 + word.shape[1]] = word
    far = page.shape[1] - 100 - word.shape[1]
    page[300 : 300 + word.shape[0], far : far + word.shape[1]] = word
    assert len(R.detect_lines(page)) == 2


def test_an_underline_is_not_writing_and_does_not_join_two_lines(printed):
    page = printed.copy()
    lines = R.detect_lines(printed)
    x0, _, x1, y1 = lines[0]
    gap = lines[1][1] - y1
    cv2.line(page, (x0, y1 + gap // 2), (x1, y1 + gap // 2), 0, 2)  # a rule under the heading
    assert len(R.detect_lines(page)) == len(lines)


def test_lines_that_touch_are_parted_where_the_ink_thins(printed):
    """Handwritten lines touch - a descender reaches the line below - and arrive as one block."""
    lines = R.detect_lines(printed)
    (x0, y0, x1, y1), (a0, b0, a1, b1) = lines[3], lines[4]
    page = np.full_like(printed, 255)
    page[300 : 300 + (y1 - y0), 100 : 100 + (x1 - x0)] = printed[y0:y1, x0:x1]
    top = 300 + (y1 - y0) - 2  # the second line set so close that its ascenders reach the first
    region = page[top : top + (b1 - b0), 100 : 100 + (a1 - a0)]
    np.minimum(region, printed[b0:b1, a0:a1], out=region)
    assert len(R.detect_lines(page)) == 2


def test_ink_is_found_on_grey_paper_under_uneven_light(printed):
    """A photograph's paper is grey; blue ballpoint on it is never darker than mid-grey."""
    photo = printed.astype(np.float32) / 255.0 * 100.0 + 100.0  # paper 200, ink at 100
    photo += np.linspace(-15, 15, photo.shape[1], dtype=np.float32)[None, :]
    photo = np.clip(photo, 0, 255).astype(np.uint8)
    assert int((photo < 80).sum()) == 0  # nothing here is "dark" by a fixed threshold
    assert len(R.detect_lines(photo)) == len(HEAD) + len(BODY)


# ------------------------------------------------------------------ AC1: printed and handwritten


def test_a_printed_header_and_a_handwritten_body_are_told_apart(letterhead_and_hand):
    page, split = letterhead_and_hand
    regions = R.detect_regions(page, crops=False)
    head = [r for r in regions if r.bbox[3] <= split]
    body = [r for r in regions if r.bbox[1] >= split]
    assert len(head) == len(HEAD) and len(body) >= len(BODY)
    assert [r.label for r in head] == ["printed"] * len(HEAD)
    assert {r.label for r in body} == {"handwritten"}


def test_printed_goes_to_the_print_reader_and_handwriting_to_the_handwriting_readers(
    letterhead_and_hand,
):
    page, split = letterhead_and_hand
    for r in R.detect_regions(page, crops=False):
        assert r.readers == (("print",) if r.bbox[3] <= split else ("handwriting",))


@pytest.mark.parametrize("scan", SAMPLES, ids=lambda p: p.name)
def test_no_line_of_a_printed_scan_is_called_handwritten(scan):
    regions = R.detect_regions(cv2.imread(str(scan), cv2.IMREAD_GRAYSCALE), crops=False)
    labels = [r.label for r in regions]
    assert "handwritten" not in labels
    assert labels.count("printed") >= 0.6 * len(labels)  # most of a printed page is plainly so


def test_capital_letters_are_still_print():
    regions = R.detect_regions(_render([s.upper() for s in HEAD + BODY]), crops=False)
    assert {r.label for r in regions} == {"printed"}


def test_a_script_typeface_is_type():
    """Why handwriting cannot be faked with a font: OpenCV's script face is made of upright,
    even strokes, and reads as print - to this module and to the print reader alike."""
    page = np.full((900, 1600), 255, np.uint8)
    for i, text in enumerate(BODY):
        cv2.putText(page, text, (80, 150 + 130 * i), cv2.FONT_HERSHEY_SCRIPT_SIMPLEX, 1.6, 30, 3,
                    cv2.LINE_AA)  # fmt: skip
    assert "handwritten" not in {r.label for r in R.detect_regions(page, crops=False)}


def test_the_numbers_behind_a_label_are_kept(letterhead_and_hand):
    for r in R.detect_regions(letterhead_and_hand[0], crops=False):
        assert {"axis_share", "height_cv", "stroke_width_cv", "letters", "height_px"} <= set(
            r.features
        )


def test_type_stands_upright_and_the_stand_in_does_not(printed):
    marks = R._marks(R.ink_mask(printed))[0]
    typed = [R.line_features(printed, b, marks)["axis_share"] for b in R.detect_lines(printed)]
    leaned = _by_hand(printed)
    marks = R._marks(R.ink_mask(leaned))[0]
    hand = [f["axis_share"] for b in R.detect_lines(leaned)
            if (f := R.line_features(leaned, b, marks))]  # fmt: skip
    assert min(typed) > max(hand)


# ------------------------------------------------------------------ AC2: uncertain goes to both


def test_a_line_too_short_to_measure_is_uncertain_and_goes_to_both_readers(printed):
    x0, y0, x1, y1 = R.detect_lines(printed)[0]
    stub = (x0, y0, x0 + (y1 - y0), y1)  # about one letter of it
    label, features = R.classify_line(printed, stub)
    assert label == "uncertain" and R.READERS[label] == ("print", "handwriting")
    assert features["letters"] == 0


def test_a_short_upright_piece_is_not_called_printed():
    """Measured on a real prescription: a handwritten "TSH", "HS" and "1-0-1" stand as upright
    as type. Sent to the print reader alone they would be the confident misreading this story
    exists to stop - so a short piece is never "printed", however upright."""
    regions = R.detect_regions(_render(["TSH", "HS", "1-0-1"]), crops=False)
    assert regions and {r.label for r in regions} == {"uncertain"}


def test_tiny_writing_is_uncertain_not_guessed(printed):
    small = cv2.resize(printed, None, fx=0.25, fy=0.25, interpolation=cv2.INTER_AREA)
    labels = {r.label for r in R.detect_regions(small, crops=False)}
    assert "handwritten" not in labels


def test_every_label_has_a_reader_and_nothing_is_dropped(letterhead_and_hand):
    assert set(R.READERS) == set(R.LABELS)
    assert all(R.READERS[label] for label in R.LABELS)
    assert R.READERS["mixed"] == R.READERS["uncertain"] == ("print", "handwriting")
    page, _ = letterhead_and_hand
    regions = R.detect_regions(page, crops=False)
    assert len(regions) == len(R.detect_lines(page))  # a region for every line found
    assert all(r.readers for r in regions)


def test_the_labels_are_the_ones_the_evidence_table_accepts():
    """``ocr_observation.region_kind`` is CHECKed against exactly these four."""
    migration = (Path(__file__).resolve().parents[1] / "db" / "alembic" / "versions"
                 / "0013_evidence_schema.py").read_text(encoding="utf-8")  # fmt: skip
    for label in R.LABELS:
        assert f"'{label}'" in migration


# ------------------------------------------------------------------ mixed


def test_a_printed_label_with_a_handwritten_value_is_mixed(printed):
    """A form line: type, a clear space, then what was written in."""
    x0, y0, x1, y1 = R.detect_lines(printed)[1]
    leaned = _by_hand(printed)
    hx0, hy0, hx1, hy1 = R.detect_lines(leaned)[len(HEAD) + 1]
    page = np.full_like(printed, 255)
    page[400 : 400 + (y1 - y0), 80 : 80 + (x1 - x0)] = printed[y0:y1, x0:x1]
    start = 80 + (x1 - x0) + 3 * (y1 - y0)
    width = min(hx1 - hx0, page.shape[1] - start - 10)
    target = page[400 : 400 + (hy1 - hy0), start : start + width]
    np.minimum(target, leaned[hy0:hy1, hx0 : hx0 + width][: target.shape[0]], out=target)
    labels = [r.label for r in R.detect_regions(page, crops=False)]
    assert labels in (["mixed"], ["printed", "handwritten"]), labels
    assert all(len(R.READERS[label]) >= 1 for label in labels)


# ------------------------------------------------------------------ the print reader's opinion


class _Read:
    def __init__(self, bbox: tuple[int, int, int, int], conf: float) -> None:
        self.bbox, self.conf = list(bbox), conf


def test_an_unsure_print_reader_makes_a_printed_line_uncertain(printed):
    box = R.detect_lines(printed)[0]
    assert R.classify_line(printed, box)[0] == "printed"
    label, features = R.classify_line(printed, box, ocr=[_Read(box, 0.55)])
    assert label == "uncertain" and features["print_read_conf"] == 0.55


def test_a_confident_print_reader_never_makes_a_line_printed(printed):
    """It read a real handwritten "Metformin" at 0.99. Its confidence is not evidence of type."""
    leaned = _by_hand(printed)
    box = R.detect_lines(leaned)[len(HEAD)]
    assert R.classify_line(leaned, box, ocr=[_Read(box, 0.99)])[0] == "handwritten"


def test_a_reading_somewhere_else_on_the_page_says_nothing_about_this_line(printed):
    a, b = R.detect_lines(printed)[0], R.detect_lines(printed)[4]
    label, features = R.classify_line(printed, a, ocr=[_Read(b, 0.10)])
    assert label == "printed" and "print_read_conf" not in features


# ------------------------------------------------------------------ AC4: the crop standard


def test_a_crop_keeps_paper_round_the_line_so_no_stroke_is_cut(printed):
    box = R.detect_lines(printed)[0]
    crop = R.crop_png(printed, box)
    px0, py0, px1, py1 = crop.padded_bbox
    assert px0 < box[0] and py0 < box[1] and px1 > box[2] and py1 > box[3]
    assert crop.pad_px >= R.STANDARD.min_pad_px
    cut = cv2.imdecode(np.frombuffer(crop.png, np.uint8), cv2.IMREAD_GRAYSCALE)
    assert cut[0].min() > 200 and cut[-1].min() > 200  # the top and bottom rows are paper


def test_the_crop_is_cut_from_the_page_it_is_given_and_fingerprinted(printed):
    box = R.detect_lines(printed)[2]
    crop = R.crop_png(printed, box)
    x0, y0, x1, y1 = crop.padded_bbox
    pixels = np.ascontiguousarray(printed[y0:y1, x0:x1])
    want = hashlib.sha256(f"{x1 - x0}x{y1 - y0}:".encode() + pixels.tobytes()).hexdigest()
    assert crop.sha256 == want and crop.scale == 1.0
    assert np.array_equal(cv2.imdecode(np.frombuffer(crop.png, np.uint8), 0), pixels)
    assert R.crop_png(printed, box).sha256 == crop.sha256  # the same pixels, the same print
    assert R.crop_png(printed, R.detect_lines(printed)[3]).sha256 != crop.sha256


def test_a_very_small_crop_is_enlarged_and_its_fingerprint_is_still_the_pages(printed):
    small = cv2.resize(printed, None, fx=0.3, fy=0.3, interpolation=cv2.INTER_AREA)
    box = R.detect_lines(small)[0]
    crop = R.crop_png(small, box)
    assert crop.source_size[1] < R.STANDARD.min_height_px <= crop.size[1]
    assert 1.0 < crop.scale <= R.STANDARD.max_scale
    x0, y0, x1, y1 = crop.padded_bbox
    pixels = np.ascontiguousarray(small[y0:y1, x0:x1])
    assert (
        crop.sha256
        == hashlib.sha256(f"{x1 - x0}x{y1 - y0}:".encode() + pixels.tobytes()).hexdigest()
    )  # of the page's pixels, not of the enlargement


@pytest.mark.parametrize("factor", [1.0, 0.6, 0.35, 0.2])
def test_every_crop_meets_the_standard_and_records_its_size(printed, factor):
    page = printed if factor == 1.0 else cv2.resize(
        printed, None, fx=factor, fy=factor, interpolation=cv2.INTER_AREA)  # fmt: skip
    regions = R.detect_regions(page)
    assert regions
    for r in regions:
        assert r.crop is not None and R.meets_standard(r.crop)
        rec = r.crop.record()
        assert {"sha256", "bbox", "padded_bbox", "pad_px", "scale", "source_size", "size"} == set(
            rec
        )
        assert rec["size"][1] == round(rec["source_size"][1] * rec["scale"])


def test_a_crop_at_the_page_edge_is_clipped_not_refused(printed):
    crop = R.crop_png(printed, (0, 0, 200, 40))
    assert crop.padded_bbox[:2] == (0, 0)
    with pytest.raises(ValueError, match="outside the page"):
        R.crop_png(printed, (5000, 5000, 5100, 5040))


def test_colour_pages_are_cropped_in_colour(printed):
    colour = cv2.cvtColor(printed, cv2.COLOR_GRAY2BGR)
    regions = R.detect_regions(_png(colour))
    assert regions and [r.label for r in regions] == [
        r.label for r in R.detect_regions(printed, crops=False)
    ]
    cut = cv2.imdecode(np.frombuffer(regions[0].crop.png, np.uint8), cv2.IMREAD_UNCHANGED)
    assert cut.ndim == 3


def test_a_page_summary_counts_each_kind(letterhead_and_hand):
    page, _ = letterhead_and_hand
    s = R.summary(R.detect_regions(page))
    assert s["lines"] == s["printed"] + s["handwritten"] + s["mixed"] + s["uncertain"]
    assert s["printed"] == len(HEAD) and s["handwritten"] >= len(BODY)
    assert s["to_both_readers"] == s["mixed"] + s["uncertain"]


def test_a_region_record_is_plain_data(printed):
    import json

    rec = R.detect_regions(printed)[0].record()
    assert json.loads(json.dumps(rec)) == rec
    assert rec["readers"] == ["print"] and rec["crop"]["sha256"]


# ------------------------------------------------------------------ AC3: counting against a key


def _line(box: tuple[int, int, int, int], label: str) -> dict[str, Any]:
    return {"bbox": list(box), "label": label}


def test_iou_is_one_for_the_same_box_and_zero_for_boxes_apart():
    assert R.iou((0, 0, 10, 10), (0, 0, 10, 10)) == 1.0
    assert R.iou((0, 0, 10, 10), (20, 20, 30, 30)) == 0.0
    assert R.iou((0, 0, 10, 10), (5, 0, 15, 10)) == pytest.approx(1 / 3)


def test_recall_is_the_share_of_drawn_lines_that_were_found():
    truth = [_line((0, 0, 100, 20), "printed"), _line((0, 40, 100, 60), "handwritten"),
             _line((0, 80, 100, 100), "handwritten")]  # fmt: skip
    found = [_line((0, 0, 100, 20), "printed"), _line((2, 41, 99, 60), "handwritten")]
    s = R.score(found, truth)
    assert s["recall"] == pytest.approx(2 / 3) and s["matched"] == 2 and s["extra_lines"] == 0
    assert s["label_accuracy"] == 1.0


def test_caution_is_counted_apart_from_being_right_and_from_being_wrong():
    """A line sent to both readers reaches the right one, but it was not labelled right: if
    "uncertain" scored as correct, a module that called everything uncertain would score 100%."""
    truth = [_line((0, 0, 100, 20), "handwritten"), _line((0, 40, 100, 60), "handwritten"),
             _line((0, 80, 100, 100), "handwritten")]  # fmt: skip
    found = [_line((0, 0, 100, 20), "handwritten"), _line((0, 40, 100, 60), "uncertain"),
             _line((0, 80, 100, 100), "printed")]  # fmt: skip
    s = R.score(found, truth)
    assert (s["label_right"], s["label_safe"], s["label_wrong"]) == (1, 1, 1)
    assert s["label_accuracy"] == pytest.approx(1 / 3)


def test_a_found_line_answers_for_one_drawn_line_only():
    truth = [_line((0, 0, 100, 20), "printed"), _line((0, 2, 100, 22), "printed")]
    s = R.score([_line((0, 1, 100, 21), "printed")], truth)
    assert s["matched"] == 1 and s["recall"] == 0.5


def test_results_are_reported_by_document_type():
    page = {"found": [_line((0, 0, 100, 20), "printed")],
            "truth": [_line((0, 0, 100, 20), "printed"),
                      _line((0, 40, 100, 60), "printed")]}  # fmt: skip
    out = R.score_by_type([{**page, "doc_type": "prescription"}, {**page, "doc_type": "lab"},
                           {**page, "doc_type": "prescription"}])  # fmt: skip
    assert set(out) == {"prescription", "lab"}
    assert out["prescription"]["pages"] == 2 and out["prescription"]["truth_lines"] == 4
    assert out["prescription"]["recall"] == 0.5 and out["lab"]["label_accuracy"] == 1.0


def test_the_module_scores_itself_against_a_key_it_cannot_see(letterhead_and_hand):
    """End to end: the key is where the lines were *put*, the answer is what the module found."""
    page, split = letterhead_and_hand
    truth = [_line(b, "printed" if b[3] <= split else "handwritten")
             for b in R.detect_lines(page)]  # fmt: skip
    found = [r.record() for r in R.detect_regions(page, crops=False)]
    s = R.score(found, truth)
    assert s["recall"] == 1.0 and s["label_wrong"] == 0


def test_the_measuring_script_prints_the_table_from_an_answer_key(
    letterhead_and_hand, tmp_path, capsys
):
    """``scripts/measure_regions.py --key``: the run the answer-key set will be put through."""
    import importlib.util
    import json

    page, split = letterhead_and_hand
    cv2.imwrite(str(tmp_path / "rx.png"), page)
    lines = [_line(b, "printed" if b[3] <= split else "handwritten")
             for b in R.detect_lines(page)]  # fmt: skip
    key = tmp_path / "key.json"
    key.write_text(json.dumps([{"image": "rx.png", "doc_type": "prescription", "lines": lines}]))
    path = Path(__file__).resolve().parents[1] / "scripts" / "measure_regions.py"
    spec = importlib.util.spec_from_file_location("measure_regions", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.main(["--key", str(key), "--draw", str(tmp_path / "out")]) == 0
    out = capsys.readouterr().out
    assert "prescription" in out and "100.0" in out and "recall %" in out
    assert (tmp_path / "out" / "rx.regions.png").exists()
    assert module.main(["--key", str(key), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["prescription"]["label_wrong"] == 0


# ------------------------------------------------------------------ in S3, and off by default


def _reads(page: Any) -> list[Any]:
    """What the print reader would hand over: one reading per line, confident."""
    return [_Read(b, 0.97) for b in R.detect_lines(page)]


def test_acting_on_the_labels_is_off_by_default():
    from cdi_adapter.config import Settings

    assert Settings().line_regions is False
    assert Settings().line_regions_min_handwritten == 3


def test_each_reading_takes_the_label_of_the_line_it_lies_on(letterhead_and_hand):
    from cdi_adapter.ocr import service

    page, split = letterhead_and_hand
    reads = _reads(page)
    kinds = service.region_kinds(reads, service.page_regions(_png(page), reads))
    assert len(kinds) == len(reads)
    for read, kind in zip(reads, kinds, strict=True):
        assert kind == ("printed" if read.bbox[3] <= split else "handwritten")


def test_a_reading_on_no_line_keeps_the_readers_own_kind(printed):
    """``None`` means "say nothing": the observation is stored as the reader's kind, as before."""
    from cdi_adapter.ocr import service

    found = service.page_regions(_png(printed), [])
    stray = _Read((5, 5, 60, 20), 0.9)  # the top-left corner: no writing there
    assert service.region_kinds([stray], found) == [None]
    assert service.region_kinds([stray], []) == [None]


def test_the_run_records_how_many_lines_of_each_kind(letterhead_and_hand, printed):
    from cdi_adapter.ocr import service

    hand_page, _ = letterhead_and_hand
    pages = [service.page_regions(_png(printed), []), service.page_regions(_png(hand_page), [])]
    counts = service._region_counts(pages, enforced=False)
    assert counts["lines"] == sum(counts[k] for k in R.LABELS)
    assert counts["printed"] == len(HEAD) + len(BODY) + len(HEAD)
    assert counts["handwritten"] >= len(BODY)
    assert counts["max_handwritten_on_a_page"] == counts["handwritten"]  # all on the one page
    assert counts["enforced"] is False
    assert service._region_counts([], enforced=True)["lines"] == 0


def test_a_failure_finding_lines_never_fails_the_page(monkeypatch):
    from cdi_adapter.ocr import service

    def boom(*a: Any, **k: Any) -> Any:
        raise RuntimeError("no lines today")

    monkeypatch.setattr(service.regions, "detect_regions", boom)
    assert service.page_regions(b"not a picture", []) == []


def test_the_reader_is_still_chosen_by_the_classifier_alone():
    """``choose_engine`` is untouched: line labels never pick the engine inside S3."""
    from cdi_adapter.config import Settings
    from cdi_adapter.ocr import service

    on = Settings(line_regions=True)
    assert service.choose_engine(None, {"is_handwritten": False}, on) == "rapidocr"
    assert service.choose_engine(None, {"is_handwritten": True}, on) == "vlm"
    assert service.choose_engine("rapidocr", {"is_handwritten": True}, on) == "rapidocr"


def test_the_handwriting_pass_follows_the_lines_only_when_switched_on():
    from cdi_adapter.config import Settings
    from cdi_adapter.webapp.jobs import handwritten_lines

    read = {"blocks": 30, "regions": {"handwritten": 9, "max_handwritten_on_a_page": 9}}
    assert handwritten_lines(read, Settings()) is False  # off: the classifier alone decides
    assert handwritten_lines(read, Settings(line_regions=True)) is True
    few = {"regions": {"handwritten": 2, "max_handwritten_on_a_page": 2}}
    assert handwritten_lines(few, Settings(line_regions=True)) is False  # a signature, a tick
    assert handwritten_lines(few, Settings(line_regions=True,
                                           line_regions_min_handwritten=2)) is True  # fmt: skip


def test_no_line_counts_means_the_classifier_alone_decides():
    """An older run, a fake stage in a test, or a line finder that failed."""
    from cdi_adapter.config import Settings
    from cdi_adapter.webapp.jobs import handwritten_lines

    on = Settings(line_regions=True)
    for read in (None, {}, {"blocks": 3}, {"regions": None}, "ok"):
        assert handwritten_lines(read, on) is False


def test_s5_already_reads_the_label_s3_now_writes():
    """Why the label is stored on the observation: resolution treats evidence as handwritten
    when an observation says so, and until now only the VLM pass ever said so."""
    from cdi_adapter.resolution import facts

    source = Path(facts.__file__).read_text(encoding="utf-8")
    assert 'o["region_kind"] == "handwritten"' in source


def test_finding_the_lines_of_a_page_is_quick(printed):
    import time

    started = time.perf_counter()
    R.detect_regions(printed)
    assert time.perf_counter() - started < 5.0  # ~0.2 s here; the bound is for a slow CI box
