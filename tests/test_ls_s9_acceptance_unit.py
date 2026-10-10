"""LS-S9 acceptance: keep the listener running, restart-safe and visible.

One group of tests per acceptance criterion. The watched folder is a real folder (``tmp_path``)
through the real ``local`` connector, with the real listener loop on it. The reader (the
pipeline) is a stand-in the test can make slow, or stop dead. The ledger is the in-memory one,
which follows the same rules as the tables; the clock is one the test moves, so a 15-minute
lease takes no time.

"The pod is restarted" is a listener that is thrown away mid-batch and a new one made on the
same folder and the same ledger - the folder and the database being the two things that
survive a restart. A real pod has not been restarted for this.
"""

from __future__ import annotations

import json
import signal
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
from cdi_adapter.listener import recovery as R
from cdi_adapter.listener import service as S
from cdi_adapter.webapp import alerts, auth
from cdi_adapter.webapp.listener_page import listener_page
from cdi_adapter.webapp.theme import ALERTS_SCRIPT, header, page
from tests.helpers import blank_pdf

ROOT = Path(__file__).resolve().parents[1]
START = datetime(2026, 10, 9, 9, 0, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.at = START

    def __call__(self) -> datetime:
        return self.at

    def pass_(self, seconds: float) -> None:
        self.at += timedelta(seconds=seconds)


class Died(BaseException):
    """The pod going away in the middle of a file. Nothing catches it."""


@pytest.fixture
def folder(tmp_path) -> Path:
    root = tmp_path / "File listener"
    root.mkdir()
    return root


def _connector(folder: Path) -> Any:
    settings = Settings(listener_connector="local", listener_local_root=str(folder),
                        listener_inbox="")  # fmt: skip
    connector = C.get_connector(settings)
    connector.ensure_folders()
    return connector


def _ok(name: str, raw: bytes, mime: str) -> S.Outcome:
    return S.Outcome(True, job_id="job-" + name)


def _listener(folder: Path, ledger: L.MemoryLedger, clock: Clock, read: Any = _ok,
              **more: Any) -> S.Listener:  # fmt: skip
    return S.Listener(_connector(folder), S.Reader(read=read), ledger=ledger, now=clock, **more)


def _names(folder: Path, sub: str = "") -> list[str]:
    return sorted(p.name for p in (folder / sub).iterdir() if p.is_file())


def _drop(folder: Path, *names: str) -> None:
    for n, name in enumerate(names):
        (folder / name).write_bytes(blank_pdf(1, size=(200 + n, 280)))


def _everywhere(folder: Path) -> list[str]:
    return sorted(_names(folder) + _names(folder, "processing") + _names(folder, "success")
                  + _names(folder, "error") + _names(folder, "quarantine"))  # fmt: skip


# ------------------------------------------------------------------ AC1
# GIVEN the pod is restarted during a batch WHEN it comes back
# THEN no file is lost and each unfinished file is read again after its lease expires


def _pod_dies_mid_batch(folder: Path, ledger: L.MemoryLedger, clock: Clock) -> None:
    """Three files; the pod dies while the batch is being read."""

    def read(name: str, raw: bytes, mime: str) -> S.Outcome:
        raise Died

    _drop(folder, "a.pdf", "b.pdf", "c.pdf")
    first = _listener(folder, ledger, clock, read, worker="pod-1")
    first.poll()
    with pytest.raises(Died):
        first.poll()


def test_ac1_a_restart_during_a_batch_loses_no_file(folder) -> None:
    clock, ledger = Clock(), L.MemoryLedger()
    ledger._now = clock
    _pod_dies_mid_batch(folder, ledger, clock)
    assert _everywhere(folder) == ["a.pdf", "b.pdf", "c.pdf"]  # all three still exist
    assert _names(folder, "processing") == ["a.pdf", "b.pdf", "c.pdf"]  # where they were left
    assert _names(folder, "log") == []  # and none is written up as a failure


def test_ac1_a_new_pod_reads_each_unfinished_file_once_its_lease_has_expired(folder) -> None:
    clock, ledger = Clock(), L.MemoryLedger()
    ledger._now = clock
    _pod_dies_mid_batch(folder, ledger, clock)
    read: list[str] = []
    new = _listener(folder, ledger, clock, lambda n, r, m: read.append(n) or S.Outcome(True),
                    worker="pod-2", lease_seconds=900)  # fmt: skip
    assert new.poll()["held"] == 3 and read == []  # the dead pod's leases still hold them
    clock.pass_(600)
    assert new.poll()["held"] == 3 and read == []  # ten minutes: not yet
    clock.pass_(301)  # past 900 s: the leases have run out
    report = new.poll()
    assert report["resumed"] == 3 and sorted(read) == ["a.pdf", "b.pdf", "c.pdf"]
    assert _names(folder, "success") == ["a.pdf", "b.pdf", "c.pdf"]
    assert _names(folder, "processing") == []


def test_ac1_it_is_picked_up_on_a_later_look_not_only_the_first_one_after_the_restart(
    folder,
) -> None:
    """Before LS-S9 a file left in ``processing`` was looked at once, on the first pass of a
    run - so a new pod that started before the lease ran out never came back to it."""
    clock, ledger = Clock(), L.MemoryLedger()
    ledger._now = clock
    _pod_dies_mid_batch(folder, ledger, clock)
    new = _listener(folder, ledger, clock, worker="pod-2")
    for _ in range(5):  # five looks while the leases still hold
        clock.pass_(10)
        assert new.poll()["resumed"] == 0
    clock.pass_(900)
    assert new.poll()["resumed"] == 3  # the sixth look, after the lease: taken up


def test_ac1_the_same_pod_coming_back_takes_its_own_files_at_once(folder) -> None:
    clock, ledger = Clock(), L.MemoryLedger()
    ledger._now = clock
    _pod_dies_mid_batch(folder, ledger, clock)
    back = _listener(folder, ledger, clock, worker="pod-1")  # the same name: the leases are its own
    assert back.poll()["resumed"] == 3 and _names(folder, "success") == ["a.pdf", "b.pdf", "c.pdf"]


def test_ac1_a_file_another_live_worker_is_reading_is_left_alone(folder) -> None:
    clock, ledger = Clock(), L.MemoryLedger()
    ledger._now = clock
    _pod_dies_mid_batch(folder, ledger, clock)
    other = _listener(folder, ledger, clock, worker="pod-2")
    for _ in range(3):
        clock.pass_(60)
        report = other.poll()
        assert report["held"] == 3 and report["resumed"] == 0
    assert _names(folder, "processing") == ["a.pdf", "b.pdf", "c.pdf"]


def test_ac1_files_dropped_while_the_pod_was_down_are_read_when_it_is_back(folder) -> None:
    clock, ledger = Clock(), L.MemoryLedger()
    ledger._now = clock
    _pod_dies_mid_batch(folder, ledger, clock)
    _drop(folder, "while-down.pdf")
    back = _listener(folder, ledger, clock, worker="pod-1")
    back.poll()
    back.poll()
    assert _names(folder, "success") == ["a.pdf", "b.pdf", "c.pdf", "while-down.pdf"]


def test_ac1_the_state_a_restart_needs_is_in_the_database_and_the_folder() -> None:
    """Nothing a restart needs is kept in the process: the lease, the run count, the history
    and the heartbeat are rows; the files are in the folder."""
    fields = {f.name for f in __import__("dataclasses").fields(S.Listener)}
    assert "_resumed" not in fields  # the "first pass" flag is gone: every pass looks
    versions = ROOT / "db" / "alembic" / "versions"
    table = (versions / "0029_listener_file.py").read_text(encoding="utf-8")
    assert "lease_owner" in table and "lease_until" in table
    assert (versions / "0034_listener_heartbeat.py").is_file()
    assert Settings.model_fields["listener_lease_seconds"].default == 900.0


def test_ac1_the_listener_and_the_retry_agent_start_by_themselves_with_the_pod() -> None:
    start_all = (ROOT / "infra" / "runpod" / "start_all.sh").read_text(encoding="utf-8")
    assert 'if [ "${CDI_START_LISTENER:-0}" = 1 ]; then' in start_all
    on, off = start_all.split('if [ "${CDI_START_LISTENER:-0}" = 1 ]; then')[1].split("else")[:2]
    assert 'start_listener.sh"' in on and "recover" not in on.split("||")[0]  # both
    assert 'start_listener.sh" recover' in off  # the retry agent, every time
    script = (ROOT / "infra" / "runpod" / "start_listener.sh").read_text(encoding="utf-8")
    assert "start listener listener.log run" in script  # /workspace/logs/listener.log
    assert "start listener-recover listener-recover.log recover" in script
    assert "already running" in script  # safe to call again


def test_ac1_a_lost_database_never_leaves_the_listener_waiting_for_ever() -> None:
    """Seen on 2026-10-09: the link to the database dropped, and the listener sat in one
    connection attempt until it was killed. Every attempt now gives up after a few seconds, so
    the next look happens and, once the database is back, succeeds."""
    from sqlalchemy.engine import make_url

    from cdi_adapter import db

    plain = make_url("postgresql+psycopg://cdi@127.0.0.1:5432/cdi")
    assert db.connect_args(plain) == {"connect_timeout": db.CONNECT_TIMEOUT_S}
    assert 0 < db.CONNECT_TIMEOUT_S <= 30
    given = make_url("postgresql+psycopg://cdi@127.0.0.1:5432/cdi?connect_timeout=3")
    assert db.connect_args(given) == {}  # a timeout in the URL is left as it is
    source = (ROOT / "src" / "cdi_adapter" / "db.py").read_text(encoding="utf-8")
    assert "connect_args=connect_args(url)" in source


# ------------------------------------------------------------------ AC2
# GIVEN the listener gets a stop signal during a batch WHEN it stops
# THEN the running batch finishes first and then it exits


def test_ac2_a_stop_during_a_batch_lets_the_batch_finish_then_the_loop_ends(folder) -> None:
    clock, ledger = Clock(), L.MemoryLedger()
    reading, finished = threading.Event(), []

    def slow(name: str, raw: bytes, mime: str) -> S.Outcome:
        reading.set()
        time.sleep(0.3)  # the stop arrives while this is going on
        finished.append(name)
        return S.Outcome(True, job_id=name)

    _drop(folder, "a.pdf", "b.pdf", "c.pdf")
    listener = _listener(folder, ledger, clock, slow)
    done: dict[str, Any] = {}
    thread = threading.Thread(target=lambda: done.update(report=listener.run(interval=0.01)))
    thread.start()
    assert reading.wait(5)  # the batch is being read ...
    listener.stop.set()  # ... and the stop signal comes
    thread.join(10)
    assert not thread.is_alive()  # it exited
    assert sorted(finished) == ["a.pdf", "b.pdf", "c.pdf"]  # after the whole batch finished
    assert _names(folder, "success") == ["a.pdf", "b.pdf", "c.pdf"]
    assert _names(folder, "processing") == []  # nothing left half-read


def test_ac2_no_new_batch_is_started_after_the_stop(folder) -> None:
    clock, ledger = Clock(), L.MemoryLedger()
    read: list[str] = []
    listener = _listener(folder, ledger, clock)

    def stop_after_the_first(name: str, raw: bytes, mime: str) -> S.Outcome:
        read.append(name)
        listener.stop.set()  # the signal arrives during the first batch
        return S.Outcome(True, job_id=name)

    listener.reader = S.Reader(read=stop_after_the_first)
    _drop(folder, *[f"rx-{n}.pdf" for n in range(6)])  # two batches' worth
    listener.poll()
    report = listener.poll()
    assert len(report["batches"]) == 1 and len(read) == 3  # the running batch, and no other
    assert len(_names(folder)) == 3 and report["batch_waiting"] == 3  # the rest wait, untouched


def test_ac2_the_wait_between_looks_ends_at_once_when_the_stop_comes(folder) -> None:
    listener = _listener(folder, L.MemoryLedger(), Clock())
    thread = threading.Thread(target=lambda: listener.run(interval=3600))  # an hour between looks
    began = time.perf_counter()
    thread.start()
    time.sleep(0.2)
    listener.stop.set()
    thread.join(5)
    assert not thread.is_alive() and time.perf_counter() - began < 5


def test_ac2_a_stop_signal_sets_the_stop_and_does_not_kill_the_process() -> None:
    class Running:
        stop = threading.Event()

    before = signal.getsignal(signal.SIGINT)
    with cli._stops_gently(Running):
        assert signal.getsignal(signal.SIGINT) is not before
        signal.raise_signal(signal.SIGINT)  # Ctrl+C, or the pod's SIGTERM
        assert Running.stop.is_set()  # no KeyboardInterrupt: the loop is asked, not killed
        signal.raise_signal(signal.SIGINT)  # a second one changes nothing
    assert signal.getsignal(signal.SIGINT) is before  # put back afterwards
    source = (ROOT / "src" / "cdi_adapter" / "listener" / "__main__.py").read_text("utf-8")
    assert "for sig in (signal.SIGTERM, signal.SIGINT):" in source


def test_ac2_the_run_command_and_the_retry_agent_both_stop_gently() -> None:
    source = (ROOT / "src" / "cdi_adapter" / "listener" / "__main__.py").read_text("utf-8")
    assert "with _stops_gently(listener):" in source and "with _stops_gently(agent):" in source
    agent = R.RetryAgent(None, L.MemoryLedger())  # type: ignore[arg-type]
    agent.stop.set()
    passes: list[int] = []
    agent.pass_once = lambda: passes.append(1) or {"retried": [], "waiting": [], "missing": []}  # type: ignore[method-assign]
    agent.run(interval=3600)
    assert passes == [1]  # the pass that was running; no other


def test_ac2_the_stop_script_asks_first_and_waits_for_the_batch() -> None:
    script = (ROOT / "infra" / "runpod" / "start_listener.sh").read_text(encoding="utf-8")
    stop = script[script.index("stop() {") : script.index("status() {")]
    assert stop.index("kill -TERM") < stop.index("kill -KILL")  # asked first
    assert 'seq "${CDI_LISTENER_LEASE_SECONDS:-900}"' in stop  # given time to finish its batch


# ------------------------------------------------------------------ AC3
# GIVEN the health view is opened WHEN it is read THEN it shows last good poll time,
# files waiting, error count, quarantine count and oldest waiting age


def _busy_folder(folder: Path, clock: Clock) -> S.Listener:
    """A folder with something in every state: read, failed, quarantined, waiting, ignored."""

    def read(name: str, raw: bytes, mime: str) -> S.Outcome:
        if name.startswith("bad"):
            raise TimeoutError("model server")
        return S.Outcome(True, job_id=name)

    ledger = L.MemoryLedger(now=clock)
    listener = _listener(folder, ledger, clock, read, batch_wait_seconds=60.0, max_attempts=0)
    _drop(folder, "good-1.pdf", "good-2.pdf", "bad-1.pdf")
    (folder / "scan.pdf").write_bytes(b"%PDF-1.7\n" + b"\x00" * 99)  # a data error
    (folder / "letter.docx").write_bytes(b"PK\x03\x04" + b"\x00" * 50)  # ignored
    listener.poll()
    listener.poll()  # four files: a batch of three now ...
    clock.pass_(70)
    listener.poll()  # ... and the fourth after its wait
    _drop(folder, "waiting-1.pdf", "waiting-2.pdf")
    clock.pass_(10)
    listener.poll()  # seen once
    clock.pass_(45)
    listener.poll()  # finished, but fewer than a batch: they wait
    return listener


def test_ac3_the_health_view_shows_the_five_things_the_story_names(folder) -> None:
    clock = Clock()
    listener = _busy_folder(folder, clock)
    clock.pass_(8)  # eight seconds since the last look
    (health,) = listener.ledger.health()
    assert health["last_good_poll_at"] == (clock.at - timedelta(seconds=8)).isoformat()
    assert health["seconds_since_good_poll"] == 8.0 and health["healthy"] is True
    assert health["waiting"] == 2  # files waiting
    assert health["error"] == 1  # scan.pdf: a data error stays in error
    assert health["quarantine"] == 1  # bad-1.pdf: its runs were used up
    assert health["oldest_waiting_seconds"] == 53.0  # first seen 45 s + 8 s ago
    assert _names(folder, "error") == ["scan.pdf"] and _names(folder, "quarantine") == ["bad-1.pdf"]


def test_ac3_it_also_lists_the_files_being_ignored_with_the_reason(folder) -> None:
    clock = Clock()
    (health,) = _busy_folder(folder, clock).ledger.health()
    assert health["ignored"] == [{"file": "letter.docx", "reason": "file type not allowed"}]
    assert health["drive"] == "local" and health["source_id"].startswith("local:")


def test_ac3_the_page_draws_each_of_them() -> None:
    html = listener_page()
    for label in ("Last good poll", "Files waiting", "Oldest waiting", "In error",
                  "In quarantine"):  # fmt: skip
        assert f"tile('{label}'" in html, label
    assert "fetch('/api/listener/health')" in html and "setInterval(load, 15000)" in html
    assert "NOT POLLING" in html and "are ignored" in html
    assert 'href="/listener"' in header() and "Listener" in header()


def test_ac3_the_api_and_the_page_are_for_staff_who_may_see_results() -> None:
    """The view lists file names, and a file name can carry a patient's name."""
    clinical = auth.POLICY[("GET", "/api/documents/{document_id}/result.json")]
    assert auth.POLICY[("GET", "/api/listener/health")] == clinical
    assert auth.POLICY[("GET", "/listener")] == clinical
    web = (ROOT / "src" / "cdi_adapter" / "webapp" / "app.py").read_text(encoding="utf-8")
    assert '@app.get("/api/listener/health")' in web and '@app.get("/listener"' in web
    assert "DbLedger().health(stale_seconds=stale)" in web  # read from the database


def test_ac3_the_health_command_prints_the_same(monkeypatch, capsys, folder) -> None:
    clock = Clock()
    listener = _busy_folder(folder, clock)
    monkeypatch.setattr(
        S, "pipeline_reader", lambda settings=None: S.Reader(read=_ok, ledger=listener.ledger)
    )
    assert cli.main(["health"]) == 0
    out = capsys.readouterr().out
    assert "status         OK" in out and "last good poll 2026-10-09T" in out
    assert "waiting        2 file(s), the oldest for 45 s" in out
    assert "in error       1" in out and "in quarantine  1" in out and "ignored        1" in out
    assert cli.main(["health", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["waiting"] == 2


def test_ac3_a_folder_with_nothing_waiting_shows_no_age(folder) -> None:
    clock, ledger = Clock(), L.MemoryLedger()
    ledger._now = clock
    _listener(folder, ledger, clock).poll()
    (health,) = ledger.health()
    assert health["waiting"] == 0 and health["oldest_waiting_seconds"] is None
    assert health["error"] == 0 and health["quarantine"] == 0 and health["ignored"] == []


def test_ac3_before_any_listener_has_looked_there_is_nothing_to_show(capsys, monkeypatch) -> None:
    empty = L.MemoryLedger()
    assert empty.health() == []
    monkeypatch.setattr(S, "pipeline_reader", lambda s=None: S.Reader(read=_ok, ledger=empty))
    assert cli.main(["health"]) == 4  # not "healthy": nobody is polling
    assert "no listener has reported yet" in capsys.readouterr().out


# ------------------------------------------------------------------ AC4
# GIVEN no poll succeeds for 5 minutes WHEN the alert rule runs THEN an alert is raised


def test_ac4_a_listener_that_has_not_polled_for_five_minutes_is_not_healthy(folder) -> None:
    clock, ledger = Clock(), L.MemoryLedger()
    ledger._now = clock
    _listener(folder, ledger, clock).poll()
    clock.pass_(299)
    assert ledger.health()[0]["healthy"] is True  # 4 min 59 s: still fine
    clock.pass_(2)
    health = ledger.health()[0]
    assert health["healthy"] is False and health["seconds_since_good_poll"] == 301.0
    assert Settings.model_fields["listener_stale_seconds"].default == 300.0


def test_ac4_a_look_that_fails_does_not_count_as_a_good_poll(folder) -> None:
    """The listener is alive but its pipeline is not: it keeps looking, and the time of the
    last *good* look stands still - with the reason beside it."""
    clock, ledger = Clock(), L.MemoryLedger()
    ledger._now = clock
    state = {"down": None}
    listener = S.Listener(_connector(folder), S.Reader(read=_ok, ready=lambda: state["down"]),
                          ledger=ledger, now=clock)  # fmt: skip
    listener.poll()
    good = clock.at
    state["down"] = "the database cannot be reached"  # type: ignore[assignment]
    for _ in range(40):  # looking every 10 s for almost 7 minutes
        clock.pass_(10)
        assert listener.poll()["ready"] is False
    health = ledger.health()[0]
    assert health["last_good_poll_at"] == good.isoformat() and health["healthy"] is False
    assert health["last_poll_at"] == clock.at.isoformat()  # it IS still looking
    assert health["reason"] == "the database cannot be reached"
    state["down"] = None
    clock.pass_(10)
    listener.poll()
    assert ledger.health()[0]["healthy"] is True and ledger.health()[0]["reason"] is None


def test_ac4_there_is_an_alert_rule_for_it() -> None:
    import yaml

    rules = yaml.safe_load((ROOT / "infra" / "monitoring" / "rules" / "cdi-alerts.yml").read_text(
        encoding="utf-8"))["groups"][0]["rules"]  # fmt: skip
    rule = next(r for r in rules if r["alert"] == "CdiListenerNotPolling")
    assert rule["expr"] == "time() - max(cdi_listener_last_ok_poll_timestamp_seconds) > 300"
    assert rule["labels"]["severity"] == "page"
    assert "## CdiListenerNotPolling" in (ROOT / "docs" / "alerts.md").read_text(encoding="utf-8")


def test_ac4_the_rule_reads_the_time_of_the_last_good_look_from_the_database() -> None:
    from prometheus_client import CollectorRegistry, generate_latest

    from cdi_adapter import metrics

    registry = CollectorRegistry()
    registry.register(
        metrics.ListenerCollector(
            lambda: [], lambda: [("local", 1791500000.0, 2, 47.0), ("onedrive", None, 0, None)]
        )
    )
    text = generate_latest(registry).decode()
    assert 'cdi_listener_last_ok_poll_timestamp_seconds{drive="local"} 1.7915e+09' in text
    assert 'cdi_listener_last_ok_poll_timestamp_seconds{drive="onedrive"} 0.0' in text  # never
    assert 'cdi_listener_waiting_files{drive="local"} 2.0' in text
    assert 'cdi_listener_oldest_waiting_seconds{drive="local"} 47.0' in text
    assert "listener_heartbeat" in metrics.LISTENER_BEATS_SQL


def test_ac4_a_host_with_no_listener_raises_no_alert() -> None:
    """No heartbeat row, no series: the rule has nothing to fire on."""
    from prometheus_client import CollectorRegistry, generate_latest

    from cdi_adapter import metrics

    registry = CollectorRegistry()
    registry.register(metrics.ListenerCollector(lambda: [], lambda: []))
    assert "cdi_listener_last_ok_poll_timestamp_seconds{" not in generate_latest(registry).decode()


def test_ac4_writing_the_heartbeat_never_fails_a_look(folder) -> None:
    class Broken(L.MemoryLedger):
        def heartbeat(self, *a: Any, **k: Any) -> None:
            raise OSError("the database went away")

    _drop(folder, "rx.pdf")
    listener = _listener(folder, Broken(), Clock())
    listener.poll()
    assert listener.poll()["succeeded"] == ["rx.pdf"]


# "A person is told": no email, chat or pager has been named for on-call, so the people an alert
# can reach are the staff who are signed in. Every page asks the web app what is firing, the web
# app asks Alertmanager, and the page shows it in a banner.


def _firing(name: str, severity: str, summary: str, *, since: str = "2026-10-09T12:27:59Z",
            state: str = "active") -> dict[str, Any]:  # fmt: skip
    return {"labels": {"alertname": name, "severity": severity}, "startsAt": since,
            "annotations": {"summary": summary}, "status": {"state": state}}  # fmt: skip


NOT_POLLING = _firing("CdiListenerNotPolling", "page",
                      "The file listener has not polled its folder for 5 minutes.")  # fmt: skip


def test_ac4_the_alert_is_shown_to_the_staff_who_are_signed_in() -> None:
    alerts.forget()
    asked: list[str] = []

    def fetch(url: str) -> list[dict[str, Any]]:
        asked.append(url)
        return [NOT_POLLING]

    answer = alerts.active("http://127.0.0.1:9093/", fetch=fetch)
    assert asked == ["http://127.0.0.1:9093" + alerts.QUERY]
    assert answer == {"reachable": True, "alerts": [{
        "name": "CdiListenerNotPolling", "severity": "page", "since": "2026-10-09T12:27:59Z",
        "summary": "The file listener has not polled its folder for 5 minutes."}]}  # fmt: skip
    alerts.forget()


def test_ac4_the_summary_shown_is_the_one_the_rule_carries() -> None:
    import yaml

    rules = yaml.safe_load((ROOT / "infra" / "monitoring" / "rules" / "cdi-alerts.yml").read_text(
        encoding="utf-8"))  # fmt: skip
    rule = next(r for g in rules["groups"] for r in g["rules"]
                if r.get("alert") == "CdiListenerNotPolling")  # fmt: skip
    assert rule["annotations"]["summary"] == NOT_POLLING["annotations"]["summary"]
    assert rule["labels"]["severity"] in alerts.SEVERITIES


def test_ac4_every_page_carries_the_banner_and_asks_once_a_minute() -> None:
    html = page("Any page", "<p>body</p>")
    assert '<div id="alerts" aria-live="polite"></div>' in html
    assert "fetch('/api/alerts/active')" in html and "setInterval(look,60000)" in html
    assert "textContent" in ALERTS_SCRIPT and "innerHTML" not in ALERTS_SCRIPT  # never as HTML
    assert listener_page().count('id="alerts"') == 1  # a real page, built from the same frame
    web = (ROOT / "src" / "cdi_adapter" / "webapp" / "app.py").read_text(encoding="utf-8")
    assert '@app.get("/api/alerts/active")' in web and "alerts.active()" in web
    # whoever is at a screen: the front desk too
    assert auth.POLICY[("GET", "/api/alerts/active")] == auth.POLICY[("GET", "/api/me")]


def test_ac4_pages_come_first_and_one_alert_is_one_line() -> None:
    quarantined = _firing("CdiListenerFileQuarantined", "ticket", "A file is in quarantine.",
                          since="2026-10-09T10:00:00Z")  # fmt: skip
    again = _firing("CdiListenerNotPolling", "page", "x", since="2026-10-09T12:30:00Z")
    lines = alerts.shown([quarantined, again, NOT_POLLING])
    assert [(a["name"], a["severity"]) for a in lines] == [
        ("CdiListenerNotPolling", "page"), ("CdiListenerFileQuarantined", "ticket")]  # fmt: skip
    assert lines[0]["since"] == "2026-10-09T12:27:59Z"  # the earliest start of the two


def test_ac4_an_alert_silenced_in_alertmanager_is_not_shown() -> None:
    silenced = _firing("CdiListenerNotPolling", "page", "x", state="suppressed")
    unknown = _firing("SomethingElse", "info", "x")
    assert alerts.shown([silenced, unknown]) == []
    assert "silenced=false" in alerts.QUERY and "inhibited=false" in alerts.QUERY


def test_ac4_with_no_alertmanager_the_page_still_opens() -> None:
    def down(url: str) -> list[dict[str, Any]]:
        raise ConnectionError("refused")

    alerts.forget()
    assert alerts.active("http://127.0.0.1:9093", fetch=down) == {"reachable": False, "alerts": []}
    alerts.forget()
    assert alerts.active("", fetch=down) == {"reachable": False, "alerts": []}  # switched off


def test_ac4_alertmanager_is_asked_at_most_every_fifteen_seconds() -> None:
    alerts.forget()
    now, asked = [100.0], []

    def fetch(url: str) -> list[dict[str, Any]]:
        asked.append(url)
        return [NOT_POLLING]

    for at in (100.0, 105.0, 114.0, 116.0):
        now[0] = at
        alerts.active("http://am", fetch=fetch, clock=lambda: now[0])
    assert len(asked) == 2
    alerts.forget()


def test_ac4_nothing_can_be_posted_to_the_banner_from_outside() -> None:
    """The web app asks Alertmanager on loopback; it has no route that takes an alert in."""
    assert not [key for key in auth.POLICY if "alerts" in key[1] and key[0] != "GET"]
    assert Settings.model_fields["alertmanager_url"].default == "http://127.0.0.1:9093"


@pytest.mark.xfail(strict=True, reason="LS-S9 AC4: a firing alert is shown to the staff who are "
                   "signed in. Somebody who is not at a screen is still not told: no email, chat "
                   "or pager has been named for the 'oncall' receiver (ENT-S4).")  # fmt: skip
def test_ac4_somebody_who_is_not_at_a_screen_is_told() -> None:
    import yaml

    config = yaml.safe_load((ROOT / "infra" / "monitoring" / "alertmanager.yml").read_text(
        encoding="utf-8"))  # fmt: skip
    assert len(next(r for r in config["receivers"] if r["name"] == "oncall")) > 1


# ------------------------------------------------------------------ light on the host


def test_the_listener_itself_uses_no_model_and_no_gpu() -> None:
    """It moves files and submits jobs; the reading happens in the pipeline's workers."""
    for name in ("service.py", "ledger.py", "recovery.py", "connectors.py", "auth.py"):
        code = (ROOT / "src" / "cdi_adapter" / "listener" / name).read_text(encoding="utf-8")
        for heavy in ("import torch", "transformers", "rapidocr", "cuda", "cdi_adapter.mlserve",
                      "cdi_adapter.ml.", "get_client"):  # fmt: skip
            assert heavy not in code, (name, heavy)
    # LS-S10: it does ask whether the model server answers - a knock on its port, nothing more
    service = (ROOT / "src" / "cdi_adapter" / "listener" / "service.py").read_text("utf-8")
    assert "_port_closed(u, 8077)" in service


def test_between_looks_the_listener_sleeps(folder) -> None:
    looks: list[float] = []
    listener = _listener(folder, L.MemoryLedger(), Clock())
    real = listener.poll
    listener.poll = lambda: looks.append(time.perf_counter()) or real()  # type: ignore[method-assign]
    thread = threading.Thread(target=lambda: listener.run(interval=0.2))
    thread.start()
    time.sleep(0.75)
    listener.stop.set()
    thread.join(5)
    assert 3 <= len(looks) <= 6  # a look every 0.2 s, not a busy loop
    assert get_settings().listener_poll_seconds == 10.0
