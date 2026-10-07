"""Check every picture first (backlog IM-S1; used by UP-S2; ARCHITECTURE §5 S1).

A person at the desk photographs a prescription. If the photo cannot be read they should be told
**at the desk, in plain words, while the page is still in front of them** - not after a reviewer
finds an empty extraction. This module looks at each page before anything else reads it:

* :func:`measure` - what the picture is like, as numbers. **Always run and always recorded**, pass
  or fail, in ``document_page.preproc.quality`` (IM-S1 functional 3), so the limits can later be
  moved on evidence rather than on taste.
* :func:`assess_png` / :func:`assess` - those numbers judged against the configured limits, giving
  a verdict with a specific reason code that UP-S2 shows the person.

**What is measured**, each one checked against real rendered pages and adversarial copies of them
(the numbers are asserted in ``tests/test_recognition_unit.py``):

==================  ====================================================  ====================
measurement         what it is                                            reason code
==================  ====================================================  ====================
``extent_px``       the shorter side of the page                          ``too_small``
``ink_fraction``    share of pixels darker than mid-grey                  ``too_dark``, ``blank``
``glare_fraction``  share of pixels burnt out to white (photos only)      ``glare``
``text_height_px``  the median height of the written marks (v3)           ``text_too_small``
``sharpness``       variance of the Laplacian                             ``blurred``
``rotated_90``      the lines run down the page instead of across         flag only
==================  ====================================================  ====================

Three things the measurements taught, which is why the checks look the way they do:

* **Sharpness cannot see tiny text.** A photo taken from far away is perfectly sharp - shrinking
  a page to a fifth *raises* the Laplacian variance (844 to 7471) - while its letters fall from 19
  px to 4 px. Text height is measured on its own for exactly this case (the v3 requirement).
* **Glare only means something on a photograph.** Real paper photographs as grey (about 210) and
  glare burns a patch to 255, so the burnt-out share reads 0.000, 0.028, 0.174 as the glare grows.
  A digital render or a clean flatbed scan has a paper tone that is *already* white, where the
  same number is about 0.98 and means nothing - so glare is judged only when ``paper_tone`` is
  below ``CDI_QUALITY_PAPER_WHITE``.
* **Brightness is not judged at all.** A document is mostly paper: a good page and a ruined one
  both average about 250, and the 5th percentile is white on any sparse page. Ink is what counts.

**Sideways is a flag, not a failure.** A page whose lines run down it can still be read once it
is turned, and turning it is IM-S2's work, so ``rotated_90_suspected`` is recorded and nothing is
held. It finds a quarter turn - the gaps between lines become blank columns instead of blank rows -
and cannot see an upside-down page, which looks identical by this measure (also IM-S2).

**The limits are settings, not code** (``CDI_QUALITY_*``), and holding is **off by default**
(``CDI_QUALITY_CHECK``). Switching it on starts rejecting pages, and a limit set too high rejects
good ones; "a readable picture must not be rejected" is this story's own rule, and its
false-reject rate has to be measured on the answer-key set (ENT-S3) before the limits are trusted.
Measuring is not gated: it costs well under a second a page on a CPU, and it is what that tuning
will need.

The wording in :data:`REASONS` is draft - UP-S2's open question is who approves the plain-language
messages. The codes are stable; the sentences are theirs to replace.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# code -> what the person at the desk is told. DRAFT wording (UP-S2 open question).
REASONS: dict[str, str] = {
    "blurred": (
        "This photo is too blurry to read. Hold the phone still, let it focus, and take it again."
    ),
    "too_dark": (
        "This photo is too dark to read. Move into better light, avoid your own shadow, and take "
        "it again."
    ),
    "glare": (
        "Light is reflecting off this page and hiding part of the writing. Tilt the page away "
        "from the light and take it again."
    ),
    "blank": (
        "No writing could be found on this page. Check it is the right side of the paper, keep "
        "the glare off it, and take it again."
    ),
    "text_too_small": (
        "The writing in this photo is too small to read. Move closer so the page fills the frame, "
        "and take it again."
    ),
    "too_small": (
        "This picture is too small to read. Fill the frame with the page and take it again."
    ),
    "unreadable": (
        "This page could not be read as a picture. Try taking it again, or pick the file from "
        "this device."
    ),
}

# Marks shorter than this are specks and noise; marks taller than a tenth of the page are rules,
# boxes and logos. Neither is writing, so neither counts towards the text height.
_MIN_MARK_PX = 3
# How much darker than the paper a pixel must be to count as *soft* ink. A blurred page has no
# pixel darker than mid-grey once the blur is strong enough, yet 3-5 % of it stays this much below
# the paper tone at every strength measured; a blank page - white, or a noisy photo of blank paper -
# has none at all. That is what tells "smeared writing" from "no writing".
_SOFT_INK_BELOW_PAPER = 30.0


@dataclass(frozen=True)
class Verdict:
    """What the check decided, and what it measured to decide it."""

    ok: bool
    code: str | None = None  # a key of REASONS; None when the page is fine
    measures: dict[str, Any] = field(default_factory=dict)

    @property
    def message(self) -> str | None:
        return REASONS.get(self.code or "")

    def as_json(self) -> dict[str, Any] | None:
        """What the API carries per page, or ``None`` when there is nothing to say."""
        if self.ok:
            return None
        return {"code": self.code, "message": self.message, "measures": self.measures}


OK = Verdict(ok=True)

_UNREADABLE: dict[str, Any] = {
    "extent_px": 0.0, "sharpness": 0.0, "ink_fraction": 0.0, "soft_ink_fraction": 0.0,
    "glare_fraction": 0.0, "paper_tone": 0.0, "text_height_px": 0.0, "blank_rows": 0.0,
    "blank_cols": 0.0, "rotated_90_suspected": False,
}  # fmt: skip


def _decode(png: bytes) -> Any:
    import cv2
    import numpy as np

    try:
        return cv2.imdecode(np.frombuffer(png, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
    except Exception:  # noqa: BLE001 - a corrupt upload is a verdict, not a crash
        return None


def _text_height(ink: Any, page_height: int) -> float:
    """The median height, in pixels, of the written marks - what a far-away photo shrinks."""
    import cv2
    import numpy as np

    mask = ink.astype(np.uint8) * 255
    count, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    if count <= 1:
        return 0.0
    heights = stats[1:, cv2.CC_STAT_HEIGHT]
    tallest = max(_MIN_MARK_PX, page_height // 10)
    marks = heights[(heights >= _MIN_MARK_PX) & (heights <= tallest)]
    return float(np.median(marks)) if marks.size else 0.0


def _line_gaps(ink: Any) -> tuple[float, float]:
    """Inside the written area: the share of rows, and of columns, with no ink at all. Lines of
    text leave blank bands *between* them, so an upright page has many blank rows and few blank
    columns - and a page turned a quarter has it the other way round."""
    import numpy as np

    ys, xs = np.where(ink)
    if ys.size == 0:
        return 0.0, 0.0
    box = ink[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1]
    return float((box.sum(axis=1) == 0).mean()), float((box.sum(axis=0) == 0).mean())


def rotated_90(blank_rows: float, blank_cols: float) -> bool:
    """Do the lines run down the page? Said only when the evidence is clear: a tilted page smears
    both kinds of gap away, and calling that "sideways" would be a guess."""
    return blank_cols >= 0.15 and blank_cols >= 2.0 * max(blank_rows, 0.01)


def measure(png: bytes) -> dict[str, Any]:
    """Everything this module knows about one page. Never raises: a page that cannot be decoded
    is reported as ``extent_px = 0``, which :func:`verdict` turns into ``unreadable``."""
    import cv2
    import numpy as np

    arr = _decode(png)
    if arr is None or arr.size == 0:
        return dict(_UNREADABLE)
    ink = arr < 128
    blank_rows, blank_cols = _line_gaps(ink)
    paper = float(np.median(arr))
    return {
        "extent_px": float(min(arr.shape[:2])),
        "width_px": float(arr.shape[1]),
        "height_px": float(arr.shape[0]),
        "sharpness": float(cv2.Laplacian(arr, cv2.CV_64F).var()),
        "ink_fraction": float(ink.mean()),
        # writing that is there but smeared: darker than the paper without being dark
        "soft_ink_fraction": float((arr < paper - _SOFT_INK_BELOW_PAPER).mean()),
        "glare_fraction": float((arr >= 250).mean()),
        # the tone of the paper itself: white on a render or a clean scan, grey on a photograph
        "paper_tone": paper,
        "text_height_px": _text_height(ink, arr.shape[0]),
        "blank_rows": blank_rows,
        "blank_cols": blank_cols,
        "rotated_90_suspected": rotated_90(blank_rows, blank_cols),
        # recorded for the tuning, never judged: see the module docstring
        "brightness": float(arr.mean()),
    }


def verdict(m: dict[str, Any], *, min_side_px: int, min_sharpness: float, min_ink: float,
            max_ink: float, min_text_px: float = 0.0, max_glare: float = 1.0,
            paper_white: float = 250.0) -> Verdict:  # fmt: skip
    """The measurements judged against the limits. The order is deliberate: the person is told
    what to **change**, and a measurement is only used where it can be trusted. A thumbnail is
    called small and a dark page dark rather than "blurry", which they would also measure as;
    text height is read only once the page is known to be sharp."""
    if m.get("extent_px", 0) <= 0:
        return Verdict(False, "unreadable", m)
    if m["extent_px"] < min_side_px:
        return Verdict(False, "too_small", m)
    ink = m.get("ink_fraction", 0.0)
    if ink >= max_ink:
        return Verdict(False, "too_dark", m)
    if ink <= min_ink:
        # nothing dark on the page. If there is still soft ink the writing is there and smeared -
        # that is a focus problem, and telling the person the page is blank would send them to
        # check the wrong thing
        smeared = m.get("soft_ink_fraction", 0.0) > min_ink
        return Verdict(False, "blurred" if smeared else "blank", m)
    # glare is a property of a photograph: where the paper is already white there is none to find
    if m.get("paper_tone", 255.0) < paper_white and m.get("glare_fraction", 0.0) > max_glare:
        return Verdict(False, "glare", m)
    # blur before text height, because blur corrupts it: a soft focus erodes each stroke to a few
    # dark specks, the median mark shrinks, and the person would be told to move closer when the
    # fault is focus. A far-away photo is *sharp*, so it passes this and is caught just below.
    if m["sharpness"] < min_sharpness:
        return Verdict(False, "blurred", m)
    text = m.get("text_height_px", 0.0)
    if min_text_px and 0 < text < min_text_px:
        return Verdict(False, "text_too_small", m)
    return Verdict(True, None, m)


def limits(settings: Any) -> dict[str, Any]:
    """The configured limits, by the names :func:`verdict` takes them under."""
    s = settings
    return {
        "min_side_px": s.quality_min_side_px,
        "min_sharpness": s.quality_min_sharpness,
        "min_ink": s.quality_min_ink_fraction,
        "max_ink": s.quality_max_ink_fraction,
        "min_text_px": s.quality_min_text_px,
        "max_glare": s.quality_max_glare_fraction,
        "paper_white": s.quality_paper_white,
    }


def assess_png(png: bytes, *, settings: Any = None) -> Verdict:
    """One rendered page judged against the configured limits. Always measures, whatever
    ``CDI_QUALITY_CHECK`` says: this is the verdict the page *would* get."""
    from cdi_adapter.config import get_settings

    return verdict(measure(png), **limits(settings or get_settings()))


def record(png: bytes, *, settings: Any = None) -> dict[str, Any]:
    """What goes into ``document_page.preproc.quality`` for every page, pass or fail: the
    measurements, the verdict they produce under today's limits, and those limits themselves - so
    a later reader knows what "passed" meant when it was written (IM-S1 AC1 and AC3)."""
    from cdi_adapter.config import get_settings

    s = settings or get_settings()
    lim = limits(s)
    v = verdict(measure(png), **lim)
    return {"passed": v.ok, "code": v.code, "measures": v.measures, "limits": lim,
            "enforced": bool(s.quality_check)}  # fmt: skip


def assess(raw: bytes, mime: str, *, settings: Any = None) -> Verdict:
    """The verdict that **holds** an uploaded file, or :data:`OK` while holding is switched off.

    A PDF or multi-frame TIFF is judged page by page and the first page that fails is what the
    person is told about - they retake the page, not the file. Rendering reuses S1's own renderer,
    so what is judged is what the pipeline will read.
    """
    from cdi_adapter.config import get_settings

    s = settings or get_settings()
    if not s.quality_check:
        return OK
    from cdi_adapter.ingest.pages import render_pages

    try:
        pages = render_pages(raw, mime)
    except Exception:  # noqa: BLE001 - an undecodable upload is a verdict the desk can act on
        return Verdict(False, "unreadable", {"extent_px": 0.0})
    if not pages:
        return Verdict(False, "unreadable", {"extent_px": 0.0})
    last = OK
    for png in pages:
        last = assess_png(png, settings=s)
        if not last.ok:
            return last
    return last
