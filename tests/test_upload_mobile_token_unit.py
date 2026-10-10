"""The upload screen takes a mobile number and a token number (owner, 2026-10-09).

Two more optional boxes beside the ABHA number. Both are checked on the page and again by the
server, in plain words, and both are kept with the upload. Neither is used to match a patient.

The server-side checks and the stored job are tested here. That the page shows the boxes, stops
a wrong value before sending and sends a right one was checked in a browser against a stand-in
for the server; here the page is only checked for what it contains.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException

from cdi_adapter.webapp import app as web
from cdi_adapter.webapp import jobstore
from cdi_adapter.webapp.jobs import DocState, Job
from cdi_adapter.webapp.page import upload_page

ROOT = Path(__file__).resolve().parents[1]
FILES = [("rx.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 32, "image/png")]


# ------------------------------------------------------------------ the mobile number


@pytest.mark.parametrize("typed", ["9876543210", "98765 43210", "98765-43210", "+91 98765 43210",
                                   "+919876543210", "919876543210", "09876543210",
                                   "  9876543210  "])  # fmt: skip
def test_a_mobile_number_is_kept_as_its_ten_digits(typed: str) -> None:
    assert web.clean_mobile(typed) == "9876543210"


@pytest.mark.parametrize("typed", ["", "   ", None])
def test_an_empty_mobile_box_is_fine(typed: str | None) -> None:
    assert web.clean_mobile(typed) is None


@pytest.mark.parametrize("typed", ["12345", "98765432101", "5876543210", "0000000000",
                                   "abcdefghij", "98765 4321O", "+1 202 555 0143",
                                   "9876543210 ext 2"])  # fmt: skip
def test_a_wrong_mobile_number_is_refused_in_plain_words(typed: str) -> None:
    with pytest.raises(HTTPException) as refused:
        web.clean_mobile(typed)
    assert refused.value.status_code == 422 and refused.value.detail == web.BAD_MOBILE
    assert "10 digits" in web.BAD_MOBILE and "leave the box empty" in web.BAD_MOBILE


# ------------------------------------------------------------------ the token number


@pytest.mark.parametrize("typed", ["A-12", "45", "OPD/7", "A 12", "T20261009-003", "  B7  "])
def test_a_token_number_is_kept_as_typed(typed: str) -> None:
    assert web.clean_token(typed) == typed.strip()


@pytest.mark.parametrize("typed", ["", "   ", None])
def test_an_empty_token_box_is_fine(typed: str | None) -> None:
    assert web.clean_token(typed) is None


@pytest.mark.parametrize("typed", ["-12", "tok#1", "A" * 21, "<b>1</b>", "12;drop", "A\n12"])
def test_a_wrong_token_number_is_refused_in_plain_words(typed: str) -> None:
    with pytest.raises(HTTPException) as refused:
        web.clean_token(typed)
    assert refused.value.status_code == 422 and refused.value.detail == web.BAD_TOKEN


# ------------------------------------------------------------------ kept with the upload


class _Objects:
    def put_bytes(self, key: str, raw: bytes, mime: str) -> None:
        pass

    def delete(self, key: str) -> None:
        pass


class _Store:
    """A job store that keeps what it is given."""

    objects = _Objects()

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def by_key(self, key: str) -> Job | None:
        return None

    def insert(self, job: Job, files: Any, **kw: Any) -> None:
        self.rows.append({"job": job, **kw})


def test_the_two_values_are_stored_on_the_job(monkeypatch) -> None:
    store = _Store()
    monkeypatch.setattr(jobstore, "dispatch", lambda *a, **k: None, raising=False)
    try:
        job = jobstore.submit(FILES, actor="desk", st=store, mobile="9876543210",  # type: ignore[arg-type]
                              token_no="A-12")  # fmt: skip
    except Exception:  # noqa: BLE001 - dispatching needs a broker; the row is what matters here
        job = store.rows[0]["job"]
    assert (job.mobile, job.token_no) == ("9876543210", "A-12")
    assert store.rows and store.rows[0]["job"].token_no == "A-12"


def test_they_are_written_to_and_read_from_the_job_row() -> None:
    source = (ROOT / "src" / "cdi_adapter" / "webapp" / "jobstore.py").read_text(encoding="utf-8")
    assert "mobile, token_no) VALUES" in source and '"m": job.mobile, "tk": job.token_no' in source
    assert 'mobile=row.get("mobile"), token_no=row.get("token_no")' in source
    migration = (ROOT / "db" / "alembic" / "versions" / "0032_web_job_mobile_token.py").read_text(
        encoding="utf-8")  # fmt: skip
    assert "ADD COLUMN mobile" in migration and "ADD COLUMN token_no" in migration
    assert 'down_revision = "0031"' in migration


def test_the_job_tells_the_page_what_was_typed() -> None:
    job = Job(id="j1", docs=[DocState(filename="rx.png")], mobile="9876543210", token_no="A-12")
    out = job.as_json()
    assert out["mobile"] == "9876543210" and out["token_no"] == "A-12"
    assert Job(id="j2", docs=[]).as_json()["token_no"] is None  # nothing typed: nothing shown


def test_a_mismatched_upload_does_not_show_the_mobile_number() -> None:
    """A mismatch never surfaces any identity; the token is not one, the mobile number is."""
    source = (ROOT / "src" / "cdi_adapter" / "webapp" / "app.py").read_text(encoding="utf-8")
    assert 'if job.state == "mismatch":\n            out["mobile"] = None' in source


def test_an_upload_without_them_keeps_the_key_it_always_had() -> None:
    """The key that recognises a repeated upload: unchanged when the boxes are empty, and
    different when the same file is sent for another token."""
    plain = jobstore.idempotency_key(FILES, actor="desk")
    assert jobstore.idempotency_key(FILES, actor="desk", mobile=None, token_no=None) == plain
    a12 = jobstore.idempotency_key(FILES, actor="desk", token_no="A-12")
    assert a12 != plain and a12 == jobstore.idempotency_key(FILES, actor="desk", token_no="A-12")
    assert a12 != jobstore.idempotency_key(FILES, actor="desk", token_no="A-13")
    assert plain != jobstore.idempotency_key(FILES, actor="desk", mobile="9876543210")


def test_the_upload_route_takes_checks_and_passes_both_on() -> None:
    source = (ROOT / "src" / "cdi_adapter" / "webapp" / "app.py").read_text(encoding="utf-8")
    assert "mobile: str | None = Form(None)" in source
    assert "token_no: str | None = Form(None)" in source
    assert "mobile, token_no = clean_mobile(mobile), clean_token(token_no)" in source
    assert source.index("clean_mobile(mobile), clean_token(token_no)") < source.index(
        "jobstore.submit, payload"
    )  # checked before anything is stored


# ------------------------------------------------------------------ the page


def test_the_page_has_the_two_boxes_under_the_abha_number() -> None:
    html = upload_page()
    abha, mobile, token = (html.index(s) for s in ("id=abha ", "id=mobile ", "id=token "))
    assert abha < mobile < token
    assert "Mobile number (optional)" in html and "Token number (optional)" in html
    assert 'placeholder="98765 43210"' in html and 'placeholder="A-12"' in html


def test_the_page_checks_them_before_sending_and_says_what_is_wrong() -> None:
    html = upload_page()
    assert "const mobileOk" in html and "const tokenOk" in html
    assert "if (!mobileOk($('#mobile').value)){ $('#mobilemsg').style.display = 'inline'" in html
    assert "if (!tokenOk($('#token').value)){ $('#tokenmsg').style.display = 'inline'" in html
    assert html.count("The mobile number must be 10 digits, like 98765 43210.") == 1
    assert html.count("The token number can be up to 20 letters, digits and hyphens") == 1


def test_the_page_sends_them_only_when_filled_in() -> None:
    html = upload_page()
    assert "if ($('#mobile').value.trim()) fd.append('mobile', $('#mobile').value.trim());" in html
    assert "if ($('#token').value.trim()) fd.append('token_no', $('#token').value.trim());" in html


def test_the_progress_panel_shows_the_token_and_mobile_of_the_upload() -> None:
    html = upload_page()
    assert (
        "<p id=visit" in html and "'Token ' + j.token_no" in html and "'Mobile ' + j.mobile" in html
    )
