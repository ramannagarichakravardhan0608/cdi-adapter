"""RD-S5 acceptance: ten prescriptions at once, each finished within a minute, on one GPU.

This story is measured on a GPU host, not in a unit test: its proof is the reports the burst
harness wrote there (``docs/evidence/RD-S5/``). What is tested here is the instrument - that
``loadtest_burst.judge`` counts lost, doubled, waiting and the 60-second share right, and that the
report carries the MEASURED tag, the GPU and the date - and the one change the story made to the
product: a document read and waiting for a model slot now says "waiting" on the screen.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from cdi_adapter import loadtest_burst as B

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs" / "evidence" / "RD-S5"
T0 = datetime(2026, 10, 10, 6, 0, tzinfo=UTC)


def _row(n: int, seconds: float | None, *, state: str = "review", status: str = "done",
         doc: str | None = None, attempts: int = 1) -> dict[str, Any]:  # fmt: skip
    return {"id": f"job-{n}", "state": state, "created_at": T0, "attempts": attempts,
            "finished_at": T0 + timedelta(seconds=seconds) if seconds is not None else None,
            "docs": [{"status": status, "document_id": doc or f"doc-{n}"}]}  # fmt: skip


def _burst(n: int = 10, **kw: Any) -> B.Burst:
    return B.Burst(label="test", count=n, sent_seconds=0.2, price_per_hour=0.366, date="2026-10-10",
                   gpu={"name": "NVIDIA RTX PRO 4500 Blackwell", "total_mib": 32623,
                        "used_mib": [16100, 16900, 17400, 17000]}, **kw)  # fmt: skip


def _judge(seconds: list[float | None], **kw: Any) -> dict[str, Any]:
    rows = kw.pop("rows", None) or [_row(i, s) for i, s in enumerate(seconds)]
    per_doc = kw.pop("per_doc", None) or {f"doc-{i}": (1, 5, 1) for i in range(len(rows))}
    return B.judge(_burst(len(rows)), rows, waiting=kw.pop("waiting", [False] * len(rows)),
                   per_doc=per_doc)  # fmt: skip


# ------------------------------------------------------------------ AC1


def test_ac1_nine_of_ten_within_a_minute_is_not_95_percent() -> None:
    rep = _judge([20, 25, 30, 35, 40, 45, 50, 55, 58, 75])
    assert rep["within_60s"] == 9 and rep["within_60s_share"] == 0.9
    assert rep["meets_95_within_60"] is False


def test_ac1_ten_of_ten_within_a_minute_meets_it() -> None:
    rep = _judge([10 + 4 * i for i in range(10)])
    assert rep["meets_95_within_60"] is True and rep["lost"] == 0
    assert rep["seconds"] == {"median": 26.0, "p95": 46.0, "fastest": 10, "slowest": 46}


def test_ac1_a_document_never_finished_is_lost_and_not_within() -> None:
    rep = _judge([10] * 9 + [None])
    assert rep["lost"] == 1 and rep["within_60s"] == 9 and not rep["meets_95_within_60"]
    rep = _judge([], rows=[_row(i, 10) for i in range(9)] + [_row(9, 12, state="error",
                                                                  status="error")])  # fmt: skip
    assert rep["lost"] == 1 and "lost: ended in error" in rep["documents"][9]["problems"][0]


# ------------------------------------------------------------------ AC2


def test_ac2_the_later_ones_are_seen_waiting() -> None:
    rep = _judge([10 * i for i in range(1, 21)], waiting=[i >= 2 for i in range(20)])
    assert rep["count"] == 20 and rep["seen_waiting"] == 18 and rep["lost"] == 0


def test_ac2_a_document_waiting_for_the_model_says_so_on_the_screen() -> None:
    from cdi_adapter.webapp import jobs, page

    assert jobs.WAITING == "waiting"
    source = inspect.getsource(jobs.JobRunner._run_traced)
    assert "d.step = WAITING" in source
    screen = inspect.getsource(page)
    assert "if (d.step === 'waiting') return 'waiting';" in screen
    # and it is checked before "reading", which every waiting document has done
    assert screen.index("d.step === 'waiting'") < screen.index(
        "includes('ingest')) return 'reading'"
    )


# ------------------------------------------------------------------ AC3


def test_ac3_a_document_with_two_review_items_is_doubled() -> None:
    rep = _judge([10] * 3, per_doc={"doc-0": (1, 5, 1), "doc-1": (2, 5, 2), "doc-2": (2, 5, 1)})
    assert rep["doubled"] == 1 and rep["documents"][1]["problems"] == ["doubled: 2 review items"]
    assert rep["documents"][2]["extractions"] == 2 and not rep["documents"][2]["problems"]


def test_ac3_the_harness_kills_a_gpu_worker_and_starts_it_again() -> None:
    source = (ROOT / "scripts" / "burst_test.py").read_text(encoding="utf-8")
    assert "os.kill(pid, signal.SIGKILL)" in source and "subprocess.Popen(cmd, env=" in source
    assert '"--", "-Q cdi-gpu"' in source  # a pattern starting with - is not an option


# ------------------------------------------------------------------ AC5


def test_ac5_the_report_is_tagged_measured_with_the_gpu_and_the_date() -> None:
    rep = _judge([20] * 10)
    assert (rep["tag"], rep["gpu"], rep["date"]) == (
        "MEASURED", "NVIDIA RTX PRO 4500 Blackwell", "2026-10-10")  # fmt: skip
    assert rep["gpu_memory_mib"] == {"before": 16100, "peak": 17400}
    assert rep["cost_per_document_usd"] == round(0.366 * 20 / 3600 / 10, 5)
    page = B.render(rep)
    assert "**MEASURED** on NVIDIA RTX PRO 4500 Blackwell (32623 MiB), 2026-10-10." in page
    assert "Cost per document: $" in page and "GPU memory: 16100 MiB before" in page


def test_ac5_the_measured_reports_are_kept_as_evidence() -> None:
    reports = sorted(EVIDENCE.glob("*.md")) if EVIDENCE.exists() else []
    assert reports, "the reports of the runs on the GPU host"
    for path in reports:
        if path.name == "README.md":
            continue
        assert "**MEASURED** on " in path.read_text(encoding="utf-8"), path.name


@pytest.mark.parametrize("share,ok", [(0.95, True), (0.949, False)])
def test_the_target_is_95_percent(share: float, ok: bool) -> None:
    assert (share >= B.TARGET_SHARE) is ok and B.WITHIN_S == 60.0


# ------------------------------------------------------------------ AC4: the serial server behind


def _client(monkeypatch: Any, urls: str, fallback: str, answer: Any) -> Any:
    import httpx

    from cdi_adapter.config import get_settings
    from cdi_adapter.ml import client as C

    monkeypatch.setenv("CDI_MLSERVE_URL", urls)
    monkeypatch.setenv("CDI_MLSERVE_FALLBACK_URL", fallback)
    get_settings.cache_clear()
    monkeypatch.setattr(C, "_down", {})
    return C.HttpMLClient(transport=httpx.MockTransport(answer))


def _served_by(seen: list[str]) -> Any:
    import httpx

    def answer(request: Any) -> Any:
        host = f"{request.url.host}:{request.url.port}"
        seen.append(host)
        return httpx.Response(200, json={"text": "ok", "model": "m", "usage": {}})

    return answer


def test_ac4_the_serial_server_takes_no_share_while_the_batching_server_is_up(monkeypatch) -> None:
    from cdi_adapter.config import get_settings

    seen: list[str] = []
    client = _client(monkeypatch, "http://127.0.0.1:8077", "http://127.0.0.1:8078",
                     _served_by(seen))  # fmt: skip
    try:
        for _ in range(12):
            client.generate("x")
    finally:
        get_settings.cache_clear()
    assert seen == ["127.0.0.1:8077"] * 12  # found under load: listed as a replica it took half


def test_ac4_with_the_batching_server_stopped_documents_go_to_the_serial_one(monkeypatch) -> None:
    import httpx

    from cdi_adapter import metrics
    from cdi_adapter.config import get_settings

    seen: list[str] = []

    def answer(request: Any) -> Any:
        host = f"{request.url.host}:{request.url.port}"
        seen.append(host)
        if host.endswith(":8077"):
            raise httpx.ConnectError("connection refused", request=request)
        return httpx.Response(200, json={"text": "ok", "model": "m", "usage": {}})

    before = metrics.MLSERVE_FAILOVERS._value.get()
    client = _client(monkeypatch, "http://127.0.0.1:8077", "http://127.0.0.1:8078", answer)
    try:
        assert client.generate("x") == "ok" and client.replica == "http://127.0.0.1:8078"
        assert client.generate("y") == "ok"
    finally:
        get_settings.cache_clear()
    assert seen == ["127.0.0.1:8077", "127.0.0.1:8078", "127.0.0.1:8078"]  # not asked again
    assert metrics.MLSERVE_FAILOVERS._value.get() == before + 1  # what the alert counts


def test_ac4_without_a_fallback_the_replicas_work_as_before(monkeypatch) -> None:
    from cdi_adapter.config import Settings, get_settings

    seen: list[str] = []
    client = _client(monkeypatch, "http://127.0.0.1:8077,http://127.0.0.1:8079", "",
                     _served_by(seen))  # fmt: skip
    try:
        for _ in range(4):
            client.generate("x")
    finally:
        get_settings.cache_clear()
    assert sorted(set(seen)) == ["127.0.0.1:8077", "127.0.0.1:8079"]  # shared, as E10-S9
    assert Settings(_env_file=None).mlserve_fallback_url == ""


def test_ac4_an_alert_says_the_batching_server_stopped_and_that_the_fallback_is_used() -> None:
    import yaml

    rules = yaml.safe_load((ROOT / "infra" / "monitoring" / "rules" / "cdi-alerts.yml").read_text(
        encoding="utf-8"))["groups"][0]["rules"]  # fmt: skip
    by = {r["alert"]: r for r in rules}
    assert by["CdiModelServerDown"]["expr"] == 'up{job="cdi-mlserve"} == 0'
    assert by["CdiModelServerDown"]["labels"]["severity"] == "page"
    assert "cdi_mlserve_failovers_total" in by["CdiModelServerFailover"]["expr"]


def test_ac4_the_routed_client_of_a_rollout_falls_back_too(monkeypatch) -> None:
    """Found on the GPU host: extraction calls go through the routed client (E10-S5), which
    passes the gateway's address in - and the fallback was then never used."""
    import httpx

    from cdi_adapter.config import get_settings
    from cdi_adapter.ml import client as C

    def answer(request: Any) -> Any:
        if request.url.port == 8077:
            raise httpx.ConnectError("connection refused", request=request)
        return httpx.Response(200, json={"text": "ok", "model": "m", "usage": {}})

    monkeypatch.setenv("CDI_MLSERVE_FALLBACK_URL", "http://127.0.0.1:8078")
    get_settings.cache_clear()
    monkeypatch.setattr(C, "_down", {})
    try:
        given = C.HttpMLClient("http://127.0.0.1:8077", transport=httpx.MockTransport(answer))
        assert given.generate("x") == "ok" and given.replica == "http://127.0.0.1:8078"
    finally:
        get_settings.cache_clear()
