"""IM-S3 acceptance: find the text lines and say which are printed and which are handwritten.

One group of tests per acceptance criterion, written on 2026-10-08 to record what the code does
today. **No application code was changed for this file.**

How they run: the real line finder (``recognition/regions.py``), the real OCR-stage wiring
(``ocr/service.py``) and the real upload-job helper, on pictures built here. The repository
holds printed pages and **no real handwriting**, so handwriting is the same *stand-in* the
story's unit tests use - printed words leaned over and made uneven
(``tests/test_regions_unit._by_hand``). It pins the rule; it does not measure it. What the
labels do on two real photographed prescriptions is in ``docs/test-IM-S3.md``.

The first check (the same day) found that lines were labelled and could be cut, but that
nothing sent a line to a reader (AC2, AC4), and that there was no answer key (AC3). Line-by-line
reading was then built. One test is still a strict expected failure: there is no answer-key set
of **real pages with hand-drawn lines** in the repository, and no code can supply one.
"""

from __future__ import annotations

import hashlib
import importlib.util
import inspect
import json
from pathlib import Path
from typing import Any

import pytest

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")

from cdi_adapter.config import Settings  # noqa: E402
from cdi_adapter.ocr import service as ocr  # noqa: E402
from cdi_adapter.recognition import regions as R  # noqa: E402
from cdi_adapter.webapp.jobs import handwritten_lines  # noqa: E402
from tests.test_regions_unit import BODY, HEAD, _by_hand, _png, _render  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


class _Read:
    """One line as the print reader hands it over."""

    def __init__(self, bbox: tuple[int, int, int, int], conf: float) -> None:
        self.bbox, self.conf = list(bbox), conf


@pytest.fixture(scope="module")
def printed() -> Any:
    return _render(HEAD + BODY)


@pytest.fixture(scope="module")
def mixed_page(printed) -> tuple[Any, int]:
    """The AC's page: a printed header and a handwritten body. Returns it with the row that
    divides the two."""
    lines = R.detect_lines(printed)
    split = (lines[len(HEAD) - 1][3] + lines[len(HEAD)][1]) // 2
    return _by_hand(printed, below=split), split


# ------------------------------------------------------------------ AC1
# GIVEN a page with a printed header and a handwritten body WHEN it is processed
# THEN header lines are labelled printed and body lines handwritten


def test_ac1_every_line_of_the_page_is_found(mixed_page) -> None:
    page, split = mixed_page
    found = R.detect_lines(page)
    assert len([b for b in found if b[3] <= split]) == len(HEAD)
    assert len([b for b in found if b[1] >= split]) >= len(BODY)
    assert [b[1] for b in found] == sorted(b[1] for b in found)  # top to bottom


def test_ac1_header_lines_are_labelled_printed(mixed_page) -> None:
    page, split = mixed_page
    head = [r for r in R.detect_regions(page, crops=False) if r.bbox[3] <= split]
    assert [r.label for r in head] == ["printed"] * len(HEAD)


def test_ac1_body_lines_are_labelled_handwritten(mixed_page) -> None:
    page, split = mixed_page
    body = [r for r in R.detect_regions(page, crops=False) if r.bbox[1] >= split]
    assert body and {r.label for r in body} == {"handwritten"}


def test_ac1_a_fully_printed_page_has_no_handwritten_line(printed) -> None:
    labels = [r.label for r in R.detect_regions(printed, crops=False)]
    assert "handwritten" not in labels and labels.count("printed") >= len(labels) - 1


def test_ac1_the_numbers_behind_each_label_are_kept(mixed_page) -> None:
    for r in R.detect_regions(mixed_page[0], crops=False):
        assert {"axis_share", "height_cv", "stroke_width_cv", "letters"} <= set(r.features)


def test_ac1_the_ocr_stage_counts_the_lines_of_each_kind_on_every_upload(mixed_page) -> None:
    """In the pipeline: S3 runs the line finder on the print reader's pass and records how many
    lines of each kind it found with the run - always, whatever the settings."""
    page, _ = mixed_page
    found = ocr.page_regions(_png(page), [])
    counts = ocr._region_counts([found], enforced=False)
    assert counts["printed"] == len(HEAD) and counts["handwritten"] >= len(BODY)
    assert counts["lines"] == sum(counts[k] for k in R.LABELS)
    source = inspect.getsource(ocr.ocr_document)
    assert 'metrics["regions"] = _region_counts(found, settings.line_regions)' in source


def test_ac1_with_line_regions_on_each_reading_is_stored_with_its_lines_label(mixed_page) -> None:
    page, split = mixed_page
    reads = [_Read(b, 0.97) for b in R.detect_lines(page)]
    kinds = ocr.region_kinds(reads, ocr.page_regions(_png(page), reads))  # type: ignore[arg-type]
    for read, kind in zip(reads, kinds, strict=True):
        assert kind == ("printed" if read.bbox[3] <= split else "handwritten")
    # the rows are built where every reader's lines become evidence (RD-S1)
    from cdi_adapter.recognition import pipeline

    assert "kinds=kinds" in inspect.getsource(ocr.ocr_document)
    assert '**({"region_kind": kind} if kind else {})' in inspect.getsource(
        pipeline.observation_rows
    )


def test_ac1_by_default_the_labels_are_counted_but_not_stored_or_acted_on() -> None:
    """ISSUE, recorded as a fact: ``CDI_LINE_REGIONS`` is off by default. The counts are
    recorded with the OCR run, but every stored reading still carries the reader's blanket
    "printed", and nothing is done with a line's label."""
    assert Settings().line_regions is False
    source = inspect.getsource(ocr.ocr_document)
    assert "kinds.append(region_kinds(lines, found[-1]) if on else [None] * len(lines))" in source


def test_ac1_a_failure_finding_lines_never_fails_the_page(monkeypatch) -> None:
    def boom(*a: Any, **k: Any) -> Any:
        raise RuntimeError("no lines today")

    monkeypatch.setattr(ocr.regions, "detect_regions", boom)
    assert ocr.page_regions(b"not a picture", []) == []


# ------------------------------------------------------------------ AC2
# GIVEN a line cannot be classified WHEN it is processed
# THEN it is labelled uncertain and goes to both readers

_AC2 = ("IM-S3 AC2, 'goes to both readers': a line that cannot be classified IS labelled "
        "uncertain and IS marked for both readers, but nothing sends it anywhere. S3 still "
        "reads a whole page with one reader; there is no reader in the code that takes a "
        "single line, so no line - uncertain or not - is routed on its label.")  # fmt: skip


def test_ac2_a_line_too_short_to_measure_is_labelled_uncertain(printed) -> None:
    x0, y0, x1, y1 = R.detect_lines(printed)[0]
    label, features = R.classify_line(printed, (x0, y0, x0 + (y1 - y0), y1))
    assert label == "uncertain" and features["letters"] == 0


def test_ac2_short_upright_handwriting_is_uncertain_not_printed() -> None:
    """Measured on a real prescription: a handwritten "TSH", "HS" and "1-0-1" stand as upright
    as type. They are left uncertain rather than called printed."""
    found = R.detect_regions(_render(["TSH", "HS", "1-0-1"]), crops=False)
    assert found and {r.label for r in found} == {"uncertain"}


def test_ac2_writing_too_small_to_measure_is_uncertain_not_guessed(printed) -> None:
    small = cv2.resize(printed, None, fx=0.25, fy=0.25, interpolation=cv2.INTER_AREA)
    assert "handwritten" not in {r.label for r in R.detect_regions(small, crops=False)}


def test_ac2_an_unsure_print_reader_makes_a_printed_line_uncertain(printed) -> None:
    box = R.detect_lines(printed)[0]
    assert R.classify_line(printed, box)[0] == "printed"
    assert R.classify_line(printed, box, ocr=[_Read(box, 0.55)])[0] == "uncertain"


def test_ac2_an_uncertain_line_is_marked_for_both_readers() -> None:
    assert R.READERS["uncertain"] == ("print", "handwriting")
    assert R.READERS["mixed"] == ("print", "handwriting")
    assert R.READERS["printed"] == ("print",) and R.READERS["handwritten"] == ("handwriting",)
    region = R.Region((0, 0, 10, 10), "uncertain", 0)
    assert region.readers == ("print", "handwriting") and region.record()["readers"] == [
        "print", "handwriting"]  # fmt: skip


def test_ac2_nothing_is_dropped_every_line_found_has_a_label_and_a_reader(mixed_page) -> None:
    page, _ = mixed_page
    found = R.detect_regions(page, crops=False)
    assert len(found) == len(R.detect_lines(page))
    assert all(r.label in R.LABELS and r.readers for r in found)


class _Reader:
    """Stands in for the handwriting reader (the model gateway): it "reads" every crop it is
    given and keeps them, so a test can see exactly what was sent."""

    def __init__(self, *, fail_on: int | None = None, blank_on: int | None = None) -> None:
        self.crops: list[bytes] = []
        self.prompts: list[str] = []
        self.fail_on, self.blank_on = fail_on, blank_on

    def generate(self, prompt: str, image_png: bytes | None = None, *, max_tokens: int = 96,
                 json_schema: Any = None) -> str:  # fmt: skip
        self.prompts.append(prompt)
        self.crops.append(image_png or b"")
        n = len(self.crops)
        if n == self.fail_on:
            raise RuntimeError("the model gateway is away")
        return "" if n == self.blank_on else f"  handwriting reading {n}\n"


def _prints(page: Any) -> list[_Read]:
    """What the print reader hands over: one reading per line, confident."""
    return [_Read(b, 0.97) for b in R.detect_lines(page)]


def _read_page(
    page: Any, reader: _Reader, *, max_lines: int = 60, prints: list[_Read] | None = None
) -> tuple[Any, list[Any], list[_Read]]:
    prints = _prints(page) if prints is None else prints
    found = ocr.page_regions(_png(page), prints, crops=True)  # type: ignore[arg-type]
    return ocr.read_lines(prints, found, reader, max_lines=max_lines), found, prints  # type: ignore[arg-type]


def test_ac2_an_uncertain_line_goes_to_both_readers(printed) -> None:
    """The AC, end to end in S3's own step: the print reader was unsure of the first line, so
    the line is uncertain - and it ends up with a print reading AND a handwriting reading."""
    prints = _prints(printed)
    prints[0].conf = 0.55  # the print reader was unsure of this one
    reader = _Reader()
    reads, found, _ = _read_page(printed, reader, prints=prints)
    assert found[0].label == "uncertain"
    assert [label for _, label, _ in reads.read] == ["uncertain"]  # the handwriting reader read it
    assert reads.replaced == set()  # and the print reader's reading of it is kept
    assert reads.read[0][0].bbox == list(found[0].bbox) and reads.read[0][0].text


def test_ac2_a_handwritten_line_goes_to_the_handwriting_reader_instead(mixed_page) -> None:
    page, split = mixed_page
    reader = _Reader()
    reads, found, prints = _read_page(page, reader)
    body = [i for i, r in enumerate(found) if r.bbox[1] >= split]
    assert len(reads.read) == len(body) and {label for _, label, _ in reads.read} == {"handwritten"}
    # the print reader's reading of each handwritten line is left out ...
    assert reads.replaced == {n for n, p in enumerate(prints) if p.bbox[1] >= split}
    # ... and its reading of every printed line is kept
    assert not {n for n, p in enumerate(prints) if p.bbox[3] <= split} & reads.replaced


def test_ac2_a_printed_line_is_not_sent_to_the_handwriting_reader(printed) -> None:
    reader = _Reader()
    reads, found, _ = _read_page(printed, reader)
    sent = [r for r in found if "handwriting" in r.readers]
    assert reads.sent == len(sent) == len(reader.crops)  # only lines that are not plainly print
    assert len(sent) < len(found) and reads.replaced == set()


def test_ac2_the_handwriting_reader_is_given_the_lines_own_crop(mixed_page) -> None:
    page, split = mixed_page
    reader = _Reader()
    _, found, _ = _read_page(page, reader)
    wanted = [r.crop.png for r in found if "handwriting" in r.readers]
    assert reader.crops == wanted  # each line's crop, in reading order, and nothing else
    assert all("one line of writing" in p for p in reader.prompts)


def test_ac2_nothing_is_dropped_when_the_handwriting_reader_fails_or_reads_nothing(
    mixed_page,
) -> None:
    """A line the handwriting reader could not read keeps the print reader's reading."""
    page, split = mixed_page
    reader = _Reader(fail_on=1, blank_on=2)
    reads, found, prints = _read_page(page, reader)
    body = [r for r in found if r.bbox[1] >= split]
    assert reads.failed == 1 and len(reads.read) == len(body) - 2
    kept = [n for n, p in enumerate(prints) if p.bbox[1] >= split and n not in reads.replaced]
    assert len(kept) == 2  # the two unread lines still have their print reading


def test_ac2_a_page_that_is_all_uncertain_cannot_make_endless_calls(mixed_page) -> None:
    page, _ = mixed_page
    reader = _Reader()
    reads, found, _ = _read_page(page, reader, max_lines=2)
    assert reads.sent == 2 and len(reader.crops) == 2 and reads.over_cap >= 1
    assert reads.counts()["over_cap"] == reads.over_cap  # and the run says how many were left


def test_ac2_the_reading_is_tidied_to_one_line() -> None:
    from cdi_adapter.ocr import vlm_ocr

    assert vlm_ocr.read_line_crop(b"png", _Reader()) == "handwriting reading 1"
    assert vlm_ocr.read_line_crop(b"png", _Reader(blank_on=1)) == ""


def test_ac2_the_pipeline_sends_each_line_to_the_reader_its_label_names() -> None:
    source = inspect.getsource(ocr.read_lines)
    assert '"handwriting" not in r.readers' in source  # printed lines stay with the print reader
    assert '"print" not in r.readers' in source  # only a plainly handwritten line is replaced
    stage = inspect.getsource(ocr.ocr_document)
    assert "read_lines(lines, found[-1], line_client" in stage
    assert '"engine": "vlm"' in stage and '"region_kind": label' in stage  # stored as its own row


def test_ac2_there_is_a_reader_that_takes_one_line() -> None:
    from cdi_adapter.ocr import vlm_ocr

    assert callable(vlm_ocr.read_line_crop) and "one line" in vlm_ocr.LINE_PROMPT


def test_ac2_line_by_line_reading_is_off_unless_both_switches_are_on() -> None:
    """ISSUE, recorded as a fact: it is one model call per line that is not plainly printed, so
    it is off by default. With the default settings a page is still read by one reader."""
    assert Settings().line_readers is False and Settings().line_regions is False
    stage = inspect.getsource(ocr.ocr_document)
    assert 'by_line = engine != "vlm" and settings.line_regions and settings.line_readers' in stage
    assert Settings().line_readers_max_per_page == 60


def test_ac2_a_page_read_line_by_line_is_not_read_whole_as_well() -> None:
    both = Settings(line_regions=True, line_readers=True)
    read = {"regions": {"max_handwritten_on_a_page": 9}, "line_reads": {"sent": 9}}
    assert handwritten_lines(read, both) is False
    # without line-by-line reading, a page with handwritten lines still gets the whole-page pass
    assert handwritten_lines({"regions": {"max_handwritten_on_a_page": 9}},
                             Settings(line_regions=True)) is True  # fmt: skip


# ------------------------------------------------------------------ AC3
# GIVEN the answer-key set is run WHEN results are counted
# THEN line recall and label accuracy are reported by document type

_AC3_SET = ("IM-S3 AC3: the counter and the command are built, but there is no answer-key set "
            "of real pages with hand-drawn line boxes in the repository (ENT-S3). Recall and "
            "label accuracy on real handwriting cannot be reported until it exists.")  # fmt: skip


def _line(box: tuple[int, int, int, int], label: str) -> dict[str, Any]:
    return {"bbox": list(box), "label": label}


def _measure_script() -> Any:
    path = ROOT / "scripts" / "measure_regions.py"
    spec = importlib.util.spec_from_file_location("measure_regions", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_ac3_recall_is_the_share_of_drawn_lines_that_were_found() -> None:
    truth = [_line((0, 0, 100, 20), "printed"), _line((0, 40, 100, 60), "handwritten"),
             _line((0, 80, 100, 100), "handwritten")]  # fmt: skip
    found = [_line((0, 0, 100, 20), "printed"), _line((2, 41, 99, 60), "handwritten")]
    s = R.score(found, truth)
    assert s["recall"] == pytest.approx(2 / 3) and s["label_accuracy"] == 1.0


def test_ac3_sent_to_both_is_counted_apart_from_right_and_from_wrong() -> None:
    """If "uncertain" scored as correct, a finder that called everything uncertain would
    score 100 %."""
    truth = [_line((0, y, 100, y + 20), "handwritten") for y in (0, 40, 80)]
    found = [_line((0, 0, 100, 20), "handwritten"), _line((0, 40, 100, 60), "uncertain"),
             _line((0, 80, 100, 100), "printed")]  # fmt: skip
    s = R.score(found, truth)
    assert (s["label_right"], s["label_safe"], s["label_wrong"]) == (1, 1, 1)
    assert s["label_accuracy"] == pytest.approx(1 / 3)


def test_ac3_results_are_reported_by_document_type() -> None:
    page = {
        "found": [_line((0, 0, 100, 20), "printed")],
        "truth": [_line((0, 0, 100, 20), "printed"), _line((0, 40, 100, 60), "printed")],
    }
    out = R.score_by_type([{**page, "doc_type": "prescription"}, {**page, "doc_type": "lab_report"},
                           {**page, "doc_type": "prescription"}])  # fmt: skip
    assert set(out) == {"prescription", "lab_report"}
    assert out["prescription"]["pages"] == 2 and out["prescription"]["recall"] == 0.5
    assert out["lab_report"]["label_accuracy"] == 1.0


def test_ac3_the_command_runs_an_answer_key_and_prints_the_table(mixed_page, tmp_path,
                                                                 capsys) -> None:  # fmt: skip
    page, split = mixed_page
    cv2.imwrite(str(tmp_path / "rx.png"), page)
    lines = [_line(b, "printed" if b[3] <= split else "handwritten")
             for b in R.detect_lines(page)]  # fmt: skip
    key = tmp_path / "key.json"
    key.write_text(json.dumps([{"image": "rx.png", "doc_type": "prescription", "lines": lines}]))
    module = _measure_script()
    assert module.main(["--key", str(key)]) == 0
    said = capsys.readouterr().out
    assert "document type" in said and "recall %" in said and "label %" in said
    assert "prescription" in said and "100.0" in said
    assert module.main(["--key", str(key), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["prescription"]["label_wrong"] == 0


def test_ac3_a_missed_line_and_a_wrong_label_show_in_the_table(mixed_page, tmp_path,
                                                               capsys) -> None:  # fmt: skip
    """The key says the body is *printed* and draws one line that is not on the page."""
    page, split = mixed_page
    cv2.imwrite(str(tmp_path / "rx.png"), page)
    lines = [_line(b, "printed") for b in R.detect_lines(page)]
    lines.append(_line((10, page.shape[0] - 60, 400, page.shape[0] - 30), "printed"))
    key = tmp_path / "key.json"
    key.write_text(json.dumps([{"image": "rx.png", "doc_type": "prescription", "lines": lines}]))
    assert _measure_script().main(["--key", str(key), "--json"]) == 0
    out = json.loads(capsys.readouterr().out)["prescription"]
    assert out["recall"] < 1.0 and out["label_wrong"] >= len(BODY)  # handwriting vs the key


def test_ac3_a_draft_key_can_be_made_for_a_person_to_correct(mixed_page, tmp_path,
                                                             capsys) -> None:  # fmt: skip
    """The way to an answer key: the module lists what it found, a person corrects it."""
    page, _ = mixed_page
    picture = tmp_path / "rx.png"
    cv2.imwrite(str(picture), page)
    draft = tmp_path / "draft.json"
    module = _measure_script()
    assert module.main([str(picture), "--draft", str(draft), "--doc-type", "prescription"]) == 0
    (entry,) = json.loads(draft.read_text(encoding="utf-8"))
    assert entry["reviewed"] is False and entry["doc_type"] == "prescription"
    assert len(entry["lines"]) == len(R.detect_lines(page))
    assert set(entry["lines"][0]) == {"bbox", "label"}
    assert "NOT an answer key yet" in capsys.readouterr().out


def test_ac3_a_draft_nobody_has_reviewed_cannot_be_scored(mixed_page, tmp_path, capsys) -> None:
    """Scoring the module against its own draft would report 100 % and mean nothing."""
    page, _ = mixed_page
    picture = tmp_path / "rx.png"
    cv2.imwrite(str(picture), page)
    draft = tmp_path / "draft.json"
    module = _measure_script()
    module.main([str(picture), "--draft", str(draft)])
    capsys.readouterr()
    assert module.main(["--key", str(draft)]) == 2
    assert "still a draft" in capsys.readouterr().err
    reviewed = json.loads(draft.read_text(encoding="utf-8"))
    reviewed[0]["reviewed"] = True
    draft.write_text(json.dumps(reviewed), encoding="utf-8")
    assert module.main(["--key", str(draft)]) == 0  # once a person has signed it off


@pytest.mark.xfail(strict=True, reason=_AC3_SET)
def test_ac3_there_is_an_answer_key_set_of_real_pages_with_drawn_lines() -> None:
    found = []
    for path in ROOT.glob("data/**/*.json"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if '"lines"' in text and '"bbox"' in text and ".sample." not in path.name:
            found.append(path)
    assert found


# ------------------------------------------------------------------ AC4
# GIVEN lines of different heights are cut WHEN they are sent to the readers
# THEN every crop meets the crop standard and its size is recorded


@pytest.mark.parametrize("factor", [1.0, 0.6, 0.35, 0.2])
def test_ac4_lines_of_every_height_are_cut_to_the_standard(printed, factor: float) -> None:
    page = printed if factor == 1.0 else cv2.resize(printed, None, fx=factor, fy=factor,
                                                    interpolation=cv2.INTER_AREA)  # fmt: skip
    found = R.detect_regions(page)
    assert found
    for r in found:
        assert r.crop is not None and R.meets_standard(r.crop)
        assert r.crop.size[1] >= R.STANDARD.min_height_px or r.crop.scale == R.STANDARD.max_scale


def test_ac4_the_standard_is_a_minimum_height_padding_and_a_cap_on_enlarging() -> None:
    s = R.STANDARD
    assert (s.min_height_px, s.pad_fraction, s.min_pad_px, s.max_scale) == (32, 0.15, 4, 4.0)


def test_ac4_a_crop_keeps_paper_round_the_line_so_no_stroke_is_cut(printed) -> None:
    box = R.detect_lines(printed)[0]
    crop = R.crop_png(printed, box)
    px0, py0, px1, py1 = crop.padded_bbox
    assert px0 < box[0] and py0 < box[1] and px1 > box[2] and py1 > box[3]
    cut = cv2.imdecode(np.frombuffer(crop.png, np.uint8), cv2.IMREAD_GRAYSCALE)
    assert cut[0].min() > 200 and cut[-1].min() > 200  # top and bottom rows are paper


def test_ac4_a_very_small_line_is_enlarged_before_it_is_handed_over(printed) -> None:
    small = cv2.resize(printed, None, fx=0.3, fy=0.3, interpolation=cv2.INTER_AREA)
    crop = R.crop_png(small, R.detect_lines(small)[0])
    assert crop.source_size[1] < R.STANDARD.min_height_px <= crop.size[1]
    assert 1.0 < crop.scale <= R.STANDARD.max_scale


def test_ac4_the_size_of_every_crop_is_recorded(printed) -> None:
    for factor in (1.0, 0.3):
        page = printed if factor == 1.0 else cv2.resize(printed, None, fx=factor, fy=factor,
                                                        interpolation=cv2.INTER_AREA)  # fmt: skip
        for r in R.detect_regions(page):
            rec = r.crop.record()
            assert set(rec) == {"sha256", "bbox", "padded_bbox", "pad_px", "scale",
                                "source_size", "size"}  # fmt: skip
            assert rec["size"][1] == round(rec["source_size"][1] * rec["scale"])
            assert json.loads(json.dumps(rec)) == rec


def test_ac4_the_fingerprint_is_of_the_pages_own_pixels_not_of_the_enlargement(printed) -> None:
    small = cv2.resize(printed, None, fx=0.3, fy=0.3, interpolation=cv2.INTER_AREA)
    crop = R.crop_png(small, R.detect_lines(small)[0])
    x0, y0, x1, y1 = crop.padded_bbox
    pixels = np.ascontiguousarray(small[y0:y1, x0:x1])
    assert (
        crop.sha256
        == hashlib.sha256(f"{x1 - x0}x{y1 - y0}:".encode() + pixels.tobytes()).hexdigest()
    )


def test_ac4_a_line_at_the_page_edge_is_clipped_not_refused(printed) -> None:
    crop = R.crop_png(printed, (0, 0, 200, 40))
    assert crop.padded_bbox[:2] == (0, 0)
    with pytest.raises(ValueError, match="outside the page"):
        R.crop_png(printed, (5000, 5000, 5100, 5040))


def test_ac4_every_crop_sent_to_a_reader_meets_the_standard(printed) -> None:
    """In S3's own step, on lines of very different heights: every crop the handwriting reader
    is given is at least the standard's minimum height (or enlarged as far as is allowed)."""
    for factor in (1.0, 0.35, 0.2):
        page = printed if factor == 1.0 else cv2.resize(printed, None, fx=factor, fy=factor,
                                                        interpolation=cv2.INTER_AREA)  # fmt: skip
        prints = _prints(page)
        for p in prints:
            p.conf = 0.5  # every line uncertain: every line is cut and sent
        reader = _Reader()
        reads, _, _ = _read_page(page, reader, prints=prints)
        assert reads.crops and len(reads.crops) == len(reader.crops)
        for record, png in zip(reads.crops, reader.crops, strict=True):
            sent = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_GRAYSCALE)
            assert record["meets_standard"] is True
            assert (sent.shape[1], sent.shape[0]) == tuple(record["size"])  # what was really sent
            assert (
                sent.shape[0] >= R.STANDARD.min_height_px or record["scale"] == R.STANDARD.max_scale
            )


def test_ac4_the_size_of_every_crop_sent_is_recorded_with_the_run(mixed_page) -> None:
    page, _ = mixed_page
    reads, _, _ = _read_page(page, _Reader())
    for record in reads.crops:
        assert {"sha256", "bbox", "padded_bbox", "pad_px", "scale", "source_size", "size",
                "label", "read", "meets_standard"} == set(record)  # fmt: skip
        assert json.loads(json.dumps(record)) == record  # goes into the run's metrics as it is
    stage = inspect.getsource(ocr.ocr_document)
    assert 'metrics["line_crops"] = [' in stage and '"page_no": page["page_no"]' in stage
    assert 'metrics["line_reads"] = totals' in stage


def test_ac4_a_small_line_is_enlarged_before_the_reader_sees_it(printed) -> None:
    small = cv2.resize(printed, None, fx=0.3, fy=0.3, interpolation=cv2.INTER_AREA)
    prints = _prints(small)
    for p in prints:
        p.conf = 0.5
    reads, _, _ = _read_page(small, _Reader(), prints=prints)
    assert reads.crops and all(c["scale"] > 1.0 for c in reads.crops)
    assert all(c["size"][1] >= R.STANDARD.min_height_px for c in reads.crops)


def test_ac4_each_reading_is_stored_with_the_fingerprint_of_its_crop(mixed_page) -> None:
    page, _ = mixed_page
    reads, found, _ = _read_page(page, _Reader())
    by_box = {tuple(r.bbox): r.crop.sha256 for r in found}
    for line, _, fingerprint in reads.read:
        assert fingerprint == by_box[tuple(line.bbox)]
    assert '"crop_hash": crop_hash' in inspect.getsource(ocr.ocr_document)


def test_ac4_crops_are_cut_only_when_lines_are_read_one_by_one() -> None:
    """Printed-only pages must not get slower: with line-by-line reading off, no crop is cut."""
    assert "found.append(page_regions(png, lines, crops=by_line))" in inspect.getsource(
        ocr.ocr_document
    )
    assert inspect.signature(ocr.page_regions).parameters["crops"].default is False


def test_ac4_the_crop_records_are_kept_out_of_the_audit_row_and_the_log() -> None:
    """Dozens of crop records per page belong with the run, not in every audit line."""
    stage = inspect.getsource(ocr.ocr_document)
    assert 'brief = {k: v for k, v in metrics.items() if k != "line_crops"}' in stage
