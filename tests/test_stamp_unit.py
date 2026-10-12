"""Finding a stamp in the picture (EX-S2 AC4 / FR6).

Every page here is drawn by the test: print, pen strokes and stamp outlines made with OpenCV.
No real prescription is used, so these tests show the rule does what it says on clean cases -
they do NOT measure how often it is right on real stamped pages.
"""

from __future__ import annotations

from typing import Any

import cv2
import numpy as np
import pytest

from cdi_adapter.recognition.stamp import Stamp, find_stamps, lines_inside

BLUE, VIOLET, RED, BLACK = (170, 60, 30), (150, 40, 110), (40, 40, 200), (20, 20, 20)  # BGR
W, H = 1000, 1400


def _page() -> Any:
    """A white page with a printed letterhead and a few printed lines."""
    img = np.full((H, W, 3), 255, np.uint8)
    for i, line in enumerate(("CITY CARE CLINIC", "Dr. A. Example  MBBS, MD",
                              "Reg No 71112")):  # fmt: skip
        cv2.putText(img, line, (60, 80 + 45 * i), cv2.FONT_HERSHEY_SIMPLEX, 1.0, BLACK, 2)
    for i in range(6):
        cv2.putText(img, f"Tab. Example {i + 1}  1-0-1 x 5 days", (80, 420 + 60 * i),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, BLACK, 2)  # fmt: skip
    return img


def _pen(img: Any, colour: tuple[int, int, int], top: int = 800, lines: int = 5) -> None:
    """Lines of joined-up pen strokes: handwriting in coloured ink."""
    rng = np.random.default_rng(7)
    for row in range(lines):
        x, y = 90, top + 55 * row
        pts = [(x, y)]
        while x < 820:
            x += int(rng.integers(8, 22))
            pts.append((x, y + int(rng.integers(-16, 16))))
        cv2.polylines(img, [np.array(pts, np.int32)], False, colour, 2, cv2.LINE_AA)


def _round_stamp(img: Any, centre: tuple[int, int], radius: int = 110,
                 colour: tuple[int, int, int] = BLUE, oval: float = 1.0) -> None:  # fmt: skip
    cv2.ellipse(img, centre, (radius, int(radius * oval)), 0, 0, 360, colour, 4, cv2.LINE_AA)
    for i, word in enumerate(("DR EXAMPLE", "REG 71112")):
        cv2.putText(img, word, (centre[0] - radius + 35, centre[1] - 10 + 34 * i),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, colour, 2)  # fmt: skip


def _box_stamp(img: Any, top_left: tuple[int, int], size: tuple[int, int] = (300, 120),
               colour: tuple[int, int, int] = VIOLET) -> None:  # fmt: skip
    x, y = top_left
    cv2.rectangle(img, (x, y), (x + size[0], y + size[1]), colour, 4)
    for i, word in enumerate(("Dr. A. Example", "MBBS MD  Reg 71112")):
        cv2.putText(img, word, (x + 18, y + 45 + 40 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                    colour, 2)  # fmt: skip


def _png(img: Any) -> bytes:
    ok, buf = cv2.imencode(".png", img)
    assert ok
    return bytes(buf)


def _jpeg(img: Any, quality: int = 70) -> bytes:
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    assert ok
    return bytes(buf)


# ------------------------------------------------------------- a stamp is found


def test_a_round_stamp_is_found_where_it_is() -> None:
    img = _page()
    _round_stamp(img, (760, 1180))
    (stamp,) = find_stamps(_png(img))
    assert (stamp.shape, stamp.ink) == ("round", "blue") and stamp.score >= 0.8
    x0, y0, x1, y1 = stamp.bbox
    assert abs(x0 - 650) < 25 and abs(y0 - 1070) < 25 and abs(x1 - 870) < 25 and abs(y1 - 1290) < 25


def test_an_oval_stamp_is_found() -> None:
    img = _page()
    _round_stamp(img, (700, 1150), radius=150, oval=0.62)
    (stamp,) = find_stamps(_png(img))
    assert stamp.shape == "round"


def test_a_box_stamp_in_violet_is_found() -> None:
    img = _page()
    _box_stamp(img, (560, 1120))
    (stamp,) = find_stamps(_png(img))
    assert (stamp.shape, stamp.ink) == ("rectangular", "blue")  # violet is the blue family


def test_a_red_seal_is_found() -> None:
    img = _page()
    _round_stamp(img, (300, 1150), colour=RED)
    (stamp,) = find_stamps(_png(img))
    assert stamp.ink == "red"


def test_a_stamp_is_found_beside_handwriting_in_the_same_ink() -> None:
    img = _page()
    _pen(img, BLUE)
    _round_stamp(img, (760, 1250))
    stamps = find_stamps(_png(img))
    assert len(stamps) == 1 and stamps[0].bbox[1] > 1100


def test_a_tilted_box_stamp_is_found() -> None:
    img = _page()
    _box_stamp(img, (520, 1100))
    turn = cv2.getRotationMatrix2D((670, 1160), 12, 1.0)
    img = cv2.warpAffine(img, turn, (W, H), borderValue=(255, 255, 255))
    assert [s.shape for s in find_stamps(_png(img))] == ["rectangular"]


def test_it_survives_a_phone_photo_jpeg_and_a_larger_picture() -> None:
    img = _page()
    _round_stamp(img, (760, 1180))
    big = cv2.resize(img, (W * 2, H * 2), interpolation=cv2.INTER_CUBIC)
    big = cv2.GaussianBlur(big, (5, 5), 0)
    (stamp,) = find_stamps(_jpeg(big))
    assert stamp.bbox[0] > 1200  # in the big picture's own pixels, not the reduced copy's


def test_two_stamps_are_two_regions() -> None:
    img = _page()
    _round_stamp(img, (250, 1200))
    _box_stamp(img, (560, 1140))
    assert sorted(s.shape for s in find_stamps(_png(img))) == ["rectangular", "round"]


# ------------------------------------------------------------- what is not a stamp


def test_a_printed_page_has_no_stamp() -> None:
    assert find_stamps(_png(_page())) == []


def test_handwriting_in_blue_ink_is_not_a_stamp() -> None:
    img = _page()
    _pen(img, BLUE, lines=9)
    assert find_stamps(_png(img)) == []


def test_a_number_circled_by_hand_is_not_a_stamp() -> None:
    """The doctor's "(30) days": a small ring round a number, in pen."""
    img = _page()
    cv2.circle(img, (500, 900), 28, BLUE, 2, cv2.LINE_AA)
    cv2.putText(img, "30", (484, 910), cv2.FONT_HERSHEY_SIMPLEX, 0.7, BLUE, 2)
    assert find_stamps(_png(img)) == []


def test_a_filled_coloured_logo_or_banner_is_not_a_stamp() -> None:
    img = _page()
    cv2.circle(img, (860, 110), 70, BLUE, -1)  # a solid logo
    cv2.rectangle(img, (60, 1280), (460, 1360), RED, -1)  # a solid banner
    assert find_stamps(_png(img)) == []


def test_an_empty_ring_or_box_is_not_a_stamp() -> None:
    """An outline with nothing written in it: a tick box, a drawn frame."""
    img = _page()
    cv2.circle(img, (700, 1150), 110, BLUE, 4)
    cv2.rectangle(img, (120, 1080), (420, 1200), BLUE, 4)
    assert find_stamps(_png(img)) == []


def test_a_coloured_frame_round_the_whole_page_is_not_a_stamp() -> None:
    img = _page()
    cv2.rectangle(img, (20, 20), (W - 20, H - 20), BLUE, 5)
    assert find_stamps(_png(img)) == []


def test_a_long_underline_is_not_a_stamp() -> None:
    img = _page()
    cv2.line(img, (80, 700), (900, 704), RED, 4)
    assert find_stamps(_png(img)) == []


@pytest.mark.parametrize("picture", [None, b"", b"not a picture", b"\x89PNG\r\n\x1a\n broken"])
def test_a_picture_that_cannot_be_read_has_no_stamp_and_no_error(picture: Any) -> None:
    assert find_stamps(picture) == []


# ------------------------------------------------------------- what it does not claim


@pytest.mark.xfail(strict=True, reason=(
    "A stamp in black ink cannot be told from print by colour: not found here. The reading "
    "model's own answer is the only source for it."))  # fmt: skip
def test_a_black_stamp_is_found() -> None:
    img = _page()
    _round_stamp(img, (760, 1180), colour=BLACK)
    assert find_stamps(_png(img))


@pytest.mark.xfail(strict=True, reason=(
    "A stamp with no outline - a few lines of violet text - is, by shape, handwriting: not "
    "found here."))  # fmt: skip
def test_a_stamp_with_no_outline_is_found() -> None:
    img = _page()
    for i, word in enumerate(("Dr. A. Example", "MBBS MD", "Reg 71112")):
        cv2.putText(img, word, (600, 1150 + 36 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.7, VIOLET, 2)
    assert find_stamps(_png(img))


# ------------------------------------------------------------- the stamp's own lines


def test_the_ocr_lines_inside_the_stamp_are_told_apart() -> None:
    stamp = Stamp([650, 1070, 870, 1290], "round", "blue", 0.95)
    blocks = [{"text": "CITY CARE CLINIC", "bbox": [60, 50, 420, 90]},
              {"text": "DR EXAMPLE", "bbox": [685, 1150, 840, 1180]},
              {"text": "REG 71112", "bbox": [685, 1185, 830, 1215]},
              {"text": "Tab. Example", "bbox": [80, 400, 500, 430]}]  # fmt: skip
    assert lines_inside(stamp, blocks) == [1, 2]
    assert lines_inside(stamp.as_json(), blocks) == [1, 2]


def test_a_reading_of_the_whole_page_is_not_inside_the_stamp() -> None:
    """The handwriting reader gives every line the page's box: none of them is "in" a stamp."""
    stamp = Stamp([650, 1070, 870, 1290], "round", "blue", 0.95)
    page_wide = [{"text": "anything", "bbox": [0, 0, W, H]}, {"text": "no box"}]
    assert lines_inside(stamp, page_wide) == []
