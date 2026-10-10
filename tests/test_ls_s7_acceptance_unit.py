"""LS-S7 acceptance: retry a failed file 3 times, then hand it to a person.

One group of tests per acceptance criterion, then the rules behind them. The watched folder is
a real folder (``tmp_path``) through the real ``local`` connector; the real listener loop and
the real retry agent run on it, sharing one ledger as they share one table in real life. The
reader (the pipeline) is a stand-in whose "model server" the test switches off and on. The
clock is one the test moves, so waiting 60, 120 and 240 seconds takes no time.

Not shown here: the agent and the listener as two separate processes on a real database, and a
person actually being told about a quarantined file (ENT-S4).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from cdi_adapter.config import Settings, get_settings
from cdi_adapter.listener import __main__ as cli
from cdi_adapter.listener import auth as A
from cdi_adapter.listener import connectors as C
from cdi_adapter.listener import ledger as L
from cdi_adapter.listener import recovery as R
from cdi_adapter.listener import service as S
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

    @property
    def seconds(self) -> float:
        return (self.at - START).total_seconds()


class ModelServer:
    """The pipeline, with a model server that can be down. It records when each run of a file
    was made (by the test's clock)."""

    def __init__(self, clock: Clock, *, down_until: float = 0.0, always: Exception | None = None,
                 answer: S.Outcome | None = None) -> None:  # fmt: skip
        self.clock, self.down_until, self.always, self.answer = clock, down_until, always, answer
        self.runs: list[tuple[str, float]] = []

    def reader(self) -> S.Reader:
        def read(name: str, raw: bytes, mime: str) -> S.Outcome:
            self.runs.append((name, self.clock.seconds))
            if self.always is not None:
                raise self.always
            if self.clock.seconds < self.down_until:
                raise ConnectionError("the model server is unavailable (503)")
            return self.answer or S.Outcome(True, job_id=f"job-{len(self.runs)}")

        return S.Reader(read=read)


class Site:
    """One watched folder with its listener and its retry agent, on one clock and one ledger."""

    def __init__(self, folder: Path, server: ModelServer, clock: Clock, **listener: Any) -> None:
        settings = Settings(listener_connector="local", listener_local_root=str(folder),
                            listener_inbox="")  # fmt: skip
        self.folder, self.clock = folder, clock
        self.connector = C.get_connector(settings)
        self.connector.ensure_folders()
        self.ledger = L.MemoryLedger(now=clock)
        self.alerts: list[tuple[str, int]] = []
        self.listener = S.Listener(self.connector, server.reader(), ledger=self.ledger, now=clock,
                                   alert=lambda name, runs: self.alerts.append((name, runs)),
                                   **listener)  # fmt: skip
        self.agent = R.RetryAgent(self.connector, self.ledger,
                                  max_attempts=self.listener.max_attempts,
                                  base_seconds=self.listener.retry_base_seconds)  # fmt: skip
        self.retries: list[tuple[str, int, float]] = []

    def tick(self, seconds: float = 10.0) -> dict[str, Any]:
        """Ten seconds pass; the agent looks, then the listener looks."""
        self.clock.pass_(seconds)
        for item in self.agent.pass_once()["retried"]:
            self.retries.append((item["file"], item["retry"], self.clock.seconds))
        return self.listener.poll()

    def run_for(self, seconds: float) -> None:
        for _ in range(int(seconds // 10)):
            self.tick()

    def names(self, sub: str = "") -> list[str]:
        return sorted(p.name for p in (self.folder / sub).iterdir() if p.is_file())

    def notes(self) -> list[str]:
        return sorted((self.folder / "log" / n).read_text(encoding="utf-8")
                      for n in self.names("log"))  # fmt: skip


@pytest.fixture
def folder(tmp_path) -> Path:
    root = tmp_path / "File listener"
    root.mkdir()
    return root


def _rx(n: int = 0) -> bytes:
    return blank_pdf(1, size=(200 + n, 280))


# ------------------------------------------------------------------ AC1
# GIVEN the model server is down for 3 minutes WHEN a file is read
# THEN it is retried after 60 s, then 120 s, and succeeds once the server is back


def test_ac1_a_server_down_for_three_minutes_retried_at_60_then_120_then_read(folder) -> None:
    clock = Clock()
    server = ModelServer(clock, down_until=180.0)  # down for 3 minutes
    site = Site(folder, server, clock)
    (folder / "rx.pdf").write_bytes(_rx())
    site.run_for(400)
    times = [at for _, at in server.runs]
    assert len(times) == 3  # the first run and two retries
    first, second, third = times
    assert second - first == pytest.approx(70, abs=10)  # 60 s wait (+ one look at the folder)
    assert third - second == pytest.approx(130, abs=10)  # 120 s wait (+ one look)
    assert first < 180 and second < 180 and third >= 180  # the third run found it back
    assert site.names("success") == ["rx.pdf"] and site.names("error") == []
    assert site.names("quarantine") == [] and site.alerts == []


def test_ac1_the_waits_are_60_then_120_exactly_as_the_agent_measures_them(folder) -> None:
    clock = Clock()
    site = Site(folder, ModelServer(clock, down_until=180.0), clock)
    (folder / "rx.pdf").write_bytes(_rx())
    site.run_for(400)
    row = next(iter(site.ledger._rows.values()))
    retries = [e for e in row.history if e["event"] == "retry"]
    assert [e["attempt"] for e in retries] == [1, 2]
    assert [e["waited_seconds"] for e in retries] == [60.0, 120.0]


def test_ac1_it_is_not_retried_before_its_wait_is_over(folder) -> None:
    clock = Clock()
    server = ModelServer(clock, down_until=180.0)
    site = Site(folder, server, clock)
    (folder / "rx.pdf").write_bytes(_rx())
    site.tick()
    site.tick()  # run 1 fails here
    assert len(server.runs) == 1 and site.names("error") == ["rx.pdf"]
    for _ in range(5):  # 50 s on: still waiting
        site.tick()
        assert site.names("error") == ["rx.pdf"] and len(server.runs) == 1
    waiting = site.agent.pass_once()["waiting"]
    assert waiting == [{"file": "rx.pdf", "retry": 1, "in_seconds": 10.0}]


def test_ac1_each_failed_run_left_a_note_and_the_success_left_none(folder) -> None:
    clock = Clock()
    site = Site(folder, ModelServer(clock, down_until=180.0), clock)
    (folder / "rx.pdf").write_bytes(_rx())
    site.run_for(400)
    notes = sorted(site.notes(), key=lambda n: n[n.index("run:") :])  # by run number
    assert len(notes) == 2  # runs 1 and 2 failed; run 3 was read
    assert "run:     1 of 4" in notes[0] and "in about 60 s (retry 1 of 3)" in notes[0]
    assert "run:     2 of 4" in notes[1] and "in about 120 s (retry 2 of 3)" in notes[1]
    assert all("class:   transient" in n for n in notes)


def test_ac1_the_file_is_the_same_file_after_its_retries(folder) -> None:
    clock, raw = Clock(), _rx()
    site = Site(folder, ModelServer(clock, down_until=180.0), clock)
    (folder / "rx.pdf").write_bytes(raw)
    site.run_for(400)
    assert (folder / "success" / "rx.pdf").read_bytes() == raw


def test_ac1_other_files_are_read_while_one_waits_for_its_retry(folder) -> None:
    clock = Clock()
    server = ModelServer(clock, down_until=30.0)
    site = Site(folder, server, clock)
    (folder / "early.pdf").write_bytes(_rx(1))
    site.tick()
    site.tick()  # early.pdf fails at 20 s: the server is still down
    (folder / "later.pdf").write_bytes(_rx(2))
    site.run_for(40)  # 60 s: the server is back; early.pdf still has 20 s of its wait to go
    assert site.names("success") == ["later.pdf"] and site.names("error") == ["early.pdf"]
    site.run_for(60)
    assert site.names("success") == ["early.pdf", "later.pdf"]


# ------------------------------------------------------------------ AC2
# GIVEN a file fails on every run WHEN the 3rd retry fails
# THEN it is in 'quarantine' with a note for each of the 4 runs


def test_ac2_a_file_that_always_fails_is_in_quarantine_after_the_third_retry(folder) -> None:
    clock = Clock()
    server = ModelServer(clock, always=TimeoutError("model server"))
    site = Site(folder, server, clock)
    (folder / "rx.pdf").write_bytes(_rx())
    site.run_for(700)
    assert len(server.runs) == 4  # a file runs at most four times
    assert site.names("quarantine") == ["rx.pdf"]
    assert site.names("error") == [] and site.names("success") == [] and site.names() == []


def test_ac2_there_is_a_note_for_each_of_the_four_runs(folder) -> None:
    clock = Clock()
    site = Site(folder, ModelServer(clock, always=TimeoutError("model server")), clock)
    (folder / "rx.pdf").write_bytes(_rx())
    site.run_for(700)
    names = site.names("log")
    assert sorted(n.rsplit(".", 2)[1] for n in names) == ["run1", "run2", "run3", "run4"]
    assert all(n.startswith("rx.pdf.") for n in names)
    last = next(n for n in site.notes() if "run:     4 of 4" in n)
    assert "the retries are used up" in last and "quarantine" in last
    assert "until a person looks at it" in last


def test_ac2_the_three_waits_were_60_120_and_240_seconds(folder) -> None:
    clock = Clock()
    server = ModelServer(clock, always=TimeoutError("model server"))
    site = Site(folder, server, clock)
    (folder / "rx.pdf").write_bytes(_rx())
    site.run_for(700)
    row = next(iter(site.ledger._rows.values()))
    assert [e["waited_seconds"] for e in row.history if e["event"] == "retry"] == [
        60.0,
        120.0,
        240.0,
    ]
    times = [at for _, at in server.runs]
    gaps = [b - a for a, b in zip(times, times[1:], strict=False)]
    assert gaps == pytest.approx([70, 130, 250], abs=10)  # each wait, plus one look


def test_ac2_it_is_never_run_a_fifth_time(folder) -> None:
    clock = Clock()
    server = ModelServer(clock, always=TimeoutError("model server"))
    site = Site(folder, server, clock)
    (folder / "rx.pdf").write_bytes(_rx())
    site.run_for(700)
    site.run_for(3600)  # an hour more
    assert len(server.runs) == 4 and site.names("quarantine") == ["rx.pdf"]
    assert site.agent.pass_once() == {"retried": [], "waiting": [], "missing": []}


def test_ac2_the_row_says_quarantined_and_keeps_the_whole_story(folder) -> None:
    clock = Clock()
    site = Site(folder, ModelServer(clock, always=TimeoutError("model server")), clock)
    (folder / "rx.pdf").write_bytes(_rx())
    site.run_for(700)
    (row,) = site.ledger._rows.values()
    assert row.state == "quarantined"
    events = [e["event"] for e in row.history if e["event"] in ("failed", "retry", "quarantined")]
    assert events == ["failed", "retry", "failed", "retry", "failed", "retry", "quarantined"]


def test_ac2_an_unplanned_error_is_retried_and_quarantined_the_same_way(folder) -> None:
    """A bug, not a busy server. There is no telling from here whether it will heal, so it is
    tried - and a person gets it if it does not."""
    clock = Clock()
    server = ModelServer(clock, always=KeyError("field"))
    site = Site(folder, server, clock)
    (folder / "rx.pdf").write_bytes(_rx())
    site.run_for(700)
    assert len(server.runs) == 4 and site.names("quarantine") == ["rx.pdf"]
    assert all("class:   code" in n for n in site.notes())


# ------------------------------------------------------------------ AC3
# GIVEN an empty file is dropped WHEN it fails THEN it is not retried and the note says to rescan


def test_ac3_an_empty_file_fails_is_not_retried_and_the_note_says_to_rescan(folder) -> None:
    clock = Clock()
    server = ModelServer(clock)
    site = Site(folder, server, clock)
    (folder / "empty.pdf").write_bytes(b"")
    site.run_for(120)
    assert site.names("error") == ["empty.pdf"] and server.runs == []  # never sent to be read
    (note,) = site.notes()
    assert "reason:  the file is empty (0 bytes)." in note and "kind:    empty" in note
    assert "class:   data" in note and "will not be tried again" in note
    assert "Rescan and drop it again." in note
    site.run_for(3600)  # an hour on: the agent has not touched it
    assert site.names("error") == ["empty.pdf"] and len(site.names("log")) == 1
    assert site.retries == [] and site.names("quarantine") == []


def test_ac3_an_empty_file_is_given_a_minute_before_it_is_called_empty(folder) -> None:
    """A file just created is empty for a moment; it is not refused on sight."""
    clock = Clock()
    site = Site(folder, ModelServer(clock), clock)
    (folder / "arriving.pdf").write_bytes(b"")
    for _ in range(4):
        assert site.tick()["settling"] == 1
    (folder / "arriving.pdf").write_bytes(_rx())  # the rest of it arrives
    site.run_for(40)
    assert site.names("success") == ["arriving.pdf"] and site.names("error") == []


@pytest.mark.parametrize(("name", "raw", "kind"), [
    ("scan.pdf", b"%PDF-1.7\n" + b"\x00" * 200, "not_readable"),  # corrupt
    ("bundle.pdf", blank_pdf(60), "too_many_pages"),  # too big to be one prescription
])  # fmt: skip
def test_ac3_corrupt_and_oversized_files_are_not_retried_either(name, raw, kind, folder) -> None:
    clock = Clock()
    server = ModelServer(clock)
    site = Site(folder, server, clock)
    (folder / name).write_bytes(raw)
    site.run_for(600)
    assert site.names("error") == [name] and site.retries == [] and server.runs == []
    (note,) = site.notes()
    assert f"kind:    {kind}" in note and "class:   data" in note


def test_ac3_a_picture_too_blurred_to_read_is_not_retried(folder) -> None:
    clock = Clock()
    held = S.Outcome(False, "rescan: image quality below threshold - too blurry", "rescan", "j1")
    server = ModelServer(clock, answer=held)
    site = Site(folder, server, clock)
    (folder / "blurry.pdf").write_bytes(_rx())
    site.run_for(600)
    assert len(server.runs) == 1 and site.retries == []  # read once, never again
    (note,) = site.notes()
    assert "Rescan and drop it again." in note and "class:   data" in note


def test_ac3_a_file_over_the_size_limit_is_not_retried(folder) -> None:
    clock = Clock()
    site = Site(folder, ModelServer(clock), clock, max_bytes=300)
    (folder / "huge.pdf").write_bytes(_rx() + b"\x00" * 600)
    site.run_for(600)
    assert site.names("error") == ["huge.pdf"] and site.retries == []
    assert "Rescan and drop it again." in site.notes()[0]


# ------------------------------------------------------------------ AC4
# GIVEN a file reaches 'quarantine' WHEN the alert rule runs THEN a person is told (ENT-S4)


def test_ac4_an_alert_is_raised_when_a_file_reaches_quarantine(folder) -> None:
    clock = Clock()
    site = Site(folder, ModelServer(clock, always=TimeoutError("model server")), clock)
    (folder / "rx.pdf").write_bytes(_rx())
    site.run_for(300)
    assert site.alerts == []  # not for a failure that is still being retried
    site.run_for(400)
    assert site.alerts == [("rx.pdf", 4)]  # once, when it reaches quarantine


def test_ac4_the_alert_is_a_critical_log_line_and_a_line_in_the_alert_list(
    capsys, tmp_path
) -> None:
    from cdi_adapter.logging import configure

    settings = Settings(listener_alert_file=str(tmp_path / "logs" / "listener_alert.json"))
    configure()
    A.quarantine_alert(settings)("rx.pdf", 4)
    A.quarantine_alert(settings)("other.pdf", 4)
    line = next(json.loads(ln) for ln in capsys.readouterr().out.splitlines()
                if '"listener_alert"' in ln)  # fmt: skip
    assert line["level"] == "critical" and line["alert"] == "ListenerFileQuarantined"
    assert line["file"] == "rx.pdf" and line["runs"] == 4
    kept = (tmp_path / "logs" / "listener_alert.quarantine.jsonl").read_text("utf-8").splitlines()
    assert [json.loads(k)["file"] for k in kept] == ["rx.pdf", "other.pdf"]  # added to, kept


def test_ac4_a_clean_start_does_not_hide_a_quarantine_alert(tmp_path) -> None:
    settings = Settings(listener_alert_file=str(tmp_path / "listener_alert.json"))
    A.quarantine_alert(settings)("rx.pdf", 4)
    A.clear_alert(settings)  # what a clean start does to the sign-in alert
    assert (tmp_path / "listener_alert.quarantine.jsonl").is_file()


def test_ac4_the_running_listener_is_given_the_alert() -> None:
    made = S.Listener.from_settings(None, S.Reader(read=lambda n, r, m: S.Outcome(True)),  # type: ignore[arg-type]
                                    Settings())  # fmt: skip
    assert made.alert is not None


def test_ac4_a_data_error_raises_no_alert(folder) -> None:
    clock = Clock()
    site = Site(folder, ModelServer(clock), clock)
    (folder / "scan.pdf").write_bytes(b"%PDF-1.7\n" + b"\x00" * 200)
    site.run_for(600)
    assert site.alerts == []


def test_ac4_there_is_an_alert_rule_for_a_quarantined_file() -> None:
    """Was an expected failure until the rule was added (2026-10-09), beside the project's
    other alert rules and tested the same way (tests/test_monitoring_unit.py)."""
    import yaml

    rules = yaml.safe_load((ROOT / "infra" / "monitoring" / "rules" / "cdi-alerts.yml").read_text(
        encoding="utf-8"))["groups"][0]["rules"]  # fmt: skip
    rule = next(r for r in rules if r["alert"] == "CdiListenerFileQuarantined")
    assert rule["expr"] == "max(cdi_listener_quarantined_recent) > 0"
    assert rule["labels"]["severity"] == "ticket"
    assert "runbook_url" in rule["annotations"]
    runbook = (ROOT / "docs" / "alerts.md").read_text(encoding="utf-8")
    assert "## CdiListenerFileQuarantined" in runbook


def test_ac4_the_rule_reads_a_number_that_comes_from_the_listeners_table() -> None:
    from prometheus_client import CollectorRegistry, generate_latest

    from cdi_adapter import metrics

    registry = CollectorRegistry()
    registry.register(
        metrics.ListenerCollector(
            lambda: [("succeeded", 40, 0), ("failed", 2, 0), ("quarantined", 3, 1)], lambda: []
        )
    )
    text = generate_latest(registry).decode()
    assert "cdi_listener_quarantined_recent 1.0" in text  # one in the last day; two are older
    assert 'cdi_listener_files{state="quarantined"} 3.0' in text
    assert "listener_file" in metrics.LISTENER_SQL and "24 hours" in metrics.LISTENER_SQL
    web = (ROOT / "src" / "cdi_adapter" / "webapp" / "app.py").read_text(encoding="utf-8")
    assert "metrics.ListenerCollector()" in web  # the web app exposes it


def test_ac4_a_database_that_cannot_be_read_gives_no_number_and_no_error() -> None:
    from prometheus_client import CollectorRegistry, generate_latest

    from cdi_adapter import metrics

    def broken() -> list[tuple[str, int, int]]:
        raise RuntimeError("relation listener_file does not exist")

    registry = CollectorRegistry()
    registry.register(metrics.ListenerCollector(broken, broken))  # type: ignore[arg-type]
    assert "cdi_listener" not in generate_latest(registry).decode()


def test_ac4_the_alert_goes_where_every_other_alert_goes() -> None:
    import yaml

    route = yaml.safe_load((ROOT / "infra" / "monitoring" / "alertmanager.yml").read_text(
        encoding="utf-8"))  # fmt: skip
    assert route["route"]["receiver"] == "oncall"


def test_ac4_the_staff_who_are_signed_in_are_told_on_every_page() -> None:
    """The banner every page shows (webapp/alerts.py) carries this alert too, as a notice."""
    from cdi_adapter.webapp import alerts
    from cdi_adapter.webapp.theme import page

    firing = {"labels": {"alertname": "CdiListenerFileQuarantined", "severity": "ticket"},
              "annotations": {"summary": "A file is in quarantine."}, "status": {"state": "active"},
              "startsAt": "2026-10-09T10:00:00Z"}  # fmt: skip
    assert alerts.shown([firing]) == [{"name": "CdiListenerFileQuarantined", "severity": "ticket",
                                       "since": "2026-10-09T10:00:00Z",
                                       "summary": "A file is in quarantine."}]  # fmt: skip
    assert "fetch('/api/alerts/active')" in page("x", "") and "'Notice: '" in page("x", "")


@pytest.mark.xfail(strict=True, reason="LS-S7 AC4: a firing alert is shown to the staff who are "
                   "signed in (the banner). Somebody who is not at a screen is still not told: the "
                   "'oncall' receiver has no email, chat or pager behind it (ENT-S4).")  # fmt: skip
def test_ac4_the_on_call_receiver_reaches_somebody() -> None:
    import yaml

    config = yaml.safe_load((ROOT / "infra" / "monitoring" / "alertmanager.yml").read_text(
        encoding="utf-8"))  # fmt: skip
    oncall = next(r for r in config["receivers"] if r["name"] == "oncall")
    assert len(oncall) > 1  # more than its name: an integration


# ------------------------------------------------------------------ sorting failures


@pytest.mark.parametrize(("kind", "reason", "expected"), [
    ("empty", "the file is empty (0 bytes).", "data"),
    ("not_readable", "this is not a PDF that can be opened", "data"),
    ("rescan", "rescan: image quality below threshold", "data"),
    ("too_large", "the file is 61.0 MB; the limit is 50 MB.", "data"),
    ("too_many_pages", "the document has 60 pages", "data"),
    ("not_clinical", 'read as "invoice"', "data"),
    ("mismatch", "the document does not belong to the expected patient", "data"),
    ("timeout", "not finished after 900 s", "transient"),
    ("failed", "TimeoutError: model server", "transient"),
    ("failed", "ConnectionError: [Errno 111] Connection refused", "transient"),
    ("failed", "ReadTimeout: timed out", "transient"),
    ("failed", "OperationalError: server closed the connection unexpectedly", "transient"),
    ("failed", "HTTPStatusError: 503 Service Unavailable", "transient"),
    ("failed", "HTTPStatusError: 429 Too Many Requests", "transient"),
    ("failed", "Throttled: the drive is still saying 'too many requests'", "transient"),
    ("failed", "RuntimeError: CUDA out of memory", "transient"),
    ("failed", "LeaseLost: job taken by another runner", "transient"),
    ("failed", "KeyError: 'dose'", "code"),
    ("failed", "ValueError: bad literal", "code"),
    ("failed", "ended as error", "code"),
    (None, None, "code"),
])  # fmt: skip
def test_failures_are_sorted_into_data_transient_and_code(kind, reason, expected) -> None:
    assert R.classify_error(kind, reason) == expected
    assert R._classify_error is R.classify_error  # the name the backlog uses


def test_only_data_errors_are_not_retried() -> None:
    assert [R.retried(c) for c in ("data", "transient", "code")] == [False, True, True]


def test_the_waits_double_from_the_base() -> None:
    assert [R.wait_seconds(n, 60) for n in (1, 2, 3)] == [60.0, 120.0, 240.0]
    assert [R.wait_seconds(n, 5) for n in (1, 2, 3)] == [5.0, 10.0, 20.0]


def test_the_number_of_retries_and_the_base_wait_are_settings(folder) -> None:
    fields = Settings.model_fields
    assert fields["listener_max_attempts"].default == 3
    assert fields["listener_retry_base_seconds"].default == 60.0
    clock = Clock()
    server = ModelServer(clock, always=TimeoutError("x"))
    site = Site(folder, server, clock, max_attempts=1, retry_base_seconds=20.0)
    (folder / "rx.pdf").write_bytes(_rx())
    site.run_for(200)
    assert len(server.runs) == 2 and site.names("quarantine") == ["rx.pdf"]  # a run and a retry
    row = next(iter(site.ledger._rows.values()))
    assert [e["waited_seconds"] for e in row.history if e["event"] == "retry"] == [20.0]


# ------------------------------------------------------------------ the agent, on its own


def test_the_agent_leaves_another_drives_rows_alone(folder, tmp_path) -> None:
    """LS-S1 AC2: after the drive setting changes, the failed files of the old drive are not
    this agent's to retry."""
    clock = Clock()
    site = Site(folder, ModelServer(clock, always=TimeoutError("x")), clock)
    (folder / "rx.pdf").write_bytes(_rx())
    site.run_for(30)  # failed once, on this drive
    other_root = tmp_path / "another drive"
    other = C.get_connector(Settings(listener_connector="local",
                                     listener_local_root=str(other_root)))  # fmt: skip
    other.ensure_folders()
    elsewhere = R.RetryAgent(other, site.ledger)  # the same table, a different drive
    clock.pass_(600)
    assert elsewhere.pass_once() == {"retried": [], "waiting": [], "missing": []}
    assert site.names("error") == ["rx.pdf"]  # untouched


def test_a_failed_file_someone_took_out_of_error_is_reported_not_chased(folder) -> None:
    clock = Clock()
    site = Site(folder, ModelServer(clock, always=TimeoutError("x")), clock)
    (folder / "rx.pdf").write_bytes(_rx())
    site.run_for(30)
    (folder / "error" / "rx.pdf").unlink()  # a person removed it
    clock.pass_(600)
    assert site.agent.pass_once()["missing"] == ["rx.pdf"]


def test_the_agent_keeps_nothing_itself_so_it_can_be_restarted(folder) -> None:
    clock = Clock()
    server = ModelServer(clock, down_until=180.0)
    site = Site(folder, server, clock)
    (folder / "rx.pdf").write_bytes(_rx())
    site.run_for(50)
    site.agent = R.RetryAgent(site.connector, site.ledger)  # a new agent, mid-wait
    site.run_for(400)
    assert site.names("success") == ["rx.pdf"] and len(server.runs) == 3


def test_the_recover_command_makes_one_pass(monkeypatch, capsys, folder) -> None:
    clock = Clock()
    site = Site(folder, ModelServer(clock, always=TimeoutError("x")), clock)
    (folder / "rx.pdf").write_bytes(_rx())
    site.run_for(30)
    clock.pass_(60)
    monkeypatch.setenv("CDI_LISTENER_CONNECTOR", "local")
    monkeypatch.setenv("CDI_LISTENER_LOCAL_ROOT", str(folder))
    monkeypatch.setenv("CDI_LISTENER_INBOX", "")
    monkeypatch.setattr(S, "pipeline_reader",
                        lambda settings=None: S.Reader(read=lambda n, r, m: S.Outcome(True),
                                                       ledger=site.ledger))  # fmt: skip
    get_settings.cache_clear()
    try:
        assert cli.main(["recover", "--once"]) == 0
    finally:
        get_settings.cache_clear()
    out = capsys.readouterr().out
    assert "retried      1 file(s) -> back in the inbox" in out and "rx.pdf: retry 1 of 3" in out
    assert site.names() == ["rx.pdf"] and site.names("error") == []


def test_the_retry_agent_is_started_with_the_listener() -> None:
    script = (ROOT / "infra" / "runpod" / "start_listener.sh").read_text(encoding="utf-8")
    assert "cdi_adapter.listener run" in script and "cdi_adapter.listener recover" in script
    start_all = (ROOT / "infra" / "runpod" / "start_all.sh").read_text(encoding="utf-8")
    # LS-S9 renamed the switch and made the retry agent start on every pod start
    assert "start_listener.sh" in start_all and "CDI_START_LISTENER" in start_all
    assert 'start_listener.sh" recover' in start_all
