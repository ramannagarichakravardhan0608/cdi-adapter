"""IM-S1 on real PostgreSQL: every page's picture measurements are saved, pass or fail.

The unit tests prove what is measured. This proves the other half of the acceptance - that the
measurements *land*: ingest writes them to ``document_page.preproc.quality`` with the limits they
were judged against, for a page that passes and for one that does not, and a failing page is still
stored (recording never stops a page; holding one is the web app's decision).

The object store here only records what it is given: the claim under test is the database row.
"""

from __future__ import annotations

import hashlib
from typing import Any

import pytest
from sqlalchemy import text

from cdi_adapter import repo
from cdi_adapter.config import get_settings
from cdi_adapter.db import session_scope
from cdi_adapter.ingest import service
from cdi_adapter.recognition import quality
from tests.helpers import make_pdf

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")

pytestmark = pytest.mark.integration

MEASURES = {"extent_px", "sharpness", "ink_fraction", "soft_ink_fraction", "glare_fraction",
            "paper_tone", "text_height_px", "rotated_90_suspected"}  # fmt: skip


class _Recorder:
    def __init__(self) -> None:
        self.keys: list[str] = []

    def put_bytes(self, key: str, raw: bytes, mime: str) -> None:
        self.keys.append(key)


def _sharp(tag: str) -> bytes:
    """A rendered prescription; ``tag`` keeps each test's document (and its SHA-256) its own."""
    from cdi_adapter.ingest.pages import render_pages

    lines = [f"Line {i}: Tab Amoxicillin 500mg 1-0-1 x5 days {tag}" for i in range(12)]
    return render_pages(make_pdf([lines]), "application/pdf")[0]


def _blurred(png: bytes) -> bytes:
    arr = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_GRAYSCALE)
    return cv2.imencode(".png", cv2.GaussianBlur(arr, (25, 25), 0))[1].tobytes()


def _stored(raw: bytes) -> tuple[dict[str, Any], _Recorder]:
    """Run ingest's render-and-store on one picture and return the page's saved ``quality``."""
    sha, store = hashlib.sha256(raw).hexdigest(), _Recorder()
    with session_scope() as c:
        doc = repo.insert_source_document(c, sha256=sha, filename="page.png", mime_type="image/png",
                                          byte_size=len(raw), storage_key=f"t/{sha}")  # fmt: skip
    assert doc is not None
    assert service._render_and_store(doc, sha, raw, "image/png", store) == 1  # type: ignore[arg-type]
    with session_scope() as c:
        saved = c.execute(text("SELECT preproc -> 'quality' FROM document_page "
                               "WHERE document_id = :d AND page_no = 1"),
                          {"d": doc}).scalar_one()  # fmt: skip
    assert saved is not None, "the page was stored without its measurements"
    return saved, store


def test_a_passing_pages_measurements_are_saved_with_the_limits(db_env):
    saved, _ = _stored(_sharp("pass"))
    assert saved["passed"] is True and saved["code"] == "ok"
    assert MEASURES <= set(saved["measures"]), MEASURES - set(saved["measures"])
    assert saved["limits"] == quality.limits(get_settings())  # what "passed" meant, kept


def test_a_failing_pages_measurements_are_saved_and_the_page_is_still_stored(db_env):
    saved, store = _stored(_blurred(_sharp("fail")))
    assert saved["passed"] is False and saved["code"] == "blurred"
    assert MEASURES <= set(saved["measures"])
    assert saved["measures"]["sharpness"] < saved["limits"]["min_sharpness"]
    assert len(store.keys) == 2  # page and thumbnail: a recorded failure does not drop the page


def test_recording_does_not_wait_for_holding_to_be_switched_on(db_env):
    """Holding a page is off by default; the record is written all the same, and says so."""
    saved, _ = _stored(_blurred(_sharp("off")))
    assert saved["enforced"] is False
    assert saved["passed"] is False  # measured and judged, only not enforced


def test_the_saved_measurements_can_be_queried_to_move_a_limit(db_env):
    """What the record is for: read every page's sharpness straight from the database."""
    _stored(_sharp("q1"))
    _stored(_blurred(_sharp("q2")))
    with session_scope() as c:
        rows = c.execute(text(
            "SELECT (preproc #>> '{quality,measures,sharpness}')::float AS s, "
            "       (preproc #>> '{quality,passed}')::boolean AS ok "
            "FROM document_page WHERE preproc ? 'quality'")).all()  # fmt: skip
    assert any(ok for _, ok in rows) and any(not ok for _, ok in rows)
    assert min(s for s, ok in rows if ok) > max(s for s, ok in rows if not ok)
