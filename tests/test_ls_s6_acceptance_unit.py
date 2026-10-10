"""LS-S6 acceptance: file each prescription by outcome, and keep only the failure reasons in a
log folder.

One group of tests per acceptance criterion, then the rules behind them. The watched folder is
a real folder (``tmp_path``) through the real ``local`` connector, with the real listener loop
on it. The reader (the pipeline) is a stand-in that answers as told.

AC3 is about a crash between the database save and the move. Here the "database" is a stand-in
that keeps one result per file's bytes, the way the real job store does (an upload already
accepted is recognised by its bytes). Whether the real job store does so for a listener file is
proved by its own tests, not by this one.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from cdi_adapter.config import Settings
from cdi_adapter.listener import connectors as C
from cdi_adapter.listener import ledger as L
from cdi_adapter.listener import service as S
from tests.helpers import blank_pdf
from tests.test_listener_connectors_unit import Drive

ROOT = Path(__file__).resolve().parents[1]
WHEN = datetime(2026, 10, 9, 9, 30, tzinfo=UTC)
# what was read from the picture, which a note must never hold
PATIENT = {"name": "Anjali Das", "age": "48", "phone": "9434388275", "abha": "91-1234-5678-9012",
           "drug": "Metformin 500 mg", "doctor": "Dr. Sibaji Das", "bp": "167/82"}  # fmt: skip


class Database:
    """Where results are saved. One result per file's bytes, however often it is sent - as the
    real job store answers a second identical upload with the job it already has."""

    def __init__(self) -> None:
        self.results: dict[str, str] = {}
        self.saves = 0
        self.asked = 0

    def save(self, raw: bytes) -> str:
        self.asked += 1
        key = hashlib.sha256(raw).hexdigest()
        if key not in self.results:
            self.saves += 1
            self.results[key] = f"job-{self.saves}"
        return self.results[key]


def _reader(database: Database | None = None, *, fail: dict[str, S.Outcome] | None = None,
            crash: dict[str, Exception] | None = None) -> S.Reader:  # fmt: skip
    database = database or Database()

    def read(name: str, raw: bytes, mime: str) -> S.Outcome:
        if crash and name in crash:
            raise crash[name]
        if fail and name in fail:
            return fail[name]
        return S.Outcome(True, job_id=database.save(raw))

    return S.Reader(read=read)


@pytest.fixture
def folder(tmp_path) -> Path:
    root = tmp_path / "File listener"
    root.mkdir()
    return root


def _listener(folder: Path, reader: S.Reader, **more: Any) -> S.Listener:
    settings = Settings(listener_connector="local", listener_local_root=str(folder),
                        listener_inbox="")  # fmt: skip
    connector = C.get_connector(settings)
    connector.ensure_folders()
    return S.Listener(connector, reader, now=lambda: WHEN, **more)


def _run(listener: S.Listener) -> dict[str, Any]:
    listener.poll()
    return listener.poll()


def _names(folder: Path, sub: str = "") -> list[str]:
    return sorted(p.name for p in (folder / sub).iterdir() if p.is_file())


def _note(folder: Path) -> str:
    (name,) = _names(folder, "log")
    return (folder / "log" / name).read_text(encoding="utf-8")


def _rx(n: int = 0) -> bytes:
    return blank_pdf(1, size=(200 + n, 280))


# ------------------------------------------------------------------ AC1
# GIVEN a file is read successfully WHEN the result is saved
# THEN the file is in 'success' and no note was written


def test_ac1_a_file_that_was_read_is_in_success_and_no_note_is_written(folder) -> None:
    database = Database()
    (folder / "rx.pdf").write_bytes(_rx())
    report = _run(_listener(folder, _reader(database)))
    assert report["succeeded"] == ["rx.pdf"] and database.saves == 1
    assert _names(folder, "success") == ["rx.pdf"]
    assert _names(folder, "log") == []  # nothing for a success
    assert _names(folder) == [] and _names(folder, "error") == []
    assert _names(folder, "processing") == [] and _names(folder, "quarantine") == []


def test_ac1_the_file_is_the_same_file_byte_for_byte(folder) -> None:
    raw = _rx()
    (folder / "rx.pdf").write_bytes(raw)
    _run(_listener(folder, _reader()))
    assert (folder / "success" / "rx.pdf").read_bytes() == raw


def test_ac1_the_move_comes_after_the_save(folder) -> None:
    """The reader answers only once the result is saved; the file is still in ``processing``
    at that moment, and reaches ``success`` afterwards."""
    seen: dict[str, Any] = {}
    database = Database()

    def read(name: str, raw: bytes, mime: str) -> S.Outcome:
        job = database.save(raw)
        seen["processing"] = _names(folder, "processing")
        seen["success"] = _names(folder, "success")
        return S.Outcome(True, job_id=job)

    (folder / "rx.pdf").write_bytes(_rx())
    _run(_listener(folder, S.Reader(read=read)))
    assert seen == {"processing": ["rx.pdf"], "success": []}
    assert _names(folder, "success") == ["rx.pdf"]


def test_ac1_many_successes_still_write_nothing_to_log(folder) -> None:
    for n in range(6):
        (folder / f"rx-{n}.pdf").write_bytes(_rx(n))
    report = _run(_listener(folder, _reader()))
    assert len(report["succeeded"]) == 6 and _names(folder, "log") == []


# ------------------------------------------------------------------ AC2
# GIVEN a file fails WHEN the run ends
# THEN the file is in 'error' and exactly one note in 'log' says why


def test_ac2_a_failed_file_is_in_error_with_exactly_one_note_saying_why(folder) -> None:
    held = S.Outcome(False, "rescan: image quality below threshold - This photo is too blurry "
                            "to read.", "rescan", "job-7")  # fmt: skip
    (folder / "blurry.pdf").write_bytes(_rx())
    report = _run(_listener(folder, _reader(fail={"blurry.pdf": held})))
    assert _names(folder, "error") == ["blurry.pdf"] and _names(folder, "success") == []
    assert _names(folder, "log") == ["blurry.pdf.20261009T093000Z.run1.log"]  # exactly one
    note = _note(folder)
    assert "reason:  rescan: image quality below threshold - This photo is too blurry" in note
    assert report["failed"][0]["to"] == "error"


def test_ac2_the_note_has_every_line_the_story_asks_for(folder) -> None:
    (folder / "rx.pdf").write_bytes(_rx())
    listener = _listener(folder, _reader(crash={"rx.pdf": ConnectionError("model server gone")}))
    report = _run(listener)
    lines = dict(ln.split(":", 1) for ln in _note(folder).splitlines() if ln[:1].isalpha())
    assert lines["file"].strip() == "rx.pdf"
    assert lines["reason"].strip().startswith("ConnectionError")  # the reason
    assert lines["kind"].strip() == "failed"  # the kind of error
    assert lines["batch"].strip() == report["batches"][0]["id"]  # the batch id
    assert lines["run"].strip() == "1 of 4"  # the run number (LS-S7: a run and three retries)
    assert "will be tried again by itself" in lines["next"]  # what happens next
    assert "test_ls_s6_acceptance_unit.py" in _note(folder)  # the end of the error trace
    assert _note(folder).rstrip().endswith("ConnectionError")


def test_ac2_three_failed_files_get_one_note_each(folder) -> None:
    for n in range(3):
        (folder / f"rx-{n}.pdf").write_bytes(_rx(n))
    boom = {f"rx-{n}.pdf": RuntimeError("x") for n in range(3)}
    _run(_listener(folder, _reader(crash=boom)))
    assert _names(folder, "error") == ["rx-0.pdf", "rx-1.pdf", "rx-2.pdf"]
    assert [n.split(".2026")[0] for n in _names(folder, "log")] == ["rx-0.pdf", "rx-1.pdf",
                                                                   "rx-2.pdf"]  # fmt: skip


def test_ac2_a_good_file_beside_a_bad_one_gets_no_note(folder) -> None:
    (folder / "good.pdf").write_bytes(_rx(1))
    (folder / "bad.pdf").write_bytes(_rx(2))
    _run(_listener(folder, _reader(crash={"bad.pdf": RuntimeError("x")})))
    assert _names(folder, "success") == ["good.pdf"] and _names(folder, "error") == ["bad.pdf"]
    assert len(_names(folder, "log")) == 1 and _names(folder, "log")[0].startswith("bad.pdf.")


def test_ac2_a_data_error_says_it_will_not_be_tried_again(folder) -> None:
    (folder / "scan.pdf").write_bytes(b"%PDF-1.7\n" + b"\x00" * 200)
    report = _run(_listener(folder, _reader()))
    note = _note(folder)
    assert "kind:    not_readable" in note and "will not be tried again" in note
    assert report["failed"][0]["retry"] is False and _names(folder, "error") == ["scan.pdf"]


# ------------------------------------------------------------------ AC3
# GIVEN the pod is stopped after the database save but before the move
# WHEN the listener restarts THEN the file is moved to 'success' and the result is not saved twice


class Stopped(BaseException):
    """The pod going away. Not an ordinary error: nothing catches it."""


def _stopped_after_the_save(folder: Path, database: Database, ledger: L.MemoryLedger) -> None:
    """Run until the result is saved, then stop the pod before the file is moved."""

    def read(name: str, raw: bytes, mime: str) -> S.Outcome:
        database.save(raw)
        raise Stopped  # saved - and gone, before the listener could move the file

    (folder / "rx.pdf").write_bytes(_rx())
    listener = _listener(folder, S.Reader(read=read), ledger=ledger, worker="pod-1")
    listener.poll()
    with pytest.raises(Stopped):
        listener.poll()


def test_ac3_a_stop_between_the_save_and_the_move_leaves_the_file_where_it_is(folder) -> None:
    database, ledger = Database(), L.MemoryLedger()
    _stopped_after_the_save(folder, database, ledger)
    assert database.saves == 1  # the result is in the database ...
    assert _names(folder, "processing") == ["rx.pdf"]  # ... and the file has not moved
    assert _names(folder, "success") == [] and _names(folder, "error") == []
    assert _names(folder, "log") == []  # and it is not written up as a failure
    (row,) = ledger._rows.values()
    assert row.state == "processing" and row.lease_owner == "pod-1"  # the lease still holds it


def test_ac3_after_the_restart_the_file_is_in_success_and_saved_once(folder) -> None:
    database, ledger = Database(), L.MemoryLedger()
    _stopped_after_the_save(folder, database, ledger)
    restarted = _listener(folder, _reader(database), ledger=ledger, worker="pod-1")
    report = restarted.poll()  # the first pass of the new run
    assert report["resumed"] == 1 and report["succeeded"] == ["rx.pdf"]
    assert _names(folder, "success") == ["rx.pdf"] and _names(folder, "processing") == []
    assert database.saves == 1 and database.asked == 2  # asked again, saved once
    assert _names(folder, "log") == []


def test_ac3_the_restarted_run_points_at_the_result_that_was_already_saved(folder) -> None:
    database, ledger = Database(), L.MemoryLedger()
    _stopped_after_the_save(folder, database, ledger)
    _listener(folder, _reader(database), ledger=ledger, worker="pod-1").poll()
    (row,) = ledger._rows.values()
    assert row.state == "succeeded" and row.job_id == "job-1"  # the first save's job


def test_ac3_another_pod_takes_it_over_once_the_lease_has_run_out(folder) -> None:
    clock = [WHEN]
    database, ledger = Database(), L.MemoryLedger(now=lambda: clock[0])
    _stopped_after_the_save(folder, database, ledger)
    other = _listener(folder, _reader(database), ledger=ledger, worker="pod-2",
                      lease_seconds=600)  # fmt: skip
    assert other.poll()["held"] == 1 and _names(folder, "processing") == ["rx.pdf"]  # not yet
    clock[0] = clock[0].replace(hour=10)  # an hour on: pod-1's lease is over
    later = _listener(folder, _reader(database), ledger=ledger, worker="pod-2")
    assert later.poll()["succeeded"] == ["rx.pdf"] and database.saves == 1


def test_ac3_the_real_job_store_knows_an_upload_by_its_bytes() -> None:
    """What "not saved twice" rests on: the same bytes from the listener give the same key,
    and the job store answers a key it has with the job it already made."""
    from cdi_adapter.webapp import jobstore

    raw = _rx()
    first = jobstore.idempotency_key([("rx.pdf", raw, "application/pdf")], actor="listener")
    again = jobstore.idempotency_key([("rx.pdf", raw, "application/pdf")], actor="listener")
    other = jobstore.idempotency_key([("rx.pdf", _rx(1), "application/pdf")], actor="listener")
    assert first == again and first != other


@pytest.mark.xfail(strict=True, reason="LS-S6 AC3: shown here with a stand-in for the database. "
                   "Stopping a real pod between the save and the move, and finding one result "
                   "afterwards, needs PostgreSQL, the document store and the pipeline running. "
                   "It has not been done.")  # fmt: skip
def test_ac3_has_been_shown_on_a_running_pipeline() -> None:
    assert (ROOT / "tests" / "test_listener_crash_integration.py").is_file()


# ------------------------------------------------------------------ AC4
# GIVEN a failure note is searched for patient names and values WHEN it is read
# THEN none are found


def _leaks_the_patient(name: str, raw: bytes, mime: str) -> S.Outcome:
    """A pipeline error whose message quotes everything that was read from the picture."""
    raise ValueError(
        f"validation failed for patient {PATIENT['name']!r} age={PATIENT['age']} phone "
        f"{PATIENT['phone']} ABHA {PATIENT['abha']}: dose {PATIENT['drug']!r} written by "
        f"{PATIENT['doctor']} with BP {PATIENT['bp']} is out of range"
    )


def test_ac4_a_failure_note_holds_no_patient_name_or_value(folder) -> None:
    (folder / "scan-0042.pdf").write_bytes(_rx())
    _run(_listener(folder, S.Reader(read=_leaks_the_patient)))
    note = _note(folder)
    # Two things in a note are this system's own numbers and can contain any digits by chance:
    # the batch id (random) and the line numbers in the trace. They are taken out before the
    # search, so a batch called "a8c248..." is not mistaken for the patient's age of 48.
    import re

    searched = "\n".join(ln for ln in note.splitlines() if not ln.startswith("batch:"))
    searched = re.sub(r":\d+ in ", " in ", searched)
    assert "ValueError" in searched and "kind:" in searched  # the note is still all there
    for what, value in PATIENT.items():
        assert value not in searched, what
    for word in ("Anjali", "Das", "Metformin", "Sibaji", "9434", "167"):
        assert word not in searched, word


def test_ac4_it_still_says_what_kind_of_error_it_was_and_where(folder) -> None:
    (folder / "scan-0042.pdf").write_bytes(_rx())
    _run(_listener(folder, S.Reader(read=_leaks_the_patient)))
    note = _note(folder)
    assert "reason:  ValueError (the message is not written here" in note
    assert "kind:    failed" in note and "in _leaks_the_patient" in note  # where, not what


def test_ac4_a_job_that_failed_in_the_pipeline_is_written_up_the_same_way(folder) -> None:
    """The error text of a failed job comes from another process and can quote the document."""
    failed = S.Outcome(False, f"ExtractionError: could not parse dose for {PATIENT['name']} "
                              f"({PATIENT['drug']})", "failed", "job-3")  # fmt: skip
    (folder / "rx.pdf").write_bytes(_rx())
    _run(_listener(folder, _reader(fail={"rx.pdf": failed})))
    note = _note(folder)
    assert PATIENT["name"] not in note and PATIENT["drug"] not in note
    assert "reason:  ExtractionError (the message is not written here" in note
    assert "job:     job-3" in note  # where to look, for someone who may


def test_ac4_an_error_with_no_name_is_still_not_quoted(folder) -> None:
    failed = S.Outcome(False, f"patient {PATIENT['name']} not matched", "failed")
    (folder / "rx.pdf").write_bytes(_rx())
    _run(_listener(folder, _reader(fail={"rx.pdf": failed})))
    note = _note(folder)
    assert PATIENT["name"] not in note and "reason:  the pipeline failed" in note


def test_ac4_the_trace_gives_places_never_the_message_or_the_code_line() -> None:
    try:
        raise ValueError(f"dose {PATIENT['drug']} for {PATIENT['name']}")
    except ValueError as exc:
        end = S.trace_end(exc)
    assert end.splitlines()[-1] == "ValueError"
    assert "test_ls_s6_acceptance_unit.py" in end and " in test_ac4_the_trace_gives" in end
    assert PATIENT["drug"] not in end and PATIENT["name"] not in end and "raise" not in end


def test_ac4_reasons_this_system_writes_itself_are_given_in_full() -> None:
    """They are technical facts - a size, a page count, a picture-quality verdict."""
    file = C.RemoteFile("error/rx.pdf", "rx.pdf", "error")
    for kind, reason in (("too_many_pages", "the document has 60 pages; the limit is 10."),
                         ("too_large", "the file is 61.0 MB; the limit is 50 MB."),
                         ("rescan", "rescan: image quality below threshold - too blurry"),
                         ("timeout", "not finished after 900 s")):  # fmt: skip
        assert f"reason:  {reason}" in S.failure_note(file, S.Outcome(False, reason, kind))


def test_ac4_the_file_name_is_the_only_thing_from_outside_in_the_note(folder) -> None:
    """ISSUE, recorded as a fact: the note names the file. If the person who dropped it put
    the patient's name in the file name, the name is in the note - and on the file itself."""
    (folder / "anjali-das.pdf").write_bytes(_rx())
    _run(_listener(folder, S.Reader(read=_leaks_the_patient)))
    assert "file:    anjali-das.pdf" in _note(folder)


# ------------------------------------------------------------------ AC5
# GIVEN a file with the same name is already in 'success' WHEN another file is moved there
# THEN both are kept; nothing is overwritten


def test_ac5_a_second_file_of_the_same_name_is_kept_beside_the_first(folder) -> None:
    listener = _listener(folder, _reader())
    first, second = _rx(1), _rx(2)  # two different prescriptions, both called rx.pdf
    (folder / "rx.pdf").write_bytes(first)
    _run(listener)
    (folder / "rx.pdf").write_bytes(second)
    report = _run(listener)
    assert _names(folder, "success") == ["rx.1.pdf", "rx.pdf"]  # both kept
    assert (folder / "success" / "rx.pdf").read_bytes() == first  # the first is untouched
    assert (folder / "success" / "rx.1.pdf").read_bytes() == second
    assert report["succeeded"] == ["rx.1.pdf"]  # and the report names where it went


def test_ac5_the_same_in_error_and_each_run_has_its_own_note(folder) -> None:
    boom = {"rx.pdf": RuntimeError("x")}
    listener = _listener(folder, _reader(crash=boom))
    for n in (1, 2):
        (folder / "rx.pdf").write_bytes(_rx(n))
        _run(listener)
    assert _names(folder, "error") == ["rx.1.pdf", "rx.pdf"]
    assert len(_names(folder, "log")) == 2


@pytest.mark.parametrize("kind", ["local", "onedrive", "sharepoint", "gdrive"])
def test_ac5_every_drive_keeps_both(kind: str, tmp_path) -> None:
    drive = Drive(kind, tmp_path)
    drive.put("success/rx.pdf", b"the first")
    drive.put("inbox/rx.pdf", b"the second")
    drive.connector.ensure_folders()
    (waiting,) = drive.connector.list("inbox")
    moved = drive.connector.move(waiting, "success")
    assert moved.name == "rx.1.pdf" and drive.names("success") == ["rx.1.pdf", "rx.pdf"]
    kept = {f.name: drive.connector.download(f) for f in drive.connector.list("success")}
    assert kept == {"rx.pdf": b"the first", "rx.1.pdf": b"the second"}


def test_ac5_nothing_is_ever_deleted() -> None:
    """No connector has a delete, and the loop never asks for one."""
    for name in ("service.py", "connectors.py"):
        source = (ROOT / "src" / "cdi_adapter" / "listener" / name).read_text(encoding="utf-8")
        code = "\n".join(ln for ln in source.splitlines() if not ln.lstrip().startswith("#"))
        for call in (".unlink(", "os.remove(", "shutil.rmtree(", 'method="DELETE"', '"DELETE"',
                     ".delete("):  # fmt: skip
            assert call not in code, (name, call)


def test_ac5_every_file_dropped_is_still_somewhere_afterwards(folder) -> None:
    names = [f"rx-{n}.pdf" for n in range(5)]
    for n, name in enumerate(names):
        (folder / name).write_bytes(_rx(n))
    (folder / "bad.pdf").write_bytes(b"%PDF-1.7\n" + b"\x00" * 99)
    (folder / "letter.docx").write_bytes(b"PK\x03\x04" + b"\x00" * 99)
    _run(_listener(folder, _reader(crash={"rx-3.pdf": RuntimeError("x")})))
    everywhere = (_names(folder) + _names(folder, "success") + _names(folder, "error")
                  + _names(folder, "quarantine") + _names(folder, "processing"))  # fmt: skip
    assert sorted(everywhere) == sorted([*names, "bad.pdf", "letter.docx"])


# ------------------------------------------------------------------ quarantine: the runs used up


def _fail_again(folder: Path, listener: S.Listener, raw: bytes) -> dict[str, Any]:
    """Put the file back in the inbox, as a person (or the retry agent, LS-S7) would."""
    (folder / "rx.pdf").write_bytes(raw)
    return _run(listener)["failed"][0]


def test_a_file_that_fails_on_every_run_goes_to_quarantine(folder) -> None:
    """Four runs since LS-S7 - the first and three retries - where LS-S6 alone had three."""
    raw = _rx()
    listener = _listener(folder, _reader(crash={"rx.pdf": TimeoutError("model server")}))
    first, second, third, fourth = (_fail_again(folder, listener, raw) for _ in range(4))
    assert (first["run"], first["to"], first["retry"]) == (1, "error", True)
    assert (second["run"], second["to"], second["retry"]) == (2, "error", True)
    assert (third["run"], third["to"], third["retry"]) == (3, "error", True)
    assert (fourth["run"], fourth["to"], fourth["retry"]) == (4, "quarantine", False)
    assert _names(folder, "quarantine") == ["rx.pdf"]
    assert _names(folder, "error") == ["rx.1.pdf", "rx.2.pdf", "rx.pdf"]  # earlier tries, kept


def test_each_failed_run_has_its_own_note_with_its_run_number(folder) -> None:
    raw = _rx()
    listener = _listener(folder, _reader(crash={"rx.pdf": TimeoutError("model server")}))
    for _ in range(4):
        _fail_again(folder, listener, raw)
    notes = sorted(_names(folder, "log"), key=lambda n: n.rsplit(".", 2)[1])  # by run number
    assert [n.rsplit(".", 2)[1] for n in notes] == ["run1", "run2", "run3", "run4"]
    last = (folder / "log" / notes[-1]).read_text(encoding="utf-8")
    assert "run:     4 of 4" in last and "the retries are used up" in last
    assert "quarantine" in last and "until a person looks at it" in last


def test_a_data_error_never_goes_to_quarantine_however_often_it_is_dropped(folder) -> None:
    listener = _listener(folder, _reader())
    for _ in range(4):
        (folder / "scan.pdf").write_bytes(b"%PDF-1.7\n" + b"\x00" * 200)
        _run(listener)
    assert _names(folder, "quarantine") == [] and len(_names(folder, "error")) == 4


def test_a_success_after_two_failures_is_a_success(folder) -> None:
    raw, calls = _rx(), []

    def read(name: str, data: bytes, mime: str) -> S.Outcome:
        calls.append(name)
        if len(calls) < 3:
            raise TimeoutError("model server")
        return S.Outcome(True, job_id="job-1")

    listener = _listener(folder, S.Reader(read=read))
    for _ in range(3):
        (folder / "rx.pdf").write_bytes(raw)
        report = _run(listener)
    assert report["succeeded"] == ["rx.pdf"] and _names(folder, "quarantine") == []
    assert len(_names(folder, "log")) == 2  # the two failed runs; nothing for the success


def test_the_number_of_runs_is_a_setting(folder) -> None:
    assert Settings.model_fields["listener_max_attempts"].default == 3  # retries: 4 runs
    made = S.Listener.from_settings(None, _reader(), Settings(listener_max_attempts=0))  # type: ignore[arg-type]
    assert made.max_runs == 1
    (folder / "rx.pdf").write_bytes(_rx())
    report = _run(_listener(folder, _reader(crash={"rx.pdf": RuntimeError("x")}), max_attempts=0))
    assert report["failed"][0]["to"] == "quarantine"


def test_a_quarantined_file_is_recorded_as_such() -> None:
    assert "quarantined" in L.STATES
    migration = ROOT / "db" / "alembic" / "versions" / "0031_listener_file_quarantined.py"
    text = migration.read_text(encoding="utf-8")
    assert "'quarantined'" in text and 'down_revision = "0030"' in text


def test_failed_files_are_tried_again_without_a_person() -> None:
    """Was an expected failure until LS-S7 built the retry agent (2026-10-09)."""
    names = {p.stem for p in (ROOT / "src" / "cdi_adapter" / "listener").glob("*.py")}
    assert names & {"recovery", "retry", "retries"}


# ------------------------------------------------------------------ every move is in the history


def test_every_move_of_a_file_is_written_to_its_history(folder) -> None:
    (folder / "rx.pdf").write_bytes(_rx())
    listener = _listener(folder, _reader())
    _run(listener)
    (row,) = listener.ledger._rows.values()  # type: ignore[attr-defined]
    moves = [(e["to"], e["name"]) for e in row.history if e["event"] == "moved"]
    assert moves == [("processing", "rx.pdf"), ("success", "rx.pdf")]
    assert all(e["at"] == WHEN.isoformat() for e in row.history if e["event"] == "moved")


def test_a_failed_files_history_shows_where_it_went_and_under_what_name(folder) -> None:
    listener = _listener(folder, _reader(crash={"rx.pdf": RuntimeError("x")}))
    for n in (1, 2):
        (folder / "rx.pdf").write_bytes(_rx(n))
        _run(listener)
    rows = sorted(listener.ledger._rows.values(), key=lambda r: r.name)  # type: ignore[attr-defined]
    last = [[(e["to"], e["name"]) for e in r.history if e["event"] == "moved"][-1] for r in rows]
    assert last == [("error", "rx.1.pdf"), ("error", "rx.pdf")]  # the name it was kept under


def test_the_history_can_be_stored_as_it_is() -> None:
    entry = {"event": "moved", "to": "success", "name": "rx.pdf", "at": WHEN.isoformat()}
    assert json.loads(json.dumps([entry])) == [entry]


# ------------------------------------------------------------------ the folders are settings


def test_the_four_folder_names_are_settings(tmp_path) -> None:
    root = tmp_path / "watch"
    settings = Settings(listener_connector="local", listener_local_root=str(root),
                        listener_inbox="", listener_success="completed", listener_error="failed",
                        listener_quarantine="held", listener_log="notes")  # fmt: skip
    connector = C.get_connector(settings)
    connector.ensure_folders()
    (root / "good.pdf").write_bytes(_rx(1))
    (root / "bad.pdf").write_bytes(_rx(2))
    listener = S.Listener(connector, _reader(crash={"bad.pdf": RuntimeError("x")}), max_attempts=0,
                          now=lambda: WHEN)  # fmt: skip
    _run(listener)
    assert _names(root, "completed") == ["good.pdf"]  # the folder a person sees is the setting
    assert _names(root, "held") == ["bad.pdf"] and len(_names(root, "notes")) == 1
    assert not (root / "success").exists() and not (root / "log").exists()
