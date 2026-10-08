"""IM-S1: check every picture first (blur, glare, dark, small, sideways, tiny text).

Every limit this story sets is a guess until it is measured on real photographs - "a readable
picture must not be rejected" is its own rule, and the false-reject rate belongs to the answer-key
set (ENT-S3). So these tests do not defend the numbers. They pin three other things:

* **each measurement responds to the fault it is named for**, on a real rendered page and on
  adversarial copies of it (the v3 list: tiny text, rotated, over-compressed, glare, partial page);
* **the order of judgement** tells the person what to change;
* **every measurement is recorded, pass or fail**, with the limits it was judged against.

The pictures are built here from a rendered prescription, not loaded from files, so the suite
needs no fixture images and says exactly what each one is.
"""

from __future__ import annotations

from typing import Any

import pytest

from cdi_adapter.config import Settings
from cdi_adapter.recognition import quality

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")

LIMITS: dict[str, Any] = {"min_side_px": 700, "min_sharpness": 20.0, "min_ink": 0.0005,
                          "max_ink": 0.90, "min_text_px": 8.0, "max_glare": 0.05,
                          "paper_white": 250.0, "min_writing_sharpness": 500.0}  # fmt: skip


# ------------------------------------------------------------------ the pictures


@pytest.fixture(scope="module")
def page() -> Any:
    """A rendered prescription: twelve lines of text on white, as a grey image array."""
    from cdi_adapter.ingest.pages import render_pages
    from tests.helpers import make_pdf

    lines = [f"Line {i}: Tab Amoxicillin 500mg 1-0-1 x5 days" for i in range(12)]
    png = render_pages(make_pdf([lines]), "application/pdf")[0]
    return cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_GRAYSCALE)


def _png(arr: Any) -> bytes:
    return cv2.imencode(".png", arr)[1].tobytes()


def _photo(arr: Any, paper: float = 212.0) -> Any:
    """The same page as a phone would photograph it: grey paper, a lighting gradient, sensor
    noise. A render has a pure-white background, which no photograph of paper has."""
    rng = np.random.default_rng(7)
    grad = np.linspace(-10, 10, arr.shape[1], dtype=np.float32)[None, :]
    out = arr.astype(np.float32) / 255.0 * paper + grad
    out += rng.normal(0, 3.0, arr.shape).astype(np.float32)
    return np.clip(out, 0, 255).astype(np.uint8)


def _glare(arr: Any, share: int) -> Any:
    """A burnt-out patch over the middle of the page; a larger ``share`` is a smaller patch."""
    out = arr.copy()
    h, w = arr.shape
    cv2.ellipse(out, (w // 2, h // 2), (w // share, h // (share * 2)), 0, 0, 360, 255, -1)
    return out


def _m(arr: Any) -> dict[str, Any]:
    return quality.measure(_png(arr))


def _v(arr: Any) -> quality.Verdict:
    return quality.verdict(_m(arr), **LIMITS)


# ------------------------------------------------------------------ AC1: a sharp page passes


def test_a_sharp_page_passes_and_every_measurement_is_there(page: Any) -> None:
    m = _m(page)
    assert quality.verdict(m, **LIMITS).ok
    assert set(m) >= {"extent_px", "sharpness", "ink_fraction", "glare_fraction", "paper_tone",
                      "text_height_px", "blank_rows", "blank_cols",
                      "rotated_90_suspected"}  # fmt: skip


def test_a_sharp_photograph_passes_too(page: Any) -> None:
    """The page as a phone sees it - grey paper, uneven light, noise - is still a good picture.
    This is the case the story is about; a render is only the easy one."""
    assert _v(_photo(page)).ok


def test_the_default_limits_accept_a_real_page(page: Any) -> None:
    """Not only the limits this file passes in: the shipped defaults must not reject a good page."""
    s = Settings(quality_check=True)
    assert quality.assess_png(_png(page), settings=s).ok
    assert quality.assess_png(_png(_photo(page)), settings=s).ok


# ------------------------------------------------------------------ AC2: a blurred page is held


def test_a_blurred_page_is_held_as_too_blurry(page: Any) -> None:
    v = _v(cv2.GaussianBlur(page, (9, 9), 0))
    assert not v.ok and v.code == "blurred"
    assert "blurry" in (v.message or "")


def test_sharpness_falls_as_the_blur_grows(page: Any) -> None:
    sharp = _m(page)["sharpness"]
    soft = _m(cv2.GaussianBlur(page, (9, 9), 0))["sharpness"]
    softer = _m(cv2.GaussianBlur(page, (25, 25), 0))["sharpness"]
    assert sharp > soft * 10 and soft > softer


# ------------------------------------------------------------------ AC4 (v3): tiny text


def test_a_far_away_photo_is_sharp_but_its_text_is_too_small(page: Any) -> None:
    """The v3 case. Shrinking the page makes it *sharper* by the Laplacian - so blur alone would
    pass it - while the letters fall below what can be read. Text height is its own check."""
    tiny = cv2.resize(page, (page.shape[1] // 5, page.shape[0] // 5))
    near, far = _m(page), _m(tiny)
    assert far["sharpness"] > near["sharpness"]  # sharper, and still unreadable
    assert near["text_height_px"] >= 12 and far["text_height_px"] <= 6
    # a file that small is caught by its extent first; judged on text alone it is still held
    v = quality.verdict({**far, "extent_px": 2000.0}, **LIMITS)
    assert not v.ok and v.code == "text_too_small"
    assert "closer" in (v.message or "")


def test_text_height_is_not_judged_when_the_limit_is_zero(page: Any) -> None:
    tiny = {**_m(page), "text_height_px": 2.0}
    assert quality.verdict(tiny, **{**LIMITS, "min_text_px": 0.0}).ok


def test_a_page_with_no_marks_has_no_text_height_to_judge() -> None:
    """A blank page is reported as blank, not as tiny text."""
    blank = np.full((1200, 900), 255, np.uint8)
    assert _m(blank)["text_height_px"] == 0.0
    assert _v(blank).code == "blank"


# ------------------------------------------------------------------ glare (photographs only)


def test_glare_reads_zero_on_a_good_photo_and_grows_with_the_patch(page: Any) -> None:
    photo = _photo(page)
    none = _m(photo)["glare_fraction"]
    small = _m(_glare(photo, 8))["glare_fraction"]
    big = _m(_glare(photo, 3))["glare_fraction"]
    assert none < 0.001 < small < big
    assert big > 0.10


def test_a_photo_with_a_large_burnt_out_patch_is_held_for_glare(page: Any) -> None:
    v = _v(_glare(_photo(page), 3))
    assert not v.ok and v.code == "glare"
    assert "reflecting" in (v.message or "")


def test_a_small_reflection_inside_the_limit_is_let_through(page: Any) -> None:
    """A readable picture must not be rejected: a small highlight is not a reason to retake."""
    assert _v(_glare(_photo(page), 8)).ok


def test_glare_is_not_judged_where_the_paper_is_already_white(page: Any) -> None:
    """A render or a clean flatbed scan is ~98 % white by construction. That is its paper, not a
    reflection - so the same number that means glare on a photo means nothing here."""
    m = _m(page)
    assert m["paper_tone"] >= 250 and m["glare_fraction"] > 0.9
    assert quality.verdict(m, **LIMITS).ok


# ------------------------------------------------------------------ dark, blank, small


def test_a_page_photographed_in_the_dark_is_held(page: Any) -> None:
    v = _v((page * 0.10).astype(np.uint8))
    assert not v.ok and v.code == "too_dark"


def test_a_blank_page_is_held_as_no_writing_found() -> None:
    v = _v(np.full((1600, 1200), 255, np.uint8))
    assert not v.ok and v.code == "blank"


def test_a_thumbnail_is_held_as_too_small(page: Any) -> None:
    v = _v(cv2.resize(page, (320, 420)))
    assert not v.ok and v.code == "too_small"


def test_something_that_is_not_a_picture_is_a_verdict_not_a_crash() -> None:
    m = quality.measure(b"definitely not a png")
    assert m["extent_px"] == 0.0
    assert quality.verdict(m, **LIMITS).code == "unreadable"


# ------------------------------------------------------------------ sideways: a flag, not a hold


def test_an_upright_page_is_not_flagged_sideways(page: Any) -> None:
    m = _m(page)
    assert m["blank_rows"] > m["blank_cols"] * 2  # the gaps between lines are blank ROWS
    assert m["rotated_90_suspected"] is False


def test_a_page_turned_a_quarter_is_flagged(page: Any) -> None:
    m = _m(np.rot90(page).copy())
    assert m["blank_cols"] > m["blank_rows"] * 2  # the same gaps are now blank COLUMNS
    assert m["rotated_90_suspected"] is True


def test_a_sideways_page_is_flagged_but_never_held(page: Any) -> None:
    """Turning a page is IM-S2's work. A sideways page is readable once turned, so IM-S1 records
    the flag and holds nothing."""
    v = _v(np.rot90(page).copy())
    assert v.ok and v.measures["rotated_90_suspected"] is True


def test_an_upside_down_page_looks_upright_by_this_measure(page: Any) -> None:
    """Said plainly so nobody relies on it: a half turn leaves the gaps where they were, so this
    flag cannot see it. Finding an upside-down page is IM-S2."""
    assert _m(np.rot90(page, 2).copy())["rotated_90_suspected"] is False


def test_a_tilted_page_is_not_called_sideways(page: Any) -> None:
    """A tilt smears both kinds of gap away. With no clear evidence the flag stays down rather
    than guessing - the deskew in S1 handles a tilt."""
    h, w = page.shape
    rot = cv2.getRotationMatrix2D((w / 2, h / 2), 8, 1)
    tilted = cv2.warpAffine(page, rot, (w, h), borderValue=255)
    assert _m(tilted)["rotated_90_suspected"] is False


def test_the_sideways_rule_needs_clear_evidence() -> None:
    assert quality.rotated_90(blank_rows=0.13, blank_cols=0.47) is True
    assert quality.rotated_90(blank_rows=0.47, blank_cols=0.13) is False
    assert quality.rotated_90(blank_rows=0.0, blank_cols=0.0) is False  # a tilt: no evidence
    assert quality.rotated_90(blank_rows=0.20, blank_cols=0.25) is False  # too close to call


# ------------------------------------------------------------------ the rest of the v3 list


def test_a_heavily_compressed_page_is_still_readable_and_passes(page: Any) -> None:
    """Over-compression softens edges but leaves the text its size. It must not be rejected."""
    jpg = cv2.imdecode(cv2.imencode(".jpg", page, [cv2.IMWRITE_JPEG_QUALITY, 12])[1],
                       cv2.IMREAD_GRAYSCALE)  # fmt: skip
    m = _m(jpg)
    assert abs(m["text_height_px"] - _m(page)["text_height_px"]) <= 2
    assert quality.verdict(m, **LIMITS).ok


def test_a_partial_page_is_not_a_quality_fault(page: Any) -> None:
    """The top third of a page is a sharp, well-lit picture of part of a document. Whether the
    whole page is in frame is a question of its edges (IM-S2), not of picture quality."""
    assert _v(page[: page.shape[0] // 3, :]).ok


# ------------------------------------------------------------------ the order of judgement


@pytest.mark.parametrize(("measures", "code"), [
    ({"extent_px": 0.0}, "unreadable"),
    ({"extent_px": 300.0, "ink_fraction": 1.0, "sharpness": 0.0}, "too_small"),
    ({"extent_px": 1600.0, "ink_fraction": 0.99, "sharpness": 0.0}, "too_dark"),
    ({"extent_px": 1600.0, "ink_fraction": 0.0, "sharpness": 0.0}, "blank"),
    ({"extent_px": 1600.0, "ink_fraction": 0.02, "sharpness": 0.0, "paper_tone": 210.0,
      "glare_fraction": 0.4, "text_height_px": 2.0}, "glare"),
    # blur is judged before text height: a soft focus erodes strokes to specks, so on a blurred
    # page the text height is not a measurement worth acting on
    ({"extent_px": 1600.0, "ink_fraction": 0.02, "sharpness": 1.0, "text_height_px": 3.0},
     "blurred"),
    ({"extent_px": 1600.0, "ink_fraction": 0.02, "sharpness": 900.0, "text_height_px": 3.0},
     "text_too_small"),
])  # fmt: skip
def test_the_person_is_told_the_first_thing_to_change(measures: dict[str, Any],
                                                      code: str) -> None:  # fmt: skip
    """Several of these pictures are also blurry. Each is reported as what the person can fix."""
    assert quality.verdict(measures, **LIMITS).code == code


def test_every_reason_has_a_plain_sentence_with_no_numbers() -> None:
    for code, sentence in quality.REASONS.items():
        assert sentence.endswith("."), code
        assert not any(ch.isdigit() for ch in sentence), code  # no thresholds shown to the desk
        assert "again" in sentence or "device" in sentence, code  # it says what to do


def test_the_reason_codes_up_s2_shows_are_all_named() -> None:
    assert set(quality.REASONS) == {"blurred", "too_dark", "glare", "blank", "text_too_small",
                                    "too_small", "unreadable"}  # fmt: skip


# ------------------------------------------------------------------ recorded, pass or fail


def test_a_passing_page_is_recorded_with_its_measurements_and_limits(page: Any) -> None:
    """IM-S1 functional 3 and AC1: every measurement, for every page, pass or fail."""
    rec = quality.record(_png(page), settings=Settings())
    assert rec["passed"] is True and rec["code"] is None
    assert rec["measures"]["text_height_px"] > 0
    assert rec["limits"]["min_sharpness"] == Settings().quality_min_sharpness
    assert rec["enforced"] is True  # on by default since 2026-10-08
    off = quality.record(_png(page), settings=Settings(quality_check=False))
    assert off["enforced"] is False and off["measures"] == rec["measures"]  # measured either way


def test_a_failing_page_is_recorded_too_even_while_nothing_is_held(page: Any) -> None:
    """With CDI_QUALITY_CHECK off nothing is held - but the record still says what the page
    would have got, which is exactly what the limit tuning needs (AC3)."""
    blurred = _png(cv2.GaussianBlur(page, (25, 25), 0))
    s = Settings(quality_check=False)
    rec = quality.record(blurred, settings=s)
    assert rec["passed"] is False and rec["code"] == "blurred" and rec["enforced"] is False
    assert quality.assess(blurred, "image/png", settings=s) is quality.OK  # and it is not held


def test_the_record_is_plain_json(page: Any) -> None:
    """It goes into ``document_page.preproc`` (jsonb), so it must serialise as it stands."""
    import json

    rec = quality.record(_png(page), settings=Settings())
    assert json.loads(json.dumps(rec))["measures"]["extent_px"] == rec["measures"]["extent_px"]


# ------------------------------------------------------------------ settings, not code


def test_every_limit_is_a_setting() -> None:
    s = Settings()
    lim = quality.limits(s)
    assert set(lim) == {"min_side_px", "min_sharpness", "min_ink", "max_ink", "min_text_px",
                        "max_glare", "paper_white", "min_writing_sharpness"}  # fmt: skip
    moved = quality.limits(Settings(quality_min_text_px=14.0, quality_max_glare_fraction=0.2))
    assert (moved["min_text_px"], moved["max_glare"]) == (14.0, 0.2)


def test_holding_is_on_by_default_and_can_be_switched_off() -> None:
    assert Settings().quality_check is True
    assert quality.assess(b"not a picture", "image/png", settings=Settings()).code == "unreadable"
    off = Settings(quality_check=False)
    assert quality.assess(b"not a picture", "image/png", settings=off) is quality.OK


def test_measuring_one_page_is_fast(page: Any) -> None:
    """CPU only and quick: the story's own figure is 0.5 s for a 3406x4750 page on the pod. This
    box is slower and the page smaller, so the bar here is only "well under a few seconds"."""
    import time

    png = _png(page)
    start = time.perf_counter()
    quality.measure(png)
    assert time.perf_counter() - start < 5.0


# ------------------------------------------------------------------ smeared is not blank


@pytest.mark.parametrize("kernel", [9, 15, 25, 41])
def test_a_blurred_page_is_called_blurry_at_every_strength(page: Any, kernel: int) -> None:
    """Two ways a blurred page used to be misdescribed, both sending the person to fix the wrong
    thing. A strong blur leaves no pixel darker than mid-grey, so by ink alone the page looked
    *blank*; a medium one erodes each stroke to a few dark specks, so by text height it looked
    *too far away*. The writing is there and smeared - the soft ink shows it - and it is blurry."""
    blurred = cv2.GaussianBlur(page, (kernel, kernel), 0)
    m = _m(blurred)
    assert m["soft_ink_fraction"] > 0.02  # the writing is still on the page
    assert quality.verdict(m, **LIMITS).code == "blurred"


def test_a_blank_page_has_no_soft_ink_either() -> None:
    """White paper, and a noisy photograph of blank paper: neither has anything darker than its
    own paper tone, so both are blank and neither is mistaken for a blurred page."""
    white = np.full((1600, 1200), 255, np.uint8)
    rng = np.random.default_rng(3)
    photo = np.clip(212 + rng.normal(0, 3, (1600, 1200)), 0, 255).astype(np.uint8)
    for blank in (white, photo):
        m = _m(blank)
        assert m["soft_ink_fraction"] < 0.0005
        assert quality.verdict(m, **LIMITS).code == "blank"


def test_sensor_noise_alone_does_not_make_a_blank_photo_look_sharp() -> None:
    """A noisy photo of blank paper has a high Laplacian variance - noise is all edges. That is
    why blank is judged before blur: sharpness on its own would pass an empty page."""
    rng = np.random.default_rng(3)
    photo = np.clip(212 + rng.normal(0, 3, (1600, 1200)), 0, 255).astype(np.uint8)
    m = _m(photo)
    assert m["sharpness"] > LIMITS["min_sharpness"]
    assert quality.verdict(m, **LIMITS).code == "blank"
