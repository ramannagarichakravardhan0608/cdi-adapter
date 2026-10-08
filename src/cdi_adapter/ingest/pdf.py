"""The PDF renderer: pages of a PDF as pictures (backlog LS-S4; decided 2026-10-04).

PDF pages are rendered by **pypdfium2** (Apache-2.0; PDFium itself is Apache-2.0 / BSD-3). It
replaced PyMuPDF, whose AGPL licence is a blocker for a product that is sold. There is no
PyMuPDF option: nothing in ``src`` imports it.

Everything that needs a PDF opened goes through :class:`PdfRenderer` - three questions, and no
PDF library type leaves this module - so the renderer can be changed again in one place:

* ``page_count(raw)`` - how many pages;
* ``page_sizes(raw)`` - each page's width and height in points (1/72 inch), without drawing it;
* ``render(raw, dpi)`` - one RGB PNG per page at that resolution.

A PDF is always read as pictures, like a scan - a text layer, if it has one, is not used.

PDFium is **not thread-safe**, and the pipeline renders from several worker threads, so every
call into it is made under one lock.
"""

from __future__ import annotations

import io
import threading
from typing import Any, Protocol

POINTS_PER_INCH = 72.0


class NotAPdf(ValueError):
    """The bytes are not a PDF that can be opened (damaged, encrypted, or not a PDF at all)."""


class PdfRenderer(Protocol):
    name: str

    def page_count(self, raw: bytes) -> int: ...
    def page_sizes(self, raw: bytes) -> list[tuple[float, float]]: ...
    def render(self, raw: bytes, dpi: int) -> list[bytes]: ...


class PdfiumRenderer:
    """pypdfium2. One lock around every use: PDFium keeps global state."""

    name = "pypdfium2"
    _lock = threading.RLock()

    def _open(self, raw: bytes) -> Any:
        import pypdfium2 as pdfium

        try:
            return pdfium.PdfDocument(raw)
        except Exception as exc:  # noqa: BLE001 - PdfiumError, and anything a bad file provokes
            raise NotAPdf(f"the PDF could not be opened ({type(exc).__name__})") from exc

    def page_count(self, raw: bytes) -> int:
        with self._lock:
            doc = self._open(raw)
            try:
                return len(doc)
            finally:
                doc.close()

    def page_sizes(self, raw: bytes) -> list[tuple[float, float]]:
        with self._lock:
            doc = self._open(raw)
            try:
                return [tuple(doc.get_page_size(n)) for n in range(len(doc))]  # type: ignore[misc]
            finally:
                doc.close()

    def render(self, raw: bytes, dpi: int) -> list[bytes]:
        out: list[bytes] = []
        with self._lock:
            doc = self._open(raw)
            try:
                for n in range(len(doc)):
                    page = doc[n]
                    try:
                        bitmap = page.render(scale=dpi / POINTS_PER_INCH)
                        try:
                            picture = bitmap.to_pil().convert("RGB")
                        finally:
                            bitmap.close()
                    finally:
                        page.close()
                    buf = io.BytesIO()
                    picture.save(buf, format="PNG", dpi=(dpi, dpi))
                    out.append(buf.getvalue())
            finally:
                doc.close()
        return out


_RENDERER: PdfRenderer = PdfiumRenderer()


def renderer() -> PdfRenderer:
    """The PDF renderer in use."""
    return _RENDERER
