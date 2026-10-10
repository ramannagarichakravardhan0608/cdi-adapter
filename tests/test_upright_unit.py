"""The Reviewer shows each page upright (owner, 2026-10-10): ``recognition/upright.py``.

Synthetic printed pages, turned and tilted on purpose, read by the real print reader (RapidOCR)
on this machine's CPU. No real prescription is used here; on 21 real ones on the test host the
same rule left every page upright (see the module note).
"""

from __future__ import annotations

import inspect
import io
from typing import Any

import cv2
import numpy as np
import pytest
from PIL import Image

from cdi_adapter.recognition import upright
from tests.helpers import make_pdf

pytest.importorskip("rapidocr_onnxruntime")

LINES = ["CareFlow Polyclinic - Rx", "Name: Test Patient   Age: 44 Y / M", "Date: 09/10/2026",
         "Diagnosis: Hypertension", "Tab Telma 40 mg 1-0-0 x 30 days",
         "Tab Amlodipine 5 mg 0-0-1 x 30 days", "Advice: low salt diet",
         "Review after 1 month"]  # fmt: skip


@pytest.fixture(scope="module")
def page() -> Any:
    from cdi_adapter.ingest import pages

    png = pages.render_pages(make_pdf([LINES], dpi=150), "application/pdf", dpi=150)[0]
    return cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_GRAYSCALE)


def _reads_upright(gray: Any) -> float:
    """How well the page reads as it stands (the reader with nothing turned)."""
    return upright.score(upright._read_with_rapidocr(upright._small(gray)))


@pytest.mark.parametrize("turns", [1, 2, 3])
def test_a_turned_page_comes_back_the_way_it_reads(page, turns) -> None:
    turned = np.ascontiguousarray(np.rot90(page, turns))
    made = upright.make_upright(turned)
    assert (made.quarter_turns + turns) % 4 == 0  # turned back exactly
    assert made.image.shape == page.shape
    assert _reads_upright(made.image) >= 0.9 * _reads_upright(page)


def test_a_page_that_is_already_upright_is_left_alone(page) -> None:
    made = upright.make_upright(page)
    assert made.quarter_turns == 0 and made.tilt_deg == 0.0
    assert np.array_equal(made.image, page)


def test_a_tilted_page_is_levelled(page) -> None:
    tilted = upright.level(page, 5.0)
    made = upright.make_upright(tilted)
    assert made.quarter_turns == 0
    assert -6.0 <= made.tilt_deg <= -4.0  # the 5 degrees found and turned back
    assert upright.tilt(upright._read_with_rapidocr(upright._small(made.image))) == pytest.approx(
        0.0, abs=1.0)  # fmt: skip


def test_a_page_nobody_can_read_is_not_turned() -> None:
    blank = np.full((800, 600), 255, np.uint8)
    cv2.line(blank, (50, 100), (550, 700), 0, 3)
    made = upright.make_upright(blank)
    assert made.quarter_turns == 0 and np.array_equal(made.image, blank)


def test_a_turn_must_read_clearly_better_and_count_only_latin() -> None:
    quad = [[0, 0], [100, 0], [100, 20], [0, 20]]
    as_is = [(quad, "Tab Telma 40", 0.9)]
    tall = [[0, 0], [20, 0], [20, 100], [0, 100]]
    assert upright.score([(tall, "Tab Telma 40", 0.9)]) == 0.0  # read sideways: does not count
    chinese = [
        (quad, "中文中文中文中文中文中文中文中文中文中文", 0.99)
    ]  # sideways Hindi, read by a Chinese model
    assert upright.score(chinese) == 0.0
    calls = iter([as_is, chinese, chinese, chinese])
    made = upright.make_upright(np.full((100, 200), 255, np.uint8), read=lambda g: next(calls))
    assert made.quarter_turns == 0
    slightly = iter([as_is, [(quad, "Tab Telma 40 mg x", 0.9)], [], []])
    made = upright.make_upright(np.full((100, 200), 255, np.uint8), read=lambda g: next(slightly))
    assert made.quarter_turns == 0  # 13 letters against 10 is not "clearly better"


def test_the_copy_is_a_png_and_says_what_was_done(page) -> None:
    ok, buf = cv2.imencode(".png", np.ascontiguousarray(np.rot90(page, 1)))
    made, record = upright.upright_png(buf.tobytes())
    assert Image.open(io.BytesIO(made)).format == "PNG"
    assert record["turned_deg"] == 270 and len(record["scores"]) == 4
    with pytest.raises(ValueError):
        upright.upright_png(b"not a picture")


def test_the_reviewer_shows_the_upright_copy_and_falls_back_to_the_page() -> None:
    from cdi_adapter.webapp import app as web
    from cdi_adapter.webapp import auth
    from cdi_adapter.webapp.reviewer_page import reviewer_page

    html = reviewer_page() if callable(reviewer_page) else ""
    js = inspect.getsource(__import__("cdi_adapter.webapp.reviewer_page", fromlist=["x"]))
    assert "img.src = page + '/upright'" in js and "img.src = page; return;" in js
    assert html == "" or "/upright" in html
    route = inspect.getsource(web.document_page_upright)
    assert "upright.upright_png" in route and "store.put_bytes(upright_key(key)" in route
    assert 'return Response(page, media_type="image/png")' in route  # never a broken picture
    assert web.upright_key("documents/ab/abc/page-1.png") == "documents/ab/abc/page-1.upright.png"
    same = auth.POLICY[("GET", "/api/documents/{document_id}/pages/{page_no}")]
    assert auth.POLICY[("GET", "/api/documents/{document_id}/pages/{page_no}/upright")] == same


def test_it_is_for_the_eye_only() -> None:
    """The page the pipeline reads, and every box on it, are unchanged: nothing in the pipeline
    and no page that draws boxes uses the upright copy."""
    from pathlib import Path

    src = Path(upright.__file__).resolve().parents[1]
    marks = ("recognition import upright", "upright_png", "/upright")
    users = sorted(p.relative_to(src).as_posix() for p in src.rglob("*.py")
                   if any(m in p.read_text(encoding="utf-8") for m in marks)
                   and p.name != "upright.py")  # fmt: skip
    # the route, its access rule and the page that shows it
    assert users == ["webapp/app.py", "webapp/auth.py", "webapp/reviewer_page.py"]
