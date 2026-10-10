"""LS-S10 acceptance: prove the listener on a real drive and under load.

This story is a set of runs, not a feature: its proof is the evidence those runs leave
(``docs/evidence/LS-S10/``). What can be tested here is the instrument - the soak harness that
drops the files and then judges where each one ended. The tests below feed it folders and
tables with every kind of fault in them (a missing file, one in two places, one read twice,
one changed) and check that it sees each. A harness that cannot see a lost file proves nothing.

The runs themselves that need the owner - the OneDrive folder through Microsoft's API (an Entra
client id and a person to sign in) and Google Drive (a service account) - are expected-fails.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from cdi_adapter.config import Settings
from cdi_adapter.listener import connectors, soak

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs" / "evidence" / "LS-S10"
NAMES = {"inbox": "", "processing": "processing", "success": "success", "error": "error",
         "quarantine": "quarantine", "rescan": "rescan"}  # fmt: skip


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _drops(n: int) -> list[soak.Drop]:
    return [soak.Drop(f"soak-{i:04d}.png", _sha(f"file {i}".encode()), 1000.0 + i)
            for i in range(n)]  # fmt: skip


def _tree(dropped: list[soak.Drop], where: dict[int, str | None] | None = None) -> dict[str, Any]:
    """Every file in ``success`` unless ``where`` puts it elsewhere (``None`` = nowhere)."""
    tree: dict[str, dict[str, str]] = {p: {} for p in soak.PLACES}
    for i, d in enumerate(dropped):
        place = (where or {}).get(i, "success")
        if place:
            tree[place][d.name] = d.sha256
    return tree


def _rows(dropped: list[soak.Drop], states: dict[int, str] | None = None) -> list[dict[str, Any]]:
    return [{"name": d.name, "original_name": d.name,
             "state": (states or {}).get(i, "succeeded"), "document_id": f"doc-{i}",
             "history": [{"event": "succeeded", "seconds_from_drop": 30.0 + i}]}
            for i, d in enumerate(dropped)]  # fmt: skip


def _jobs(dropped: list[soak.Drop], twice: tuple[int, ...] = ()) -> list[dict[str, Any]]:
    jobs = [{"id": f"job-{i}", "files": [{"filename": d.name}]} for i, d in enumerate(dropped)]
    return jobs + [{"id": f"again-{i}", "files": [{"filename": dropped[i].name}]} for i in twice]


def _judge(n: int = 6, **kw: Any) -> dict[str, Any]:
    d = _drops(n)
    return soak.reconcile(d, _tree(d, kw.get("where")), _rows(d, kw.get("states")),
                          _jobs(d, kw.get("twice", ())))  # fmt: skip


# ------------------------------------------------------------------ AC3
# GIVEN 200 files are dropped over 2 hours WHEN the run ends THEN success + error + quarantine
# equals 200, with none missing and none duplicated


def test_ac3_two_hundred_files_all_accounted_for_pass() -> None:
    rep = _judge(200, where={7: "error", 8: "quarantine"},
                 states={7: "failed", 8: "quarantined"})  # fmt: skip
    assert rep["dropped"] == 200 and rep["final"] == 200 and rep["adds_up"]
    assert (rep["counts"]["success"], rep["counts"]["error"], rep["counts"]["quarantine"]) == (
        198, 1, 1)  # fmt: skip
    assert rep["none_missing"] and rep["none_duplicated"] and soak.passed(rep)


def test_ac3_a_missing_file_is_seen() -> None:
    rep = _judge(where={3: None})
    assert rep["counts"]["missing"] == 1 and not rep["none_missing"] and not rep["adds_up"]
    assert rep["problems"]["soak-0003.png"] == ["missing: in no folder"] and not soak.passed(rep)


def test_ac3_a_file_in_two_places_is_seen() -> None:
    d = _drops(6)
    tree = _tree(d)
    tree["error"][d[2].name] = d[2].sha256  # in success and in error
    rep = soak.reconcile(d, tree, _rows(d), _jobs(d))
    assert rep["counts"]["duplicated"] == 1 and not rep["none_duplicated"]
    assert rep["problems"][d[2].name] == ["duplicated: in success and error"]
    assert not soak.passed(rep)


def test_ac3_a_file_still_waiting_does_not_count_as_finished() -> None:
    rep = _judge(where={0: "inbox", 1: "processing"})
    assert rep["final"] == 4 and not rep["adds_up"] and not soak.passed(rep)
    assert rep["problems"]["soak-0001.png"] == ["not finished: still in processing"]


def test_ac3_a_file_held_for_a_rescan_is_counted_apart() -> None:
    """ISSUE, recorded as a fact: the story names three places a file may end. The listener has
    a fourth, ``rescan`` (a picture too poor to read). Such a file is not lost, but it makes
    the story's sum come up short, and the run is reported as not passing."""
    rep = _judge(where={5: "rescan"})
    assert rep["counts"]["rescan"] == 1 and rep["none_missing"] and not rep["adds_up"]


def test_ac3_a_file_nobody_dropped_is_seen() -> None:
    d = _drops(3)
    tree = _tree(d)
    tree["success"]["stranger.png"] = "0" * 64
    rep = soak.reconcile(d, tree, _rows(d), _jobs(d))
    assert rep["files_nobody_dropped"] == ["stranger.png"] and not soak.passed(rep)


def test_ac3_a_file_changed_on_the_way_is_seen() -> None:
    d = _drops(3)
    tree = _tree(d)
    tree["success"][d[1].name] = "f" * 64
    rep = soak.reconcile(d, tree, _rows(d), _jobs(d))
    assert rep["changed"] == [d[1].name]
    assert rep["problems"][d[1].name] == ["changed: not the bytes that were dropped"]


def test_ac3_a_file_the_listener_renamed_is_still_found() -> None:
    d = _drops(2)
    tree = _tree(d, where={1: None})
    tree["success"]["soak-0001.1.png"] = d[1].sha256  # kept under another name (LS-S6)
    rows = _rows(d)
    rows[1]["name"] = "soak-0001.1.png"
    rep = soak.reconcile(d, tree, rows, _jobs(d))
    assert soak.passed(rep) and rep["counts"]["success"] == 2


def test_ac3_the_table_must_agree_with_the_folder() -> None:
    rep = _judge(states={4: "processing"})
    assert rep["problems"]["soak-0004.png"] == ["in success but the table says processing"]


def test_ac3_the_drops_are_spread_evenly_over_the_window() -> None:
    at = soak.offsets(200, 7200)
    assert len(at) == 200 and at[0] == 0 and at[1] == 36.0 and at[-1] == 7164.0
    assert soak.offsets(1, 60) == [0.0] and soak.offsets(0, 60) == []


def test_ac3_a_dropped_file_never_appears_half_written(tmp_path) -> None:
    drop = soak.place(tmp_path, "rx.png", b"a fake prescription")
    assert (tmp_path / "rx.png").read_bytes() == b"a fake prescription"
    assert drop.sha256 == _sha(b"a fake prescription")
    assert [p.name for p in tmp_path.iterdir()] == ["rx.png"]  # the part file is gone
    source = Path(soak.__file__).read_text(encoding="utf-8")
    assert "os.replace(part, inbox / name)" in source  # written aside, then renamed
    # and the name it is written under is one the listener does not pick up
    patterns = Settings(_env_file=None).listener_patterns.split(",")
    assert not any(Path(".rx.png.part").match(p) for p in patterns)


def test_ac3_each_drop_is_written_down_as_it_happens(tmp_path) -> None:
    for i in range(3):
        soak.note(tmp_path, soak.Drop(f"f{i}.png", _sha(bytes([i])), 1.0 + i))
    assert [d.name for d in soak.drops(tmp_path)] == ["f0.png", "f1.png", "f2.png"]
    assert soak.drops(tmp_path / "nothing") == []


def test_ac3_the_folder_is_read_as_it_is(tmp_path) -> None:
    (tmp_path / "success").mkdir()
    (tmp_path / "success" / "a.png").write_bytes(b"a")
    (tmp_path / "b.png").write_bytes(b"b")
    (tmp_path / ".hidden.part").write_bytes(b"x")
    tree = soak.folders(tmp_path, NAMES)
    assert tree["success"] == {"a.png": _sha(b"a")} and tree["inbox"] == {"b.png": _sha(b"b")}
    assert tree["error"] == {} and tree["quarantine"] == {}


# ------------------------------------------------------------------ AC4
# GIVEN the listener is killed in the middle of a batch WHEN it restarts THEN no file is lost
# and none is read twice


def test_ac4_a_file_read_twice_is_seen() -> None:
    rep = _judge(twice=(2,))
    assert rep["read_more_than_once"] == ["soak-0002.png"] and not soak.passed(rep)
    assert rep["problems"]["soak-0002.png"] == ["read 2 times"]


def test_ac4_a_run_that_failed_and_was_retried_is_not_a_second_reading() -> None:
    d = _drops(2)
    jobs = _jobs(d)
    jobs[1]["docs"] = [{"filename": d[1].name, "status": "error"}]  # the first run failed
    jobs.append({"id": "retry", "files": [{"filename": d[1].name}],
                 "docs": [{"filename": d[1].name, "status": "done"}]})  # fmt: skip
    rep = soak.reconcile(d, _tree(d), _rows(d), jobs)
    assert soak.passed(rep) and rep["read_more_than_once"] == []
    assert [(f["runs"], f["jobs"]) for f in rep["files"]] == [(1, 1), (2, 1)]


def test_ac4_two_files_that_became_one_document_are_seen() -> None:
    d = _drops(3)
    rows = _rows(d)
    rows[2]["document_id"] = rows[1]["document_id"]
    assert soak.reconcile(d, _tree(d), rows, _jobs(d))["documents_shared"] == ["doc-1"]


def test_ac4_a_clean_restart_passes() -> None:
    assert soak.passed(_judge(12))


def test_found_under_load_a_job_handed_to_the_gpu_pool_can_be_claimed_there() -> None:
    """The first three-file run on the instance left two files in ``processing`` for good. The
    CPU pool gave each job's lease up and queued it for the GPU pool; the GPU worker was busy
    with the first; the CPU worker's heartbeat then renewed the leases it had given up - they
    still carried its name - and the GPU worker was refused both jobs. Giving a lease up now
    clears the owner, so no heartbeat can renew it. (On a database:
    ``tests/test_apptier_integration.py``.)"""
    from cdi_adapter.webapp import jobstore

    source = Path(jobstore.__file__).read_text(encoding="utf-8")
    release = source.split("def release(")[1].split("def ")[0]
    assert "lease_owner = NULL" in release and "lease_until = now() - interval" in release
    heartbeat = source.split("def heartbeat(")[1].split("def ")[0]
    assert "WHERE lease_owner = :o AND finished_at IS NULL" in heartbeat  # by owner: hence the fix


def test_found_under_load_the_folder_is_left_alone_while_the_model_server_is_away(
    monkeypatch,
) -> None:
    """With the model server stopped for five minutes, all six files in flight failed four
    times and ended in quarantine (none lost - but each then needed a person). The listener now
    asks whether the model server answers before it takes anything from the folder."""
    from cdi_adapter.listener import service as S

    def knock(url: str, port: int, timeout: float = 3.0) -> str | None:
        return (
            "nothing answers at 127.0.0.1:8077 - ConnectionRefusedError" if port == 8077 else None
        )

    monkeypatch.setattr(S, "_port_closed", knock)
    away = S.pipeline_reader(Settings(_env_file=None, mlserve_backend="hf")).ready()
    assert away == ("the model server cannot be reached (nothing answers at 127.0.0.1:8077 - "
                    "ConnectionRefusedError)")  # fmt: skip
    # one of two replicas answering is enough
    monkeypatch.setattr(S, "_port_closed", lambda url, port, timeout=3.0: (
        "down" if "8077" in url else "stop here" if port == 5432 else None))  # fmt: skip
    two = Settings(_env_file=None, mlserve_backend="hf",
                   mlserve_url="http://127.0.0.1:8077,http://127.0.0.1:8078")  # fmt: skip
    assert S.pipeline_reader(two).ready() == "the database cannot be reached (stop here)"


def test_found_under_test_a_look_that_breaks_half_way_does_not_end_the_listener(tmp_path) -> None:
    """The network was cut while a file was being moved to ``processing``: the write to the
    listener's table raised, nothing caught it, and the listener process ended - with nothing
    on the pod to start it again. A broken look is now given up and the next one carries on;
    the file it had taken is picked up where it was left."""
    import threading

    from cdi_adapter.listener import ledger as L
    from cdi_adapter.listener import service as S
    from tests.helpers import blank_pdf

    class Flaky(L.MemoryLedger):
        """The database goes away once, in the middle of the batch."""

        broke = 0

        def note(self, entry: Any, event: Any) -> None:
            if not Flaky.broke and isinstance(event, dict) and event.get("event") == "moved":
                Flaky.broke += 1
                raise ConnectionError("server closed the connection unexpectedly")
            super().note(entry, event)

    root = tmp_path / "watched"
    root.mkdir()
    settings = Settings(_env_file=None, listener_connector="local",
                        listener_local_root=str(root), listener_inbox="")  # fmt: skip
    connector = connectors.get_connector(settings)
    connector.ensure_folders()
    (root / "rx.pdf").write_bytes(blank_pdf(1, size=(200, 280)))
    listener = S.Listener(connector, S.Reader(read=lambda n, r, m: S.Outcome(True, job_id="j")),
                          ledger=Flaky(), stable_polls=1, worker="pod")  # fmt: skip
    thread = threading.Thread(target=lambda: listener.run(interval=0.05), daemon=True)
    thread.start()
    for _ in range(200):
        if (root / "success" / "rx.pdf").exists():
            break
        thread.join(0.05)
    alive = thread.is_alive()
    listener.stop.set()
    thread.join(5)
    assert Flaky.broke == 1  # the look did break
    assert alive  # and the listener went on looking
    assert (root / "success" / "rx.pdf").exists()  # the file it had taken was finished
    assert not list((root / "processing").iterdir())
    source = Path(S.__file__).read_text(encoding="utf-8")
    assert "except SignInRequired:\n            raise" in source  # a lost sign-in still stops it


class _Jobs:
    """Stands where the job store is: one job per key, as the real one (E9-S3)."""

    def __init__(self) -> None:
        self.by_key: dict[str, Any] = {}
        self.submitted: list[str] = []
        self.fail = True  # the model server is away

    def submit(self, files: Any, *, actor: str, given_key: str | None = None) -> Any:
        from types import SimpleNamespace

        key = given_key or "body:" + _sha(files[0][1])
        self.submitted.append(key)
        if key not in self.by_key:
            doc = SimpleNamespace(status="running", error=None, document_id=None, doc_type="rx")
            self.by_key[key] = SimpleNamespace(id=f"job{len(self.by_key)}", state="running",
                                               docs=[doc])  # fmt: skip
        return self.by_key[key]

    def wait(self, job_id: str, timeout: float) -> Any:
        job = next(j for j in self.by_key.values() if j.id == job_id)
        if job.state == "running":  # it runs now, once
            job.state = "error" if self.fail else "review"
            job.docs[0].status = "error" if self.fail else "done"
            job.docs[0].error = "ConnectError" if self.fail else None
            job.docs[0].document_id = None if self.fail else "doc-1"
        return job


def test_found_under_load_a_retry_is_a_new_run_not_the_old_failure_again(monkeypatch) -> None:
    """With the model server stopped, each file failed - and then every retry came back with
    the same job number and the same error, after the server was up again: the same bytes were
    answered with the job that had already failed, which never runs again. Six files went to
    quarantine without being read a second time."""
    from cdi_adapter.listener import service as S
    from cdi_adapter.webapp import jobstore

    jobs = _Jobs()
    monkeypatch.setattr(jobstore, "submit", jobs.submit)
    monkeypatch.setattr(jobstore, "wait", jobs.wait)
    read = S.pipeline_reader(Settings(_env_file=None)).read
    first = read("rx.png", b"the same bytes", "image/png")
    assert (first.ok, first.kind, first.job_id) == (False, "failed", "job0")
    again = read("rx.png", b"the same bytes", "image/png")  # the retry, server still away
    assert again.job_id == "job1"  # a new run, not job0 handed back
    jobs.fail = False  # the model server is back
    third = read("rx.png", b"the same bytes", "image/png")
    assert (third.ok, third.job_id, third.document_id) == (True, "job2", "doc-1")
    assert jobs.submitted[-1] == "listener-retry:job1"  # keyed on the run it follows
    # a file that went through is still answered with its one job: a restart reads nothing twice
    after = read("rx.png", b"the same bytes", "image/png")
    assert after.job_id == "job2" and len(jobs.by_key) == 3


def test_found_under_load_only_a_finished_failure_is_passed_over() -> None:
    from types import SimpleNamespace as N

    from cdi_adapter.listener import service as S

    def job(state: str, status: str, **doc: Any) -> Any:
        return N(id="j", state=state, docs=[N(status=status, error=None, document_id=None,
                                              doc_type="x", quality=None, **doc)])  # fmt: skip

    assert S.spent(job("error", "error")) is True
    assert S.spent(job("running", "running")) is False  # after a restart: still being read
    assert S.spent(job("review", "done")) is False  # went through
    assert S.spent(job("review", "quality_hold")) is False  # a rescan: never retried
    assert S.spent(job("review", "skipped")) is False


# ------------------------------------------------------------------ FR4: the times


def test_fr4_seconds_from_drop_to_saved_are_reported() -> None:
    t = _judge(11)["seconds_from_drop"]
    assert t == {"files": 11, "median": 35.0, "p95": 40.0, "min": 30.0, "max": 40.0}
    assert soak.timing([]) == {"files": 0}


def test_fr4_only_files_that_succeeded_are_timed() -> None:
    rep = _judge(4, where={0: "error"}, states={0: "failed"})
    assert rep["seconds_from_drop"]["files"] == 3


def test_the_report_a_person_reads_says_pass_or_fail_and_why() -> None:
    good = soak.render(_judge(6), title="Soak", notes=["folder: /x"])
    assert "**PASS** - 6 files dropped; success + error + quarantine = 6." in good
    assert "| none missing | yes |" in good and "- folder: /x" in good
    assert "median 32.5" in good
    bad = soak.render(_judge(where={3: None}, twice=(1,)), title="Soak")
    assert "**FAIL**" in bad and "| none missing | NO |" in bad
    assert "| soak-0003.png | missing: in no folder |" in bad
    assert "| none read more than once | NO |" in bad


# ------------------------------------------------------------------ the fake prescriptions


def _script() -> Any:
    path = ROOT / "scripts" / "listener_soak.py"
    spec = importlib.util.spec_from_file_location("listener_soak", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["listener_soak"] = module
    spec.loader.exec_module(module)
    return module


def test_every_fake_prescription_is_different_and_says_it_is_fake() -> None:
    script = _script()
    made = [script.fake_prescription("t", n, 7) for n in range(4)]
    assert len({name for name, _ in made}) == 4 and len({_sha(data) for _, data in made}) == 4
    assert all(data.startswith(b"\x89PNG") for _, data in made)
    assert script.fake_prescription("t", 2, 7) == made[2]  # the same again for the same seed
    source = (ROOT / "scripts" / "listener_soak.py").read_text(encoding="utf-8")
    assert "FAKE - SOAK TEST" in source and "from make_sample_docs import scanify" in source


def test_the_harness_drops_and_writes_them_down(tmp_path, monkeypatch) -> None:
    script = _script()
    monkeypatch.setenv("CDI_LISTENER_LOCAL_ROOT", str(tmp_path / "watched"))
    monkeypatch.setenv("CDI_LISTENER_INBOX", "")
    from cdi_adapter.config import get_settings

    get_settings.cache_clear()
    try:
        assert script.main(["drop", "--count", "3", "--over", "0", "--out",
                            str(tmp_path / "results"), "--run", "t"]) == 0  # fmt: skip
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
    names = sorted(p.name for p in (tmp_path / "watched").iterdir())
    assert names == ["soak-t-0000.png", "soak-t-0001.png", "soak-t-0002.png"]
    assert [d.name for d in soak.drops(tmp_path / "results")] == names


# ------------------------------------------------------------------ AC1 / AC2: the real drives


def test_ac2_the_drive_is_chosen_by_a_setting_alone() -> None:
    """What "no code change" rests on: one setting names the drive, and each drive has its
    connector."""
    assert Settings(_env_file=None).listener_connector == "local"
    source = Path(connectors.__file__).read_text(encoding="utf-8")
    for drive in ("local", "onedrive", "sharepoint", "gdrive"):
        assert f'"{drive}"' in source, drive


@pytest.mark.xfail(strict=True, reason="LS-S10 AC1: the listener has run on the owner's OneDrive "
                   "folder only as a folder synced to a PC (the local connector). Through "
                   "Microsoft's own API it needs the owner's Entra client id and a person to "
                   "sign in (LS-S2), which have not been given.")  # fmt: skip
def test_ac1_three_files_on_the_real_onedrive_through_microsofts_api() -> None:
    assert (EVIDENCE / "onedrive-graph.md").exists()


@pytest.mark.xfail(strict=True, reason="LS-S10 AC2: no Google Drive folder or service account "
                   "has been given, so the switch by settings alone has not been run.")  # fmt: skip
def test_ac2_three_files_on_google_drive_by_settings_alone() -> None:
    assert (EVIDENCE / "google-drive.md").exists()


def test_the_evidence_folder_holds_only_fake_prescriptions() -> None:
    """Whatever evidence is kept names only soak files - never a real document."""
    for path in EVIDENCE.glob("*.json") if EVIDENCE.exists() else []:
        for item in json.loads(path.read_text(encoding="utf-8")).get("files", []):
            assert item["name"].startswith("soak-"), (path.name, item["name"])
