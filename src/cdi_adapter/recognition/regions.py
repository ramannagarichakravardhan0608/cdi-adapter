"""Find the text lines and say which are printed and which are handwritten (backlog IM-S3).

A prescription is a printed letterhead with a handwritten body. A print reader forced over the
handwriting returns confident nonsense; a handwriting reader run over the letterhead is slow and
no better. This module finds each line of writing on a page and labels it ``printed``,
``handwritten``, ``mixed`` or ``uncertain``, so each can go to the reader built for it
(:data:`READERS`) - and anything it cannot call goes to **both**. Nothing is dropped.

* :func:`detect_lines` - where the lines are;
* :func:`line_features` / :func:`classify_line` - what each one is, and the numbers behind it;
* :func:`crop_png` - the line cut from the page to the :class:`CropStandard`, with its position,
  its size and the fingerprint of the pixels it was cut from;
* :func:`detect_regions` - all of that for one page;
* :func:`score` - found lines against hand-drawn ones (recall and label accuracy by document
  type), which is the measurement the story's acceptance asks for.

**What tells print from handwriting was measured, and it is not what the story expected.** The
story names two cues - stroke-width variation and overlap with the print reader's boxes. Both
were measured on printed lines (rendered pages, the three sample scans, a real letterhead) and
on the handwritten body of a real photographed prescription, and neither separates them:

====================================  ================  ================
cue                                   printed           handwritten
====================================  ================  ================
stroke-width variation (story)        0.08 - 0.49       0.27 - 0.33
print reader's confidence (story)     0.92 - 1.00       0.80 - 1.00
**strokes on the page's axes**        **0.41 - 0.61**   **0.20 - 0.38**
====================================  ================  ================

Stroke width varies as much within a serif typeface as within a pen line, and the print reader
is *confident* on neat handwriting (it read a handwritten "150" at 1.00 and "Metformin" at
0.99) - which is exactly why a confident nonsense reading is the failure this story exists to
prevent. What does separate them is what type is: its strokes stand upright and lie level
(``axis_share``, the share of the writing's edge energy within 12 degrees of the vertical or the
horizontal). A hand slants and curves.

**The two mistakes are not equal, so the two labels are not given equally.** Handwriting sent to
the print reader alone is the dangerous one - the dose misread with confidence; print sent to
the handwriting readers costs time. So ``printed`` is said only of a line of eight letters or
more whose letters also keep to fixed heights: on real pages a handwritten "TSH", "HS" and
"1-0-1" stood as upright as type (0.45 - 0.61), and a form line - printed "Age ... Sex" with the
values written in - passed for print until letter heights were asked as well. A line too short,
too small or too mixed to call is ``uncertain``; a line whose parts are confidently different is
``mixed``. Both go to both readers. The other cues are still measured and recorded with every
line, so the answer-key run can test them.

**The limits are a first measurement, not a calibration.** Two real photographed prescriptions
are not an answer-key set. On the neat one, of 52 lines found, 34 were labelled right, 18 sent
to both readers and none wrong. On a hard one (hurried hand, a shadow across the page, Kannada
letterhead) 38 of 45 went to both readers, and one line was wrong: a printed Kannada address
called handwritten - a curved script has no upright strokes, so **print in a non-Latin script
is not recognised as print**. Line recall and label accuracy by document type (the story's AC3)
come from :func:`score` over the answer key (ENT-S3); ``scripts/measure_regions.py`` runs it.
**In the pipeline** (``ocr/service.py``): on the print reader's pass S3 runs this on every page
and records how many lines of each kind it found with the OCR run - always, and nothing else
changes. *Acting* on the labels is ``CDI_LINE_REGIONS``, off by default until that answer-key run
exists: with it on, each observation is stored with the label of the line it lies on, and a page
with handwritten lines is sent to the handwriting pass even when the classifier took it for
printed. Switching readers on an unmeasured label is the change the story itself says must first
"prove its worth" by ablation, which is why it is a switch and not the default.

**The crop standard** (:class:`CropStandard`, v3): a reader is handed a line with a little paper
round it, so no stroke is cut, and never a sliver - a crop shorter than the minimum is enlarged.
The fingerprint is always taken from the pixels as they are on the page, before any enlarging,
so it identifies the evidence; the padding, the enlargement and both sizes are recorded with
every crop, so reader errors can later be sliced by crop size.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from cdi_adapter import _cpu
from cdi_adapter.config import get_settings

# thread caps must be in place before OpenCV / NumPy load (§7.4 #4), as in ingest/pages.py
_THREADS = _cpu.apply_thread_caps(get_settings().job_max_workers)

import cv2  # noqa: E402
import numpy as np  # noqa: E402

cv2.setNumThreads(_THREADS)

LABELS = ("printed", "handwritten", "mixed", "uncertain")  # ocr_observation.region_kind's values
# label -> the reader paths the line is for. "print" is RapidOCR; "handwriting" is the pair of
# handwriting readers. Anything not plainly one kind goes to both: nothing is dropped (AC2).
READERS: dict[str, tuple[str, ...]] = {
    "printed": ("print",),
    "handwritten": ("handwriting",),
    "mixed": ("print", "handwriting"),
    "uncertain": ("print", "handwriting"),
}

# --- finding ink and lines
_INK_BELOW_PAPER = 45  # grey levels darker than the local paper tone: that pixel is ink
_MIN_MARK_PX = 4  # shorter marks are specks
_RULE_ASPECT = 12.0  # a mark this many times longer than it is tall is a ruled line, not writing
_JOIN_WORDS = 2.5  # letters are joined across gaps up to this many letter-heights: one line
_MIN_LINE_HEIGHT = 0.6  # of the page's letter height: lower is a dash or a stray mark
_TALL_LINE = 2.4  # of the page's letter height: taller than this is two lines that touch
_VALLEY = 0.25  # a row with this little of the block's ink (against its fullest row) parts lines

# --- telling print from handwriting (see the module note for where these come from)
_MIN_INK_PX = 150  # less writing than this cannot be measured: uncertain
_MIN_HEIGHT_PX = 14  # nor can a line this low: its strokes are a pixel or two wide
_MIN_ASPECT = 1.5  # nor can a mark that is not at least this much wider than tall (a numeral)
_PRINT_AXIS = 0.44  # at or above: the strokes stand upright and lie level, as type does
_PRINT_LETTERS = 8  # ...on a line of at least this many letters: block capitals and dose
#                     patterns written by hand ("TSH", "1-0-1") stand as upright as type does
_HAND_AXIS = 0.38  # at or below: they slant and curve, as a hand does
_PRINT_MAX_HEIGHT_CV = 0.27  # type comes in fixed heights: more spread than this is not type
_HAND_MIN_HEIGHT_CV = 0.12  # a hand's letters vary: less spread than this is not a hand
_AXIS_WINDOW_DEG = 12.0
_LOW_READ_CONF = 0.80  # a print reader this unsure of a "printed" line makes it uncertain
_SPLIT_GAP = 2.0  # a gap this many letter-heights wide splits a line into parts (for "mixed")


@dataclass(frozen=True)
class CropStandard:
    """What every crop handed to a reader must meet (IM-S3 functional 5)."""

    min_height_px: int = 32  # a reader is never given a line shorter than this
    pad_fraction: float = 0.15  # paper kept round the line, as a share of its height
    min_pad_px: int = 4  # and never less than this, so no stroke is cut
    max_scale: float = 4.0  # enlarging beyond this adds blur, not detail


STANDARD = CropStandard()


@dataclass(frozen=True)
class Crop:
    """One line cut from the page: the picture, where it came from, and its fingerprint."""

    png: bytes  # what the reader is given (padded; enlarged if it was too small)
    sha256: str  # of the padded pixels as they are on the page, before any enlarging
    bbox: tuple[int, int, int, int]  # the line itself, in page pixels
    padded_bbox: tuple[int, int, int, int]  # what was cut, in page pixels
    pad_px: int
    scale: float  # 1.0 unless the crop was enlarged to meet the standard
    source_size: tuple[int, int]  # (width, height) of the cut, on the page
    size: tuple[int, int]  # (width, height) of what the reader is given

    def record(self) -> dict[str, Any]:
        """The crop's size and padding, kept with the observation (IM-S3 technical 3)."""
        return {"sha256": self.sha256, "bbox": list(self.bbox),
                "padded_bbox": list(self.padded_bbox), "pad_px": self.pad_px,
                "scale": round(self.scale, 4), "source_size": list(self.source_size),
                "size": list(self.size)}  # fmt: skip


@dataclass(frozen=True)
class Region:
    """One line of writing on a page."""

    bbox: tuple[int, int, int, int]  # x0, y0, x1, y1 in page pixels
    label: str  # one of LABELS
    reading_order: int
    features: dict[str, Any] = field(default_factory=dict)
    crop: Crop | None = None

    @property
    def readers(self) -> tuple[str, ...]:
        return READERS[self.label]

    def record(self) -> dict[str, Any]:
        return {"bbox": list(self.bbox), "label": self.label, "readers": list(self.readers),
                "reading_order": self.reading_order, "features": self.features,
                "crop": self.crop.record() if self.crop else None}  # fmt: skip


# ------------------------------------------------------------------ ink and lines


def _gray(png: bytes) -> Any | None:
    return cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_GRAYSCALE)


def ink_mask(gray: Any) -> Any:
    """Where the writing is, judged against the paper *round it* rather than against a fixed
    grey: a photograph's paper is grey and unevenly lit, a render's is white, and blue ballpoint
    on grey paper is never darker than mid-grey at all."""
    k = max(15, (min(gray.shape[:2]) // 20) | 1)
    paper = cv2.blur(cv2.morphologyEx(gray, cv2.MORPH_CLOSE, np.ones((k, k), np.uint8)), (k, k))
    return ((paper.astype(np.int16) - gray.astype(np.int16)) > _INK_BELOW_PAPER).astype(np.uint8)


def _marks(ink: Any) -> tuple[Any, float]:
    """The writing alone - rules, brackets and specks taken out - and the page's typical letter
    height. An underline is not writing: left in, it welds a heading to the line below and reads
    as the most "printed" stroke on the page."""
    count, labels, stats, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    if count <= 1:
        return np.zeros_like(ink), 0.0
    w, h = stats[:, cv2.CC_STAT_WIDTH], stats[:, cv2.CC_STAT_HEIGHT]
    keep = (np.maximum(w, h) >= _MIN_MARK_PX) & (h < ink.shape[0] / 10)
    keep &= w < _RULE_ASPECT * np.maximum(h, 1)
    keep[0] = False  # the paper
    if not keep.any():
        return np.zeros_like(ink), 0.0
    return keep[labels].astype(np.uint8), float(np.median(h[keep]))


def _letter_height(block: Any) -> float:
    """The typical letter height within one block of writing."""
    tall = cv2.connectedComponentsWithStats(np.ascontiguousarray(block), connectivity=8)[2][
        1:, cv2.CC_STAT_HEIGHT]  # fmt: skip
    tall = tall[tall >= _MIN_MARK_PX]
    return float(np.median(tall)) if tall.size else 0.0


def _split_rows(marks: Any, box: tuple[int, int, int, int], letter: float) -> list[
        tuple[int, int, int, int]]:  # fmt: skip
    """A block of writing taller than one line, parted where its ink thins out between lines.
    Handwritten lines touch - a descender reaches the line below - so they arrive joined."""
    x0, y0, x1, y1 = box
    rows = marks[y0:y1, x0:x1].sum(axis=1).astype(np.float64)
    k = max(3, int(letter * 0.3)) | 1
    smooth = np.convolve(rows, np.ones(k) / k, mode="same")
    full = smooth > _VALLEY * float(np.percentile(smooth, 90))
    edges = np.flatnonzero(np.diff(np.concatenate([[False], full, [False]]).astype(np.int8)))
    bands = [(int(a), int(b)) for a, b in zip(edges[::2], edges[1::2], strict=True)
             if b - a >= _MIN_LINE_HEIGHT * letter * 0.5]  # fmt: skip
    if len(bands) < 2:
        return [box]
    # each line runs from the thinnest row above its band to the thinnest row below it, so its
    # ascenders and descenders stay with it
    cuts = [0]
    for (_, end), (start, _) in zip(bands, bands[1:], strict=False):
        cuts.append(end + int(np.argmin(smooth[end:start])) if start > end else end)
    cuts.append(y1 - y0)
    out = []
    for a, b in zip(cuts, cuts[1:], strict=False):
        part = marks[y0 + a : y0 + b, x0:x1]
        rows_on, cols = np.flatnonzero(part.any(axis=1)), np.flatnonzero(part.any(axis=0))
        if rows_on.size and rows_on[-1] + 1 - rows_on[0] >= _MIN_LINE_HEIGHT * letter:
            out.append((int(x0 + cols[0]), int(y0 + a + rows_on[0]), int(x0 + cols[-1] + 1),
                        int(y0 + a + rows_on[-1] + 1)))  # fmt: skip
    return out or [box]


def detect_lines(gray: Any) -> list[tuple[int, int, int, int]]:
    """The lines of writing on a page, top to bottom then left to right, as ``(x0, y0, x1, y1)``.

    Letters are joined sideways into lines and never up or down, so two lines set close together
    stay two; where handwritten lines touch anyway, the block is parted at the thin rows between
    them. Rules, brackets and specks are left out first. Works on a line, not a paragraph: two
    columns side by side are two lines, which a prescription's "BP / PR / Wt" corner needs."""
    marks, letter = _marks(ink_mask(gray))
    if letter <= 0:
        return []
    reach = max(3, int(letter * _JOIN_WORDS)) | 1
    joined = cv2.dilate(marks, np.ones((1, reach), np.uint8))
    out: list[tuple[int, int, int, int]] = []
    for x, y, w, h, _ in cv2.connectedComponentsWithStats(joined, connectivity=8)[2][1:]:
        if h < _MIN_LINE_HEIGHT * letter or w < 2 * letter:
            continue
        cols = np.flatnonzero(marks[y : y + h, x : x + w].any(axis=0))  # undo the sideways reach
        if not cols.size:
            continue
        box = (int(x + cols[0]), int(y), int(x + cols[-1] + 1), int(y + h))
        # judged against the letters of this block, not of the page: a letterhead's small type
        # and the large handwriting under it are on the same page
        local = _letter_height(marks[y : y + h, x : x + w]) or letter
        out.extend(_split_rows(marks, box, local) if h > _TALL_LINE * local else [box])
    return _reading_order(out, letter)


def _reading_order(boxes: list[tuple[int, int, int, int]],
                   letter: float) -> list[tuple[int, int, int, int]]:  # fmt: skip
    """Top to bottom in rows about a letter high, left to right within a row."""
    rows = sorted(boxes, key=lambda b: ((b[1] + b[3]) / 2, b[0]))
    out: list[tuple[int, int, int, int]] = []
    row: list[tuple[int, int, int, int]] = []
    for b in rows:
        if row and (b[1] + b[3]) / 2 - (row[0][1] + row[0][3]) / 2 > letter:
            out.extend(sorted(row, key=lambda r: r[0]))
            row = []
        row.append(b)
    out.extend(sorted(row, key=lambda r: r[0]))
    return out


# ------------------------------------------------------------------ printed or handwritten


def line_features(gray: Any, bbox: tuple[int, int, int, int],
                  marks: Any | None = None) -> dict[str, Any] | None:  # fmt: skip
    """The numbers one line is judged on, or ``None`` when it holds too little to measure.
    ``marks`` is the page's writing (:func:`_marks`); rules are not in it, so an underline is
    not counted as a level stroke."""
    x0, y0, x1, y1 = bbox
    crop = gray[y0:y1, x0:x1]
    if marks is None:
        marks = _marks(ink_mask(gray))[0]
    mask = np.ascontiguousarray(marks[y0:y1, x0:x1])
    too_little = int(mask.sum()) < _MIN_INK_PX or (y1 - y0) < _MIN_HEIGHT_PX
    if crop.size == 0 or too_little or (x1 - x0) < _MIN_ASPECT * (y1 - y0):
        return None
    stats = cv2.connectedComponentsWithStats(mask, connectivity=8)[2][1:]
    letters = stats[stats[:, cv2.CC_STAT_HEIGHT] >= 0.2 * (y1 - y0)]
    if len(letters) == 0:
        return None
    tall = letters[:, cv2.CC_STAT_HEIGHT].astype(np.float64)
    wide = letters[:, cv2.CC_STAT_WIDTH].astype(np.float64)
    # which way the strokes run: type's stand upright and lie level, a hand's slant and curve.
    # Only the edges of the writing itself are counted.
    gx = cv2.Sobel(crop, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(crop, cv2.CV_32F, 0, 1, ksize=3)
    energy = np.hypot(gx, gy) * cv2.dilate(mask, np.ones((3, 3), np.uint8))
    lit = energy > 0
    strong = energy > (np.percentile(energy[lit], 50) if lit.any() else 0.0)
    angle = np.degrees(np.arctan2(gy[strong], gx[strong])) % 90.0
    weight = energy[strong]
    on_axis = np.minimum(angle, 90.0 - angle) < _AXIS_WINDOW_DEG
    axis_share = float(weight[on_axis].sum() / max(float(weight.sum()), 1e-6))
    # stroke width: measured and kept because the story names it, not decided on (module note)
    depth = cv2.distanceTransform(mask, cv2.DIST_L2, 3)
    ridge = depth[(depth > 0) & (depth >= cv2.dilate(depth, np.ones((3, 3), np.uint8)) - 1e-6)]
    return {
        "letters": int(len(letters)),
        "axis_share": round(axis_share, 4),
        "height_cv": round(float(tall.std() / max(tall.mean(), 1e-6)), 4),
        "letter_aspect": round(float(np.median(wide / tall)), 4),
        "stroke_width_cv": round(float(ridge.std() / ridge.mean()), 4) if ridge.size else 0.0,
        "height_px": int(y1 - y0),
    }


def _call(features: dict[str, Any] | None) -> str:
    """``printed`` / ``handwritten`` when the strokes say so and the letter heights do not
    contradict it; ``uncertain`` otherwise."""
    if features is None:
        return "uncertain"
    axis, cv = features["axis_share"], features["height_cv"]
    plain = axis >= _PRINT_AXIS and features["letters"] >= _PRINT_LETTERS
    if plain and cv <= _PRINT_MAX_HEIGHT_CV:
        return "printed"
    if axis <= _HAND_AXIS and (cv >= _HAND_MIN_HEIGHT_CV or features["letters"] < 4):
        return "handwritten"
    return "uncertain"


def _parts(bbox: tuple[int, int, int, int], ink: Any) -> list[
        tuple[int, int, int, int]]:  # fmt: skip
    """A line split at its widest gaps: a form's printed label and the value written after it
    sit on one line with a clear space between them."""
    x0, y0, x1, y1 = bbox
    cols = ink[y0:y1, x0:x1].any(axis=0)
    gap = max(2, int((y1 - y0) * 0.5 * _SPLIT_GAP))
    parts, start, blank = [], None, 0
    for i, on in enumerate(cols):
        if on:
            if start is None:
                start = i
            blank = 0
        elif start is not None:
            blank += 1
            if blank >= gap:
                parts.append((x0 + start, y0, x0 + i - blank + 1, y1))
                start, blank = None, 0
    if start is not None:
        parts.append((x0 + start, y0, x0 + len(cols) - blank, y1))
    return parts


def classify_line(gray: Any, bbox: tuple[int, int, int, int], marks: Any | None = None,
                  ocr: list[Any] | None = None) -> tuple[str, dict[str, Any]]:  # fmt: skip
    """Label one line and return the label with what it was decided on.

    ``ocr`` is the print reader's lines for the page, if it has already run (objects with
    ``bbox`` and ``conf``, as ``ocr.rapid.OcrLine``). It can only make a line *less* certain: a
    line that looks printed but that the print reader itself was unsure of becomes
    ``uncertain``. It never makes a line "printed" - a print reader is confident on neat
    handwriting, which is the very mistake this module is here to catch.
    """
    if marks is None:
        marks = _marks(ink_mask(gray))[0]
    whole = line_features(gray, bbox, marks)
    label = _call(whole)
    features: dict[str, Any] = dict(whole or {"letters": 0, "height_px": bbox[3] - bbox[1]})
    # mixed: the line's parts, each measured on its own, are confidently of different kinds
    calls = {_call(line_features(gray, p, marks)) for p in _parts(bbox, marks)}
    if {"printed", "handwritten"} <= calls:
        label = "mixed"
    conf = _read_confidence(bbox, ocr)
    if conf is not None:
        features["print_read_conf"] = round(conf, 4)
        if label == "printed" and conf < _LOW_READ_CONF:
            label = "uncertain"
    return label, features


def _read_confidence(bbox: tuple[int, int, int, int], ocr: list[Any] | None) -> float | None:
    """The print reader's confidence over this line: the lowest among its boxes that lie mostly
    inside the line, or ``None`` when it read nothing there."""
    if not ocr:
        return None
    x0, y0, x1, y1 = bbox
    inside = []
    for ln in ocr:
        a0, b0, a1, b1 = (float(v) for v in ln.bbox)
        overlap = max(0.0, min(x1, a1) - max(x0, a0)) * max(0.0, min(y1, b1) - max(y0, b0))
        if overlap >= 0.5 * max((a1 - a0) * (b1 - b0), 1.0):
            inside.append(float(ln.conf))
    return min(inside) if inside else None


# ------------------------------------------------------------------ the crop standard


def _fingerprint(pixels: Any) -> str:
    """SHA-256 of a cut's pixels with its size, as ``ocr.service.crop_hashes`` fingerprints a
    box: two cuts of different shape never collide."""
    h = hashlib.sha256(f"{pixels.shape[1]}x{pixels.shape[0]}:".encode())
    h.update(np.ascontiguousarray(pixels).tobytes())
    return h.hexdigest()


def crop_png(page: Any, bbox: tuple[int, int, int, int],
             standard: CropStandard = STANDARD) -> Crop:  # fmt: skip
    """Cut one line from the page to the crop standard.

    ``page`` is the page picture as an array (grey or colour) or as PNG bytes; the line is cut
    from *it*, so pass the original. The cut keeps a margin of paper round the line; if it is
    still shorter than the standard's minimum it is enlarged (cubic, capped at ``max_scale``).
    The fingerprint is of the cut before enlarging: it names pixels that are on the page.
    """
    img = cv2.imdecode(np.frombuffer(page, np.uint8), cv2.IMREAD_UNCHANGED) \
        if isinstance(page, bytes | bytearray) else page  # fmt: skip
    if img is None:
        raise ValueError("the page picture could not be decoded")
    height, width = img.shape[:2]
    x0, y0, x1, y1 = (int(v) for v in bbox)
    pad = max(standard.min_pad_px, int(round((y1 - y0) * standard.pad_fraction)))
    px0, py0 = max(0, x0 - pad), max(0, y0 - pad)
    px1, py1 = min(width, x1 + pad), min(height, y1 + pad)
    if px1 <= px0 or py1 <= py0:
        raise ValueError(f"the box {bbox} lies outside the page")
    cut = img[py0:py1, px0:px1]
    scale = 1.0
    out = cut
    if cut.shape[0] < standard.min_height_px:
        scale = min(standard.max_scale, standard.min_height_px / cut.shape[0])
        size = (max(1, int(round(cut.shape[1] * scale))), max(1, int(round(cut.shape[0] * scale))))
        out = cv2.resize(cut, size, interpolation=cv2.INTER_CUBIC)
    ok, png = cv2.imencode(".png", out)
    if not ok:
        raise ValueError("the crop could not be encoded")
    return Crop(png.tobytes(), _fingerprint(cut), (x0, y0, x1, y1), (px0, py0, px1, py1), pad,
                scale, (int(cut.shape[1]), int(cut.shape[0])),
                (int(out.shape[1]), int(out.shape[0])))  # fmt: skip


def meets_standard(crop: Crop, standard: CropStandard = STANDARD) -> bool:
    """Is this crop fit to hand to a reader? Tall enough, or enlarged as far as is allowed."""
    return crop.size[1] >= standard.min_height_px or crop.scale >= standard.max_scale


# ------------------------------------------------------------------ one page


def detect_regions(page: Any, *, ocr: list[Any] | None = None, crops: bool = True,
                   standard: CropStandard = STANDARD) -> list[Region]:  # fmt: skip
    """Every line on one page, labelled, in reading order, each with its crop.

    ``page`` is PNG bytes or an image array - the picture the crops are cut from, so the
    original. ``ocr`` is optional (see :func:`classify_line`)."""
    img = cv2.imdecode(np.frombuffer(page, np.uint8), cv2.IMREAD_UNCHANGED) \
        if isinstance(page, bytes | bytearray) else page  # fmt: skip
    if img is None:
        return []
    gray = img if img.ndim == 2 else cv2.cvtColor(img[:, :, :3], cv2.COLOR_BGR2GRAY)
    marks = _marks(ink_mask(gray))[0]
    out = []
    for n, bbox in enumerate(detect_lines(gray)):
        label, features = classify_line(gray, bbox, marks, ocr)
        out.append(Region(bbox, label, n, features,
                          crop_png(img, bbox, standard) if crops else None))  # fmt: skip
    return out


def summary(regions: list[Region]) -> dict[str, Any]:
    """How many lines of each kind a page has, and how many crops had to be enlarged."""
    counts = {label: sum(1 for r in regions if r.label == label) for label in LABELS}
    return {"lines": len(regions), **counts,
            "to_both_readers": counts["mixed"] + counts["uncertain"],
            "enlarged_crops": sum(1 for r in regions if r.crop and r.crop.scale > 1.0)}  # fmt: skip


# ------------------------------------------------------------------ measuring against an answer key


def iou(a: tuple[float, ...] | list[float], b: tuple[float, ...] | list[float]) -> float:
    """How much two boxes are the same box: overlap over union, 0 to 1."""
    w = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    h = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    both = w * h
    either = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - both
    return both / either if either > 0 else 0.0


def score(found: list[dict[str, Any]], truth: list[dict[str, Any]], *,
          min_iou: float = 0.5) -> dict[str, Any]:  # fmt: skip
    """Found lines against hand-drawn ones, for one page.

    Both are lists of ``{"bbox": [x0, y0, x1, y1], "label": ...}``. Each drawn line is matched
    to the found line that overlaps it best (each found line used once). **Recall** is the share
    of drawn lines that were found; **label accuracy** is, of those found, the share given the
    drawn label - where a line sent to both readers (``mixed`` / ``uncertain``) is *safe* but
    not *right*, and is counted separately so caution cannot pass for accuracy.
    """
    used: set[int] = set()
    matched = right = safe = wrong = 0
    for t in truth:
        best, best_iou = None, min_iou
        for i, f in enumerate(found):
            if i in used:
                continue
            v = iou(t["bbox"], f["bbox"])
            if v >= best_iou:
                best, best_iou = i, v
        if best is None:
            continue
        used.add(best)
        matched += 1
        got = found[best]["label"]
        if got == t["label"]:
            right += 1
        elif len(READERS[got]) == 2:
            safe += 1  # not the drawn label, but it reaches the right reader all the same
        else:
            wrong += 1  # sent to the one reader that cannot read it
    return {"truth_lines": len(truth), "found_lines": len(found), "matched": matched,
            "recall": matched / len(truth) if truth else None,
            "label_right": right, "label_safe": safe, "label_wrong": wrong,
            "label_accuracy": right / matched if matched else None,
            "extra_lines": len(found) - len(used)}  # fmt: skip


def score_by_type(pages: list[dict[str, Any]], *, min_iou: float = 0.5) -> dict[str, Any]:
    """:func:`score` summed over pages and reported by document type (IM-S3 AC3). Each page is
    ``{"doc_type": ..., "found": [...], "truth": [...]}``."""
    out: dict[str, dict[str, Any]] = {}
    for page in pages:
        s = score(page["found"], page["truth"], min_iou=min_iou)
        agg = out.setdefault(page.get("doc_type") or "unknown", {
            "pages": 0, "truth_lines": 0, "found_lines": 0, "matched": 0, "label_right": 0,
            "label_safe": 0, "label_wrong": 0, "extra_lines": 0})  # fmt: skip
        agg["pages"] += 1
        for k in ("truth_lines", "found_lines", "matched", "label_right", "label_safe",
                  "label_wrong", "extra_lines"):  # fmt: skip
            agg[k] += s[k]
    for agg in out.values():
        agg["recall"] = agg["matched"] / agg["truth_lines"] if agg["truth_lines"] else None
        agg["label_accuracy"] = agg["label_right"] / agg["matched"] if agg["matched"] else None
    return out
