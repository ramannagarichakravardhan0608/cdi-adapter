"""S1 page rendering + normalization (ARCHITECTURE §5 S1 steps 5–6).

``render_pages``: PyMuPDF for PDF (one PNG per page at ``CDI_PAGE_DPI``), Pillow for images,
multi-frame TIFF supported. ``normalize_image``: grayscale -> deskew (``cv2.minAreaRect`` on
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
import pymupdf  # noqa: E402
from PIL import Image, ImageSequence  # noqa: E402

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
    """One PNG per page."""
    dpi = dpi or get_settings().page_dpi
    if mime == "application/pdf":
        with pymupdf.open(stream=raw, filetype="pdf") as doc:
            return [page.get_pixmap(dpi=dpi, alpha=False).tobytes("png") for page in doc]
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
