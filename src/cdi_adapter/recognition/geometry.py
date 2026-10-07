"""Straighten and crop the page safely, keeping the original untouched (backlog IM-S2).

A phone photo is tilted, turned, or taken at an angle. This module makes it upright **when it is
confident, and leaves it alone when it is not** - and whatever it does is pure geometry, written
down as one 3x3 matrix, so a box found on the straightened page can be carried back to the very
pixels of the original (evidence stays true). No sharpening, no filter: nothing here can change
a letter.

Four moves, in this order, each returning the image and the matrix that produced it:

==================  ====================================================  ========================
move                when                                                  function
==================  ====================================================  ========================
perspective crop    the table shows round the paper, and its four         :func:`find_page_quad`,
                    corners can be found                                  :func:`warp_perspective`
deskew              a page that fills the frame, tilted 0.15 to 15        :func:`deskew`
                    degrees (S1's own rule)
quarter turn        the writing runs down the page                        :func:`writing_axis`,
                                                                          :func:`rotate90`
half turn           the page is upside down                               :func:`rotate90` twice
==================  ====================================================  ========================

**A cropped page is not deskewed as well.** Once the paper's four corners have set the page
square, S1's ink-rectangle estimate has nothing left to add and was measured doing harm: on a
flattened photograph it read a tilt of -4.8 degrees that was not there (sensor noise and the
sliver of table at the paper's edge move the rectangle) and turned level lines crooked. So the
deskew runs only on a page that fills the frame - the flat scan it was written for.

**Which way is up** was measured, not assumed, and the measurement changed the design. The
obvious cue - letters have more ascenders than descenders - reads +0.59 on a typed prescription
and **-0.27 on one written in capitals** ("TAB PAN 40 OD"), which is how prescriptions are
written; on the sample scans it sits at +/-0.03 and says nothing. The cue that held on every page
tried is the margin: writing starts at a left margin and ends ragged, so the line *starts* line
up (spread 0-20 px) and the line *ends* do not (60-290 px). Upside down, the two swap. So
:func:`upside_down` compares those two spreads and answers only when one is clearly the margin;
a centred or fully justified page has no such margin and is left as it is.

**When unsure, change nothing.** Two cases go further and ask for a retake
(:data:`HOLD_REASONS`), because there the picture is known to be wrong and cannot be put right:

* ``edges_not_found`` - the table is visible round the page but four corners cannot be found;
* ``orientation_unsure`` - the page is sideways and nothing says which of the two ways.

A page that merely *might* be upside down is not held: "a readable picture must not be rejected"
(IM-S1) outranks a guess, and a page that fills the frame has no edges to find and needs none.

**The original is never written to.** These functions take an image and return a new one; S1
keeps the uploaded bytes at their own key and stores the straightened copy beside them, with the
matrix in ``document_page.preproc.transform``. :func:`to_original` and :func:`to_straightened`
are the two directions of that matrix.

Everything beyond S1's existing deskew is **off by default** (``CDI_STRAIGHTEN``): turning and
cropping change what every later stage reads, and the limits below have been proven on rendered
pages and simulated photographs, not yet on the answer-key set of real ones (ENT-S3). The matrix
is recorded either way - for today's deskew too - so evidence can always be mapped back.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from cdi_adapter import _cpu
from cdi_adapter.config import get_settings

# thread caps must be in place before OpenCV / NumPy load (§7.4 #4), as in ingest/pages.py
_THREADS = _cpu.apply_thread_caps(get_settings().job_max_workers)

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from cdi_adapter.recognition import quality  # noqa: E402

cv2.setNumThreads(_THREADS)

# code -> what the person at the desk is told. DRAFT wording, like quality.REASONS.
HOLD_REASONS: dict[str, str] = {
    "edges_not_found": (
        "The edges of this page could not be found. Lay the page flat on a plain surface, keep "
        "all four corners in the picture, and take it again."
    ),
    "orientation_unsure": (
        "This page looks sideways and it is not clear which way is up. Hold the phone upright "
        "over the page and take it again."
    ),
}

SKEW_MIN_DEG = 0.15  # S1's rule, unchanged: below this a rotation only blurs
SKEW_MAX_DEG = 15.0  # above this the estimate is not a tilt any more

# --- the table round the page
_BACKGROUND_DARKER_BY = 40.0  # grey levels below the paper tone: that pixel is table, not paper
_BACKGROUND_BORDER_SHARE = 0.5  # of the outer ring: the table shows most of the way round
_BORDER_RING = 0.02  # the outer ring's width, as a share of the shorter side
_MIN_PAGE_SHARE = 0.20  # a "page" smaller than this share of the frame is something else
_QUAD_EPSILON = 0.02  # corner-finding tolerance, as a share of the outline's length

# --- which way is up
_MIN_LINES = 4  # fewer lines than this and a margin cannot be told from chance
_MIN_LINE_PX = 8  # a "line" shorter than this is a speck or a rule
_MARGIN_RATIO = 3.0  # the ragged side must be this many times more spread than the aligned side
_MIN_RAGGED_PX = 12.0  # and ragged in absolute terms, or both sides are simply straight
_MIN_MARKS = 10  # fewer written marks than this is not a page of writing
_MIN_WORDS = 4  # words that must agree on a direction
_WORD_ELONGATION = 1.5  # a word is at least this much longer than it is tall
_AXIS_RATIO = 3.0  # and the words one way must outnumber the other way by this much


@dataclass(frozen=True)
class Straightened:
    """One page made upright: the picture, how it got there, and the way back."""

    image: Any  # the straightened grey image
    matrix: Any  # 3x3: original pixel -> straightened pixel
    source_size: tuple[int, int]  # (width, height) of the original
    steps: tuple[str, ...] = ()
    turned_deg: int = 0  # clockwise, a multiple of 90
    skew_deg: float = 0.0
    quad: tuple[tuple[float, float], ...] | None = None  # the page's corners in the original
    hold: str | None = None  # a key of HOLD_REASONS: nothing was changed, ask for a retake
    measures: dict[str, Any] = field(default_factory=dict)

    @property
    def size(self) -> tuple[int, int]:
        return int(self.image.shape[1]), int(self.image.shape[0])

    def record(self, *, enabled: bool) -> dict[str, Any]:
        """What goes into ``document_page.preproc.transform``: the numbers used, so any box on
        the stored page can be carried back to the original (IM-S2 functional 4, technical 3)."""
        return {
            "matrix": [[round(float(v), 9) for v in row] for row in self.matrix],
            "source_size": list(self.source_size),
            "size": list(self.size),
            "steps": list(self.steps),
            "turned_deg": self.turned_deg,
            "skew_deg": round(self.skew_deg, 3),
            "quad": [[round(x, 2), round(y, 2)] for x, y in self.quad] if self.quad else None,
            "hold": self.hold,
            "measures": self.measures,
            "enabled": enabled,
        }


# ------------------------------------------------------------------ the four moves


def identity() -> Any:
    return np.eye(3, dtype=np.float64)


def rotate90(img: Any, turns: int) -> tuple[Any, Any]:
    """Turn the picture ``turns`` quarter turns clockwise. Exact: pixels are moved, never
    resampled, so a quarter turn loses nothing."""
    k = turns % 4
    h, w = img.shape[:2]
    if k == 0:
        return img, identity()
    if k == 1:  # (x, y) -> (h - 1 - y, x)
        m = [[0, -1, h - 1], [1, 0, 0], [0, 0, 1]]
        return cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE), np.array(m, dtype=np.float64)
    if k == 2:  # (x, y) -> (w - 1 - x, h - 1 - y)
        m = [[-1, 0, w - 1], [0, -1, h - 1], [0, 0, 1]]
        return cv2.rotate(img, cv2.ROTATE_180), np.array(m, dtype=np.float64)
    m = [[0, 1, 0], [-1, 0, w - 1], [0, 0, 1]]  # (x, y) -> (y, w - 1 - x)
    return cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE), np.array(m, dtype=np.float64)


def background_visible(gray: Any) -> bool:
    """Does the table show round the paper? Only then is there a page edge to look for: a scan
    or a render fills the frame, and looking for corners there would find the text block."""
    h, w = gray.shape[:2]
    ring = max(2, int(min(h, w) * _BORDER_RING))
    border = np.concatenate([gray[:ring].ravel(), gray[-ring:].ravel(),
                             gray[:, :ring].ravel(), gray[:, -ring:].ravel()])  # fmt: skip
    paper = float(np.percentile(gray, 75))  # the paper is the bright majority of a page photo
    return float((border < paper - _BACKGROUND_DARKER_BY).mean()) >= _BACKGROUND_BORDER_SHARE


def _ordered(pts: Any) -> Any:
    """Four corners as top-left, top-right, bottom-right, bottom-left."""
    pts = np.asarray(pts, dtype=np.float64).reshape(4, 2)
    s, d = pts.sum(axis=1), pts[:, 0] - pts[:, 1]
    return np.array([pts[s.argmin()], pts[d.argmax()], pts[s.argmax()], pts[d.argmin()]])


def find_page_quad(gray: Any) -> Any | None:
    """The four corners of the paper in a photograph, or ``None`` when they cannot be found with
    confidence: the outline must be one bright, convex, four-cornered shape, large enough to be
    the page, and the four corners must account for it."""
    h, w = gray.shape[:2]
    soft = cv2.GaussianBlur(gray, (5, 5), 0)
    _, mask = cv2.threshold(soft, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    k = max(3, int(min(h, w) * 0.01)) | 1
    # close the writing so the page is one piece; the paper's outline is not moved by this
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((k, k), np.uint8))
    found, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not found:
        return None
    outline = max(found, key=cv2.contourArea)
    if cv2.contourArea(outline) < _MIN_PAGE_SHARE * h * w:
        return None
    hull = cv2.convexHull(outline)
    quad = cv2.approxPolyDP(hull, _QUAD_EPSILON * cv2.arcLength(hull, True), True)
    if len(quad) != 4 or not cv2.isContourConvex(quad):
        return None
    # the four corners must explain the shape: a torn or half-hidden page fits a quad badly
    if cv2.contourArea(outline) < 0.90 * cv2.contourArea(quad):
        return None
    # a corner on the frame's edge means the page runs out of the picture: not four real corners
    corners = _ordered(quad)
    on_edge = sum(1 for x, y in corners if x <= 1 or y <= 1 or x >= w - 2 or y >= h - 2)
    return None if on_edge >= 2 else corners


def warp_perspective(img: Any, quad: Any) -> tuple[Any, Any]:
    """Flatten the page whose corners are ``quad`` into an upright rectangle the size of the
    page as photographed. Returns the picture and the 3x3 matrix that made it."""
    tl, tr, br, bl = _ordered(quad)
    w = int(round(max(np.linalg.norm(tr - tl), np.linalg.norm(br - bl))))
    h = int(round(max(np.linalg.norm(bl - tl), np.linalg.norm(br - tr))))
    w, h = max(w, 2), max(h, 2)
    dst = np.array([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]], dtype=np.float32)
    m = cv2.getPerspectiveTransform(np.array([tl, tr, br, bl], dtype=np.float32), dst)
    out = cv2.warpPerspective(img, m, (w, h), flags=cv2.INTER_CUBIC,
                              borderMode=cv2.BORDER_CONSTANT, borderValue=255)  # fmt: skip
    return out, m.astype(np.float64)


def estimate_skew(gray: Any) -> float:
    """Skew of the ink in degrees (positive = content rotated counter-clockwise)."""
    _, ink = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
    pts = cv2.findNonZero(ink)
    if pts is None or len(pts) < 50:
        return 0.0
    angle = cv2.minAreaRect(pts)[2]
    # the reported range differs across OpenCV versions; fold any convention into (-45, 45]
    angle = angle % 90.0
    if angle > 45:
        angle -= 90
    # image y points down, so counter-clockwise content reads as a negative rect angle
    return float(-angle)


def deskew(gray: Any, deg: float | None = None) -> tuple[Any, Any, float]:
    """Take a small tilt out. Returns the picture, the 3x3 matrix, and the tilt that was
    measured; outside S1's 0.15-15 degree window the picture comes back untouched."""
    skew = estimate_skew(gray) if deg is None else deg
    if not SKEW_MIN_DEG < abs(skew) <= SKEW_MAX_DEG:
        return gray, identity(), skew
    h, w = gray.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2, h / 2), -skew, 1.0)
    out = cv2.warpAffine(gray, m, (w, h), flags=cv2.INTER_CUBIC,
                         borderMode=cv2.BORDER_CONSTANT, borderValue=255)  # fmt: skip
    return out, np.vstack([m, [0.0, 0.0, 1.0]]), skew


# ------------------------------------------------------------------ which way is up


def _lines(ink: Any) -> list[tuple[int, int]]:
    """The rows each line of writing occupies, top to bottom."""
    on = np.concatenate([[False], ink.any(axis=1), [False]])
    edges = np.flatnonzero(on[1:] != on[:-1])
    pairs = zip(edges[::2], edges[1::2], strict=True)
    return [(int(a), int(b)) for a, b in pairs if b - a >= _MIN_LINE_PX]


def margins(gray: Any) -> dict[str, Any]:
    """How well the lines line up at each side: the spread (in px) of where they start and of
    where they end. Writing has a margin on the side it starts from."""
    ink = gray < 128
    starts, ends = [], []
    for a, b in _lines(ink):
        cols = np.flatnonzero(ink[a:b].any(axis=0))
        starts.append(cols[0])
        ends.append(cols[-1])
    if not starts:
        return {"lines": 0, "start_spread_px": 0.0, "end_spread_px": 0.0}
    return {"lines": len(starts), "start_spread_px": float(np.std(starts)),
            "end_spread_px": float(np.std(ends))}  # fmt: skip


def upside_down(gray: Any) -> tuple[bool | None, dict[str, Any]]:
    """Is this page (lines already level) upside down? ``True`` / ``False`` when the margin says
    so clearly, ``None`` when it does not - and ``None`` means *leave it alone*."""
    m = margins(gray)
    if m["lines"] < _MIN_LINES:
        return None, m
    a, b = m["start_spread_px"], m["end_spread_px"]
    if b >= _MIN_RAGGED_PX and b >= _MARGIN_RATIO * max(a, 1.0):
        return False, m  # the starts line up: the margin is on the left, where it belongs
    if a >= _MIN_RAGGED_PX and a >= _MARGIN_RATIO * max(b, 1.0):
        return True, m  # the "ends" line up: that is the left margin, turned to the right
    return None, m


def writing_axis(gray: Any) -> tuple[str | None, dict[str, Any]]:
    """Which way the writing runs: ``"across"`` (upright or upside down), ``"down"`` (sideways),
    or ``None`` when the page does not say. Letters are joined into words and the words counted
    by shape: a word is wider than it is tall, whichever way the paper lies.

    IM-S1's ``rotated_90_suspected`` (blank columns between the lines) was the first thing tried
    here and it missed all three sample scans turned on their side - a rule, a stamp or a little
    tilt fills the gaps it counts. Word shapes separated all thirty pages tried, upright and
    sideways, scanned, photographed and tilted 8 degrees: 13-72 words one way against 0-13 the
    other. So the turn is decided on this and the flag stays what IM-S1 made it, a record."""
    ink = (gray < 128).astype(np.uint8)
    marks = cv2.connectedComponentsWithStats(ink, connectivity=8)[2][1:, cv2.CC_STAT_HEIGHT]
    marks = marks[(marks >= quality._MIN_MARK_PX) & (marks < gray.shape[0] / 10)]
    if marks.size < _MIN_MARKS:
        return None, {"words_across": 0, "words_down": 0}
    k = max(3, int(float(np.median(marks)) * 0.6)) | 1  # about half a letter: joins a word
    stats = cv2.connectedComponentsWithStats(
        cv2.dilate(ink, np.ones((k, k), np.uint8)), connectivity=8)[2][1:]  # fmt: skip
    w = stats[:, cv2.CC_STAT_WIDTH].astype(np.float64)
    h = stats[:, cv2.CC_STAT_HEIGHT].astype(np.float64)
    longer = np.maximum(w, h)
    word = (longer >= 3 * k) & (longer < 0.9 * max(gray.shape[:2]))  # not a speck, not a rule
    across = int((w[word] > _WORD_ELONGATION * h[word]).sum())
    down = int((h[word] > _WORD_ELONGATION * w[word]).sum())
    why = {"words_across": across, "words_down": down}
    if across >= _MIN_WORDS and across >= _AXIS_RATIO * max(down, 1):
        return "across", why
    if down >= _MIN_WORDS and down >= _AXIS_RATIO * max(across, 1):
        return "down", why
    return None, why


# ------------------------------------------------------------------ one page, end to end


def straighten(gray: Any, *, full: bool = True) -> Straightened:
    """Make one page upright, or leave it exactly as it was.

    ``full=False`` is S1 as it has always been: the small-tilt deskew alone, with its matrix now
    recorded. ``full=True`` adds the perspective crop, the quarter turn and the half turn. A page
    that is held (:attr:`Straightened.hold`) comes back **unchanged**, with the identity matrix.
    """
    src = (int(gray.shape[1]), int(gray.shape[0]))
    if not full:
        out, m, skew = deskew(gray)
        return Straightened(out, m, src, ("deskew",) if out is not gray else (), 0, skew)

    def held(code: str) -> Straightened:
        return Straightened(gray, identity(), src, hold=code, measures=measures)

    img, total, turned = gray, identity(), 0
    steps: list[str] = []
    measures: dict[str, Any] = {}
    quad_src = None

    # 1. the table round the page: find the paper before judging the writing on it
    measures["background_visible"] = background_visible(img)
    if measures["background_visible"]:
        quad = find_page_quad(img)
        if quad is None:
            return held("edges_not_found")
        quad_src = tuple((float(x), float(y)) for x, y in quad)
        img, m = warp_perspective(img, quad)
        total = m @ total
        steps.append("perspective")

    # 2. a small tilt, so the lines are level before anything is read off them. Not after a
    #    crop: the paper's own edges are a better level than the ink is (see the module note).
    skew = 0.0
    if quad_src is None:
        img, m, skew = deskew(img)
        if not np.array_equal(m, identity()):
            total = m @ total
            steps.append("deskew")

    # 3. sideways: turn the lines level, then let step 4 say which of the two ways was right.
    #    A page that does not say which way its writing runs is not turned at all.
    axis, why = writing_axis(img)
    measures.update(why)
    measures["writing_axis"] = axis
    if axis is None:
        return Straightened(img, total, src, tuple(steps), 0, skew, quad_src, None, measures)
    if axis == "down":
        img, m = rotate90(img, 1)
        total, turned = m @ total, 90

    # 4. upside down
    flipped, why = upside_down(img)
    measures.update(why)
    measures["upside_down"] = flipped
    if flipped is None and axis == "down":
        return held("orientation_unsure")
    if flipped:
        img, m = rotate90(img, 2)
        total, turned = m @ total, (turned + 180) % 360
    if turned:
        steps.append(f"rotate{turned}")
    return Straightened(img, total, src, tuple(steps), turned, skew, quad_src, None, measures)


def hold_for(raw: bytes, mime: str, *, settings: Any = None) -> dict[str, Any] | None:
    """What the desk is told when a page cannot be made upright, or ``None``. ``None`` always
    while ``CDI_STRAIGHTEN`` is off: then no page is turned, cropped or held by this module.
    Shaped like ``quality.Verdict.as_json`` so UP-S2's retake screen shows it unchanged."""
    s = settings or get_settings()
    if not s.straighten:
        return None
    from cdi_adapter.ingest.pages import render_pages

    try:
        rendered = render_pages(raw, mime)
    except Exception:  # noqa: BLE001 - an undecodable upload is quality's verdict, not ours
        return None
    for n, png in enumerate(rendered, start=1):
        gray = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_GRAYSCALE)
        if gray is None:
            continue
        out = straighten(gray)
        if out.hold:
            return {"code": out.hold, "message": HOLD_REASONS[out.hold], "page": n,
                    "measures": out.measures}  # fmt: skip
    return None


# ------------------------------------------------------------------ the way back


def _apply(matrix: Any, points: Any) -> list[tuple[float, float]]:
    pts = np.asarray(points, dtype=np.float64).reshape(-1, 1, 2)
    out = cv2.perspectiveTransform(pts, np.asarray(matrix, dtype=np.float64))
    return [(float(x), float(y)) for x, y in out.reshape(-1, 2)]


def to_straightened(matrix: Any, points: Any) -> list[tuple[float, float]]:
    """Original pixels -> where they are on the straightened page."""
    return _apply(matrix, points)


def to_original(matrix: Any, points: Any) -> list[tuple[float, float]]:
    """Straightened pixels -> the original pixels they came from."""
    return _apply(np.linalg.inv(np.asarray(matrix, dtype=np.float64)), points)


def box_to_original(
    matrix: Any, box: tuple[float, float, float, float]
) -> list[tuple[float, float]]:
    """A box ``(x0, y0, x1, y1)`` on the straightened page, as its four corners in the original.
    Four corners, not a box: after a tilt or an angled shot the same ink is no longer upright."""
    x0, y0, x1, y1 = box
    return to_original(matrix, [(x0, y0), (x1, y0), (x1, y1), (x0, y1)])
