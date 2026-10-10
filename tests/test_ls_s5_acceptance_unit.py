"""LS-S5 acceptance: run files in batches of three.

One group of tests per acceptance criterion, then the rules behind them. The watched folder is
a real folder (``tmp_path``) through the real ``local`` connector, and the real listener loop
runs on it. The reader (the pipeline) is a stand-in that records when each file starts and
ends, so "at the same time" and "only when the whole batch has finished" are measured, not
assumed. The clock the listener reads is one the test moves, so "waits 60 s" takes no time.

Not measured here: how long a real batch takes. The reader is a stand-in; the real model server
reads one document at a time (24-37 s each, measured), so three files read "together" still
take about three times as long as one until the batching server (RD-S5) exists.
"""

from __future__ import annotations

import os
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from cdi_adapter.config import Settings, get_settings
from cdi_adapter.listener import __main__ as cli
from cdi_adapter.listener import connectors as C
from cdi_adapter.listener import ledger as L
from cdi_adapter.listener import service as S
from tests.helpers import blank_pdf

ROOT = Path(__file__).resolve().parents[1]
START = datetime(2026, 10, 9, 9, 0, tzinfo=UTC)


class Clock:
    """The time the listener sees. The test moves it."""

    def __init__(self) -> None:
        self.at = START

    def __call__(self) -> datetime:
        return self.at

    def pass_(self, seconds: float) -> None:
        self.at += timedelta(seconds=seconds)


class Pipeline:
    """A reader that takes a moment over each file and records when it started and ended, how
    many were being read at once, and which thread read it."""

    def __init__(self, *, takes: float = 0.05, fail: dict[str, S.Outcome] | None = None,
                 boom: set[str] | None = None, clock: Clock | None = None) -> None:  # fmt: skip
        self.takes, self.fail, self.boom, self.clock = takes, fail or {}, boom or set(), clock
        self.spans: dict[str, tuple[float, float]] = {}
        self.order: list[str] = []
        self.at_once = self.most_at_once = 0
        self._lock = threading.Lock()

    def reader(self) -> S.Reader:
        def read(name: str, raw: bytes, mime: str) -> S.Outcome:
            began = time.perf_counter()
            with self._lock:
                self.order.append(name)
                self.at_once += 1
                self.most_at_once = max(self.most_at_once, self.at_once)
            try:
                time.sleep(self.takes)
                if name in self.boom:
                    raise RuntimeError("the model server dropped the connection")
                return self.fail.get(name) or S.Outcome(True, job_id=f"job-{name}")
            finally:
                with self._lock:
                    self.at_once -= 1
                    self.spans[name] = (began, time.perf_counter())

        return S.Reader(read=read)

    def overlap(self, a: str, b: str) -> bool:
        (a0, a1), (b0, b1) = self.spans[a], self.spans[b]
        return a0 < b1 and b0 < a1


@pytest.fixture
def folder(tmp_path) -> Path:
    root = tmp_path / "File listener"
    root.mkdir()
    return root


def _drop(folder: Path, names: list[str], *, oldest_first: bool = True) -> None:
    """Drop real, different one-page PDFs, each a minute newer than the one before."""
    for n, name in enumerate(names):
        path = folder / name
        path.write_bytes(blank_pdf(1, size=(200 + n, 280)))  # different bytes: no duplicates
        stamp = START.timestamp() - 3600 + (n if oldest_first else -n) * 60
        os.utime(path, (stamp, stamp))


def _listener(folder: Path, pipeline: Pipeline, clock: Clock | None = None,
              **more: Any) -> S.Listener:  # fmt: skip
    settings = Settings(listener_connector="local", listener_local_root=str(folder),
                        listener_inbox="")  # fmt: skip
    connector = C.get_connector(settings)
    connector.ensure_folders()
    clock = clock or Clock()
    # the ledger reads the same clock as the listener, as the table and the listener share one
    # in real life
    return S.Listener(connector, pipeline.reader(), now=clock, ledger=L.MemoryLedger(now=clock),
                      **{"batch_size": 3, "batch_wait_seconds": 60.0, **more})  # fmt: skip


def _names(folder: Path, sub: str = "") -> list[str]:
    return sorted(p.name for p in (folder / sub).iterdir() if p.is_file())


SEVEN = [f"rx-{n}.pdf" for n in range(1, 8)]


# ------------------------------------------------------------------ AC1
# GIVEN 7 finished files wait WHEN the listener runs
# THEN it processes 3, then 3, then waits up to 60 s for the last one


def test_ac1_seven_files_go_three_then_three_then_the_last_one_waits(folder) -> None:
    clock, pipeline = Clock(), Pipeline()
    _drop(folder, SEVEN)
    listener = _listener(folder, pipeline, clock)
    listener.poll()  # seen once
    report = listener.poll()  # seen unchanged: all seven are finished
    assert [b["files"] for b in report["batches"]] == [SEVEN[0:3], SEVEN[3:6]]  # 3, then 3
    assert sorted(report["succeeded"]) == SEVEN[0:6]
    assert report["batch_waiting"] == 1 and _names(folder) == ["rx-7.pdf"]  # the last one waits


def test_ac1_the_last_one_starts_when_it_has_waited_sixty_seconds(folder) -> None:
    clock, pipeline = Clock(), Pipeline()
    _drop(folder, SEVEN)
    listener = _listener(folder, pipeline, clock)
    listener.poll()
    listener.poll()
    for waited in (10, 20, 30, 40, 50):  # a look every 10 s: not yet
        clock.pass_(10)
        report = listener.poll()
        assert report["batches"] == [] and report["batch_waiting"] == 1, waited
    clock.pass_(10)  # 60 s
    report = listener.poll()
    assert [b["files"] for b in report["batches"]] == [["rx-7.pdf"]]
    assert report["succeeded"] == ["rx-7.pdf"] and _names(folder) == []


def test_ac1_the_second_batch_starts_only_when_the_whole_first_has_finished(folder) -> None:
    pipeline = Pipeline(takes=0.08)
    _drop(folder, SEVEN[:6])
    listener = _listener(folder, pipeline)
    listener.poll()
    listener.poll()
    first, second = SEVEN[0:3], SEVEN[3:6]
    last_end_of_first = max(pipeline.spans[n][1] for n in first)
    first_start_of_second = min(pipeline.spans[n][0] for n in second)
    assert first_start_of_second >= last_end_of_first
    assert pipeline.most_at_once == 3  # never more than a batch at the model server


def test_ac1_the_files_of_a_batch_run_at_the_same_time(folder) -> None:
    pipeline = Pipeline(takes=0.15)
    _drop(folder, SEVEN[:3])
    listener = _listener(folder, pipeline)
    listener.poll()
    began = time.perf_counter()
    report = listener.poll()
    took = time.perf_counter() - began
    a, b, c = SEVEN[:3]
    assert pipeline.overlap(a, b) and pipeline.overlap(b, c) and pipeline.overlap(a, c)
    assert took < 3 * 0.15  # together, not one after another
    assert report["batches"][0]["read_together"] == 3


def test_ac1_the_oldest_files_are_in_the_first_batch(folder) -> None:
    pipeline = Pipeline()
    _drop(folder, SEVEN, oldest_first=False)  # rx-7 is now the oldest, rx-1 the newest
    listener = _listener(folder, pipeline)
    listener.poll()
    report = listener.poll()
    assert [b["files"] for b in report["batches"]] == [
        ["rx-7.pdf", "rx-6.pdf", "rx-5.pdf"],
        ["rx-4.pdf", "rx-3.pdf", "rx-2.pdf"],
    ]
    assert _names(folder) == ["rx-1.pdf"]  # the newest is the one left waiting


def test_ac1_a_file_that_arrives_while_one_waits_fills_the_batch_sooner(folder) -> None:
    clock, pipeline = Clock(), Pipeline()
    _drop(folder, SEVEN[:2])
    listener = _listener(folder, pipeline, clock)
    listener.poll()
    assert listener.poll()["batch_waiting"] == 2  # two wait: fewer than a batch
    _drop(folder, ["late.pdf"])
    clock.pass_(10)
    listener.poll()  # the third is seen once
    clock.pass_(10)
    report = listener.poll()  # ... and is finished: a full batch, 40 s before the waiting time
    assert len(report["batches"]) == 1 and len(report["batches"][0]["files"]) == 3


# ------------------------------------------------------------------ AC2
# GIVEN one of the 3 files in a batch is corrupt WHEN the batch runs
# THEN the other 2 finish and the corrupt one goes to 'error'


def test_ac2_a_corrupt_file_goes_to_error_and_the_other_two_finish(folder) -> None:
    pipeline = Pipeline()
    _drop(folder, ["a.pdf", "c.pdf"])
    (folder / "b.pdf").write_bytes(b"%PDF-1.7\n" + b"\x00" * 300)  # named a PDF, not one
    os.utime(folder / "b.pdf", (START.timestamp() - 3570, START.timestamp() - 3570))
    listener = _listener(folder, pipeline)
    listener.poll()
    report = listener.poll()
    assert [b["files"] for b in report["batches"]] == [["a.pdf", "b.pdf", "c.pdf"]]  # one batch
    assert sorted(report["succeeded"]) == ["a.pdf", "c.pdf"]
    (failed,) = report["failed"]
    assert failed["file"] == "b.pdf" and failed["kind"] == "not_readable"
    assert _names(folder, "error") == ["b.pdf"] and _names(folder, "success") == ["a.pdf", "c.pdf"]
    assert sorted(pipeline.order) == ["a.pdf", "c.pdf"]  # the corrupt one never reached it


def test_ac2_a_file_the_pipeline_fails_on_does_not_stop_the_others(folder) -> None:
    held = S.Outcome(False, "rescan: image quality below threshold", "rescan", "job-9")
    pipeline = Pipeline(fail={"b.pdf": held})
    _drop(folder, ["a.pdf", "b.pdf", "c.pdf"])
    listener = _listener(folder, pipeline)
    listener.poll()
    report = listener.poll()
    assert sorted(report["succeeded"]) == ["a.pdf", "c.pdf"]
    assert [f["file"] for f in report["failed"]] == ["b.pdf"]


def test_ac2_a_crash_while_reading_one_file_does_not_stop_the_others(folder) -> None:
    pipeline = Pipeline(boom={"b.pdf"})
    _drop(folder, ["a.pdf", "b.pdf", "c.pdf"])
    listener = _listener(folder, pipeline)
    listener.poll()
    report = listener.poll()
    assert sorted(report["succeeded"]) == ["a.pdf", "c.pdf"]
    (failed,) = report["failed"]
    assert failed["kind"] == "failed" and "dropped the connection" in failed["reason"]
    assert len(_names(folder, "log")) == 1  # one note, for the one that failed


def test_ac2_the_batch_still_ends_and_the_next_one_runs(folder) -> None:
    pipeline = Pipeline(boom={"rx-2.pdf"})
    _drop(folder, SEVEN[:6])
    listener = _listener(folder, pipeline)
    listener.poll()
    report = listener.poll()
    assert len(report["batches"]) == 2 and len(report["succeeded"]) == 5
    assert [f["file"] for f in report["failed"]] == ["rx-2.pdf"]


def test_ac2_all_three_bad_is_three_files_in_error_and_nothing_stuck(folder) -> None:
    pipeline = Pipeline(boom={"a.pdf", "b.pdf", "c.pdf"})
    _drop(folder, ["a.pdf", "b.pdf", "c.pdf"])
    listener = _listener(folder, pipeline)
    listener.poll()
    report = listener.poll()
    assert len(report["failed"]) == 3 and _names(folder, "error") == ["a.pdf", "b.pdf", "c.pdf"]
    assert _names(folder) == [] and _names(folder, "processing") == []


# ------------------------------------------------------------------ AC3
# GIVEN the waiting time is 0 and 2 files wait WHEN the listener polls THEN they start at once


def test_ac3_with_no_waiting_time_two_files_start_at_once(folder) -> None:
    clock, pipeline = Clock(), Pipeline()
    _drop(folder, ["a.pdf", "b.pdf"])
    listener = _listener(folder, pipeline, clock, batch_wait_seconds=0.0)
    listener.poll()
    report = listener.poll()  # finished on this look - and started on this look
    assert [b["files"] for b in report["batches"]] == [["a.pdf", "b.pdf"]]
    assert sorted(report["succeeded"]) == ["a.pdf", "b.pdf"] and report["batch_waiting"] == 0
    assert clock.at == START  # no time passed at all


def test_ac3_with_a_waiting_time_the_same_two_files_wait(folder) -> None:
    pipeline = Pipeline()
    _drop(folder, ["a.pdf", "b.pdf"])
    listener = _listener(folder, pipeline, batch_wait_seconds=60.0)
    listener.poll()
    report = listener.poll()
    assert report["batches"] == [] and report["batch_waiting"] == 2 and pipeline.order == []


def test_ac3_with_no_waiting_time_a_lone_file_starts_at_once_too(folder) -> None:
    pipeline = Pipeline()
    _drop(folder, ["only.pdf"])
    listener = _listener(folder, pipeline, batch_wait_seconds=0.0)
    listener.poll()
    assert listener.poll()["succeeded"] == ["only.pdf"]


def test_ac3_the_batch_size_and_the_waiting_time_are_settings() -> None:
    fields = Settings.model_fields
    assert fields["listener_batch_size"].default == 3
    assert fields["listener_batch_wait_seconds"].default == 60.0
    assert fields["listener_poll_seconds"].default == 10.0
    made = S.Listener.from_settings(None, Pipeline().reader(),  # type: ignore[arg-type]
                                    Settings(listener_batch_size=5,
                                             listener_batch_wait_seconds=0))  # fmt: skip
    assert (made.batch_size, made.batch_wait_seconds) == (5, 0.0)


def test_ac3_a_batch_size_of_one_runs_files_one_after_another(folder) -> None:
    pipeline = Pipeline()
    _drop(folder, SEVEN[:3])
    listener = _listener(folder, pipeline, batch_size=1)
    listener.poll()
    report = listener.poll()
    assert [b["files"] for b in report["batches"]] == [[n] for n in SEVEN[:3]]
    assert pipeline.most_at_once == 1 and pipeline.order == SEVEN[:3]


# ------------------------------------------------------------------ AC4
# GIVEN a batch of 3 runs WHEN time is measured
# THEN seconds from drop to saved are recorded per file for the load test (LS-S10)


class ReadingTakesTime(Pipeline):
    """Each file's reading moves the listener's clock on, so the seconds can be checked."""

    def reader(self) -> S.Reader:
        inner = super().reader()

        def read(name: str, raw: bytes, mime: str) -> S.Outcome:
            out = inner.read(name, raw, mime)
            with self._lock:
                assert self.clock is not None
                self.clock.pass_(30)  # the model server took 30 s over this one
            return out

        return S.Reader(read=read)


def test_ac4_seconds_from_drop_to_saved_are_recorded_for_every_file(folder) -> None:
    clock = Clock()
    pipeline = ReadingTakesTime(clock=clock)
    _drop(folder, ["a.pdf", "b.pdf", "c.pdf"])
    listener = _listener(folder, pipeline, clock)
    listener.poll()  # the drop: the listener sees the files
    clock.pass_(10)
    report = listener.poll()  # 10 s later they are finished and the batch runs
    timings = {t["file"]: t for t in report["timings"]}
    assert set(timings) == {"a.pdf", "b.pdf", "c.pdf"}
    for item in timings.values():
        assert item["state"] == "succeeded" and item["batch"] == report["batches"][0]["id"]
        assert item["seconds_from_drop"] == 100.0  # 10 s to be finished + 3 x 30 s of reading


def test_ac4_the_seconds_are_kept_with_the_file_and_carry_the_batch_id(folder) -> None:
    clock = Clock()
    _drop(folder, ["a.pdf", "b.pdf", "c.pdf"])
    listener = _listener(folder, ReadingTakesTime(clock=clock), clock)
    listener.poll()
    clock.pass_(10)
    report = listener.poll()
    batch_id = report["batches"][0]["id"]
    rows = list(listener.ledger._rows.values())  # type: ignore[attr-defined]
    assert len(rows) == 3
    for position, row in enumerate(sorted(rows, key=lambda r: r.name), start=1):
        # LS-S6 also writes every move into the history: here, the batch and the outcome
        taken, finished = (e for e in row.history if e["event"] != "moved")
        assert taken == {"event": "batch", "batch_id": batch_id, "position": position,
                         "size": 3, "at": (START + timedelta(seconds=10)).isoformat()}  # fmt: skip
        assert finished["event"] == "succeeded" and finished["batch_id"] == batch_id
        assert finished["seconds_from_drop"] == 100.0 and finished["at"]


def test_ac4_a_file_that_failed_is_timed_too(folder) -> None:
    clock = Clock()
    pipeline = ReadingTakesTime(clock=clock, boom={"b.pdf"})
    _drop(folder, ["a.pdf", "b.pdf", "c.pdf"])
    listener = _listener(folder, pipeline, clock)
    listener.poll()
    report = listener.poll()
    states = {t["file"]: t["state"] for t in report["timings"]}
    assert states == {"a.pdf": "succeeded", "b.pdf": "failed", "c.pdf": "succeeded"}
    assert all(t["seconds_from_drop"] is not None for t in report["timings"])


def test_ac4_the_time_a_lone_file_waited_for_its_batch_is_in_its_seconds(folder) -> None:
    clock = Clock()
    _drop(folder, ["only.pdf"])
    listener = _listener(folder, Pipeline(), clock)
    listener.poll()
    for _ in range(7):  # finished at 10 s, then it waits 60 s for a batch that never fills
        clock.pass_(10)
        report = listener.poll()
    (timing,) = report["timings"]
    assert timing["seconds_from_drop"] == 70.0


def test_ac4_the_run_command_prints_the_batch_and_the_seconds(monkeypatch, capsys, folder) -> None:
    _drop(folder, ["a.pdf", "b.pdf", "c.pdf"])
    monkeypatch.setenv("CDI_LISTENER_CONNECTOR", "local")
    monkeypatch.setenv("CDI_LISTENER_LOCAL_ROOT", str(folder))
    monkeypatch.setenv("CDI_LISTENER_INBOX", "")
    monkeypatch.setattr(S, "pipeline_reader", lambda settings=None: Pipeline().reader())
    monkeypatch.setattr(S.time, "sleep", lambda s: None)
    get_settings.cache_clear()
    try:
        assert cli.main(["run", "--once", "--interval", "0"]) == 0
    finally:
        get_settings.cache_clear()
    out = capsys.readouterr().out
    assert "3 file(s), 3 read together" in out and "s from drop to succeeded" in out
    assert out.count('"listener_file_timing"') == 3 and '"listener_batch_done"' in out


def test_ac4_the_table_has_a_history_column_for_it() -> None:
    migration = (ROOT / "db" / "alembic" / "versions" / "0030_listener_file_history.py").read_text(
        encoding="utf-8")  # fmt: skip
    assert "ADD COLUMN history jsonb" in migration and 'down_revision = "0029"' in migration
    source = (ROOT / "src" / "cdi_adapter" / "listener" / "ledger.py").read_text(encoding="utf-8")
    assert "SET history = history || CAST(:event AS jsonb)" in source


def test_ac4_the_load_test_reads_them() -> None:
    """Built and run for LS-S10 (2026-10-09): the soak harness reads each file's seconds from
    its history, and batches of 1, 3 and 6 were timed on the real model server."""
    from cdi_adapter.listener import soak

    history = [{"event": "moved"}, {"event": "succeeded", "seconds_from_drop": 41.5}]
    assert soak._seconds(history) == 41.5
    assert (ROOT / "scripts" / "listener_soak.py").exists()
    assert list((ROOT / "tests").glob("test_ls_s10*"))


# ------------------------------------------------------------------ the rules behind them


def test_select_batch_by_itself() -> None:
    clock = Clock()
    listener = S.Listener(None, Pipeline().reader(), now=clock, batch_size=3,  # type: ignore[arg-type]
                          batch_wait_seconds=60.0)  # fmt: skip
    files = [(C.RemoteFile(f"id-{n}", f"f{n}.pdf", "inbox", size=9),
              L.Entry(f"e{n}", "s", f"id-{n}", f"f{n}.pdf")) for n in range(5)]  # fmt: skip
    assert listener.select_batch([]) == []
    assert listener.select_batch(files) == files[:3]  # a full batch: at once
    listener._ready_since = {f.id: START for f, _ in files}
    assert listener.select_batch(files[:2]) == []  # two, just finished: wait
    clock.pass_(59)
    assert listener.select_batch(files[:2]) == []
    clock.pass_(1)
    assert listener.select_batch(files[:2]) == files[:2]  # the oldest has waited 60 s


def test_a_once_run_does_not_hold_a_short_batch_back(folder) -> None:
    """``run --once`` has no later pass, so it starts whatever is finished."""
    pipeline = Pipeline()
    _drop(folder, ["a.pdf", "b.pdf"])
    listener = _listener(folder, pipeline, batch_wait_seconds=60.0)
    report = listener.run(interval=0, once=True, sleep=lambda s: None)
    assert sorted(report["succeeded"]) == ["a.pdf", "b.pdf"] and len(report["batches"]) == 1


def test_the_same_picture_twice_in_one_batch_is_still_one_result(folder) -> None:
    pipeline, same = Pipeline(), blank_pdf(1)
    for n, name in enumerate(["a.pdf", "b.pdf"]):
        (folder / name).write_bytes(same)
        os.utime(folder / name, (START.timestamp() - 600 + n, START.timestamp() - 600 + n))
    _drop(folder, ["c.pdf"])
    listener = _listener(folder, pipeline)
    listener.poll()
    report = listener.poll()
    assert sorted(pipeline.order) == ["a.pdf", "c.pdf"]  # b was not read
    assert [d["file"] for d in report["duplicates"]] == ["b.pdf"]
    assert report["batches"][0]["read_together"] == 2


def test_each_batch_has_its_own_id(folder) -> None:
    _drop(folder, SEVEN[:6])
    listener = _listener(folder, Pipeline())
    listener.poll()
    report = listener.poll()
    first, second = (b["id"] for b in report["batches"])
    assert first != second and len(first) == 12
    by_batch: dict[str, set[str]] = {}
    for row in listener.ledger._rows.values():  # type: ignore[attr-defined]
        by_batch.setdefault(row.history[0]["batch_id"], set()).add(row.name)
    assert by_batch == {first: set(SEVEN[0:3]), second: set(SEVEN[3:6])}


def test_a_lost_sign_in_during_a_batch_lets_the_others_finish_then_stops(folder) -> None:
    def read(name: str, raw: bytes, mime: str) -> S.Outcome:
        if name == "b.pdf":
            raise C.SignInExpired("the Microsoft sign-in has expired")
        time.sleep(0.05)
        return S.Outcome(True, job_id=name)

    _drop(folder, ["a.pdf", "b.pdf", "c.pdf"])
    settings = Settings(listener_connector="local", listener_local_root=str(folder),
                        listener_inbox="")  # fmt: skip
    connector = C.get_connector(settings)
    connector.ensure_folders()
    listener = S.Listener(connector, S.Reader(read=read), now=Clock())
    listener.poll()
    with pytest.raises(C.SignInExpired):
        listener.poll()
    assert _names(folder, "success") == ["a.pdf", "c.pdf"]  # the other two were filed first
    assert _names(folder, "processing") == ["b.pdf"]  # taken up again on the next run


def test_the_memory_ledger_is_safe_from_several_threads() -> None:
    ledger = L.MemoryLedger()
    files = [C.RemoteFile(f"inbox/f{n}", f"f{n}", "inbox", size=n + 1) for n in range(200)]

    def work(file: C.RemoteFile) -> None:
        entry = ledger.observe("local:x", file)
        ledger.claim(entry, "w", 60)
        ledger.note(entry, {"event": "batch", "batch_id": "b"})
        ledger.processed("0" * 64)
        ledger.finish(entry, "succeeded", sha256=f"{int(file.name[1:]):064x}")

    threads = [threading.Thread(target=work, args=(f,)) for f in files]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    rows = list(ledger._rows.values())
    assert len(rows) == 200 and all(r.state == "succeeded" and len(r.history) == 1 for r in rows)
