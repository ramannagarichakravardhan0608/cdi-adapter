"""OUT-S3 "One upload gives one result, even if repeated or interrupted" - the three criteria.

* AC1  the same prescription sent twice gives one result;
* AC2  the web app restarted during a job: the job finishes with no loss;
* AC3  one corrupt document in a batch: the others finish and the corrupt one is reported.

The real job runner (``JobRunner``), the real acceptance rule (``jobstore.submit``) and the
real idempotency key run here. What stands in is the outside: a ``World`` that plays the
database of documents - one document per fingerprint of the bytes, one result per document -
and counts every time a page is actually read, so "once" can be counted and not assumed.
The same counts against a running system are ``scripts/chaos_upload.py``.
"""

from __future__ import annotations

import hashlib
import importlib.util
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from cdi_adapter import metrics
from cdi_adapter.webapp import jobstore
from cdi_adapter.webapp.jobs import STEPS, DocState, Job, JobRunner, Stages
from tests.test_apptier_unit import MemStore

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("chaos_upload", ROOT / "scripts/chaos_upload.py")
assert _spec and _spec.loader
chaos = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(chaos)

CORRUPT = b"not a picture"


class World:
    """The outside of the runner: documents by fingerprint, their results, their review."""

    def __init__(self, *, hold_after: int | None = None) -> None:
        self.lock = threading.Lock()
        self.by_sha: dict[str, str] = {}
        self.docs: dict[str, dict[str, Any]] = {}
        self.read: dict[str, int] = {}  # document id -> how many times the model read it
        self.results: dict[str, int] = {}  # document id -> results written for it
        self.hold_after, self.gate = hold_after, threading.Event()
        self.reading = threading.Event()

    def ingest(self, raw: bytes, name: str, mime: Any, patient_id: Any = None) -> Any:
        if raw.startswith((CORRUPT, chaos.CORRUPT)):
            raise ValueError("cannot identify image file")
        sha = hashlib.sha256(raw).hexdigest()
        with self.lock:
            again = sha in self.by_sha
            doc = self.by_sha.setdefault(sha, f"doc-{len(self.by_sha) + 1:03d}")
            self.docs.setdefault(doc, {"status": "received", "live": False, "patient": None})
        return SimpleNamespace(document_id=doc, deduplicated=again)

    def extract(self, doc: str, patient_id: Any = None, expected_patient: Any = None) -> Any:
        with self.lock:
            done = sum(self.read.values())
        if self.hold_after is not None and done >= self.hold_after:
            self.reading.set()
            self.gate.wait(20)  # the runner "dies" here: it never comes back in time
        with self.lock:
            self.read[doc] = self.read.get(doc, 0) + 1
            self.docs[doc]["patient"] = patient_id or "patient-1"
        return {"patient_id": patient_id or "patient-1", "facts": 3}

    def validate(self, doc: str) -> None:
        with self.lock:
            self.docs[doc]["status"] = "validated"
            self.results[doc] = 1  # the result is rebuilt from the rows: one, however often

    def enqueue(self, doc: str, job_id: str) -> None:
        with self.lock:
            self.docs[doc]["live"] = True

    def existing(self, doc: str) -> dict[str, Any] | None:
        d = self.docs.get(doc) or {}
        if d.get("status") != "validated" or not d.get("live"):
            return None
        return {"doc_type": "prescription", "patient_id": d["patient"], "facts": 3,
                "accepted": 2, "in_review": 1}  # fmt: skip

    def stages(self) -> Stages:
        return Stages(
            ingest=self.ingest, ocr=lambda d, force_engine=None: None,
            classify=lambda d: {"doc_type": "prescription", "is_handwritten": False},
            extract=self.extract, terminology=lambda d: None, validate=self.validate,
            counts=lambda d: {"facts": 3, "accepted": 2, "in_review": 1},
            merge_identity=lambda p: None, enqueue=self.enqueue, existing=self.existing,
        )  # fmt: skip


def _page(n: int) -> bytes:
    return f"prescription page {n}".encode()


def _run(world: World, files: list[tuple[str, bytes, str | None]], **job: Any) -> Job:
    runner = JobRunner(world.stages(), max_workers=4)
    return runner.wait(runner.create(files, **job).id, timeout=30)


def _stored(store: MemStore, files: list[tuple[str, bytes, str | None]], **kw: Any) -> Job:
    job = Job(id=f"{len(store.rows) + 1:032x}", docs=[DocState(filename=f[0]) for f in files])
    store.put(job, files, **kw)
    return job


def _until(done: Any, seconds: float = 20.0) -> None:
    deadline = time.monotonic() + seconds
    while not done() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert done(), "did not happen in time"


# ------------------------------------------------------------------ AC1: sent twice, one result


class Accepts:
    """What ``jobstore.submit`` needs of the job table: the key is unique."""

    def __init__(self) -> None:
        self.jobs: dict[str, Job] = {}
        self.staged: dict[str, bytes] = {}
        self.objects = self

    def by_key(self, key: str) -> Job | None:
        return self.jobs.get(key)

    def insert(self, job: Job, files: Any, *, actor: str, accepted_by: str, key: str,
               priority: int) -> None:  # fmt: skip
        if key in self.jobs:
            raise jobstore.Duplicate(key)
        self.jobs[key] = job

    def put_bytes(self, key: str, raw: bytes, mime: str) -> None:
        self.staged[key] = raw

    def delete(self, key: str) -> None:
        self.staged.pop(key, None)


@pytest.fixture
def accepts(monkeypatch) -> Accepts:
    sent: list[str] = []
    monkeypatch.setattr(jobstore, "dispatch", lambda job_id, priority=None: sent.append(job_id))
    st = Accepts()
    st.dispatched = sent  # type: ignore[attr-defined]
    return st


def test_ac1_the_same_upload_sent_twice_is_one_job_dispatched_once(accepts):
    files = [("rx.png", _page(1), "image/png")]
    one = jobstore.submit(files, actor="desk", st=accepts)  # type: ignore[arg-type]
    two = jobstore.submit(files, actor="desk", st=accepts)  # type: ignore[arg-type]
    assert one.id == two.id and len(accepts.jobs) == 1
    assert accepts.dispatched == [one.id], "the second send starts no second run"  # type: ignore[attr-defined]
    assert len(accepts.staged) == 1, "and keeps no second copy of the file"


def test_ac1_there_is_one_key_per_upload_and_the_database_keeps_it_unique():
    files = [("rx.png", _page(1), "image/png")]
    key = jobstore.idempotency_key(files, actor="desk")
    assert key == jobstore.idempotency_key(files, actor="desk") and key.startswith("body:")
    assert key != jobstore.idempotency_key([("rx.png", _page(2), "image/png")], actor="desk")
    named = jobstore.idempotency_key(files, actor="desk", given="retry-7")
    assert named.startswith("hdr:") and named == jobstore.idempotency_key(
        [("other.png", _page(9), None)], actor="desk", given="retry-7")  # fmt: skip
    sql = "\n".join(p.read_text(encoding="utf-8")
                    for p in (ROOT / "db/alembic/versions").glob("*.py"))  # fmt: skip
    assert "idempotency_key" in sql and "UNIQUE" in sql.upper()


def test_ac1_the_same_bytes_are_the_same_document_whatever_the_file_is_called():
    world = World()
    a = world.ingest(_page(1), "scan.png", None)
    b = world.ingest(_page(1), "IMG_0042.png", None)
    assert a.document_id == b.document_id and b.deduplicated and not a.deduplicated
    # and that is the real rule too: the fingerprint is the SHA-256 of the bytes
    from cdi_adapter.ingest import service

    assert "hashlib.sha256(raw).hexdigest()" in Path(service.__file__).read_text("utf-8")


def test_ac1_the_same_picture_sent_again_under_another_name_gives_one_result():
    world = World()
    first = _run(world, [("scan.png", _page(1), "image/png")])
    before = metrics.DOC_REUSED._value.get()
    second = _run(world, [("IMG_0042.png", _page(1), "image/png")])
    d1, d2 = first.docs[0], second.docs[0]
    assert d1.status == d2.status == "done" and d1.document_id == d2.document_id
    assert world.read == {d1.document_id: 1}, "it was read once, not again"
    assert world.results == {d1.document_id: 1}, "one result exists"
    assert d2.reused and not d1.reused and d2.steps_done == list(STEPS)
    assert (d2.facts, d2.accepted, d2.in_review) == (3, 2, 1), "answered with the result it has"
    assert second.patient_id == first.patient_id and second.state == "review"
    assert metrics.DOC_REUSED._value.get() == before + 1


def test_ac1_twenty_files_sent_twice_are_twenty_results():
    world = World()
    files = [(f"rx{n:02d}.png", _page(n), "image/png") for n in range(20)]
    for n in range(0, 20, 10):  # the upload form takes ten files at a time
        _run(world, files[n:n + 10])
    for n in range(0, 20, 10):  # all of them again, under other names
        again = _run(world, [(f"copy-{name}", raw, mime) for name, raw, mime in files[n:n + 10]])
        assert all(d.reused and d.status == "done" for d in again.docs)
    assert len(world.docs) == 20 and sum(world.results.values()) == 20
    assert set(world.read.values()) == {1}


def test_ac1_a_document_whose_review_was_cleared_is_read_again():
    """Owner, 2026-10-10: once its review was cleared away, the same file sent again IS read."""
    world = World()
    first = _run(world, [("scan.png", _page(1), "image/png")])
    doc = str(first.docs[0].document_id)
    world.docs[doc]["live"] = False
    second = _run(world, [("scan.png", _page(1), "image/png")])
    assert not second.docs[0].reused and world.read[doc] == 2
    assert world.results == {doc: 1}, "still one result: the new reading replaces the old"


def test_ac1_an_upload_for_another_patient_is_not_answered_with_this_ones_result():
    world = World()
    first = _run(world, [("scan.png", _page(1), "image/png")])
    doc = str(first.docs[0].document_id)
    other = _run(world, [("scan.png", _page(1), "image/png")], patient_id="patient-2")
    assert not other.docs[0].reused and world.read[doc] == 2  # the identity check had to run


def test_ac1_when_the_database_cannot_say_the_page_is_read_as_before():
    world = World()
    _run(world, [("scan.png", _page(1), "image/png")])
    stages = world.stages()
    stages.existing = lambda d: (_ for _ in ()).throw(ConnectionError("database away"))
    runner = JobRunner(stages, max_workers=2)
    job = runner.wait(runner.create([("scan.png", _page(1), "image/png")]).id, timeout=30)
    assert job.docs[0].status == "done" and not job.docs[0].reused


# ------------------------------------------------------------------ AC2: restarted mid-job


def test_ac2_a_job_whose_runner_died_is_finished_by_another_with_no_loss():
    """Twenty pages; the web app is "killed" while the fourth is being read. Another instance
    finds the job, keeps what was finished, and finishes the rest."""
    world, store = World(hold_after=3), MemStore()
    files = [(f"rx{n:02d}.png", _page(n), "image/png") for n in range(20)]
    job = _stored(store, files)
    dead = JobRunner(world.stages(), max_workers=4, store=store, owner="web-1")
    t = threading.Thread(target=dead.run_stored, args=(job.id,), daemon=True)
    t.start()
    assert world.reading.wait(20), "the first runner got as far as reading"
    before = {d["filename"]: d["status"] for d in store.state(job.id)["docs"]}
    assert sum(s == "done" for s in before.values()) == 3 and not store.rows[job.id]["finished"]
    with store.lock:  # killed: its heartbeat stops, so its lease runs out
        store.rows[job.id]["lease_until"] = time.monotonic() - 1
    world.hold_after = None
    alive = JobRunner(world.stages(), max_workers=4, store=store, owner="web-2")
    assert alive.sweep() == [job.id]
    _until(lambda: store.rows[job.id]["finished"])
    world.gate.set()  # whatever the dead runner still held comes back too late
    t.join(20)
    docs = store.state(job.id)["docs"]
    assert [d["status"] for d in docs] == ["done"] * 20, "no page was lost"
    assert len({d["document_id"] for d in docs}) == 20 == sum(world.results.values())
    assert store.rows[job.id]["attempts"] == 2 and store.finished == [job.id]
    # the three pages finished before the kill were not read a second time
    kept = [d["document_id"] for d in docs if before[d["filename"]] == "done"]
    assert len(kept) == 3 and all(world.read[d] == 1 for d in kept)


def test_ac2_the_runner_that_was_cut_off_writes_nothing_after_it_lost_the_job():
    world, store = World(hold_after=1), MemStore()
    job = _stored(store, [(f"rx{n}.png", _page(n), None) for n in range(3)])
    dead = JobRunner(world.stages(), max_workers=2, store=store, owner="web-1")
    t = threading.Thread(target=dead.run_stored, args=(job.id,), daemon=True)
    t.start()
    assert world.reading.wait(20)
    with store.lock:
        store.rows[job.id].update(owner="web-2", lease_until=time.monotonic() + 60)
        taken = dict(store.rows[job.id]["job"])
    world.gate.set()
    t.join(20)
    assert store.rows[job.id]["job"] == taken and job.id not in store.finished


def test_ac2_an_upload_accepted_just_before_the_stop_is_picked_up_after_it():
    """Accepted and stored, but the web app stopped before anyone ran it."""
    world, store = World(), MemStore()
    job = _stored(store, [("rx.png", _page(1), None)], owner="web-1",
                  lease_until=time.monotonic() - 1)  # fmt: skip
    back = JobRunner(world.stages(), max_workers=2, store=store, owner="web-1-restarted")
    assert back.sweep() == [job.id]
    _until(lambda: store.rows[job.id]["finished"])
    assert store.state(job.id)["docs"][0]["status"] == "done" and len(world.results) == 1


def test_ac2_a_page_read_but_not_yet_filed_is_not_read_again_on_resume():
    world, store = World(), MemStore()
    doc = world.ingest(_page(1), "rx.png", None).document_id
    world.read[doc] = 1
    job = Job(id="a" * 32, patient_id="patient-1", docs=[
        DocState("rx.png", status="running", step="terminology", document_id=doc,
                 steps_done=["ingest", "ocr", "classify", "extract"])])  # fmt: skip
    store.put(job, [("rx.png", _page(1), None)], owner="dead", lease_until=time.monotonic() - 1)
    JobRunner(world.stages(), max_workers=2, store=store, owner="web-2").run_stored(job.id)
    assert store.state(job.id)["docs"][0]["status"] == "done"
    assert world.read[doc] == 1 and world.results == {doc: 1}


def test_ac2_the_upload_is_stored_before_it_is_acknowledged(accepts, monkeypatch):
    order: list[str] = []
    put, insert = accepts.put_bytes, accepts.insert
    monkeypatch.setattr(accepts, "put_bytes", lambda *a: (order.append("file"), put(*a))[1])
    monkeypatch.setattr(accepts, "insert",
                        lambda *a, **k: (order.append("row"), insert(*a, **k))[1])  # fmt: skip
    monkeypatch.setattr(jobstore, "dispatch", lambda *a, **k: order.append("dispatch"))
    jobstore.submit([("rx.png", _page(1), None)], actor="desk", st=accepts)  # type: ignore[arg-type]
    assert order == ["file", "row", "dispatch"]


def test_ac2_an_upload_that_could_not_be_stored_is_refused_not_acknowledged(accepts, monkeypatch):
    def down(*a: Any, **k: Any) -> None:
        raise ConnectionError("database away")

    monkeypatch.setattr(accepts, "insert", down)
    with pytest.raises(jobstore.NotAccepted):
        jobstore.submit([("rx.png", _page(1), None)], actor="desk", st=accepts)  # type: ignore[arg-type]
    assert accepts.staged == {} and accepts.dispatched == []  # type: ignore[attr-defined]


def test_ac2_every_web_instance_sweeps_for_orphans_when_it_starts():
    import inspect

    from cdi_adapter.webapp import app as web
    from cdi_adapter.webapp import jobs

    # the app builds its runner as it starts, and a runner with a store starts the heartbeat
    # and the orphan sweep - not on the first upload
    assert "get_runner()" in Path(web.__file__).read_text(encoding="utf-8")
    assert "start_background()" in inspect.getsource(jobs.get_runner)


@pytest.mark.xfail(strict=True, reason="the kill-test on a RUNNING system - scripts/"
                   "chaos_upload.py against a deployed web app, stopped and started mid-run - "
                   "has not been run: no instance is up. The counts here are from the real "
                   "runner with a stand-in for the database and the model.")
def test_ac2_counted_on_a_running_system():
    assert (ROOT / "docs/evidence/OUT-S3/chaos_upload.json").is_file()


# ------------------------------------------------------------------ AC3: one corrupt document


def test_ac3_the_others_finish_and_the_corrupt_one_is_reported():
    world = World()
    files = [("a.png", _page(1), None), ("bad.png", CORRUPT, None), ("c.png", _page(3), None),
             ("d.png", _page(4), None), ("e.png", _page(5), None)]  # fmt: skip
    job = _run(world, files)
    by = {d.filename: d for d in job.docs}
    assert [by[n].status for n in ("a.png", "c.png", "d.png", "e.png")] == ["done"] * 4
    assert by["bad.png"].status == "error"
    assert by["bad.png"].error == "ValueError: cannot identify image file"
    assert job.state == "review" and sum(world.results.values()) == 4


def test_ac3_the_report_names_the_file_and_the_reason_where_the_screen_reads_it():
    world = World()
    job = _run(world, [("a.png", _page(1), None), ("bad.png", CORRUPT, None)])
    shown = {d["filename"]: d for d in job.as_json()["documents"]}
    assert shown["bad.png"]["status"] == "error" and "cannot identify" in shown["bad.png"]["error"]
    assert shown["bad.png"]["document_id"] is None and shown["a.png"]["status"] == "done"
    from cdi_adapter import result

    assert "error" in result._NOT_READ  # the upload's one result marks that page held, with why


def test_ac3_a_page_that_fails_while_being_read_does_not_stop_the_rest():
    world = World()
    real = world.extract

    def extract(doc: str, **k: Any) -> Any:
        if doc == "doc-002":
            raise RuntimeError("the model returned nothing")
        return real(doc, **k)

    stages = world.stages()
    stages.extract = extract
    runner = JobRunner(stages, max_workers=1)
    job = runner.wait(runner.create([(f"p{n}.png", _page(n), None) for n in range(4)]).id, 30)
    statuses = sorted(d.status for d in job.docs)
    assert statuses == ["done", "done", "done", "error"] and job.state == "review"
    assert next(d.error for d in job.docs if d.status == "error").startswith("RuntimeError")


def test_ac3_a_job_that_keeps_killing_its_runner_is_given_up_and_set_aside():
    from cdi_adapter.config import get_settings

    world, store = World(), MemStore()
    dead: list[tuple[str, str]] = []
    store.dead_letter = lambda job, reason, actor="runner": dead.append((job.id, reason))  # type: ignore[attr-defined]
    job = _stored(store, [("poison.png", _page(1), None)], owner="dead",
                  lease_until=time.monotonic() - 1)  # fmt: skip
    store.rows[job.id]["attempts"] = get_settings().job_max_attempts
    runner = JobRunner(world.stages(), max_workers=2, store=store, owner="web-2")
    assert runner.run_stored(job.id) == "error"
    assert dead and dead[0][0] == job.id and dead[0][1].startswith("gave up after")
    assert store.rows[job.id]["finished"] and world.read == {}, "it is not retried for ever"


# ------------------------------------------------------------------ the chaos script


class FakeSite:
    """The upload API as ``chaos_upload.Site`` sees it, on the real acceptance rule and the
    real runner; ``stop`` cuts the running jobs off, ``start`` brings another instance up."""

    def __init__(self) -> None:
        self.world, self.store, self.keys = World(), MemStore(), {}
        self.runner = JobRunner(self.world.stages(), max_workers=4, store=self.store, owner="w1")
        self.is_up, self.signed_in = True, 0

    def sign_in(self) -> None:
        self.signed_in += 1

    def send(self, name: str, raw: bytes) -> str:
        key = jobstore.idempotency_key([(name, raw, "image/png")], actor="desk")
        if key not in self.keys:
            self.keys[key] = _stored(self.store, [(name, raw, "image/png")]).id
            if self.is_up:
                self.runner.submit_stored(self.keys[key])
        return self.keys[key]

    def job(self, job_id: str) -> dict[str, Any]:
        if not self.is_up:
            raise ConnectionError("connection refused")
        s = self.store.state(job_id)
        return {"state": s["state"], "documents": s["docs"]}

    def has_result(self, document_id: str) -> bool:
        return self.world.results.get(document_id) == 1

    def up(self) -> bool:
        return self.is_up

    def stop(self) -> None:
        self.is_up = False
        with self.store.lock:  # every lease of the stopped instance runs out
            for row in self.store.rows.values():
                if not row["finished"]:
                    row["lease_until"] = time.monotonic() - 1

    def start(self) -> None:
        self.runner = JobRunner(self.world.stages(), max_workers=4, store=self.store, owner="w2")
        self.is_up = True
        self.runner.sweep()


def test_chaos_twenty_files_sent_twice_stopped_mid_run_and_counted():
    site = FakeSite()
    files = chaos.files_for(20, 1, seed=7)
    out = chaos.rehearse(site, files, corrupt=1, stop=site.stop, start=site.start, wait_s=60,
                         poll_s=0.05, say=lambda *_: None)  # fmt: skip
    assert out["passed"], out["checks"]
    assert out["counts"] == {
        "files_sent": 20, "sends": 40, "jobs": 20, "jobs_unfinished": 0, "documents_read": 19,
        "documents_distinct": 19, "results": 19, "reported_unreadable": 1,
        "unreadable_without_a_reason": 0}  # fmt: skip
    assert out["stopped"] and site.signed_in == 2
    assert sum(site.world.results.values()) == 19


def test_chaos_the_pages_are_made_up_and_the_same_on_every_send():
    a, b = chaos.files_for(4, 1, seed=3), chaos.files_for(4, 1, seed=3)
    assert a == b and len({raw for _, raw in a}) == 4
    assert a[-1][0].endswith("_corrupt.png") and a[0][1].startswith(b"\x89PNG")
    assert all(last in ("Testwala", "Samplekar", "Dummyani", "Mockerjee", "Fakenath")
               for last in chaos.LAST)  # fmt: skip


@pytest.mark.parametrize(("case", "failing"), [
    ("second send made new jobs", "a repeated send is answered with the first job"),
    ("a job never finished", "no job was lost: all reached an end"),
    ("one document twice", "no document is doubled"),
    ("a result is missing", "one result per readable file"),
    ("the unreadable file is not reported", "each unreadable file is reported, with its reason"),
])  # fmt: skip
def test_chaos_the_verdict_fails_when_a_count_is_wrong(case, failing):
    first = ["j1", "j2", "j3"]
    second = list(first)
    bad = {"status": "error", "document_id": None, "error": "ValueError: bad file"}
    jobs = {"j1": {"state": "review", "documents": [{"status": "done", "document_id": "d1"}]},
            "j2": {"state": "review", "documents": [{"status": "done", "document_id": "d2"}]},
            "j3": {"state": "review", "documents": [bad]}}  # fmt: skip
    results = {"d1": True, "d2": True}
    assert chaos.verdict(first, second, jobs, results, corrupt=1)["passed"]
    if case == "second send made new jobs":
        second = ["j1", "j9", "j3"]
    elif case == "a job never finished":
        jobs["j2"]["state"] = "running"
    elif case == "one document twice":
        jobs["j2"]["documents"][0]["document_id"] = "d1"
    elif case == "a result is missing":
        results["d2"] = False
    else:
        jobs["j3"]["documents"][0]["error"] = None
    out = chaos.verdict(first, second, jobs, results, corrupt=1)
    assert not out["passed"] and out["checks"][failing] is False
