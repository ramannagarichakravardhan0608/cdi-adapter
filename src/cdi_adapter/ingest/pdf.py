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
from collections.abc import Callable
from typing import Any, Protocol

POINTS_PER_INCH = 72.0


class NotAPdf(ValueError):
    """The bytes are not a PDF that can be opened (damaged, encrypted, or not a PDF at all)."""


class PdfRenderer(Protocol):
    """The interface (SW-S1). An implementation also says its ``version``, which is saved with
    every result it produced a page for."""

    name: str

    def page_count(self, raw: bytes) -> int: ...
    def page_sizes(self, raw: bytes) -> list[tuple[float, float]]: ...
    def render(self, raw: bytes, dpi: int) -> list[bytes]: ...


class PdfiumRenderer:
    """pypdfium2. One lock around every use: PDFium keeps global state."""

    name = "pypdfium2"
    _lock = threading.RLock()

    @property
    def version(self) -> str | None:
        """The installed pypdfium2 package (it carries its own PDFium build)."""
        import importlib.metadata

        try:
            return importlib.metadata.version("pypdfium2")
        except importlib.metadata.PackageNotFoundError:
            return None

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


# ------------------------------------------------------------------ the registry (SW-S1)

Factory = Callable[[], PdfRenderer]
_REGISTRY: dict[str, tuple[Factory, tuple[str, ...], str | None]] = {}
_made: dict[str, PdfRenderer] = {}
_made_lock = threading.Lock()


def register(name: str, factory: Factory, *, libraries: tuple[str, ...] = (),
             package: str | None = None) -> None:  # fmt: skip
    """Make a renderer selectable by ``CDI_PDF_RENDERER=<name>``. ``libraries``: what it
    imports (asked for at start); ``package``: the installed package that is its version."""
    key = name.strip().lower()
    _REGISTRY[key] = (factory, tuple(libraries), package)
    _made.pop(key, None)


def unregister(name: str) -> None:
    _REGISTRY.pop(name.strip().lower(), None)
    _made.pop(name.strip().lower(), None)


def registered() -> list[str]:
    return sorted(_REGISTRY)


def needs(name: str) -> tuple[tuple[str, ...], str | None]:
    """``(libraries, package)`` of a registered renderer."""
    _, libraries, package = _REGISTRY[name.strip().lower()]
    return libraries, package


register("pypdfium2", PdfiumRenderer, libraries=("pypdfium2",), package="pypdfium2")


def renderer(name: str | None = None) -> PdfRenderer:
    """The PDF renderer the settings name (``CDI_PDF_RENDERER``), made once."""
    if name is None:
        from cdi_adapter.config import get_settings

        name = get_settings().pdf_renderer
    key = name.strip().lower()
    entry = _REGISTRY.get(key)
    if entry is None:
        raise ValueError(f"CDI_PDF_RENDERER={name!r} is not a registered PDF renderer "
                         f"(available: {', '.join(registered())})")  # fmt: skip
    with _made_lock:
        if key not in _made:
            _made[key] = entry[0]()
        return _made[key]


def describe(mime: str) -> tuple[str, str | None]:
    """``(name, version)`` of what turns a document of this type into page pictures - saved
    with the document's ingest run, so a result can say what produced its pages. A PDF: the
    renderer. A picture: Pillow, which only re-encodes it."""
    if mime == "application/pdf":
        r = renderer()
        return r.name, getattr(r, "version", None)
    import PIL

    return "pillow", PIL.__version__
