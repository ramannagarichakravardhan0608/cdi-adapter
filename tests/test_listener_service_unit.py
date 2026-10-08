"""The file listener's loop: every new file in the watched folder is read and filed.

The folder here is a real one (``tmp_path``) behind the real local connector, laid out as the
owner's is: **the watched folder itself is the inbox**, with ``processing``, ``success``,
``error``, ``quarantine`` and ``log`` inside it. The *reader* is a recorder, so what is proved is
what the listener does with a file - when it takes it, where it puts it, what it writes down -
and not what the pipeline makes of it. The real reader needs PostgreSQL, the object store and
the model, and was not run here.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from cdi_adapter.config import Settings
from cdi_adapter.listener import __main__ as cli
from cdi_adapter.listener import connectors as C
from cdi_adapter.listener import service as S
from tests.helpers import blank_pdf

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
PDF = blank_pdf(1)  # a real one: since LS-S4 a PDF that cannot be opened is refused
DOCX = b"PK\x03\x04" + b"\x00" * 64
WHEN = datetime(2026, 10, 8, 9, 30, 0, tzinfo=UTC)


class Recorder:
    """A reader that remembers what it was given and answers as told."""

    def __init__(self, *, fail: dict[str, S.Outcome] | None = None, boom: set[str] | None = None,
                 not_ready: str | None = None) -> None:  # fmt: skip
        self.read_files: list[tuple[str, bytes, str]] = []
        self.fail, self.boom, self.not_ready = fail or {}, boom or set(), not_ready

    def reader(self) -> S.Reader:
        def read(name: str, raw: bytes, mime: str) -> S.Outcome:
            self.read_files.append((name, raw, mime))
            if name in self.boom:
                raise RuntimeError("the model gateway is away")
            return self.fail.get(name, S.Outcome(True, job_id="job-" + name))

        return S.Reader(read=read, ready=lambda: self.not_ready)


@pytest.fixture
def folder(tmp_path) -> Path:
    """The watched folder, as the owner's is set up: files are dropped straight into it."""
    root = tmp_path / "File listener"
    root.mkdir()
    return root


def _listener(folder: Path, rec: Recorder) -> S.Listener:
    settings = Settings(listener_connector="local", listener_local_root=str(folder),
                        listener_inbox="")  # fmt: skip
    connector = C.get_connector(settings)
    connector.ensure_folders()
    return S.Listener(connector, rec.reader(), now=lambda: WHEN)


def _drop(folder: Path, name: str, raw: bytes = PNG) -> Path:
    path = folder / name
    path.write_bytes(raw)
    return path


def _names(folder: Path, sub: str = "") -> list[str]:
    return sorted(p.name for p in (folder / sub).iterdir() if p.is_file())


def _two_looks(listener: S.Listener) -> dict[str, Any]:
    """A file is taken on the second look at it, once it is seen not to have changed."""
    listener.poll()
    return listener.poll()


# ------------------------------------------------------------------ a new file is read


def test_a_file_dropped_into_the_folder_is_read_and_moved_to_success(folder) -> None:
    _drop(folder, "rx-001.png", PNG + b"the scan")
    rec = Recorder()
    report = _two_looks(_listener(folder, rec))
    assert rec.read_files == [("rx-001.png", PNG + b"the scan", "image/png")]
    assert report["succeeded"] == ["rx-001.png"] and report["failed"] == []
    assert _names(folder, "success") == ["rx-001.png"]
    assert _names(folder) == [] and _names(folder, "processing") == []
    assert (folder / "success" / "rx-001.png").read_bytes() == PNG + b"the scan"  # untouched


def test_whatever_is_dropped_is_read_one_file_after_another(folder) -> None:
    _drop(folder, "a.png")
    _drop(folder, "b.pdf", PDF)
    _drop(folder, "c.jpg", b"\xff\xd8\xff\xe0" + b"\x00" * 32)
    rec = Recorder()
    report = _two_looks(_listener(folder, rec))
    assert sorted(n for n, _, _ in rec.read_files) == ["a.png", "b.pdf", "c.jpg"]
    assert {m for _, _, m in rec.read_files} == {"image/png", "application/pdf", "image/jpeg"}
    assert sorted(report["succeeded"]) == ["a.png", "b.pdf", "c.jpg"]


def test_a_file_that_arrives_later_is_picked_up_on_a_later_look(folder) -> None:
    rec = Recorder()
    listener = _listener(folder, rec)
    assert listener.poll()["waiting"] == 0
    _drop(folder, "late.png")
    assert listener.poll()["settling"] == 1 and rec.read_files == []  # seen once: not yet
    assert listener.poll()["succeeded"] == ["late.png"]  # unchanged on the next look: read


def test_the_folder_itself_is_the_inbox_and_its_own_subfolders_are_not_files(folder) -> None:
    rec = Recorder()
    listener = _listener(folder, rec)
    assert sorted(p.name for p in folder.iterdir()) == ["error", "log", "processing",
                                                        "quarantine", "success"]  # fmt: skip
    report = _two_looks(listener)
    assert report["waiting"] == 0 and rec.read_files == []  # the folders are not "files"


# ------------------------------------------------------------------ not until it stops changing


def test_a_file_still_being_written_is_left_alone(folder) -> None:
    """OneDrive and a slow copy write a file piece by piece: it is not read half-arrived."""
    path = _drop(folder, "big.pdf", PDF[: len(PDF) // 2])  # the first half has arrived
    rec = Recorder()
    listener = _listener(folder, rec)
    listener.poll()
    path.write_bytes(PDF)  # it grew between two looks
    report = listener.poll()
    assert report["settling"] == 1 and rec.read_files == [] and _names(folder) == ["big.pdf"]
    assert listener.poll()["succeeded"] == ["big.pdf"]  # now it has stopped
    assert rec.read_files[0][1] == PDF  # and it is the whole file that was read


def test_a_file_whose_date_changes_is_left_for_another_look(folder) -> None:
    path = _drop(folder, "rx.png")
    rec = Recorder()
    listener = _listener(folder, rec)
    listener.poll()
    os.utime(path, (1_800_000_000, 1_800_000_000))  # touched again, same size
    assert listener.poll()["settling"] == 1 and rec.read_files == []


def test_an_empty_file_is_never_taken(folder) -> None:
    """A placeholder a sync client creates before the bytes arrive."""
    _drop(folder, "empty.png", b"")
    rec = Recorder()
    listener = _listener(folder, rec)
    for _ in range(3):
        assert listener.poll()["settling"] == 1
    assert rec.read_files == [] and _names(folder) == ["empty.png"]


@pytest.mark.parametrize("name", ["~$letter.docx", ".hidden", "scan.pdf.tmp", "photo.crdownload",
                                  "desktop.ini", "rx.png.partial", "shortcut.lnk"])  # fmt: skip
def test_lock_files_and_half_downloads_are_ignored(folder, name: str) -> None:
    _drop(folder, name)
    rec = Recorder()
    report = _two_looks(_listener(folder, rec))
    assert report["ignored"] == 1 and report["waiting"] == 0
    assert rec.read_files == [] and _names(folder) == [name]  # left exactly where it is


# ------------------------------------------------------------------ a file that does not go through


def test_a_file_that_is_not_a_scan_is_ignored_with_the_reason_and_never_read(folder) -> None:
    """Since LS-S3: a type that is not allowed is not taken at all - it is left where it was
    dropped and listed as ignored. (Before, it was moved to ``error`` with a note.)"""
    _drop(folder, "letter.docx", DOCX)
    rec = Recorder()
    report = _two_looks(_listener(folder, rec))
    assert rec.read_files == []  # the reader never saw it
    assert report["failed"] == [] and report["ignored"] == 1
    assert report["ignored_files"] == [{"file": "letter.docx",
                                        "reason": "file type not allowed"}]  # fmt: skip
    assert "letter.docx" in _names(folder) and _names(folder, "error") == []
    assert _names(folder, "log") == []


def test_a_file_named_like_a_scan_that_is_not_one_still_goes_to_error_with_a_note(folder) -> None:
    """Allowed by name, but the wider patterns of a custom setting let a non-scan through."""
    _drop(folder, "letter.docx", DOCX)
    rec = Recorder()
    listener = _listener(folder, rec)
    listener.patterns = ("*",)
    report = _two_looks(listener)
    assert rec.read_files == []
    assert report["failed"] == [{"file": "letter.docx", "kind": "not_a_scan",
                                 "reason": "Please add a photo, PDF or scan",
                                 "retry": False}]  # fmt: skip
    assert _names(folder, "error") == ["letter.docx"]
    (note,) = _names(folder, "log")
    assert note == "letter.docx.20261008T093000Z.log"
    text = (folder / "log" / note).read_text(encoding="utf-8")
    assert "Please add a photo, PDF or scan" in text and "not_a_scan" in text


def test_a_picture_held_as_unreadable_goes_to_error_with_the_reason(folder) -> None:
    _drop(folder, "blurry.png")
    held = S.Outcome(False, "This photo is too blurry to read. Hold the phone still, let it "
                            "focus, and take it again.", "quality", "job-7")  # fmt: skip
    report = _two_looks(_listener(folder, Recorder(fail={"blurry.png": held})))
    assert report["failed"][0]["kind"] == "quality"
    assert _names(folder, "error") == ["blurry.png"] and _names(folder, "success") == []
    note = (folder / "log" / _names(folder, "log")[0]).read_text(encoding="utf-8")
    assert "too blurry" in note and "job:     job-7" in note
    assert "Take the picture again and drop the new one into the folder." in note


def test_a_reader_that_breaks_costs_that_file_only(folder) -> None:
    _drop(folder, "a.png")
    _drop(folder, "b.png")
    rec = Recorder(boom={"a.png"})
    report = _two_looks(_listener(folder, rec))
    assert report["succeeded"] == ["b.png"]
    assert report["failed"][0]["file"] == "a.png" and "gateway is away" in report["failed"][0][
        "reason"]  # fmt: skip
    assert _names(folder, "error") == ["a.png"] and _names(folder, "success") == ["b.png"]


def test_nothing_is_written_to_log_for_a_file_that_goes_through(folder) -> None:
    _drop(folder, "rx.png")
    _two_looks(_listener(folder, Recorder()))
    assert _names(folder, "log") == []


def test_a_second_file_of_the_same_name_does_not_overwrite_the_first(folder) -> None:
    rec = Recorder()
    listener = _listener(folder, rec)
    _drop(folder, "rx.png", PNG + b"monday")
    _two_looks(listener)
    _drop(folder, "rx.png", PNG + b"tuesday")
    _two_looks(listener)
    assert _names(folder, "success") == ["rx.1.png", "rx.png"]
    assert (folder / "success" / "rx.png").read_bytes() == PNG + b"monday"
    assert (folder / "success" / "rx.1.png").read_bytes() == PNG + b"tuesday"


def test_a_file_is_only_ever_in_one_place(folder) -> None:
    _drop(folder, "ok.png")
    _drop(folder, "bad.docx", DOCX)
    _two_looks(_listener(folder, Recorder()))
    everywhere = [p.name for sub in ("", "processing", "success", "error", "quarantine")
                  for p in (folder / sub).iterdir() if p.is_file()]  # fmt: skip
    assert sorted(everywhere) == ["bad.docx", "ok.png"]


# ------------------------------------------------------------------ never while the pipeline is off


def test_no_file_is_touched_while_the_pipeline_cannot_be_reached(folder) -> None:
    """A listener started with no pipeline behind it must not sweep the folder into error."""
    _drop(folder, "rx-001.png")
    _drop(folder, "rx-002.pdf", PDF)
    rec = Recorder(not_ready="the database cannot be reached (OperationalError)")
    listener = _listener(folder, rec)
    for _ in range(3):
        report = listener.poll()
        assert report["ready"] is False and "database cannot be reached" in report["reason"]
    assert rec.read_files == []
    assert _names(folder) == ["rx-001.png", "rx-002.pdf"]  # exactly where they were dropped
    assert _names(folder, "error") == [] and _names(folder, "processing") == []


def test_the_files_are_read_once_the_pipeline_is_back(folder) -> None:
    _drop(folder, "rx-001.png")
    rec = Recorder(not_ready="the database cannot be reached")
    listener = _listener(folder, rec)
    listener.poll()
    rec.not_ready = None
    assert _two_looks(listener)["succeeded"] == ["rx-001.png"]


def test_the_readiness_check_knocks_and_gives_up_quickly() -> None:
    """With the database down a full connection attempt can wait for minutes; the listener must
    be able to say "not now" straight away. Measured on the owner's PC: 3.5 s for the whole pass."""
    import socket
    import time

    with socket.socket() as open_port:
        open_port.bind(("127.0.0.1", 0))
        open_port.listen(1)
        port = open_port.getsockname()[1]
        assert S._port_closed(f"postgresql+psycopg://127.0.0.1:{port}/db", 5432) is None
    started = time.perf_counter()
    closed = S._port_closed(f"http://127.0.0.1:{port}", 9000, timeout=2.0)  # nothing there now
    assert closed is not None and f"127.0.0.1:{port}" in closed
    assert time.perf_counter() - started < 5.0


def test_the_real_reader_says_which_part_of_the_pipeline_is_away(monkeypatch) -> None:
    monkeypatch.setattr(
        S,
        "_port_closed",
        lambda url, port, timeout=3.0: (
            "nothing answers at 127.0.0.1:5432 - ConnectionRefusedError" if port == 5432 else None
        ),
    )
    said = S.pipeline_reader(Settings()).ready()
    assert said == ("the database cannot be reached (nothing answers at 127.0.0.1:5432 - "
                    "ConnectionRefusedError)")  # fmt: skip


# ------------------------------------------------------------------ after a crash


def test_a_file_a_crash_left_in_processing_is_taken_up_again(folder) -> None:
    rec = Recorder()
    listener = _listener(folder, rec)
    (folder / "processing" / "half-done.png").write_bytes(PNG)
    report = listener.poll()
    assert report["resumed"] == 1 and report["succeeded"] == ["half-done.png"]
    assert _names(folder, "processing") == [] and _names(folder, "success") == ["half-done.png"]
    assert listener.poll()["resumed"] == 0  # only on the first pass of a run


# ------------------------------------------------------------------ one pass, and the note


def test_one_pass_looks_twice_so_that_it_can_take_what_is_there(folder) -> None:
    _drop(folder, "rx.png")
    rec, slept = Recorder(), []
    report = _listener(folder, rec).run(interval=7, once=True, sleep=slept.append)
    assert slept == [7] and report["succeeded"] == ["rx.png"]


def test_one_pass_on_an_empty_folder_does_not_wait(folder) -> None:
    slept: list[float] = []
    report = _listener(folder, Recorder()).run(interval=7, once=True, sleep=slept.append)
    assert slept == [] and report["succeeded"] == [] and report["waiting"] == 0


def test_the_note_says_what_happened_in_plain_lines() -> None:
    file = C.RemoteFile("error/rx.png", "rx.png", "error")
    note = S.failure_note(file, S.Outcome(False, "not finished after 900 s", "timeout", "j1"))
    assert note.splitlines()[:4] == ["file:    rx.png", "reason:  not finished after 900 s",
                                     "kind:    timeout", "job:     j1"]  # fmt: skip
    assert "may still be read" in note


# ------------------------------------------------------------------ a finished job, as it is filed


def _job(status: str, *, state: str = "review", **doc: Any) -> Any:
    return SimpleNamespace(id="job-1", state=state, docs=[SimpleNamespace(
        status=status, error=doc.get("error"), doc_type=doc.get("doc_type"))])  # fmt: skip


def test_a_job_that_was_read_is_a_success() -> None:
    assert S.outcome_of(_job("done")) == S.Outcome(True, job_id="job-1")


@pytest.mark.parametrize(("job", "kind", "says"), [
    # LS-S4: a picture too poor to read is the data error "rescan", with the reason after it
    (_job("quality_hold", error="This photo is too dark to read."), "rescan", "too dark"),
    (_job("quality_hold", error="This photo is too dark to read."), "rescan",
     "rescan: image quality below threshold"),
    (_job("skipped", doc_type="invoice"), "not_clinical", 'read as "invoice"'),
    (_job("error", error="ValueError: unreadable scan"), "failed", "unreadable scan"),
    (_job("done", state="mismatch"), "mismatch", "does not belong"),
])  # fmt: skip
def test_a_job_that_was_not_read_says_why(job: Any, kind: str, says: str) -> None:
    out = S.outcome_of(job)
    assert out.ok is False and out.kind == kind and says in (out.reason or "")
    assert out.job_id == "job-1"


def test_the_real_reader_sends_the_file_as_an_upload_and_waits_for_its_job(monkeypatch) -> None:
    from cdi_adapter.webapp import jobstore

    sent: list[Any] = []

    def submit(files: Any, *, actor: str) -> Any:
        sent.append((files, actor))
        return SimpleNamespace(id="job-9")

    monkeypatch.setattr(jobstore, "submit", submit)
    monkeypatch.setattr(jobstore, "wait", lambda job_id, timeout: _job("done"))
    out = S.pipeline_reader(Settings()).read("rx.png", PNG, "image/png")
    assert out.ok and sent == [([("rx.png", PNG, "image/png")], "listener")]


def test_a_job_that_does_not_finish_in_time_is_a_timeout_not_a_hang(monkeypatch) -> None:
    from cdi_adapter.webapp import jobstore

    def never(job_id: str, timeout: float) -> Any:
        raise TimeoutError(job_id)

    monkeypatch.setattr(jobstore, "submit", lambda files, *, actor: SimpleNamespace(id="job-9"))
    monkeypatch.setattr(jobstore, "wait", never)
    out = S.pipeline_reader(Settings(listener_job_timeout_s=5)).read("rx.png", PNG, "image/png")
    assert (out.ok, out.kind, out.job_id) == (False, "timeout", "job-9")
    assert "not finished after 5 s" in (out.reason or "")


# ------------------------------------------------------------------ the run command


def test_the_run_command_says_so_and_takes_nothing_when_the_pipeline_is_away(
    monkeypatch, capsys, folder
) -> None:
    from cdi_adapter.config import get_settings

    _drop(folder, "rx.png")
    monkeypatch.setenv("CDI_LISTENER_CONNECTOR", "local")
    monkeypatch.setenv("CDI_LISTENER_LOCAL_ROOT", str(folder))
    monkeypatch.setenv("CDI_LISTENER_INBOX", "")
    monkeypatch.setattr(S, "pipeline_reader", lambda settings=None: Recorder(
        not_ready="the database cannot be reached (OperationalError)").reader())  # fmt: skip
    get_settings.cache_clear()
    try:
        code = cli.main(["run", "--once", "--interval", "0"])
    finally:
        get_settings.cache_clear()
    out = capsys.readouterr()
    assert code == 3 and "PIPELINE NOT REACHABLE" in out.err and "No file was taken" in out.err
    assert _names(folder) == ["rx.png"]


def test_the_run_command_reads_what_is_there_and_reports_it(monkeypatch, capsys, folder) -> None:
    from cdi_adapter.config import get_settings

    _drop(folder, "rx.png")
    _drop(folder, "letter.docx", DOCX)
    monkeypatch.setenv("CDI_LISTENER_CONNECTOR", "local")
    monkeypatch.setenv("CDI_LISTENER_LOCAL_ROOT", str(folder))
    monkeypatch.setenv("CDI_LISTENER_INBOX", "")
    monkeypatch.setattr(S, "pipeline_reader", lambda settings=None: Recorder().reader())
    monkeypatch.setattr(S.time, "sleep", lambda s: None)
    get_settings.cache_clear()
    try:
        code = cli.main(["run", "--once", "--interval", "0"])
    finally:
        get_settings.cache_clear()
    said = capsys.readouterr().out
    assert code == 0 and "read         1 file(s) -> success" in said and "  rx.png" in said
    assert "not read     0 file(s) -> error" in said
    assert "ignored      1 file(s)" in said and "letter.docx: file type not allowed" in said
