"""Synthetic test documents (no real patient data)."""

from __future__ import annotations

import io

from PIL import Image, ImageDraw, ImageFont

A4_POINTS = (595, 842)


def make_pdf(lines_per_page: list[list[str]], *, dpi: int = 200) -> bytes:
    """A PDF of A4 pages with the given lines on them, made with Pillow alone (no PDF library
    of ours writes PDFs, and PyMuPDF is gone since LS-S4). Each page is a picture of the text,
    as a scanner would produce - the pipeline reads a PDF as pictures either way."""
    scale = dpi / 72.0
    size = (round(A4_POINTS[0] * scale), round(A4_POINTS[1] * scale))
    font = ImageFont.load_default(size=round(12 * scale))
    pages_ = []
    for lines in lines_per_page:
        im = Image.new("RGB", size, "white")
        draw = ImageDraw.Draw(im)
        for i, line in enumerate(lines):
            draw.text((round(60 * scale), round((68 + 22 * i) * scale)), line, fill="black",
                      font=font)  # fmt: skip
        pages_.append(im)
    buf = io.BytesIO()
    pages_[0].save(buf, format="PDF", resolution=float(dpi), save_all=True,
                   append_images=pages_[1:])  # fmt: skip
    return buf.getvalue()


def blank_pdf(page_count: int, *, size: tuple[float, float] = (200, 280)) -> bytes:
    """A PDF of empty pages of ``size`` points - quick to make, for page-count and size tests."""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument.new()
    for _ in range(page_count):
        doc.new_page(*size).close()
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


def lined_page(size=(1200, 1600), n=30) -> Image.Image:
    im = Image.new("L", size, 255)
    d = ImageDraw.Draw(im)
    for i in range(n):
        d.rectangle([150, 200 + i * 40, size[0] - 150, 215 + i * 40], fill=0)
    return im


def png_bytes(im: Image.Image) -> bytes:
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def tiff_bytes(frames: list[Image.Image]) -> bytes:
    buf = io.BytesIO()
    frames[0].save(buf, format="TIFF", save_all=True, append_images=frames[1:])
    return buf.getvalue()


TEST_PASSWORD = "correct horse battery staple"


def add_web_user(role: str = "admin", username: str | None = None) -> str:
    """A staff account in the test DB (backlog E11-S1); returns its username."""
    import uuid

    from sqlalchemy import text

    from cdi_adapter.db import session_scope
    from cdi_adapter.webapp.auth import hash_password

    username = username or f"t-{role}-{uuid.uuid4().hex[:8]}"
    with session_scope() as c:
        c.execute(text("INSERT INTO web_user (username, display_name, role, password_hash) "
                       "VALUES (:u, :n, :r, :p)"),
                  {"u": username, "n": f"Test {role}", "r": role,
                   "p": hash_password(TEST_PASSWORD)})  # fmt: skip
    return username


def browser(app):
    """A TestClient that behaves like a same-origin browser over TLS (Secure cookie, Origin)."""
    from fastapi.testclient import TestClient

    return TestClient(app, base_url="https://testserver",
                      headers={"Origin": "https://testserver"})  # fmt: skip


def signed_in(app, role: str = "admin"):
    """A browser signed in through the real /login route as a new user of ``role``."""
    c = browser(app)
    r = c.post("/login", data={"username": add_web_user(role), "password": TEST_PASSWORD},
               follow_redirects=False)  # fmt: skip
    assert r.status_code == 303, r.text
    return c
