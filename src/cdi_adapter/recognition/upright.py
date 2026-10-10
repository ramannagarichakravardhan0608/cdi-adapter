"""A page turned the way its text reads, and levelled - for a person to look at (Reviewer).

Phone photos of prescriptions arrive sideways, upside down and tilted. The geometry of IM-S2
(``geometry.py``) can turn and crop a page from the paper's edges, but it is off by default and,
tried on 21 real photos (2026-10-10), it turned an upright page upside down and missed sideways
ones: the edges of a page lying on a table, a book or a hand say little about which way is up.

What says which way is up is the writing. :func:`make_upright` asks the print reader (RapidOCR,
on the CPU, its own 180-degree guess switched off) to read the page as it is and turned by 90,
180 and 270 degrees, and keeps the way it reads best - counting only Latin letters and digits,
because a reader shown sideways Hindi or Malayalam answers with confident Chinese characters. A
page is turned only when the new way reads clearly better (``MARGIN``) and reads something at all
(``MIN_SCORE``): a page nobody can read is left as it is. Then the tilt of the lines it read is
levelled, between ``MIN_TILT`` and ``MAX_TILT`` degrees.

On those 21 photos: every one came out upright. It cannot undo perspective (a page photographed
at an angle stays a trapezium) and it does not crop.

**For display only.** The page the pipeline reads, every box and every coordinate stay exactly
as they were: this is a copy, made when the Reviewer first asks for it and kept beside the page
(``webapp`` ``/api/documents/{id}/pages/{n}/upright``). Nothing that draws boxes on a page uses it.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

MAX_SIDE = 1280  # the size the page is read at to decide; the copy itself keeps full size
MIN_LINE_CONF = 0.6
MIN_SCORE = 60.0  # a turned page must read at least this much ...
MARGIN = 1.5  # ... and this many times what the page as it is reads
MIN_TILT, MAX_TILT = 0.5, 20.0  # degrees
LATIN = re.compile(r"[A-Za-z0-9]")

# one reading of an image: [(quad, text, confidence), ...] as RapidOCR gives it
Read = Callable[[Any], Sequence[Any]]


@dataclass
class Upright:
    image: Any  # the grey page, turned and levelled
    quarter_turns: int  # counter-clockwise, as numpy.rot90
    tilt_deg: float  # what was levelled (0 when it was not)
    score: float
    scores: list[float] = field(default_factory=list)  # as it is, 90, 180, 270

    def record(self) -> dict[str, Any]:
        return {"turned_deg": self.quarter_turns * 90, "tilt_deg": round(self.tilt_deg, 2),
                "score": round(self.score, 1),
                "scores": [round(s, 1) for s in self.scores]}  # fmt: skip


def _read_with_rapidocr(gray: Any) -> Sequence[Any]:  # pragma: no cover - needs the models
    import cv2

    from cdi_adapter.ocr import rapid

    ok, buf = cv2.imencode(".png", gray)
    result, _ = rapid.get_engine()(buf.tobytes(), use_cls=False)
    return result or []


def _lies_flat(quad: Any) -> bool:
    """A line wider than it is tall. The print reader turns a tall box on its own before it
    reads it, so a sideways page can read well "as it is": only lines lying flat show that
    the page is the right way up (found in testing, 2026-10-10)."""
    xs, ys = [float(p[0]) for p in quad], [float(p[1]) for p in quad]
    return (max(xs) - min(xs)) >= (max(ys) - min(ys))


def score(lines: Sequence[Any]) -> float:
    """How well a reading read: confidence times the letters and digits on each sure line that
    lies flat."""
    return float(sum(float(conf) * len(LATIN.findall(str(text)))
                     for quad, text, conf in lines
                     if float(conf) >= MIN_LINE_CONF and _lies_flat(quad)))  # fmt: skip


def tilt(lines: Sequence[Any]) -> float:
    """The tilt of the lines read, in degrees: the length-weighted median slope of their top
    edges. 0 when fewer than three good lines say anything."""
    angles, weights = [], []
    for quad, text, conf in lines:
        if float(conf) < MIN_LINE_CONF or len(str(text).strip()) < 4:
            continue
        (x0, y0), (x1, y1) = quad[0], quad[1]
        length = math.hypot(x1 - x0, y1 - y0)
        if length < 40:
            continue
        angles.append(math.degrees(math.atan2(y1 - y0, x1 - x0)))
        weights.append(length)
    if len(angles) < 3:
        return 0.0
    order = np.argsort(angles)
    a, w = np.array(angles)[order], np.array(weights)[order]
    return float(a[min(len(a) - 1, int(np.searchsorted(np.cumsum(w), w.sum() / 2)))])


def _small(gray: Any) -> Any:
    import cv2

    h, w = gray.shape[:2]
    f = MAX_SIDE / max(h, w)
    if f >= 1:
        return gray
    return cv2.resize(gray, (int(w * f), int(h * f)), interpolation=cv2.INTER_AREA)


def level(gray: Any, deg: float) -> Any:
    """Turn the page by ``deg`` degrees (counter-clockwise), growing it so no corner is cut and
    filling the new corners with white paper."""
    import cv2

    h, w = gray.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2, h / 2), deg, 1.0)
    cos, sin = abs(m[0, 0]), abs(m[0, 1])
    nw, nh = int(h * sin + w * cos), int(h * cos + w * sin)
    m[0, 2] += nw / 2 - w / 2
    m[1, 2] += nh / 2 - h / 2
    return cv2.warpAffine(gray, m, (nw, nh), borderValue=255)


def make_upright(gray: Any, *, read: Read = _read_with_rapidocr) -> Upright:
    """The page turned the way its text reads, then levelled."""
    small = _small(gray)
    tried = []
    for k in range(4):
        lines = read(np.ascontiguousarray(np.rot90(small, k)))
        tried.append((k, score(lines), lines))
    k, best, lines = max(tried, key=lambda t: t[1])
    if k != 0 and not (best >= MIN_SCORE and best >= MARGIN * tried[0][1]):
        k, best, lines = tried[0]  # not clearly better: leave it the way it came
    out = np.ascontiguousarray(np.rot90(gray, k))
    deg = tilt(lines)
    if MIN_TILT <= abs(deg) <= MAX_TILT:
        out = level(out, deg)
    else:
        deg = 0.0
    return Upright(out, k, deg, best, [t[1] for t in tried])


def upright_png(png: bytes, *, read: Read = _read_with_rapidocr) -> tuple[bytes, dict[str, Any]]:
    """A page PNG in, its upright copy (PNG) and what was done out."""
    import cv2

    gray = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_GRAYSCALE)
    if gray is None:
        raise ValueError("the page picture could not be opened")
    made = make_upright(gray, read=read)
    ok, buf = cv2.imencode(".png", made.image)
    if not ok:  # pragma: no cover
        raise ValueError("the upright picture could not be written")
    return buf.tobytes(), made.record()
