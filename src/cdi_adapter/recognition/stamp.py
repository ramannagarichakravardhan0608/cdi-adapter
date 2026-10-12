"""Finding a rubber stamp or seal in the picture itself (backlog EX-S2, AC4 and FR6).

A stamp is looked for as what it is on the page: an **outline in coloured ink** - a circle, an
oval or a box - **with writing inside it**. The rule is about shape, not about any one clinic's
stamp:

1. the ink: saturated blue / violet or red pixels (a stamp pad's colours);
2. an outline: a closed curve whose own area fills the circle, oval or box fitted to it, and
   whose border is inked nearly all the way round;
3. writing inside: some ink within the outline, but not a filled patch (a logo, a banner);
4. a stamp's size: not a circle drawn round a number, not a box that spans the page.

What this does NOT find, by design, and why the reading model's own answer stays the second
source (``extract.doctor.stamp_of``):

* a stamp in **black** ink - it cannot be told from print by colour;
* a stamp with **no outline** (a few lines of violet text) - by shape it is handwriting;
* a stamp too faint or too broken for its outline to close.

So a stamp found here is a **yes**; nothing found here is *not* a no.

ASSUMPTIONS, not measured: every threshold below was set on synthetic pages (drawn outlines,
print and pen strokes). No recall or false-alarm rate has been measured on real stamped
prescriptions; that needs a labelled set.

``find_stamps`` is pure (bytes in, regions out). ``lines_inside`` says which OCR lines sit in a
stamp, so its text can be kept apart from the letterhead's.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

MAX_SIDE = 1400  # the page is looked at no larger than this (speed); regions are scaled back
MIN_SAT, MIN_VAL, MAX_VAL = 60, 40, 245  # coloured ink: not grey print, not paper
INKS = {"blue": ((95, 165),), "red": ((0, 10), (170, 180))}  # OpenCV hue, 0-180
MIN_SIZE, MAX_SIZE = 0.07, 0.50  # an outline's shorter / longer side, as a part of the page's
MIN_ROUND, MAX_LONG = 0.55, 3.5  # an oval this flat, a box this long, is not a stamp
RING = {"round": 0.80, "rectangular": 0.85}  # how much of the outline must be inked
HUGS = 0.80  # the curve's own area against the shape fitted to it
INSIDE = (0.015, 0.50)  # ink within the outline: some writing, not a filled patch


@dataclass(frozen=True)
class Stamp:
    bbox: list[int]  # [x0, y0, x1, y1] in the picture's own pixels
    shape: str  # round | rectangular
    ink: str  # blue | red
    score: float  # how much of its outline is inked, 0-1

    def as_json(self) -> dict[str, Any]:
        return {"bbox": self.bbox, "shape": self.shape, "ink": self.ink,
                "score": round(self.score, 3)}  # fmt: skip


def _coverage(mask: Any, points: Any) -> float:
    h, w = mask.shape[:2]
    hits = total = 0
    for x, y in points:
        xi, yi = int(round(x)), int(round(y))
        if 0 <= xi < w and 0 <= yi < h:
            total += 1
            hits += 1 if mask[yi, xi] else 0
    return hits / total if total >= len(points) * 0.9 else 0.0  # an outline off the picture


def _overlap(a: list[int], b: list[int]) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    if w <= 0 or h <= 0:
        return 0.0
    small = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return (w * h) / small if small else 0.0


def find_stamps(image: bytes | None) -> list[Stamp]:
    """The stamps in a page picture (PNG / JPEG bytes), largest first; [] when none is found or
    the picture cannot be read. Never raises: a stamp is a courtesy, the page is read anyway."""
    if not image:
        return []
    try:
        import cv2
        import numpy as np

        page = cv2.imdecode(np.frombuffer(image, np.uint8), cv2.IMREAD_COLOR)
        if page is None:
            return []
        full_h, full_w = page.shape[:2]
        scale = min(1.0, MAX_SIDE / max(full_h, full_w))
        if scale < 1.0:
            page = cv2.resize(page, (round(full_w * scale), round(full_h * scale)),
                              interpolation=cv2.INTER_AREA)  # fmt: skip
        h, w = page.shape[:2]
        side = min(h, w)
        hsv = cv2.cvtColor(page, cv2.COLOR_BGR2HSV)
        near = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (max(3, side // 160),) * 2)
        found: list[Stamp] = []
        for ink, hues in INKS.items():
            mask = np.zeros((h, w), np.uint8)
            for lo, hi in hues:
                mask |= cv2.inRange(hsv, (lo, MIN_SAT, MIN_VAL), (hi, 255, MAX_VAL))
            if not mask.any():
                continue
            thick = cv2.dilate(mask, near)  # an outline printed by a worn stamp has gaps
            contours, _ = cv2.findContours(thick, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
            for c in contours:
                if len(c) < 40:
                    continue
                x, y, bw, bh = cv2.boundingRect(c)
                short, long_ = min(bw, bh), max(bw, bh)
                if short < MIN_SIZE * side or long_ > MAX_SIZE * max(h, w):
                    continue
                area = cv2.contourArea(c)
                best: tuple[str, float] | None = None
                # a circle or an oval
                (cx, cy), (d1, d2), angle = cv2.fitEllipse(c)
                if min(d1, d2) > 0 and min(d1, d2) / max(d1, d2) >= MIN_ROUND:
                    fitted = np.pi * d1 * d2 / 4
                    if fitted and HUGS <= area / fitted <= 1 / HUGS:
                        ring = cv2.ellipse2Poly((round(cx), round(cy)),
                                                (round(d1 / 2), round(d2 / 2)), round(angle),
                                                0, 360, 5)  # fmt: skip
                        score = _coverage(thick, ring)
                        if score >= RING["round"]:
                            best = ("round", score)
                # a box
                if best is None and long_ / max(short, 1) <= MAX_LONG:
                    rect = cv2.minAreaRect(c)
                    rw, rh = rect[1]
                    if rw > 0 and rh > 0 and HUGS <= area / (rw * rh) <= 1 / HUGS:
                        corners = cv2.boxPoints(rect)
                        edge = [corners[i] + (corners[(i + 1) % 4] - corners[i]) * t
                                for i in range(4) for t in np.linspace(0.05, 0.95, 18)]  # fmt: skip
                        score = _coverage(thick, edge)
                        if score >= RING["rectangular"]:
                            best = ("rectangular", score)
                if best is None:
                    continue
                # writing inside, and not a filled patch: the raw ink within the shrunk outline
                inner = np.zeros((h, w), np.uint8)
                cv2.drawContours(inner, [c], -1, 255, -1)
                shrink = cv2.getStructuringElement(cv2.MORPH_ELLIPSE,
                                                   (max(3, short // 5),) * 2)  # fmt: skip
                inner = cv2.erode(inner, shrink)
                room = int(inner.sum() // 255)
                inked = int((mask & inner).sum() // 255)
                if not room or not INSIDE[0] <= inked / room <= INSIDE[1]:
                    continue
                box = [round(x / scale), round(y / scale), round((x + bw) / scale),
                       round((y + bh) / scale)]  # fmt: skip
                found.append(Stamp(box, best[0], ink, float(best[1])))
        found.sort(key=lambda s: -(s.bbox[2] - s.bbox[0]) * (s.bbox[3] - s.bbox[1]))
        kept: list[Stamp] = []
        for s in found:  # the outline's inner and outer edge are one stamp
            if all(_overlap(s.bbox, k.bbox) < 0.5 for k in kept):
                kept.append(s)
        return kept
    except Exception:  # noqa: BLE001 - a picture this cannot read has no stamp to report
        return []


def lines_inside(stamp: Stamp | dict[str, Any], blocks: list[dict[str, Any]]) -> list[int]:
    """The indexes of the OCR lines that sit in the stamp: a line whose centre is inside its
    box, and that is a line - not a reading of the whole page given one box for everything."""
    box = stamp.bbox if isinstance(stamp, Stamp) else stamp.get("bbox") or []
    if len(box) != 4:
        return []
    x0, y0, x1, y1 = box
    region = max(1, (x1 - x0) * (y1 - y0))
    inside = []
    for i, b in enumerate(blocks):
        bb = b.get("bbox") or []
        if len(bb) != 4:
            continue
        cx, cy = (bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2
        if x0 <= cx <= x1 and y0 <= cy <= y1 and (bb[2] - bb[0]) * (bb[3] - bb[1]) <= 2 * region:
            inside.append(i)
    return inside
