"""ENT-S4 "Run it reliably: health, metrics, alerts, backups and a restart runbook".

* AC1  after a restart, ONE start command brings every service back healthy, and the row
       counts equal the counts before the restart;
* AC2  the model server stopped: within five minutes an alert is raised;
* AC3  a result that took longer than 60 s is counted in a "late" metric when it is saved;
* AC4  the backup job fails: an alert is raised at the next check.

What can be shown without a pod is shown here: the shell step that used to end the start
script is RUN (with a stand-in ``crontab``), the counts are compared, the metric is counted by
the real job runner, the alert rules are read. What needs a pod - the rehearsal itself, and
the alert rules run by Prometheus's own checker - is said at the end.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from prometheus_client import CollectorRegistry, generate_latest

from cdi_adapter import metrics
from cdi_adapter.dr import backup as bk
from cdi_adapter.dr import counts
from cdi_adapter.webapp import app as web
from cdi_adapter.webapp.jobs import JobRunner, Stages
from tests.test_ops_unit import _bash

ROOT = Path(__file__).resolve().parents[1]
RUNPOD = ROOT / "infra" / "runpod"
RULES = yaml.safe_load((ROOT / "infra/monitoring/rules/cdi-alerts.yml").read_text("utf-8"))
RULE = {r["alert"]: r for r in RULES["groups"][0]["rules"]}
CASES = yaml.safe_load((ROOT / "infra/monitoring/rules/cdi-alerts.test.yml").read_text("utf-8"))
BOOT = (RUNPOD / "bootstrap_pod.sh").read_text(encoding="utf-8")
START = (RUNPOD / "start_all.sh").read_text(encoding="utf-8")
STOP = (RUNPOD / "prepare_stop.sh").read_text(encoding="utf-8")
RUNBOOK = (ROOT / "docs/RUNBOOK.md").read_text(encoding="utf-8")


def _seconds(span: str | None) -> int:
    if not span:
        return 0
    n, unit = int(span[:-1]), span[-1]
    return n * {"s": 1, "m": 60, "h": 3600}[unit]


# ------------------------------------------------------------------ AC1: the crontab step


def _cron_step(fixed: bool) -> str:
    """Step 6 of the bootstrap as it is now, or as it was before the fix."""
    step = BOOT.split("# ---------------------------------------------------------------- 6. cron")[
        1
    ].split("# ---------------------------------------------------------------- 7.")[0]
    step = "( " + step.split("\n( ", 1)[1]  # from the subshell that writes the crontab
    if not fixed:  # the line exactly as it was
        step = re.sub(
            r"\( \{ crontab -l 2>/dev/null \|\| true; \} \\\n\s*\| \{ grep -v (.*?) \|\| true; \}",
            lambda m: "( crontab -l 2>/dev/null | grep -v " + m.group(1),
            step,
            flags=re.S,
        )
    return step


def _run_cron_step(tmp_path: Path, fixed: bool, existing: str = "") -> tuple[int, str]:
    """Run the step under ``set -euo pipefail`` with a stand-in ``crontab``: ``-l`` prints
    what is installed (and FAILS when nothing is, as the real one does); ``-`` installs."""
    bash = _bash()
    if bash is None:
        pytest.skip("no bash on this machine")
    installed = tmp_path / "installed.cron"
    if existing:
        installed.write_text(existing, encoding="utf-8")
    script = tmp_path / "step.sh"
    script.write_text(
        "set -euo pipefail\n"
        f'TAB="{installed.as_posix()}"\n'
        'crontab() { if [ "$1" = -l ]; then [ -s "$TAB" ] && cat "$TAB" || '
        '{ echo "no crontab for root" >&2; return 1; }; else cat > "$TAB.new"; fi; }\n'
        "REPO=/workspace/cdi WS=/workspace MINIO_BIN=/workspace/bin/minio\n"
        + _cron_step(fixed)
        + '\necho "REACHED-THE-NEXT-STEP"\n',
        encoding="utf-8",
        newline="\n",
    )
    done = subprocess.run([bash, script.as_posix()], capture_output=True, text=True, timeout=60)
    written = tmp_path / "installed.cron.new"  # what the step installed
    return done.returncode, done.stdout + (written.read_text("utf-8") if written.exists() else "")


def test_ac1_before_the_fix_an_empty_crontab_ended_the_start_script(tmp_path):
    """The known bug: after a restart nothing is in the crontab, and the script stopped here."""
    code, out = _run_cron_step(tmp_path, fixed=False)
    assert code != 0 and "REACHED-THE-NEXT-STEP" not in out
    assert "snapshot.sh" not in out, "and no snapshot job was installed"


def test_ac1_with_an_empty_crontab_the_backup_job_is_installed_and_the_script_goes_on(tmp_path):
    code, out = _run_cron_step(tmp_path, fixed=True)
    assert code == 0 and "REACHED-THE-NEXT-STEP" in out
    assert "*/5 * * * * flock -n /workspace/backup/.snapshot.lock bash /workspace/cdi/infra/" in out
    assert out.count("snapshot.sh") == 1 and "cdi_adapter.dr drill" in out


def test_ac1_running_it_again_does_not_install_the_jobs_twice_and_keeps_other_lines(tmp_path):
    mine = "0 1 * * * /usr/local/bin/someone-elses-job\n"
    old = "*/15 * * * * bash /workspace/cdi/infra/runpod/snapshot.sh >>/workspace/logs/x 2>&1\n"
    code, out = _run_cron_step(tmp_path, fixed=True, existing=mine + old)
    assert code == 0 and out.count("snapshot.sh") == 1 and "*/15" not in out
    assert "someone-elses-job" in out, "a line that is not ours is left alone"


def test_ac1_backups_run_every_five_minutes_and_never_over_each_other():
    assert (
        "*/5 * * * * flock -n $WS/backup/.snapshot.lock bash $REPO/infra/runpod/snapshot.sh" in BOOT
    )
    assert "*/15 * * * *" not in BOOT
    snapshot = (RUNPOD / "snapshot.sh").read_text(encoding="utf-8")
    assert 'pg_restore --list "$TMP"' in snapshot, "a dump that cannot be listed is not kept"
    assert "trap" in snapshot and "--failed" in snapshot, "a failed run is recorded"


# ------------------------------------------------------------------ AC1: the counts


class Conn:
    def __init__(self, tables: dict[str, int]) -> None:
        self.tables = tables

    def execute(self, statement: Any, params: Any = None) -> Any:
        sql = str(statement)
        if "information_schema.tables" in sql:
            rows = [(name,) for name in sorted(self.tables)]
            return SimpleNamespace(all=lambda: rows)
        name = re.search(r'FROM "(\w+)"', sql).group(1)  # type: ignore[union-attr]
        return SimpleNamespace(scalar_one=lambda: self.tables[name])


BEFORE = {"clinical_fact": 4210, "source_document": 51, "web_job": 60, "audit_log": 900}


def _take(tables: dict[str, int], objects: int | None = 51, keys: int | None = 1) -> dict[str, Any]:
    out = counts.take(Conn(tables), now=dt.datetime(2026, 10, 13, 9, 0, tzinfo=dt.UTC))
    return {**out, "objects": objects, "queue_keys": keys}


def test_ac1_every_table_is_counted():
    taken = _take(BEFORE)
    assert taken["tables"] == BEFORE and taken["rows"] == 5221
    assert taken["taken_at"] == "2026-10-13T09:00:00+00:00"


def test_ac1_equal_counts_before_and_after_are_reported_identical():
    found = counts.compare(_take(BEFORE), _take(dict(BEFORE)))
    assert found["same"] and found["differences"] == []
    assert (found["tables_compared"], found["rows_before"], found["rows_after"]) == (4, 5221, 5221)


@pytest.mark.parametrize(
    ("change", "what"),
    [
        ({"clinical_fact": 4200}, "table clinical_fact"),  # rows lost
        ({"web_job": 61}, "table web_job"),  # rows that were not there before
    ],
)
def test_ac1_one_table_with_other_counts_is_a_difference(change, what):
    found = counts.compare(_take(BEFORE), _take({**BEFORE, **change}))
    assert not found["same"] and [d["what"] for d in found["differences"]] == [what]


def test_ac1_a_table_that_is_gone_is_a_difference_and_a_new_one_is_not():
    after = {k: v for k, v in BEFORE.items() if k != "audit_log"}
    gone = counts.compare(_take(BEFORE), _take(after))
    assert gone["differences"] == [{"what": "table audit_log", "before": 900, "after": None}]
    added = counts.compare(_take(BEFORE), _take({**BEFORE, "rx_lab_test": 0}))
    assert added["same"] and added["new_tables"] == ["rx_lab_test"], "a migration added it"


def test_ac1_the_scans_and_the_queue_keys_are_compared_too():
    fewer = counts.compare(_take(BEFORE), _take(BEFORE, objects=50))
    assert fewer["differences"] == [{"what": "stored scans and pages", "before": 51, "after": 50}]
    keys = counts.compare(_take(BEFORE), _take(BEFORE, keys=0))
    assert keys["differences"][0]["what"] == "queue keys"
    unknown = counts.compare(_take(BEFORE), _take(BEFORE, objects=None))
    assert unknown["same"] and unknown["not_compared"] == ["stored scans and pages"]


def test_ac1_the_counts_file_is_written_whole(tmp_path):
    path = tmp_path / "backup" / "counts.before.json"
    counts.save(path, _take(BEFORE))
    assert counts.load(path)["tables"] == BEFORE
    assert [p.name for p in path.parent.iterdir()] == ["counts.before.json"]


def test_ac1_the_planned_stop_stops_the_writers_then_snapshots_then_counts():
    order = [
        STOP.index("1. stopping the writers"),
        STOP.index('bash "$HERE/snapshot.sh"'),
        STOP.index("counts --save"),
    ]
    assert order == sorted(order)
    for writer in ("balancer.pid", "webapp-*.pid", "worker-*.pid", "gpu-*.pid", "listener*.pid"):
        assert writer in STOP
    assert "SAFE TO STOP" in STOP and "counts.before.json" in STOP
    for stops_the_data in ("pg_ctl", "service postgresql", "redis-cli shutdown", "pkill"):
        assert stops_the_data not in STOP, "the database, the queue and the scans stay up"


def test_ac1_one_start_command_restores_checks_the_counts_and_then_starts_the_services():
    order = [
        START.index('bash "$HERE/bootstrap_pod.sh"'),
        START.index("counts \\\n"),
        START.index('bash "$HERE/start_mlserve.sh"'),
        START.index('bash "$HERE/start_apptier.sh"'),
        START.index("/healthz"),
    ]
    assert order == sorted(order), "the counts are compared before anything writes again"
    assert '--compare "$WS/backup/counts.before.json"' in START
    assert "RESTART CHECK FAILED" in START and "counts.checked.json" in START
    assert "pg_restore" in BOOT and "alembic upgrade head" in BOOT
    assert BOOT.index("pg_restore") < BOOT.index("alembic upgrade head"), "the restore order"


def test_ac1_the_start_command_says_whether_every_part_is_healthy():
    assert 'echo "HEALTH: ${HEALTH:-no answer}"' in START
    assert '"status":"ok"' in START and "WARNING: not every part is healthy yet" in START


def test_ac1_the_scripts_are_valid_bash():
    bash = _bash()
    if bash is None:
        pytest.skip("no bash on this machine")
    for name in ("prepare_stop.sh", "start_all.sh", "bootstrap_pod.sh", "snapshot.sh"):
        done = subprocess.run([bash, "-n", (RUNPOD / name).as_posix()], capture_output=True)
        assert done.returncode == 0, (name, done.stderr)
    assert STOP.startswith("#!/usr/bin/env bash") and "set -euo pipefail" in STOP


def _healthz(monkeypatch, **settings: Any) -> dict[str, Any]:
    from contextlib import contextmanager

    @contextmanager
    def scope():
        yield SimpleNamespace(execute=lambda *a, **k: None)

    base = {
        "mlserve_backend": "stub",
        "job_execution": "inprocess",
        "ocr_host_url": "",
        "redis_url": "redis://127.0.0.1:6379/0",
    }
    monkeypatch.setattr(web, "session_scope", scope)
    monkeypatch.setattr(web, "get_store", lambda: SimpleNamespace(healthy=lambda: True))
    monkeypatch.setattr(web, "get_settings", lambda: SimpleNamespace(**{**base, **settings}))
    answer = web.healthz()
    return {"code": answer.status_code, **json.loads(answer.body)}


def test_ac1_the_health_check_names_every_part(monkeypatch):
    monkeypatch.setattr(web, "_queue_up", lambda url: True)
    monkeypatch.setattr(web, "_http_up", lambda url: True)
    full = _healthz(monkeypatch, job_execution="queue", ocr_host_url="http://127.0.0.1:8079")
    assert full == {
        "code": 200,
        "status": "ok",
        "db": True,
        "s3": True,
        "mlserve": True,
        "queue": True,
        "ocr_host": True,
    }
    plain = _healthz(monkeypatch)
    assert "queue" not in plain and "ocr_host" not in plain, "only the parts this host has"


def test_ac1_one_part_down_is_named_and_the_whole_is_not_ok(monkeypatch):
    monkeypatch.setattr(web, "_queue_up", lambda url: False)
    down = _healthz(monkeypatch, job_execution="queue")
    assert down["code"] == 503 and down["status"] == "degraded" and down["queue"] is False
    assert down["db"] is True


def test_ac1_the_runbook_is_one_page_with_start_stop_restart_and_restore():
    for heading in (
        "## Start",
        "## Stop (planned)",
        "## Restart",
        "## Restore",
        "## Backups",
        "## Health, part by part",
    ):
        assert heading in RUNBOOK, heading
    assert "bash /workspace/cdi/infra/runpod/start_all.sh" in RUNBOOK
    assert "bash /workspace/cdi/infra/runpod/prepare_stop.sh" in RUNBOOK
    assert "The restore order is fixed" in RUNBOOK and len(RUNBOOK.splitlines()) < 130
    for key in ("`db`", "`s3`", "`queue`", "`mlserve`", "`ocr_host`"):
        assert key in RUNBOOK


# ------------------------------------------------------------------ AC2: the model server


def test_ac2_a_stopped_model_server_raises_an_alert_well_within_five_minutes():
    rule = RULE["CdiModelServerDown"]
    assert rule["expr"] == 'up{job="cdi-mlserve"} == 0'
    assert _seconds(rule["for"]) <= 300, "raised before five minutes have passed"
    assert rule["labels"]["severity"] == "page"
    assert rule["annotations"]["runbook_url"] == "docs/alerts.md#cdimodelserverdown"


def test_ac2_prometheus_asks_the_model_server_often_enough_to_notice():
    prom = yaml.safe_load((ROOT / "infra/monitoring/prometheus.yml").read_text("utf-8"))
    jobs = {j["job_name"]: j for j in prom["scrape_configs"]}
    assert "cdi-mlserve" in jobs
    every = _seconds(
        jobs["cdi-mlserve"].get("scrape_interval") or prom["global"]["scrape_interval"]
    )
    assert every + _seconds(RULE["CdiModelServerDown"]["for"]) + 60 <= 300


def test_ac2_the_rule_has_a_case_that_fires_and_one_that_stays_quiet():
    cases = [
        c
        for t in CASES["tests"]
        for c in t["alert_rule_test"]
        if c["alertname"] == "CdiModelServerDown"
    ]
    assert [bool(c["exp_alerts"]) for c in cases] == [False, True]
    assert _seconds(cases[1]["eval_time"]) <= 300, "the case fires inside five minutes"


def test_ac2_the_alert_reaches_the_people_at_the_screens_and_has_its_steps():
    doc = (ROOT / "docs/alerts.md").read_text(encoding="utf-8")
    section = doc.split("## CdiModelServerDown", 1)[1].split("\n## ", 1)[0]
    assert re.search(r"^1\. ", section, re.M)
    assert (ROOT / "src/cdi_adapter/webapp/alerts.py").is_file(), "the banner on every page"


# ------------------------------------------------------------------ AC3: late results


def _counter(c: Any) -> float:
    return float(c._value.get())


def test_ac3_a_result_over_sixty_seconds_is_counted_late():
    late, total = _counter(metrics.RESULTS_LATE), _counter(metrics.RESULTS)
    assert metrics.result_saved(61.0, late_after=60.0) is True
    assert metrics.result_saved(59.0, late_after=60.0) is False
    assert metrics.result_saved(60.0, late_after=60.0) is False, "longer than 60 s, not 60 s"
    assert _counter(metrics.RESULTS_LATE) == late + 1 and _counter(metrics.RESULTS) == total + 3


def test_ac3_the_line_is_sixty_seconds_and_is_a_setting():
    from cdi_adapter.config import Settings

    assert Settings.model_fields["result_late_seconds"].default == 60.0
    assert 60 in metrics.RESULT_BUCKETS, "the histogram has a bucket at the line itself"


def _stages(delay: float = 0.0) -> Stages:
    return Stages(
        ingest=lambda raw, name, mime, patient_id=None: SimpleNamespace(document_id=f"doc-{name}"),
        ocr=lambda d, force_engine=None: None,
        classify=lambda d: {"doc_type": "prescription", "is_handwritten": False},
        extract=lambda d, patient_id=None, expected_patient=None: (
            time.sleep(delay) or {"patient_id": "p-1", "facts": 3}
        ),
        terminology=lambda d: None,
        validate=lambda d: None,
        counts=lambda d: {"facts": 3, "accepted": 2, "in_review": 1},
        merge_identity=lambda p: None,
    )


def test_ac3_it_is_counted_when_the_result_is_saved_by_the_job_runner(monkeypatch):
    """The real runner, with the clock moved: the upload was 75 seconds ago when it is saved."""
    monkeypatch.setenv("CDI_QUALITY_CHECK", "false")
    from cdi_adapter.config import get_settings

    get_settings.cache_clear()
    try:
        late, total = _counter(metrics.RESULTS_LATE), _counter(metrics.RESULTS)
        runner = JobRunner(_stages(), max_workers=2)
        quick = runner.wait(runner.create([("a.png", b"x", None)]).id, timeout=30)
        assert quick.docs[0].status == "done"
        assert _counter(metrics.RESULTS) == total + 1 and _counter(metrics.RESULTS_LATE) == late

        gate = threading.Event()
        slow_stages = _stages()
        real = slow_stages.extract

        def extract(d: Any, **k: Any) -> Any:
            gate.wait(10)
            return real(d, **k)

        slow_stages.extract = extract
        runner = JobRunner(slow_stages, max_workers=2)
        job = runner.create([("b.png", b"x", None)])
        job.started_at -= 75  # the upload was 75 seconds ago
        gate.set()
        assert runner.wait(job.id, timeout=30).docs[0].status == "done"
        assert _counter(metrics.RESULTS_LATE) == late + 1
        assert _counter(metrics.RESULTS) == total + 2
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()


def test_ac3_a_page_that_failed_is_not_a_saved_result():
    def broken(raw: Any, name: Any, mime: Any, patient_id: Any = None) -> Any:
        raise ValueError("cannot identify image file")

    stages = _stages()
    stages.ingest = broken
    total = _counter(metrics.RESULTS)
    os.environ["CDI_QUALITY_CHECK"] = "false"
    from cdi_adapter.config import get_settings

    get_settings.cache_clear()
    try:
        runner = JobRunner(stages, max_workers=2)
        job = runner.wait(runner.create([("bad.png", b"x", None)]).id, timeout=30)
    finally:
        os.environ.pop("CDI_QUALITY_CHECK", None)
        get_settings.cache_clear()
    assert job.docs[0].status == "error" and _counter(metrics.RESULTS) == total


def test_ac3_the_metric_is_on_the_metrics_page_and_has_its_alert():
    page = generate_latest().decode()
    for name in ("cdi_results_late_total", "cdi_results_total", "cdi_result_seconds_bucket"):
        assert name in page, name
    rule = RULE["CdiResultsLate"]
    assert rule["expr"] == "sum(increase(cdi_results_late_total[10m])) > 0"
    assert "longer than 60 seconds" in rule["annotations"]["summary"]


# ------------------------------------------------------------------ AC4: a failed backup


def test_ac4_a_failed_backup_run_shows_at_once_in_the_metric(tmp_path):
    taken = dt.datetime(2026, 10, 13, 9, 0, tzinfo=dt.UTC)
    bk.write_status(tmp_path, ok=True, taken_at=taken, dump="cdi.1.dump", objects={"objects": 51})
    good = generate_latest(_registry(tmp_path)).decode()
    assert "cdi_backup_last_ok 1.0" in good
    bk.write_status(
        tmp_path, ok=False, taken_at=taken + dt.timedelta(minutes=5), error="snapshot.sh line 19"
    )
    failed = generate_latest(_registry(tmp_path)).decode()
    assert "cdi_backup_last_ok 0.0" in failed
    assert bk.read_status(tmp_path)["last_success_dump"] == "cdi.1.dump", "the good one is kept"


def _registry(folder: Path) -> CollectorRegistry:
    reg = CollectorRegistry()
    reg.register(metrics.DrCollector(lambda: (bk.read_status(folder), None)))
    return reg


def test_ac4_the_alert_is_raised_at_the_next_check_not_after_a_waiting_time():
    rule = RULE["CdiBackupFailed"]
    assert rule["expr"] == "max(cdi_backup_last_ok) == 0"
    assert "for" not in rule, "no waiting time: the next check raises it"
    assert rule["labels"]["severity"] == "page"
    cases = [
        c
        for t in CASES["tests"]
        for c in t["alert_rule_test"]
        if c["alertname"] == "CdiBackupFailed"
    ]
    assert [bool(c["exp_alerts"]) for c in cases] == [False, True]
    assert _seconds(cases[1]["eval_time"]) - 120 <= 60, "one check after the failure"


def test_ac4_the_backup_job_records_its_own_failure():
    snapshot = (RUNPOD / "snapshot.sh").read_text(encoding="utf-8")
    assert re.search(r"trap '.*cdi_adapter\.dr backup --failed .* ERR", snapshot)
    assert "set -euo pipefail" in snapshot, "any failing step reaches the trap"


def test_ac4_a_job_that_stopped_running_at_all_is_caught_as_well():
    stale = RULE["CdiBackupStale"]
    assert "> 900" in stale["expr"], "three missed 5-minute runs"
    assert stale["annotations"]["summary"] == "No good backup for 15 minutes."


def test_ac4_both_alerts_have_their_steps():
    doc = (ROOT / "docs/alerts.md").read_text(encoding="utf-8")
    for name in ("CdiBackupFailed", "CdiBackupStale", "CdiResultsLate", "CdiDiskNearlyFull"):
        section = doc.split(f"## {name}", 1)[1].split("\n## ", 1)[0]
        assert re.search(r"^1\. ", section, re.M), name
        assert RULE[name]["annotations"]["runbook_url"] == f"docs/alerts.md#{name.lower()}"


# ------------------------------------------------------------------ the other alerts and metrics


def test_fr4_a_disk_that_is_nearly_full_is_measured_and_alerted():
    reg = CollectorRegistry()
    usage = {
        "/workspace": SimpleNamespace(total=1000, free=50),
        "/": SimpleNamespace(total=1000, free=600),
    }

    def read(path: str) -> Any:
        if path not in usage:
            raise FileNotFoundError(path)
        return usage[path]

    reg.register(metrics.DiskCollector(["/workspace", "/", "/not-here"], read))
    page = generate_latest(reg).decode()
    assert 'cdi_disk_free_ratio{path="/workspace"} 0.05' in page
    assert 'cdi_disk_free_ratio{path="/"} 0.6' in page and "/not-here" not in page
    rule = RULE["CdiDiskNearlyFull"]
    assert rule["expr"] == "min by (path) (cdi_disk_free_ratio) < 0.10" and rule["for"] == "5m"


def test_fr4_the_five_alerts_the_story_names_all_exist():
    for name in (
        "CdiModelServerDown",
        "CdiQueueBacklog",
        "CdiResultsLate",
        "CdiDiskNearlyFull",
        "CdiBackupFailed",
    ):
        assert name in RULE, name


def test_fr3_the_metrics_the_story_names_are_exposed():
    page = generate_latest().decode()
    for name in (
        "cdi_results_total",
        "cdi_result_seconds",
        "cdi_extract_waiting",
        "cdi_stage_failures_total",
    ):
        assert name in page, name
    assert hasattr(metrics, "GpuCollector") and hasattr(metrics, "DiskCollector")
    assert 'metrics.register_once("disk", metrics.DiskCollector())' in (
        ROOT / "src/cdi_adapter/webapp/app.py"
    ).read_text(encoding="utf-8")


# ------------------------------------------------------------------ what needs a pod


def test_ac1_the_database_half_of_a_restart_was_rehearsed_with_identical_counts():
    """The log of ``scripts/restart_rehearsal.py`` on a real PostgreSQL 16: counts taken, the
    dump made and listed, the database DROPPED, restored with the pod's own pg_restore flags,
    migrated - and every table counted again."""
    log = json.loads(
        (ROOT / "docs/evidence/ENT-S4/restart_rehearsal_database.json").read_text("utf-8")
    )
    found = log["comparison"]
    assert log["passed"] and found["same"] and found["differences"] == []
    assert found["tables_compared"] >= 70 and found["rows_before"] == found["rows_after"] > 100
    assert log["before"]["tables"] == log["after"]["tables"]
    assert log["a_removed_row_is_noticed"], "the check itself was checked: one row less is seen"
    assert log["removed_row_difference"][0]["what"] == "table audit_log"
    assert "not the pod scripts" in log["scope"], "and it says what it did not rehearse"


def test_ac1_the_rehearsal_uses_the_pods_own_dump_and_restore_commands():
    script = (ROOT / "scripts/restart_rehearsal.py").read_text(encoding="utf-8")
    for flag in ("--exit-on-error", "--single-transaction", "--clean", "--if-exists", "--no-owner"):
        assert flag in script and flag in BOOT, flag
    assert '"-Fc"' in script and "pg_dump -Fc" in (RUNPOD / "snapshot.sh").read_text("utf-8")
    assert script.index('"--no-owner"') < script.index('_migrate(url)\n            step("restore')
    assert "cdi_rehearsal_" in script and "DROP DATABASE IF EXISTS" in script


@pytest.mark.xfail(
    strict=True,
    reason="AC1 on a POD: prepare_stop.sh and start_all.sh themselves have not been run - "
    "no instance is up. The database half is rehearsed (the test above); the scans, the "
    "queue keys and 'all services healthy' after a real pod restart are not.",
)
def test_a_restart_was_rehearsed_on_a_pod_with_identical_counts():
    log = ROOT / "docs/evidence/ENT-S4/restart_check.json"
    assert log.is_file() and json.loads(log.read_text(encoding="utf-8"))["same"] is True


@pytest.mark.xfail(
    strict=True,
    reason="AC2 / AC4: the alert rules and their synthetic cases "
    "have not been run by Prometheus's own checker (promtool) since they were "
    "changed - it is not on this Windows machine. They are read as YAML here.",
)
def test_the_alert_rules_were_run_by_promtool():
    import shutil

    assert os.environ.get("CDI_PROMTOOL") or shutil.which("promtool")


@pytest.mark.xfail(
    strict=True,
    reason="FR9 / NFR3: Grafana (AGPL) is still downloaded and "
    "started by infra/monitoring/start_monitoring.sh. The metrics are plain "
    "Prometheus, so a customer can attach their own dashboard, but the bundling "
    "has not been removed (the same open decision as in ENT-S1).",
)
def test_grafana_is_not_bundled():
    start = (ROOT / "infra/monitoring/start_monitoring.sh").read_text(encoding="utf-8")
    assert "grafana" not in start.lower()
