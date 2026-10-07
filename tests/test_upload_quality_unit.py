"""UP-S2: say at once that a picture is not good enough, and let the person retake that page.

Three things are pinned here:

* the **verdict** - what is measured, what each measurement means, and that the limits are
  configuration rather than a judgement baked into the code;
* that the check is **off by default**, so the pipeline behaves exactly as it did until someone
  switches it on deliberately (its limits have to be tuned on the E2-S4 gold set first - that is
  IM-S1's work, and this story's own row says the check should already exist, which it did not);
* the **screen**: the five plain stages, the plain reason on a held page, and the Retake button
  that replaces that one page.
"""

from __future__ import annotations

from typing import Any

import pytest

from cdi_adapter.config import Settings
from cdi_adapter.recognition import quality
from cdi_adapter.webapp.page import BODY, SCRIPT

LIMITS = {"min_side_px": 700, "min_sharpness": 20.0, "min_ink": 0.0005, "max_ink": 0.90}


def _m(**kw: Any) -> dict[str, float]:
    """A page that reads: big enough, sharp, with some ink on it (a real sparse prescription
    measures ink_fraction 0.0012 and sharpness 73)."""
    base = {"extent_px": 1600.0, "sharpness": 400.0, "ink_fraction": 0.02}
    return {**base, **kw}


# ------------------------------------------------------------------ off unless asked for


def test_the_check_is_off_by_default() -> None:
    """Switching it on starts rejecting pages, and a limit set too high rejects good ones, so
    the default must not change anyone's pipeline."""
    assert Settings().quality_check is False


def test_with_the_check_off_nothing_is_ever_held() -> None:
    s = Settings(quality_check=False)
    assert quality.assess(b"not an image at all", "image/png", settings=s) is quality.OK
    assert quality.assess(b"", "application/pdf", settings=s).ok is True


def test_the_limits_are_configuration_not_code() -> None:
    s = Settings()
    assert (s.quality_min_side_px, s.quality_min_sharpness) == (700, 20.0)
    assert (s.quality_min_ink_fraction, s.quality_max_ink_fraction) == (0.0005, 0.90)
    # and they are per-deployment, so the gold-set tuning lands in .env, not in a patch
    tight = Settings(quality_min_sharpness=5000.0)
    assert tight.quality_min_sharpness == 5000.0


# ------------------------------------------------------------------ the verdict


def test_a_good_page_passes() -> None:
    assert quality.verdict(_m(), **LIMITS).ok
    assert quality.verdict(_m(), **LIMITS).as_json() is None  # nothing to tell the person


@pytest.mark.parametrize(("measures", "code"), [
    ({"extent_px": 0.0}, "unreadable"),
    (_m(extent_px=320.0), "too_small"),
    (_m(ink_fraction=1.0), "too_dark"),  # a lens cap, a shadow, an inverted scan
    (_m(ink_fraction=0.0), "blank"),  # blank paper, or glare that removed the writing
    (_m(sharpness=9.0), "blurred"),
])  # fmt: skip
def test_each_fault_is_named(measures: dict[str, float], code: str) -> None:
    v = quality.verdict(measures, **LIMITS)
    assert not v.ok and v.code == code
    assert v.message and v.message in quality.REASONS.values()


def test_the_person_is_told_what_to_change_not_a_number() -> None:
    for code, text in quality.REASONS.items():
        assert "take it again" in text or "try taking it again" in text.lower(), code
        assert not any(ch.isdigit() for ch in text), code  # no thresholds in the message


def test_the_verdict_carries_what_it_measured() -> None:
    """So a run can be reviewed and the limits moved on evidence rather than on taste."""
    out = quality.verdict(_m(sharpness=9.0), **LIMITS).as_json()
    assert out is not None
    assert out["code"] == "blurred" and out["measures"]["sharpness"] == 9.0
    assert set(out) == {"code", "message", "measures"}


def test_a_too_small_page_is_not_called_blurry() -> None:
    """Order matters: the person is told what to change, and a thumbnail is not a focus problem."""
    v = quality.verdict({"extent_px": 300.0, "sharpness": 1.0, "ink_fraction": 0.02}, **LIMITS)
    assert v.code == "too_small"


def test_brightness_is_recorded_but_never_judged() -> None:
    """A document is mostly white paper, so a real page and a washed-out one both measure ~255 and
    the 5th percentile is white on any sparse page. Glare that hides the writing is reported as
    nothing to read, which is what the person can act on."""
    assert "washed_out" not in quality.REASONS
    bright = quality.verdict(_m(brightness=255.0, p1=255.0, p99=255.0), **LIMITS)
    assert bright.ok  # brightness alone decides nothing
    assert quality.verdict(_m(ink_fraction=0.0, brightness=255.0), **LIMITS).code == "blank"


def test_measuring_a_non_image_is_a_verdict_not_a_crash() -> None:
    m = quality.measure(b"certainly not a png")
    assert m["extent_px"] == 0.0
    assert quality.verdict(m, **LIMITS).code == "unreadable"


def test_the_measurements_include_what_the_tuning_will_want() -> None:
    cv2 = pytest.importorskip("cv2")
    assert cv2 is not None
    from cdi_adapter.ingest.pages import render_pages
    from tests.helpers import make_pdf

    page = render_pages(make_pdf([["Tab Amoxicillin 500mg"]]), "application/pdf")[0]
    m = quality.measure(page)
    # IM-S1 widened the record: glare, the paper tone it is judged against, text height and the
    # sideways evidence joined the three measurements UP-S2 started with
    assert set(m) >= {"extent_px", "sharpness", "ink_fraction", "brightness", "glare_fraction",
                      "paper_tone", "text_height_px", "rotated_90_suspected"}  # fmt: skip


def test_a_real_page_measures_sharp_and_a_blurred_copy_does_not() -> None:
    """The one measurement that decides most hand-held photos, on a real rendered page."""
    cv2 = pytest.importorskip("cv2")
    import numpy as np

    from cdi_adapter.ingest.pages import render_pages
    from tests.helpers import make_pdf

    page = render_pages(make_pdf([["Tab Amoxicillin 500mg", "1-0-1 x5d", "Dr A Sharma"]]),
                        "application/pdf")[0]  # fmt: skip
    sharp = quality.measure(page)
    arr = cv2.imdecode(np.frombuffer(page, np.uint8), cv2.IMREAD_GRAYSCALE)
    blurred = quality.measure(cv2.imencode(".png", cv2.GaussianBlur(arr, (9, 9), 0))[1].tobytes())
    dark = quality.measure(cv2.imencode(".png", (arr * 0.10).astype("uint8"))[1].tobytes())
    blank = quality.measure(cv2.imencode(".png", np.full_like(arr, 255))[1].tobytes())

    assert sharp["sharpness"] > blurred["sharpness"] * 10  # the signal that decides
    assert quality.verdict(sharp, **LIMITS).ok, sharp
    assert quality.verdict(blurred, **LIMITS).code == "blurred", blurred
    assert quality.verdict(dark, **LIMITS).code == "too_dark", dark
    assert quality.verdict(blank, **LIMITS).code == "blank", blank
    # and the default limits, not only the ones this test passes in, accept a real page
    assert quality.assess(page, "image/png", settings=Settings(quality_check=True)).ok


# ------------------------------------------------------------------ the screen


def test_the_five_stages_are_in_plain_words() -> None:
    for stage in ("received", "checking the picture", "reading", "saving", "done"):
        assert f"'{stage}'" in SCRIPT, stage


def test_a_held_page_shows_its_reason_and_a_retake_button() -> None:
    assert "d.status === 'quality_hold'" in SCRIPT
    assert "picture not good enough" in SCRIPT
    assert "(d.quality || {}).message || d.error" in SCRIPT  # the plain sentence, per page
    assert "Retake this page" in SCRIPT and "data-retake=" in SCRIPT


def test_retake_replaces_that_one_page_and_keeps_polling() -> None:
    assert "/pages/${retakePage}/replace" in SCRIPT
    assert "retakePage = +b.dataset.retake + 1" in SCRIPT  # the API counts pages from 1
    assert "poll(jobId);" in SCRIPT
    assert "id=retakefile" in BODY and "capture=environment" in BODY


def test_the_held_page_is_not_shown_as_finished() -> None:
    assert "(d.status === 'error' || held) ? '✖' : ''" in SCRIPT


def test_the_existing_upload_and_progress_are_unchanged() -> None:
    assert "$('#files').onchange = e => pick([...e.target.files]);" in SCRIPT
    assert "chosen.forEach(f => fd.append('files', f));" in SCRIPT
    assert "await fetch('/api/jobs', {method:'POST', body:fd});" in SCRIPT
    assert "const STEPS = ['ingest','ocr','classify','extract','terminology','validate'];" in SCRIPT


# ------------------------------------------------------------------ the replace-page contract


def test_the_replace_route_is_wired() -> None:
    import sys

    sys.path.append(".tools/xgrammar")
    from cdi_adapter.webapp.app import app

    assert "/api/jobs/{job_id}/pages/{page_no}/replace" in app.openapi()["paths"]


def test_an_empty_replacement_is_refused_before_anything_is_touched() -> None:
    from cdi_adapter.webapp import jobstore

    with pytest.raises(jobstore.PageNotReplaced, match="empty"):
        jobstore.replace_page("a" * 32, 0, "x.jpg", b"", "image/jpeg", actor="desk")


def test_a_page_number_outside_the_job_is_refused(monkeypatch: Any) -> None:
    """And nothing is staged: the check happens before the object store is touched."""
    from cdi_adapter.webapp import jobstore
    from cdi_adapter.webapp.jobs import DocState, Job

    job = Job(id="b" * 32, docs=[DocState(filename="one.jpg")])
    puts: list[str] = []

    class _St:
        objects = type("O", (), {"put_bytes": lambda self, k, b, m: puts.append(k)})()

        def load(self, job_id: str) -> Job:
            return job

    with pytest.raises(jobstore.PageNotReplaced, match="pages 1-1"):
        jobstore.replace_page(job.id, 5, "x.jpg", b"xx", "image/jpeg", actor="desk",
                              st=_St())  # type: ignore[arg-type]  # fmt: skip
    assert puts == []


def test_a_missing_job_is_refused(monkeypatch: Any) -> None:
    from cdi_adapter.webapp import jobstore

    class _St:
        def load(self, job_id: str) -> None:
            return None

    with pytest.raises(jobstore.PageNotReplaced, match="job not found"):
        jobstore.replace_page("c" * 32, 0, "x.jpg", b"xx", "image/jpeg", actor="desk",
                              st=_St())  # type: ignore[arg-type]  # fmt: skip


def test_a_held_page_is_terminal_until_it_is_replaced() -> None:
    """``quality_hold`` is not in UNFINISHED, so no sweep or resume re-runs a page the person was
    asked to retake - it waits for the retake instead of looping."""
    from cdi_adapter.webapp.jobs import UNFINISHED

    assert "quality_hold" not in UNFINISHED
