"""IM-S1 acceptance: check every picture first (blur, glare, dark, small, sideways).

One group of tests per acceptance criterion, written on 2026-10-08 to record what the code does
today. **No application code was changed for this file.**

How they run: the real picture check (``recognition/quality.py``) on pictures built here - a
rendered prescription, a blurred copy, and a large "taken from far away" frame with the same
page small in the middle. "Saved" is proved by running S1's own render-and-store step with the
database and the object store replaced by recorders, so what is checked is exactly what S1
would write; that the row really lands in PostgreSQL is
``tests/test_recognition_integration.py`` (not run on this box).

The first check (the same day) found AC3 unbuilt, AC2 off by default and a defect in AC4 (a
far-away prescription was given the wrong reason). All three were then dealt with. One test is
still a strict expected failure: there is no answer-key set of **real photographs** in the
repository, and no code can supply one.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")

from cdi_adapter.config import Settings, get_settings  # noqa: E402
from cdi_adapter.ingest import pages, service  # noqa: E402
from cdi_adapter.recognition import quality  # noqa: E402
from tests.helpers import make_pdf  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
MEASURES = {"extent_px", "width_px", "height_px", "sharpness", "ink_fraction",
            "soft_ink_fraction", "glare_fraction", "paper_tone", "text_height_px",
            "rotated_90_suspected"}  # fmt: skip
ON = Settings(quality_check=True)


# ------------------------------------------------------------------ the pictures


@pytest.fixture(scope="module")
def sharp() -> bytes:
    """A rendered prescription: twelve lines of text."""
    lines = [f"Line {i}: Tab Amoxicillin 500mg 1-0-1 x5 days" for i in range(12)]
    return pages.render_pages(make_pdf([lines]), "application/pdf")[0]


def _gray(png: bytes) -> Any:
    return cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_GRAYSCALE)


def _png(arr: Any) -> bytes:
    return cv2.imencode(".png", arr)[1].tobytes()


@pytest.fixture(scope="module")
def blurred(sharp) -> bytes:
    return _png(cv2.GaussianBlur(_gray(sharp), (25, 25), 0))


def _far(page_png: bytes, scale: float = 0.2) -> bytes:
    """A 12-megapixel frame (4000 x 3000) with the page a fifth of its size in the middle: the
    camera was held far from the paper. The file is large; only the writing is tiny."""
    small = cv2.resize(_gray(page_png), None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    frame = np.full((3000, 4000), 255, np.uint8)
    y, x = (3000 - small.shape[0]) // 2, (4000 - small.shape[1]) // 2
    frame[y : y + small.shape[0], x : x + small.shape[1]] = small
    return _png(frame)


@pytest.fixture(scope="module")
def far_away(sharp) -> bytes:
    """The twelve-line prescription, far away. A prescription is a sparse page like this one."""
    return _far(sharp)


@pytest.fixture(scope="module")
def far_away_dense() -> bytes:
    """A page full of writing (forty long lines), far away."""
    lines = [f"Line {i}: Tab Amoxicillin 500mg 1-0-1 x5 days after food for fever and cough"
             for i in range(40)]  # fmt: skip
    return _far(pages.render_pages(make_pdf([lines]), "application/pdf")[0])


# ------------------------------------------------------------------ S1's store step, recorded


class _Store:
    def __init__(self) -> None:
        self.keys: list[str] = []

    def put_bytes(self, key: str, raw: bytes, mime: str) -> None:
        self.keys.append(key)


@pytest.fixture
def saved(monkeypatch) -> list[dict[str, Any]]:
    """Run S1's render-and-store with the database replaced: every page row it would insert is
    kept here."""
    rows: list[dict[str, Any]] = []

    @contextmanager
    def scope() -> Any:
        yield "conn"

    monkeypatch.setattr(service, "session_scope", scope)
    monkeypatch.setattr(service.repo, "insert_document_page",
                        lambda conn, **row: rows.append(row))  # fmt: skip
    return rows


def _store_page(png: bytes) -> _Store:
    store = _Store()
    assert service._render_and_store("doc-1", "a" * 64, png, "image/png", store) == 1  # type: ignore[arg-type]
    return store


# ------------------------------------------------------------------ AC1
# GIVEN a sharp page WHEN it is checked THEN it passes and all measurements are saved


def test_ac1_a_sharp_page_passes(sharp) -> None:
    verdict = quality.assess(sharp, "image/png", settings=ON)
    assert verdict.ok and verdict.code is None and verdict.as_json() is None


def test_ac1_every_measurement_is_taken(sharp) -> None:
    m = quality.measure(sharp)
    assert MEASURES <= set(m), MEASURES - set(m)
    assert m["sharpness"] > 20 and m["text_height_px"] >= 8 and m["extent_px"] >= 700


def test_ac1_the_measurements_are_saved_with_the_page(saved, sharp) -> None:
    """What S1 writes for the page: the measurements, the verdict and the limits used."""
    _store_page(sharp)
    (row,) = saved
    kept = row["preproc"]["quality"]
    assert kept["passed"] is True and kept["code"] is None
    assert MEASURES <= set(kept["measures"])
    assert kept["measures"]["sharpness"] == quality.measure(sharp)["sharpness"]
    assert row["page_no"] == 1 and row["document_id"] == "doc-1"


def test_ac1_what_is_saved_is_plain_data_the_database_can_hold(saved, sharp) -> None:
    _store_page(sharp)
    kept = saved[0]["preproc"]["quality"]
    assert json.loads(json.dumps(kept)) == kept  # goes into a jsonb column as it is


def test_ac1_the_measurements_are_of_the_page_as_uploaded(saved, sharp) -> None:
    """Measured before S1's own straightening and clean-up, so the numbers describe what the
    person sent, not what the pipeline made of it."""
    import inspect

    source = inspect.getsource(service._render_and_store)
    assert 'preproc = {**preproc, "quality": quality.record(png)}' in source
    assert source.index("pages.normalize_image(png)") < source.index("quality.record(png)")


def test_ac1_measurements_are_saved_even_while_holding_is_switched_off(
    saved, sharp, monkeypatch
) -> None:
    """Saving does not depend on the switch; only *holding* a page does."""
    monkeypatch.setenv("CDI_QUALITY_CHECK", "false")
    get_settings.cache_clear()
    try:
        _store_page(sharp)
    finally:
        get_settings.cache_clear()
    kept = saved[0]["preproc"]["quality"]
    assert kept["enforced"] is False and MEASURES <= set(kept["measures"])


def test_ac1_a_page_that_fails_is_saved_too(saved, blurred) -> None:
    """Pass or fail: a rejected page's numbers are what the tuning needs most."""
    store = _store_page(blurred)
    kept = saved[0]["preproc"]["quality"]
    assert kept["passed"] is False and kept["code"] == "blurred"
    assert len(store.keys) == 2  # and the page itself is still stored (page + thumbnail)


# ------------------------------------------------------------------ AC2
# GIVEN a blurred page WHEN it is checked THEN it is held with reason 'too blurry'


def test_ac2_a_blurred_page_is_held(blurred) -> None:
    verdict = quality.assess(blurred, "image/png", settings=ON)
    assert verdict.ok is False and verdict.code == "blurred"


def test_ac2_the_reason_given_is_too_blurry(blurred) -> None:
    held = quality.assess(blurred, "image/png", settings=ON).as_json()
    assert held is not None and held["code"] == "blurred"
    assert "too blurry" in held["message"]
    assert held["message"] == quality.REASONS["blurred"]
    assert held["measures"]["sharpness"] < Settings().quality_min_sharpness


@pytest.mark.parametrize("kernel", [9, 15, 25, 41])
def test_ac2_it_is_called_blurry_however_strong_the_blur(sharp, kernel: int) -> None:
    """A strong blur leaves no dark pixel at all; it must still be "blurred", not "blank"."""
    png = _png(cv2.GaussianBlur(_gray(sharp), (kernel, kernel), 0))
    assert quality.assess(png, "image/png", settings=ON).code == "blurred"


def test_ac2_the_same_page_sharp_is_not_held(sharp) -> None:
    assert quality.assess(sharp, "image/png", settings=ON).ok


def test_ac2_the_upload_job_holds_it_before_anything_is_read() -> None:
    import inspect

    from cdi_adapter.webapp import jobs

    phase1 = inspect.getsource(jobs.JobRunner._phase1)
    assert 'd.status, d.step, d.quality = "quality_hold", None, held' in phase1
    assert phase1.index("quality.assess(") < phase1.index("s.ingest(")


def test_ac2_holding_is_on_by_default(blurred) -> None:
    """IM-S1 AC2, the gap the first check found: with no setting at all a blurred page is held."""
    default = Settings()
    assert default.quality_check is True
    held = quality.assess(blurred, "image/png", settings=default).as_json()
    assert held is not None and held["code"] == "blurred" and "too blurry" in held["message"]
    assert quality.record(blurred, settings=default)["enforced"] is True


def test_ac2_it_can_still_be_switched_off(blurred) -> None:
    """``CDI_QUALITY_CHECK=false``: the page is measured and recorded as blurred, and goes on."""
    off = Settings(quality_check=False)
    assert quality.assess(blurred, "image/png", settings=off).ok  # not held
    rec = quality.record(blurred, settings=off)
    assert rec["passed"] is False and rec["code"] == "blurred" and rec["enforced"] is False


def test_ac2_the_default_is_stated_where_an_operator_will_read_it() -> None:
    config = (ROOT / "src" / "cdi_adapter" / "config.py").read_text(encoding="utf-8")
    assert "quality_check: bool = True" in config and "CDI_QUALITY_CHECK=false" in config


# ------------------------------------------------------------------ AC3
# GIVEN the answer-key set is run WHEN results are counted
# THEN the false-reject rate is reported with the thresholds used

_AC3_SET = ("IM-S1 AC3: the runner and the report are built, but there is still no answer-key "
            "set of REAL photographs in the repository (ENT-S3). A false-reject rate from "
            "rendered pages or clean scans says nothing about phone photographs; only labelled "
            "real ones can give the number this criterion is for.")  # fmt: skip


@pytest.fixture
def answer_key(tmp_path, sharp, blurred, far_away) -> Path:
    """A small answer key, as a person would write it: which pictures can be read."""
    crisp = cv2.GaussianBlur(_gray(sharp), (5, 5), 0)  # a little soft, still plainly readable
    pictures = {
        "rx_sharp.png": (sharp, True, "prescription"),
        "rx_soft.png": (_png(crisp), True, "prescription"),
        "lab_sharp.png": (sharp, True, "lab_report"),
        "rx_blurred.png": (blurred, False, "prescription"),
        "rx_far_away.png": (far_away, False, "prescription"),
    }
    entries = []
    for name, (png, readable, doc_type) in pictures.items():
        (tmp_path / name).write_bytes(png)
        entries.append({"image": name, "readable": readable, "doc_type": doc_type})
    key = tmp_path / "answer_key.json"
    key.write_text(json.dumps(entries), encoding="utf-8")
    return key


def _evaluate(key: Path, **limits: Any) -> dict[str, Any]:
    from cdi_adapter.recognition import quality_eval

    return quality_eval.evaluate(json.loads(key.read_text(encoding="utf-8")), root=key.parent,
                                 settings=Settings(**limits))  # fmt: skip


def test_ac3_every_saved_record_carries_the_thresholds_it_was_judged_with(sharp) -> None:
    rec = quality.record(sharp, settings=ON)
    assert rec["limits"] == quality.limits(ON)
    assert set(rec["limits"]) == {"min_side_px", "min_sharpness", "min_ink", "max_ink",
                                  "min_text_px", "max_glare", "paper_white",
                                  "min_writing_sharpness", "min_stroke_sharpness",
                                  "floor_side_px"}  # fmt: skip


def test_ac3_a_page_can_be_rejudged_under_other_thresholds_from_what_was_saved(sharp) -> None:
    rec = quality.record(sharp, settings=ON)
    # both sharpness limits: since 2026-10-09 a page is "blurred" only when the whole frame
    # AND the strokes themselves are under their limits
    strict = {**rec["limits"], "min_sharpness": rec["measures"]["sharpness"] + 1,
              "min_stroke_sharpness": rec["measures"]["stroke_sharpness"] + 1}  # fmt: skip
    assert quality.verdict(rec["measures"], **rec["limits"]).ok
    assert quality.verdict(rec["measures"], **strict).code == "blurred"


def test_ac3_the_answer_key_run_reports_the_false_reject_rate(answer_key) -> None:
    """Three readable pictures and two unreadable ones, under the default limits: none of the
    readable ones is held, and both unreadable ones are."""
    out = _evaluate(answer_key)
    assert out["pictures"] == 5 and out["readable"] == 3 and out["unreadable"] == 2
    assert out["false_rejects"] == 0 and out["false_reject_rate"] == 0.0
    assert out["false_accepts"] == 0 and out["false_accept_rate"] == 0.0


def test_ac3_the_report_states_the_thresholds_used(answer_key) -> None:
    out = _evaluate(answer_key)
    assert out["thresholds"] == quality.limits(Settings())
    moved = _evaluate(answer_key, quality_min_sharpness=5000.0, quality_min_stroke_sharpness=5000.0)
    assert moved["thresholds"]["min_sharpness"] == 5000.0  # the run says what it was judged with


def test_ac3_a_limit_set_too_high_shows_up_as_false_rejects(answer_key) -> None:
    """The point of the number: raise the sharpness limit until good pictures are turned away,
    and the rate, the reason and the pictures are all in the report."""
    out = _evaluate(answer_key, quality_min_sharpness=5000.0, quality_min_stroke_sharpness=5000.0)
    assert out["false_rejects"] == 3 and out["false_reject_rate"] == 1.0
    assert out["false_reject_reasons"] == [{"code": "blurred", "count": 3}]
    assert [r["image"] for r in out["false_rejected"]] == ["rx_sharp.png", "rx_soft.png",
                                                           "lab_sharp.png"]  # fmt: skip


def test_ac3_a_limit_set_too_low_shows_up_as_false_accepts(answer_key) -> None:
    """Reported beside it, because a false-reject rate of zero is easy: accept everything."""
    out = _evaluate(answer_key, quality_min_sharpness=0.0, quality_min_text_px=0.0,
                    quality_min_ink_fraction=0.0)  # fmt: skip
    assert out["false_reject_rate"] == 0.0
    assert out["false_accepts"] >= 1 and out["false_accept_rate"] > 0


def test_ac3_the_rate_is_given_by_document_type(answer_key) -> None:
    out = _evaluate(answer_key, quality_min_sharpness=5000.0, quality_min_stroke_sharpness=5000.0)
    assert set(out["by_doc_type"]) == {"prescription", "lab_report"}
    assert out["by_doc_type"]["lab_report"]["false_reject_rate"] == 1.0
    assert out["by_doc_type"]["prescription"]["readable"] == 2


def test_ac3_the_run_judges_even_where_holding_is_switched_off(answer_key) -> None:
    out = _evaluate(answer_key, quality_check=False)
    assert out["false_accepts"] == 0  # the blurred and far-away pictures were still judged


def test_ac3_the_counting_is_right_on_its_own() -> None:
    from cdi_adapter.recognition import quality_eval

    rows = [{"image": f"r{i}", "readable": True, "doc_type": "rx", "held": i < 2,
             "code": "blurred" if i == 0 else ("too_small" if i == 1 else None)}
            for i in range(8)]  # fmt: skip
    rows += [
        {"image": "u0", "readable": False, "doc_type": "rx", "held": True, "code": "blank"},
        {"image": "u1", "readable": False, "doc_type": "rx", "held": False, "code": None},
    ]
    out = quality_eval.summarise(rows, {"min_sharpness": 20.0})
    assert (out["readable"], out["false_rejects"], out["false_reject_rate"]) == (8, 2, 0.25)
    assert (out["unreadable"], out["false_accepts"], out["false_accept_rate"]) == (2, 1, 0.5)
    assert out["false_accepted"] == ["u1"] and out["thresholds"] == {"min_sharpness": 20.0}
    empty = quality_eval.summarise([], {})
    assert empty["false_reject_rate"] is None and empty["false_accept_rate"] is None


def test_ac3_there_is_a_command_that_runs_the_check_over_an_answer_key(answer_key, capsys) -> None:
    from cdi_adapter.recognition import quality_eval

    assert (ROOT / "scripts" / "quality_answer_key.py").is_file()
    assert quality_eval.main(["--key", str(answer_key)]) == 0
    said = capsys.readouterr().out
    assert "FALSE-REJECT RATE          0.0 %" in said and "Thresholds used" in said
    assert "min_sharpness" in said and "By document type" in said
    assert quality_eval.main(["--key", str(answer_key), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["false_reject_rate"] == 0.0


def test_ac3_the_report_names_the_pictures_it_wrongly_held(answer_key) -> None:
    from cdi_adapter.recognition import quality_eval

    said = quality_eval.as_text(
        _evaluate(answer_key, quality_min_sharpness=5000.0, quality_min_stroke_sharpness=5000.0)
    )
    assert "FALSE-REJECT RATE          100.0 %" in said
    assert "Why readable pictures were held" in said and "rx_sharp.png  (blurred)" in said


@pytest.mark.xfail(strict=True, reason=_AC3_SET)
def test_ac3_there_is_an_answer_key_set_of_real_photographs_to_run() -> None:
    """A labelled set of real phone photographs, checked in or mounted under ``data/``. The
    sample key beside the sample scans does not count: three clean scans are not photographs."""
    found = [p for p in ROOT.glob("data/**/*") if p.is_file() and "answer" in p.name.lower()
             and p.suffix in (".json", ".csv") and ".sample." not in p.name]  # fmt: skip
    assert found


# ------------------------------------------------------------------ AC4
# GIVEN a high-resolution photo taken from far away so the text is tiny WHEN it is checked
# THEN it is held with 'move closer' even though the file is large


def test_ac4_the_far_away_photos_really_are_large(far_away, far_away_dense) -> None:
    """The case the AC describes: nothing is wrong with the size of these files."""
    for png in (far_away, far_away_dense):
        m = quality.measure(png)
        assert (m["width_px"], m["height_px"]) == (4000, 3000)  # 12 megapixels
        assert m["extent_px"] >= Settings().quality_min_side_px  # not "too small"
        assert 0 < m["text_height_px"] < Settings().quality_min_text_px  # and the writing is tiny


def test_ac4_the_tiny_writing_is_measured(far_away, sharp) -> None:
    """Text height is measured on its own, and it is right: 6 px far away, 23 px close (the
    upper quartile of the letters; it was the median of every dark blob, 4 and 19)."""
    assert quality.measure(far_away)["text_height_px"] <= 6
    assert quality.measure(sharp)["text_height_px"] >= 12


def test_ac4_a_far_away_photo_is_never_let_through(far_away, far_away_dense) -> None:
    """Whatever the reason given, the picture does not go on to be misread."""
    for png in (far_away, far_away_dense):
        assert quality.assess(png, "image/png", settings=ON).ok is False


def test_ac4_a_page_full_of_writing_is_told_to_move_closer(far_away_dense) -> None:
    held = quality.assess(far_away_dense, "image/png", settings=ON).as_json()
    assert held is not None and held["code"] == "text_too_small"
    assert "Move closer" in held["message"] and "too small to read" in held["message"]


def test_ac4_sharpness_alone_would_have_let_it_through(far_away_dense, sharp) -> None:
    """Why text height is measured at all: shrinking a page *raises* the sharpness number, so
    without the text rule this large, sharp, unreadable picture passes."""
    small = cv2.resize(_gray(sharp), None, fx=0.2, fy=0.2, interpolation=cv2.INTER_AREA)
    assert quality.measure(_png(small))["sharpness"] > quality.measure(sharp)["sharpness"]
    no_text_rule = {**quality.limits(ON), "min_text_px": 0.0}
    assert quality.verdict(quality.measure(far_away_dense), **no_text_rule).ok


def test_ac4_the_same_page_filling_the_frame_is_not_held(sharp) -> None:
    assert quality.assess(sharp, "image/png", settings=ON).ok


def test_ac4_the_limit_is_a_setting() -> None:
    assert Settings().quality_min_text_px == 10.0  # 8 until the measure changed, 2026-10-09
    lenient = Settings(quality_check=True, quality_min_text_px=0)
    assert "quality_min_text_px" in Settings.model_fields and lenient.quality_min_text_px == 0


def test_ac4_the_tiny_text_reading_is_saved_like_any_other(saved, far_away_dense) -> None:
    _store_page(far_away_dense)
    kept = saved[0]["preproc"]["quality"]
    assert kept["code"] == "text_too_small" and kept["measures"]["text_height_px"] < 8


def test_ac4_a_prescription_photographed_from_far_away_is_told_to_move_closer(far_away) -> None:
    """The defect the first check found: this sparse page used to be called "blank"."""
    held = quality.assess(far_away, "image/png", settings=ON).as_json()
    assert held is not None and held["code"] == "text_too_small"
    assert "Move closer" in held["message"]


@pytest.mark.parametrize("scale", [0.15, 0.2, 0.25, 0.3])
def test_ac4_it_is_told_to_move_closer_at_every_distance(sharp, scale: float) -> None:
    """From a seventh to a third of the frame: the writing is under the limit every time, and
    every time the advice is the same. (It used to be "blank" at the first two and "blurred"
    after.)"""
    verdict = quality.assess(_far(sharp, scale), "image/png", settings=ON)
    assert verdict.measures["text_height_px"] < Settings().quality_min_text_px
    assert verdict.code == "text_too_small"


def test_ac4_the_picture_is_measured_where_the_writing_is(far_away, sharp) -> None:
    """What the fix rests on: over the whole frame this picture has too little ink and detail
    to count, but inside the box that holds the writing it is plainly sharp."""
    m = quality.measure(far_away)
    assert m["ink_fraction"] < Settings().quality_min_ink_fraction  # "blank", by the frame
    assert m["writing_sharpness"] >= Settings().quality_min_writing_sharpness  # sharp, in fact
    assert m["writing_share"] < 0.02  # the writing is a sliver of the frame
    assert quality.measure(sharp)["writing_sharpness"] >= Settings().quality_min_writing_sharpness


def test_ac4_a_far_away_photo_that_is_also_blurred_is_still_called_blurry(far_away) -> None:
    """ "Move closer" is only said when the writing is sharp. Blurred, the fault is focus."""
    soft = _png(cv2.GaussianBlur(_gray(far_away), (9, 9), 0))
    verdict = quality.assess(soft, "image/png", settings=ON)
    assert verdict.ok is False and verdict.code in ("blurred", "blank")
    assert verdict.measures["writing_sharpness"] < Settings().quality_min_writing_sharpness


@pytest.mark.parametrize("kernel", [9, 15, 25, 41])
def test_ac4_a_blurred_close_up_page_is_never_told_to_move_closer(sharp, kernel: int) -> None:
    """The other side of the fix: blur erodes the strokes and the text *measures* small, but it
    is not sharp where the writing is, so the advice stays "hold still"."""
    png = _png(cv2.GaussianBlur(_gray(sharp), (kernel, kernel), 0))
    assert quality.assess(png, "image/png", settings=ON).code == "blurred"


def test_ac4_a_blank_page_is_still_blank() -> None:
    blank = _png(np.full((2339, 1653), 255, np.uint8))
    assert quality.assess(blank, "image/png", settings=ON).code == "blank"


def test_ac4_a_page_with_only_a_line_or_two_is_not_told_to_move_closer() -> None:
    """Little writing is not far-away writing: the letters are full size."""
    two = pages.render_pages(make_pdf([["Tab Metformin 500 mg", "OD"]]), "application/pdf")[0]
    assert quality.assess(two, "image/png", settings=ON).code != "text_too_small"


def test_ac4_the_new_limit_is_a_setting_and_is_saved_with_every_record(sharp) -> None:
    assert Settings().quality_min_writing_sharpness == 500.0
    assert quality.record(sharp, settings=ON)["limits"]["min_writing_sharpness"] == 500.0
    assert {"writing_sharpness", "writing_share"} <= set(quality.measure(sharp))
