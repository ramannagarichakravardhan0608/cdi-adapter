"""A readable handwritten prescription must not be held (fixed 2026-10-09).

Three real phone photographs of handwritten prescriptions, each plainly readable, were held by
the picture check - one as "writing too small", one as "blurry", one as "glare" - and a fourth
as "picture too small". The photographs are of real patients and are not in the repository, so
each test here builds a picture with the property that fooled the check:

* a page on a **patterned cloth**, saved as a JPEG: the frame is full of small dark specks, and
  the *median* dark blob is far smaller than a letter;
* a page written in a **pale pen** on shadowed paper, mostly empty: the whole-frame sharpness
  is under the limit although every stroke is crisp;
* a photo with a **white band under it** (a caption, a margin): a lot of burnt-out white, none
  of it on the writing;
* a **small picture** whose writing is still tall.

And the other side, which matters as much: a picture that really is blurred, far away, dark,
tiny or washed out by a reflection is still held, with the right reason.
"""

from __future__ import annotations

import io
import random
from typing import Any

import cv2
import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

from cdi_adapter.config import Settings
from cdi_adapter.recognition import quality

ON = Settings(quality_check=True)
LINES = ["Dr. S. Das  MBBS MS", "Name: Sunita C.    Age 66  F", "c/o itching", "Rx",
         "Sachet fosirol 1 sachet in water", "Tab urispas 1 tab BD x 5 days",
         "Oint candid B local application", "Tab estro G-100 1 tab OD x 30 days",
         "USG LA + pelvis", "review 2 weeks"]  # fmt: skip


def _page(size: tuple[int, int] = (640, 880), *, paper: int = 205, pen: int = 60,
          letter_px: int = 24, lines: list[str] | None = None) -> Image.Image:  # fmt: skip
    """A sheet of paper with writing on it, as a grey picture."""
    im = Image.new("L", size, paper)
    draw, font = ImageDraw.Draw(im), ImageFont.load_default(size=letter_px)
    step = (size[1] - 80) // len(lines or LINES)
    for i, line in enumerate(lines or LINES):
        draw.text((40 + (i % 3) * 12, 40 + i * step), line, fill=pen, font=font)
    return im


def _on_cloth(page: Image.Image, frame: tuple[int, int] = (780, 1040), seed: int = 7) -> np.ndarray:
    """The page lying on a dark-patterned cloth, with the grain and specks of a phone JPEG."""
    rnd = random.Random(seed)
    cloth = Image.new("L", frame, 170)
    draw = ImageDraw.Draw(cloth)
    for _ in range(2600):  # the pattern: many small dark marks
        x, y, r = rnd.randrange(frame[0]), rnd.randrange(frame[1]), rnd.choice((1, 2, 2, 3, 4))
        draw.ellipse([x - r, y - r, x + r, y + r], fill=rnd.randrange(20, 90))
    cloth.paste(page, ((frame[0] - page.size[0]) // 2, (frame[1] - page.size[1]) // 2))
    arr = np.array(cloth).astype(np.int16)
    arr += np.random.default_rng(seed).integers(-6, 7, arr.shape, dtype=np.int16)
    buf = io.BytesIO()
    Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)).save(buf, format="JPEG", quality=70)
    return np.array(Image.open(buf).convert("L"))


def _png(arr: Any) -> bytes:
    return cv2.imencode(".png", np.asarray(arr))[1].tobytes()


def _judge(arr: Any) -> quality.Verdict:
    return quality.assess(_png(arr), "image/png", settings=ON)


# ------------------------------------------------------------------ held as "writing too small"


def test_handwriting_on_a_patterned_cloth_is_accepted() -> None:
    photo = _on_cloth(_page())
    verdict = _judge(photo)
    assert verdict.ok, (verdict.code, verdict.measures)


def test_the_writing_is_measured_not_the_specks() -> None:
    """What went wrong: the old number (the median of every dark blob) is a few pixels on this
    picture, far under the limit, though the letters are plainly tall enough to read."""
    m = quality.measure(_png(_on_cloth(_page())))
    assert m["mark_height_px"] < ON.quality_min_text_px  # the old measure would hold it
    assert m["text_height_px"] >= ON.quality_min_text_px  # the letters themselves
    assert m["text_height_px"] > 1.5 * m["mark_height_px"]


def test_the_text_height_follows_the_size_of_the_letters() -> None:
    heights = [quality.measure(_png(np.array(_page(letter_px=px))))["text_height_px"]
               for px in (14, 22, 30)]  # fmt: skip
    assert heights[0] < heights[1] < heights[2]


def _far_away(background: str) -> np.ndarray:
    """The same page a quarter of the size, in a frame of the usual size."""
    page = _page()
    small = page.resize((page.size[0] // 4, page.size[1] // 4), Image.LANCZOS)
    if background == "cloth":
        return _on_cloth(small)
    frame = Image.new("L", (780, 1040), 150)  # a plain table
    frame.paste(small, ((780 - small.size[0]) // 2, (1040 - small.size[1]) // 2))
    return np.array(frame)


def test_the_same_page_from_far_away_is_still_told_to_move_closer() -> None:
    """Really small writing is still held."""
    verdict = _judge(_far_away("table"))
    assert verdict.code == "text_too_small", (verdict.code, verdict.measures["text_height_px"])


def test_a_far_away_page_on_a_busy_cloth_is_told_to_move_closer() -> None:
    """The writing is measured on the sheet of paper only, so the cloth's own marks - as tall
    as readable letters, and far more of them - do not hide that the writing is tiny."""
    assert _judge(_far_away("cloth")).code == "text_too_small"


# ------------------------------------------------------------------ held as "blurry"


def _pale_and_sparse() -> np.ndarray:
    """A big, mostly empty sheet in shadow, written on in a pale pen, slightly soft - as a
    messaging app delivers it."""
    page = _page((1200, 1600), paper=158, pen=105, letter_px=34,
                 lines=["Anudhya C.", "", "stop bath for 4 days", "",
                        "Zocef 2 times a day x 5d", "", "Koflet-H lozenge after breakfast",
                        "", "Relent syrup 5 ml", ""])  # fmt: skip
    return cv2.GaussianBlur(np.array(page), (0, 0), 0.9)


def test_a_pale_sparse_page_with_crisp_strokes_is_accepted() -> None:
    photo = _pale_and_sparse()
    verdict = _judge(photo)
    assert verdict.ok, (verdict.code, verdict.measures)


def test_it_was_the_whole_frame_sharpness_that_held_it() -> None:
    m = quality.measure(_png(_pale_and_sparse()))
    assert m["sharpness"] < ON.quality_min_sharpness  # under the limit, by the whole frame
    assert m["stroke_sharpness"] >= ON.quality_min_stroke_sharpness  # crisp where the ink is
    without_the_rescue = {**quality.limits(ON), "min_stroke_sharpness": 0.0}
    assert quality.verdict(m, **without_the_rescue).code == "blurred"  # what it used to say


@pytest.mark.parametrize("sigma", [3.0, 5.0, 9.0])
def test_the_same_page_really_out_of_focus_is_still_blurred(sigma: float) -> None:
    """Held either way. A pale pen blurred this far leaves nothing dark enough to count as
    writing at all, so the reason given is "blurred" or "blank" - as it was before the fix."""
    verdict = _judge(cv2.GaussianBlur(_pale_and_sparse(), (0, 0), sigma))
    assert not verdict.ok and verdict.code in ("blurred", "blank"), verdict.code
    assert verdict.measures["stroke_sharpness"] < ON.quality_min_stroke_sharpness


@pytest.mark.parametrize("sigma", [2.5, 4.0, 8.0])
def test_a_blurred_photo_on_cloth_is_still_blurred(sigma: float) -> None:
    verdict = _judge(cv2.GaussianBlur(_on_cloth(_page()), (0, 0), sigma))
    assert verdict.code == "blurred", (verdict.code, verdict.measures["stroke_sharpness"])


def test_stroke_sharpness_falls_as_the_blur_grows() -> None:
    photo = _on_cloth(_page())
    values = [quality.measure(_png(cv2.GaussianBlur(photo, (0, 0), s) if s else photo))
              ["stroke_sharpness"] for s in (0, 1.5, 3.0, 6.0)]  # fmt: skip
    assert values == sorted(values, reverse=True) and values[0] > 3 * values[2]


# ------------------------------------------------------------------ held as "glare"


def _with_a_white_band() -> np.ndarray:
    """A photographed page (grey paper) with a white caption band under it, as a screenshot or
    a shared picture has: about a tenth of the frame is pure white, none of it on the page."""
    photo = np.array(_page((880, 780), paper=200))
    band = np.full((110, 880), 255, np.uint8)
    return np.vstack([photo, band])


def test_white_that_reaches_the_edge_of_the_picture_is_not_glare() -> None:
    photo = _with_a_white_band()
    m = quality.measure(_png(photo))
    assert m["glare_fraction"] > ON.quality_max_glare_fraction  # the old number: "glare"
    assert m["glare_inside_fraction"] < 0.01  # nothing burnt out inside the page
    assert _judge(photo).ok


def test_a_reflection_in_the_middle_of_the_page_is_still_glare() -> None:
    photo = np.array(_page((880, 880), paper=200))
    cv2.ellipse(photo, (440, 430), (190, 150), 0, 0, 360, 255, -1)  # a burnt-out patch
    m = quality.measure(_png(photo))
    assert m["glare_inside_fraction"] > ON.quality_max_glare_fraction
    assert _judge(photo).code == "glare"


def test_measurements_saved_before_the_change_are_still_judged() -> None:
    """A record without the new number falls back to the old one, as before."""
    old = {"extent_px": 1200.0, "sharpness": 400.0, "ink_fraction": 0.05, "paper_tone": 200.0,
           "glare_fraction": 0.3, "text_height_px": 18.0}  # fmt: skip
    assert quality.verdict(old, **quality.limits(ON)).code == "glare"


# ------------------------------------------------------------------ held as "picture too small"


def test_a_small_picture_with_tall_crisp_writing_is_accepted() -> None:
    photo = np.array(_page((600, 820), letter_px=22))
    assert min(photo.shape) < ON.quality_min_side_px  # under the usual limit
    verdict = _judge(photo)
    assert verdict.ok, (verdict.code, verdict.measures)


def test_a_small_picture_with_small_writing_is_still_too_small() -> None:
    page = _page((1200, 1640), letter_px=22)
    photo = np.array(page.resize((480, 656), Image.LANCZOS))  # the same page, shrunk
    verdict = _judge(photo)
    assert verdict.code == "too_small", (verdict.code, verdict.measures["text_height_px"])


def test_a_small_blurred_picture_is_still_too_small() -> None:
    photo = cv2.GaussianBlur(np.array(_page((600, 820), letter_px=22)), (0, 0), 4.0)
    assert _judge(photo).code == "too_small"


def test_a_thumbnail_is_too_small_whatever_it_holds() -> None:
    photo = np.array(_page((360, 380), letter_px=26, lines=LINES[:5]))
    assert min(photo.shape) < ON.quality_floor_side_px
    assert _judge(photo).code == "too_small"


def test_the_floor_and_the_stroke_limit_are_settings() -> None:
    assert (ON.quality_floor_side_px, ON.quality_min_stroke_sharpness) == (400, 0.4)
    strict = Settings(quality_check=True, quality_floor_side_px=0)  # 0 = the old rule
    photo = np.array(_page((600, 820), letter_px=22))
    assert quality.assess(_png(photo), "image/png", settings=strict).code == "too_small"


# ------------------------------------------------------------------ the other checks are untouched


def test_a_dark_photo_is_still_too_dark() -> None:
    photo = (_on_cloth(_page()) * 0.2).astype(np.uint8)
    assert _judge(photo).code == "too_dark"


def test_a_blank_sheet_is_still_blank() -> None:
    assert _judge(np.full((1040, 780), 200, np.uint8)).code == "blank"


def test_a_file_that_is_not_a_picture_is_still_unreadable() -> None:
    assert quality.assess(b"not a picture", "image/png", settings=ON).code == "unreadable"
