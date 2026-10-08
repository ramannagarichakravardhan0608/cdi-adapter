"""S1 page rendering + normalization (ARCHITECTURE §5 S1 steps 5–6).

``render_pages``: the PDF renderer (``ingest/pdf.py`` - pypdfium2 since LS-S4, never PyMuPDF)
for PDF, one PNG per page at ``CDI_PAGE_DPI``; Pillow for images, multi-frame TIFF supported.
``normalize_image``: grayscale -> deskew (``cv2.minAreaRect`` on
Otsu-thresholded ink pixels; rotate only if 0.15 deg < |skew| <= 15 deg) -> fastNlMeansDenoising
-> CLAHE. The original bytes are never altered; these are derived render artifacts. The geometry
itself, and the transform recorded for it, is ``recognition/geometry.py`` (IM-S2).
"""

from __future__ import annotations

from cdi_adapter import _cpu
from cdi_adapter.config import get_settings

# thread caps must be in place before OpenCV / NumPy load (§7.4 #4)
_THREADS = _cpu.apply_thread_caps(get_settings().job_max_workers)

import io  # noqa: E402

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from PIL import Image, ImageSequence  # noqa: E402

from cdi_adapter.ingest import pdf  # noqa: E402
from cdi_adapter.recognition import geometry  # noqa: E402

cv2.setNumThreads(_THREADS)

SKEW_MIN_DEG = geometry.SKEW_MIN_DEG
SKEW_MAX_DEG = geometry.SKEW_MAX_DEG
THUMB_MAX_SIDE = 256

_MAGIC = (
    (b"%PDF-", "application/pdf"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"II*\x00", "image/tiff"),
    (b"MM\x00*", "image/tiff"),
)
_EXT_MIME = {
    "pdf": "application/pdf",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "tif": "image/tiff",
    "tiff": "image/tiff",
}
MIME_EXT = {"application/pdf": "pdf", "image/png": "png", "image/jpeg": "jpg", "image/tiff": "tiff"}


class UnsupportedDocument(ValueError):
    """The bytes are not a PDF or a supported image."""


class DocumentRefused(UnsupportedDocument):
    """A data error: the file itself is the problem, so trying again cannot help (LS-S3).
    ``kind`` is a short code for the reason."""

    kind = "refused"


class NotReadable(DocumentRefused):
    """Named like a PDF, but not one that can be opened (LS-S4 AC5)."""

    kind = "not_readable"


class TooManyPages(DocumentRefused):
    kind = "too_many_pages"


class TooManyPixels(DocumentRefused):
    kind = "too_many_pixels"


def inspect(raw: bytes, mime: str, *, max_pages: int | None = None,
            max_pixels: int | None = None, dpi: int | None = None) -> int:  # fmt: skip
    """Look at an untrusted document **without rendering it** and return its page count.

    Raises :class:`TooManyPages` when it has more pages than ``CDI_MAX_PAGES`` - the whole
    document is refused, never read up to the limit and cut - and :class:`TooManyPixels` when
    a page or picture would open to more than ``CDI_MAX_IMAGE_PIXELS`` (a decompression bomb:
    the size is read from the header, nothing is decoded)."""
    s = get_settings()
    max_pages = max_pages or s.max_pages
    max_pixels = max_pixels or s.max_image_pixels
    scale = (dpi or s.page_dpi) / 72.0

    def too_big(width: float, height: float, what: str) -> None:
        if width * height > max_pixels:
            raise TooManyPixels(
                f"{what} would open to {width * height / 1e6:,.0f} million pixels; "
                f"the limit is {max_pixels / 1e6:,.0f} million"
            )

    def too_long(count: int) -> None:
        if count > max_pages:
            raise TooManyPages(f"the document has {count} pages; the limit is {max_pages}. "
                               "It was refused whole: no page was read.")  # fmt: skip

    if mime == "application/pdf":
        try:
            sizes = pdf.renderer().page_sizes(raw)
        except pdf.NotAPdf as exc:
            raise NotReadable(f"this is not a PDF that can be opened: {exc}") from exc
        too_long(len(sizes))
        for n, (width, height) in enumerate(sizes, start=1):
            too_big(width * scale, height * scale, f"page {n}")
        return len(sizes)
    if mime.startswith("image/"):
        try:
            with Image.open(io.BytesIO(raw)) as im:
                count = int(getattr(im, "n_frames", 1))
                too_long(count)
                for n in range(count):
                    im.seek(n)
                    what = "the picture" if count == 1 else f"page {n + 1}"
                    too_big(im.size[0], im.size[1], what)
                return count
        except DocumentRefused:
            raise
        except Image.DecompressionBombError as exc:  # Pillow's own, far higher, limit
            raise TooManyPixels("the picture is too large to open safely") from exc
        except Exception as exc:  # noqa: BLE001 - Pillow raises many types for a bad file
            raise UnsupportedDocument(
                f"the picture could not be opened ({type(exc).__name__})"
            ) from exc
    raise UnsupportedDocument(f"cannot read {mime}")


def sniff_mime(raw: bytes, filename: str | None = None) -> str:
    """Content first (magic bytes), then the file extension."""
    for magic, mime in _MAGIC:
        if raw.startswith(magic):
            return mime
    ext = (filename or "").rsplit(".", 1)[-1].lower() if filename and "." in filename else ""
    if ext in _EXT_MIME:
        return _EXT_MIME[ext]
    raise UnsupportedDocument(f"unsupported document type: {filename!r}")


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def render_pages(raw: bytes, mime: str, dpi: int | None = None) -> list[bytes]:
    """One PNG per page. A document over the page limit, or with a page too large to open
    safely, is refused whole (:func:`inspect`) - it is never rendered up to the limit and cut."""
    dpi = dpi or get_settings().page_dpi
    if mime == "application/pdf" or mime.startswith("image/"):
        inspect(raw, mime, dpi=dpi)
    if mime == "application/pdf":
        return pdf.renderer().render(raw, dpi)
    if mime.startswith("image/"):
        with Image.open(io.BytesIO(raw)) as im:
            return [_png(frame.convert("RGB")) for frame in ImageSequence.Iterator(im)]
    raise UnsupportedDocument(f"cannot render {mime}")


def estimate_skew(gray: np.ndarray) -> float:
    """Skew of the ink in degrees (positive = content rotated counter-clockwise)."""
    return geometry.estimate_skew(gray)


def normalize_image(png: bytes, *, straighten: bool | None = None) -> tuple[bytes, dict]:
    """Return the normalized PNG and the ``preproc`` record ``{skew_deg, steps, transform}``.

    The geometry is ``recognition/geometry.py`` (IM-S2): by default the small-tilt deskew alone,
    as before; with ``CDI_STRAIGHTEN`` (or ``straighten=True``) also the quarter turn, the half
    turn and the perspective crop. ``transform`` holds the 3x3 matrix from the page as uploaded
    to the page as stored, so a box found on the stored page maps back to the original pixels -
    denoise and CLAHE move nothing, so the matrix is the whole of the geometry.
    """
    arr = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_GRAYSCALE)
    if arr is None:
        raise UnsupportedDocument("page image could not be decoded")
    full = get_settings().straighten if straighten is None else straighten
    made = geometry.straighten(arr, full=full)
    arr = made.image
    steps = ["grayscale", *made.steps]
    arr = cv2.fastNlMeansDenoising(arr, None, h=10, templateWindowSize=7, searchWindowSize=21)
    steps.append("denoise")
    arr = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(arr)
    steps.append("clahe")
    ok, out = cv2.imencode(".png", arr)
    if not ok:
        raise UnsupportedDocument("page image could not be encoded")
    return out.tobytes(), {"skew_deg": round(made.skew_deg, 3), "steps": steps,
                           "transform": made.record(enabled=full)}  # fmt: skip


def make_thumbnail(png: bytes, max_side: int = THUMB_MAX_SIDE) -> bytes:
    with Image.open(io.BytesIO(png)) as src:
        thumb = src.copy()
    thumb.thumbnail((max_side, max_side))
    return _png(thumb)


def image_size(png: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(png)) as im:
        return im.size
