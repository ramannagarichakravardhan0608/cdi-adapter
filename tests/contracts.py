"""The contract tests of the swap points (backlog SW-S1).

A contract is what the rest of the product relies on from an interface - written as a
function that takes ANY implementation and checks it. An adapter may be selected in a setting
only after its contract passes. They are plain functions, so one adapter is checked with::

    from tests import contracts
    contracts.object_store_contract(MyStore(settings))
    contracts.pdf_renderer_contract(MyRenderer())

and ``tests/test_sw_s1_acceptance_unit.py`` runs them for every adapter there is.
"""

from __future__ import annotations

import io
from typing import Any

from PIL import Image

from tests.helpers import blank_pdf


def object_store_contract(store: Any, prefix: str = "contract-test") -> None:
    """Everything ``cdi_adapter.storage.Store`` promises. Uses keys under ``prefix`` only and
    removes them again."""
    assert isinstance(store.name, str) and store.name
    assert store.version is None or isinstance(store.version, str)
    store.ensure_bucket()
    store.ensure_bucket()  # asking twice is not an error
    assert store.healthy() is True

    key, other = f"{prefix}/a/b/page-0001.png", f"{prefix}/a/original.pdf"
    body = bytes(range(256)) * 40  # every byte value: nothing is re-encoded on the way
    for k in (key, other):
        store.delete(k)  # a key that is not there: not an error
    assert store.exists(key) is False

    store.put_bytes(key, body, "image/png")
    assert store.exists(key) is True
    assert store.get_bytes(key) == body, "bytes come back exactly as they went in"
    assert store.content_type(key) == "image/png"

    store.put_bytes(key, b"second", "image/png")  # the same key again replaces
    assert store.get_bytes(key) == b"second"

    store.put_bytes(other, b"%PDF-1.4", "application/pdf")
    listed = {k: size for k, size in store.listing() if k.startswith(prefix)}
    assert listed == {key: 6, other: 8}, "every object, with its size"

    try:
        store.get_bytes(f"{prefix}/not-there")
    except Exception:  # noqa: BLE001 - which error is the adapter's own; that it raises is not
        pass
    else:
        raise AssertionError("reading a key that is not there must raise")

    store.delete(key)
    store.delete(key)  # twice
    store.delete(other)
    assert store.exists(key) is False
    assert not [k for k, _ in store.listing() if k.startswith(prefix)]


def pdf_renderer_contract(renderer: Any) -> None:
    """Everything ``cdi_adapter.ingest.pdf.PdfRenderer`` promises."""
    from cdi_adapter.ingest.pdf import NotAPdf

    assert isinstance(renderer.name, str) and renderer.name
    version = getattr(renderer, "version", None)
    assert version is None or isinstance(version, str)

    three = blank_pdf(3, size=(300, 400))
    assert renderer.page_count(three) == 3
    sizes = renderer.page_sizes(three)
    assert [(round(w), round(h)) for w, h in sizes] == [(300, 400)] * 3, "in points, undrawn"

    pages = renderer.render(three, 72)
    assert len(pages) == 3, "one picture per page, in page order"
    for raw in pages:
        with Image.open(io.BytesIO(raw)) as im:
            assert im.format == "PNG" and im.mode == "RGB"
            assert abs(im.width - 300) <= 2 and abs(im.height - 400) <= 2  # 72 dpi = 1 px / pt
    with Image.open(io.BytesIO(renderer.render(three, 144)[0])) as im:
        assert abs(im.width - 600) <= 3, "the resolution asked for is the resolution given"

    for bad in (b"", b"this is not a PDF", b"PK\x03\x04 a zip, not a PDF"):
        for ask in (renderer.page_count, renderer.page_sizes, lambda r: renderer.render(r, 72)):
            try:
                ask(bad)
            except NotAPdf:
                continue
            raise AssertionError("bytes that are not a PDF must raise NotAPdf")
