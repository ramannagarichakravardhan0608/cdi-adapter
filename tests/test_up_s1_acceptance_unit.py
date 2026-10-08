"""UP-S1 acceptance: choose, scan or take a picture of the pages and send them.

One group of tests per acceptance criterion, written on 2026-10-08 to record what the code does
today. **No application code was changed for this file.**

AC3 and AC6 were found unbuilt by this file on 2026-10-08 (their tests were strict expected
failures) and were built the same day; every test here now has to pass.

What these tests cannot show: the screen's script is checked as text and the server by calling
its route function directly. Nothing here runs in a real phone browser, and the paths that need
PostgreSQL are covered by the integration tests named in each group.
"""

from __future__ import annotations

import asyncio
import io
from typing import Any

import pytest
from fastapi import HTTPException
from starlette.datastructures import Headers, UploadFile

from cdi_adapter.webapp import app as app_module
from cdi_adapter.webapp import auth, jobstore
from cdi_adapter.webapp.page import BODY, SCRIPT
from tests.helpers import blank_pdf  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
DOCX = b"PK\x03\x04" + b"\x00" * 64  # a .docx is a zip


# ------------------------------------------------------------------ calling the upload route


class _User:
    username = "desk"


class _Request:
    def __init__(self, headers: dict[str, str] | None = None) -> None:
        self.headers = Headers(headers or {})


class _Job:
    def __init__(self, n: int) -> None:
        self.id, self.trace_id = f"{n:032x}", None


class _Store:
    """Stands in for ``jobstore.submit``: one job per idempotency key, as the database's unique
    index gives (proved on PostgreSQL by tests/test_queueing_integration.py)."""

    def __init__(self) -> None:
        self.jobs: dict[str, _Job] = {}
        self.calls: list[dict[str, Any]] = []

    def submit(self, files: Any, **kwargs: Any) -> _Job:
        self.calls.append({"files": files, **kwargs})
        return self.jobs.setdefault(kwargs["key"], _Job(len(self.jobs) + 1))


@pytest.fixture
def store(monkeypatch) -> _Store:
    s = _Store()
    monkeypatch.setattr(auth, "actor", lambda request: _User())
    monkeypatch.setattr(jobstore, "submit", s.submit)
    return s


def _file(name: str, raw: bytes, mime: str) -> UploadFile:
    return UploadFile(io.BytesIO(raw), filename=name, headers=Headers({"content-type": mime}))


def _send(files: list[UploadFile], *, abha: str | None = None,
          headers: dict[str, str] | None = None) -> dict[str, Any]:  # fmt: skip
    for f in files:
        f.file.seek(0)
    return asyncio.run(app_module.create_job(_Request(headers), files=files, abha=abha,
                                             patient_ref=None))  # fmt: skip


# ------------------------------------------------------------------ AC1
# GIVEN a person opens the screen on a phone WHEN they tap 'Take picture' twice
# THEN two page previews appear in order and can be removed or reordered


def test_ac1_take_photo_is_offered_and_a_phone_uses_its_own_camera() -> None:
    assert "id=cam " in BODY and "Take photo" in BODY
    # a phone browser without the live-camera API opens the phone's camera app through this
    assert 'id=camfile type=file accept="image/*" capture=environment' in BODY
    assert (
        "$('#camfile').onchange = e => { const f = e.target.files[0]; if (f) addPhoto(f);" in SCRIPT
    )


def test_ac1_each_photo_is_added_after_the_ones_already_there() -> None:
    """Twice tapped, twice added: a photo is appended, never put in place of another."""
    assert "pick(chosen.concat([file]));" in SCRIPT
    assert "e.target.value = ''; };" in SCRIPT  # the chooser is cleared, so a second tap fires


def test_ac1_previews_are_drawn_in_the_order_they_will_be_sent() -> None:
    assert "id=pages" in BODY
    assert "box.innerHTML = chosen.map((f, i) => {" in SCRIPT
    assert "<b>Page ${i + 1}</b>" in SCRIPT
    assert "chosen.forEach(f => fd.append('files', f));" in SCRIPT  # sent in that same order


def test_ac1_a_page_can_be_removed_and_moved_either_way() -> None:
    assert "function dropPage(i){ pick(chosen.filter((_, n) => n !== i)); }" in SCRIPT
    assert "[next[i], next[to]] = [next[to], next[i]];" in SCRIPT
    assert 'data-by="-1"' in SCRIPT and 'data-by="1"' in SCRIPT and "data-drop=" in SCRIPT
    assert "if (to < 0 || to >= chosen.length) return;" in SCRIPT  # no move off either end


# ------------------------------------------------------------------ AC2
# GIVEN two pages are added and Send is pressed WHEN the request is made
# THEN one job is created for the two pages and the person sees one result


def test_ac2_two_pages_go_in_one_request_and_make_one_job(store) -> None:
    out = _send([_file("p1.png", PNG, "image/png"), _file("p2.png", PNG + b"2", "image/png")])
    assert len(store.calls) == 1 and len(store.jobs) == 1
    assert [name for name, _, _ in store.calls[0]["files"]] == ["p1.png", "p2.png"]
    assert out["job_id"] == next(iter(store.jobs.values())).id


def test_ac2_the_screen_makes_one_request_for_all_the_pages() -> None:
    assert SCRIPT.count("fetch('/api/jobs', {method:'POST', body:fd})") == 1


def test_ac2_the_person_is_given_one_result_for_the_upload() -> None:
    """One link for the whole upload, and no link per row when there are several pages. The
    result itself (``result.build_job``) is tested in tests/test_result_unit.py."""
    assert 'href="/result/job/${id}"' in SCRIPT and "j.documents.length > 1" in SCRIPT
    assert "j.documents.length === 1 && d.document_id" in SCRIPT
    paths = set(app_module.app.openapi()["paths"])
    assert {"/result/job/{job_id}", "/api/jobs/{job_id}/result.json"} <= paths


# ------------------------------------------------------------------ AC3
# GIVEN a .docx file is chosen WHEN it is added
# THEN it is refused with the message 'Please add a photo, PDF or scan'


def test_ac3_the_file_chooser_already_hints_at_the_allowed_types() -> None:
    """What does exist: the chooser greys out other files. A hint, not a refusal - a dropped
    file, or "All files" in the chooser, gets past it."""
    assert 'accept=".pdf,.png,.jpg,.jpeg,.tif,.tiff"' in BODY


def test_ac3_the_screen_refuses_a_docx_with_the_plain_message() -> None:
    assert "Please add a photo, PDF or scan" in SCRIPT


def test_ac3_the_server_refuses_a_docx_and_creates_no_job(store) -> None:
    mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    with pytest.raises(HTTPException) as refused:
        _send([_file("letter.docx", DOCX, mime)])
    assert refused.value.status_code in (415, 422)
    assert "Please add a photo, PDF or scan" in str(refused.value.detail)
    assert store.calls == []


# ------------------------------------------------------------------ AC4
# GIVEN Send is pressed twice quickly WHEN the second press happens THEN only one job exists


def test_ac4_the_button_is_switched_off_before_the_request_leaves() -> None:
    send = SCRIPT[SCRIPT.index("$('#go').onclick = async () => {") :]
    assert send.index("$('#go').disabled = true;") < send.index("fetch('/api/jobs'")


def test_ac4_the_same_upload_sent_twice_is_one_job(store) -> None:
    """Even if both presses reach the server: the second lands on the first job."""
    files = [_file("p1.png", PNG, "image/png"), _file("p2.png", PNG + b"2", "image/png")]
    first, second = _send(files), _send(files)
    assert first["job_id"] == second["job_id"] and len(store.jobs) == 1
    assert store.calls[0]["key"] == store.calls[1]["key"]


def test_ac4_a_different_upload_is_still_its_own_job(store) -> None:
    a = _send([_file("p1.png", PNG, "image/png")])
    b = _send([_file("p1.png", PNG + b"other", "image/png")])
    assert a["job_id"] != b["job_id"] and len(store.jobs) == 2


def test_ac4_the_real_key_is_made_from_the_bytes_the_names_and_who_sent_them() -> None:
    files = [("p1.png", PNG, "image/png")]
    key = jobstore.idempotency_key(files, actor="desk")
    assert key == jobstore.idempotency_key(list(files), actor="desk")
    assert key != jobstore.idempotency_key(files, actor="someone-else")
    assert key != jobstore.idempotency_key([("p1.png", PNG + b"x", "image/png")], actor="desk")


# ------------------------------------------------------------------ AC5
# GIVEN a photo taken at 4000 px is sent WHEN the screen prepares it
# THEN the original is kept as is and any reduced copy keeps text readable
# (no repeated lossy re-compression)


@pytest.fixture(scope="module")
def photo_4000() -> bytes:
    """A 4000 x 3000 JPEG with fine black-on-white detail, as a phone would hand over."""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (4000, 3000), "white")
    draw = ImageDraw.Draw(img)
    for y in range(200, 2800, 60):
        draw.text((200, y), "Tab Metformin 500 mg 1-0-1 x 30 days", fill="black")
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=90)
    return out.getvalue()


def test_ac5_the_screen_sends_the_file_it_was_given_untouched() -> None:
    """A chosen file, or a phone camera-app photo, goes into the request as the File object:
    the screen has no resize or re-encode step for it."""
    assert "chosen.forEach(f => fd.append('files', f));" in SCRIPT
    assert "blob instanceof File ? blob" in SCRIPT  # a phone photo is kept exactly as it is
    # the only canvas on the page is the live-camera snapshot, and it encodes once
    assert SCRIPT.count("document.createElement('canvas')") == 1
    assert SCRIPT.count(".toBlob(") == 1 and "'image/jpeg', 0.92" in SCRIPT


def test_ac5_the_server_is_handed_the_exact_bytes(store, photo_4000) -> None:
    _send([_file("IMG_4000.jpg", photo_4000, "image/jpeg")])
    ((name, raw, mime),) = store.calls[0]["files"]
    assert (name, mime) == ("IMG_4000.jpg", "image/jpeg") and raw == photo_4000


def test_ac5_the_working_copy_is_full_size_and_lossless(photo_4000) -> None:
    """The page the readers use is a PNG of the same 4000 x 3000 pixels: no shrinking, and
    PNG is lossless, so the JPEG is decoded once and never compressed lossily again."""
    from PIL import Image

    from cdi_adapter.ingest import pages

    (png,) = pages.render_pages(photo_4000, "image/jpeg")
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and pages.image_size(png) == (4000, 3000)
    with Image.open(io.BytesIO(photo_4000)) as a, Image.open(io.BytesIO(png)) as b:
        assert a.convert("RGB").tobytes() == b.convert("RGB").tobytes()  # pixel for pixel


def test_ac5_the_reduced_copy_is_a_png_made_from_the_working_copy(photo_4000) -> None:
    """The only reduced copy is the thumbnail: a PNG, made once, straight from the page."""
    from cdi_adapter.ingest import pages

    (png,) = pages.render_pages(photo_4000, "image/jpeg")
    thumb = pages.make_thumbnail(png)
    assert thumb[:8] == b"\x89PNG\r\n\x1a\n"
    assert max(pages.image_size(thumb)) == pages.THUMB_MAX_SIDE


def test_ac5_the_original_is_stored_under_its_own_key_and_never_rewritten() -> None:
    """Where the bytes are kept as uploaded. That they come back identical is proved on MinIO by
    tests/test_ingest_integration.py ("original never altered")."""
    import inspect

    from cdi_adapter.ingest import service

    source = inspect.getsource(service)
    assert "key = original_key(sha, ext)" in source
    assert "The original bytes are never altered" in inspect.getsource(service.pages)


# ------------------------------------------------------------------ AC6
# GIVEN an ABHA field is typed with letters WHEN Send is pressed
# THEN it is refused with a plain message


def test_ac6_the_upload_has_an_abha_box_and_sends_what_is_typed() -> None:
    assert "id=abha" in BODY
    assert "fd.append('abha', $('#abha').value.trim());" in SCRIPT


def test_ac6_a_well_formed_abha_number_is_accepted(store) -> None:
    _send([_file("p1.png", PNG, "image/png")], abha="91-1234-5678-9012")
    assert store.calls[0]["abha"] == "91-1234-5678-9012"


def test_ac6_letters_in_the_abha_box_are_refused_with_a_plain_message(store) -> None:
    with pytest.raises(HTTPException) as refused:
        _send([_file("p1.png", PNG, "image/png")], abha="ABCD-EFGH-IJKL")
    assert refused.value.status_code == 422
    assert "ABHA" in str(refused.value.detail)
    assert store.calls == []  # and no job was made


def test_ac6_the_screen_checks_the_abha_box_before_sending() -> None:
    send = SCRIPT[SCRIPT.index("$('#go').onclick = async () => {") :]
    before_request = send[: send.index("fetch('/api/jobs'")]
    assert "if (!abhaOk($('#abha').value)){" in before_request and "return; }" in before_request
    assert r"const abhaOk = v => !v.trim() || /^\d(?:[ -]?\d){13}$/.test(v.trim());" in SCRIPT


# ------------------------------------------------------------------ AC3 and AC6, the edges


@pytest.mark.parametrize(("name", "raw", "mime"), [
    ("notes.txt", b"just some text", "text/plain"),
    ("sheet.xlsx", DOCX, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    ("page.heic", b"\x00\x00\x00\x18ftypheic" + b"\x00" * 32, "image/heic"),
    ("no-extension", b"\x00\x01\x02\x03" * 8, "application/octet-stream"),
])  # fmt: skip
def test_ac3_anything_that_is_not_a_photo_pdf_or_scan_is_refused(store, name, raw, mime) -> None:
    with pytest.raises(HTTPException) as refused:
        _send([_file(name, raw, mime)])
    assert refused.value.status_code == 415
    assert refused.value.detail == "Please add a photo, PDF or scan" and store.calls == []


@pytest.mark.parametrize(("name", "raw", "mime"), [
    # a real PDF: since LS-S4 a file named .pdf that cannot be opened is refused at upload
    ("scan.pdf", blank_pdf(1), "application/pdf"),
    ("photo.JPG", b"\xff\xd8\xff\xe0" + b"\x00" * 16, "image/jpeg"),
    ("page.png", PNG, "image/png"),
    ("fax.tiff", b"II*\x00" + b"\x00" * 16, "image/tiff"),
    ("camera-upload", PNG, "application/octet-stream"),  # judged by its bytes, not its name
])  # fmt: skip
def test_ac3_a_photo_a_pdf_and_a_scan_are_all_taken(store, name, raw, mime) -> None:
    _send([_file(name, raw, mime)])
    assert len(store.calls) == 1


def test_ac3_one_wrong_file_among_good_ones_refuses_the_upload_and_makes_no_job(store) -> None:
    mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    with pytest.raises(HTTPException) as refused:
        _send([_file("p1.png", PNG, "image/png"), _file("letter.docx", DOCX, mime)])
    assert refused.value.detail == "Please add a photo, PDF or scan" and store.calls == []


def test_ac3_the_screen_refuses_it_when_added_and_keeps_the_pages_already_there() -> None:
    adder = SCRIPT[SCRIPT.index("function addFiles(fs){") : SCRIPT.index("function pick(fs){")]
    assert "refuse(fs.filter(f => !isScan(f)).map(f => f.name));" in adder
    assert adder.index("fs = fs.filter(isScan);") < adder.index("pick(chosen.concat(fresh));")
    assert "id=addmsg role=alert" in BODY  # said on the page, where a screen reader hears it
    assert "(pdf|png|jpe?g|tiff?)$/i" in SCRIPT


def test_ac3_the_screen_and_the_server_say_the_same_words() -> None:
    assert f"const NOT_A_SCAN = '{app_module.NOT_A_SCAN}';" in SCRIPT


def test_ac3_a_retaken_page_obeys_the_same_rule() -> None:
    import inspect

    assert "_must_be_a_scan(file.filename, raw)" in inspect.getsource(app_module.replace_page)


@pytest.mark.parametrize(
    "typed",
    [
        "ABCD-EFGH-IJKL",
        "91-1234-5678-901X",
        "91-1234-5678",
        "91-1234-5678-9012-3",
        "12345",
        "९१-१२३४-५६७८-९०१२",
    ],
)
def test_ac6_anything_but_fourteen_digits_is_refused(store, typed) -> None:
    with pytest.raises(HTTPException) as refused:
        _send([_file("p1.png", PNG, "image/png")], abha=typed)
    assert refused.value.status_code == 422 and refused.value.detail == app_module.BAD_ABHA
    assert store.calls == []


@pytest.mark.parametrize("typed", ["91-1234-5678-9012", "91123456789012", "91 1234 5678 9012",
                                   "  91-1234-5678-9012  "])  # fmt: skip
def test_ac6_fourteen_digits_are_accepted_however_they_are_spaced(store, typed) -> None:
    _send([_file("p1.png", PNG, "image/png")], abha=typed)
    assert store.calls[0]["abha"] == typed.strip()


@pytest.mark.parametrize("typed", [None, "", "   "])
def test_ac6_an_empty_abha_box_is_fine(store, typed) -> None:
    _send([_file("p1.png", PNG, "image/png")], abha=typed)
    assert store.calls[0]["abha"] is None


def test_ac6_the_message_is_plain_and_says_what_to_do() -> None:
    assert "14 digits" in app_module.BAD_ABHA and "leave the box empty" in app_module.BAD_ABHA
    assert "id=abhamsg role=alert" in BODY and "The ABHA number must be 14 digits" in BODY
    send = SCRIPT[SCRIPT.index("$('#go').onclick = async () => {") :]
    assert send.index("if (!abhaOk($('#abha').value))") < send.index("fetch('/api/jobs'")
