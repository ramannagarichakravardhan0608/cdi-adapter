"""SW-S5 end to end, on real PostgreSQL with real model gateways over HTTP.

Two gateways (the real ``mlserve`` app on loopback ports) serve two registered versions; the
models are stubs with different names, since no GPU is here. The champion's stub answers
slowly, so documents are really being read when the switch happens.

* AC1  shadow mode: the real results are the same as without it; the candidate's answers are
       in ``shadow_result``, one per document, and nowhere else;
* AC2  the champion switched while 5 documents run: none lost, none doubled, each names A;
* AC3  one command back: new documents use A again (in seconds; other workers within
       ``CDI_MODEL_ROUTE_TTL_S``).

``CDI_WRITE_EVIDENCE=1`` keeps the drill's record in ``docs/evidence/SW-S5/``.
"""

from __future__ import annotations

import json
import os
import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from sqlalchemy import text

from cdi_adapter import repo
from cdi_adapter.config import get_settings
from cdi_adapter.db import session_scope
from cdi_adapter.extract.service import extract_document
from cdi_adapter.mlserve.app import create_app
from cdi_adapter.modelops import catalog, drill, ops, router, shadow
from tests.test_batch_integration import MemoryStore
from tests.test_extract_integration import _doc_with_ocr
from tests.test_modelops_integration import RUN, Named, rollout  # noqa: F401 - the fixture
from tests.test_validate_integration import _rx

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[1]


class Slow(Named):
    """A stub that takes its time, so documents are mid-read when the champion changes."""

    def __init__(self, model: str, delay: float = 0.0, extra: dict[str, Any] | None = None):
        super().__init__(model)
        self.delay, self.extra = delay, extra or {}

    def generate(self, prompt, image_png=None, *, max_tokens=1024, json_schema=None):
        time.sleep(self.delay)
        out = super().generate(prompt, image_png, max_tokens=max_tokens, json_schema=json_schema)
        if not self.extra:
            return out
        try:
            return json.dumps({**json.loads(out), **self.extra})
        except ValueError:
            return out


@contextmanager
def served(backend: Named, version: str) -> Iterator[str]:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = int(s.getsockname()[1])
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(backend, version=version), host="127.0.0.1", port=port, log_level="error"
        )
    )
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        t.join(10)


@pytest.fixture
def licensed(tmp_path, monkeypatch):
    """The two stub models in the model registry (SW-S2) with a licence text, as the switch and
    shadow mode demand."""
    licence = ROOT / "licences" / "models" / "test-sw-s5i-apache.txt"
    licence.parent.mkdir(parents=True, exist_ok=True)
    licence.write_text("Apache License, Version 2.0 (test copy)\n", encoding="utf-8")
    models = [
        {
            "id": m,
            "roles": ["extraction"],
            "model_id": m,
            "revision": "stub",
            "status": "candidate",
            "licence": {
                "name": "Apache-2.0",
                "text_file": "licences/models/test-sw-s5i-apache.txt",
            },
        }
        for m in (f"sw5-a-{RUN}", f"sw5-b-{RUN}")
    ]
    path = tmp_path / "registry.json"
    path.write_text(json.dumps({"models": models}), encoding="utf-8")
    monkeypatch.setenv("CDI_MODEL_REGISTRY_PATH", str(path))
    get_settings.cache_clear()
    catalog.forget()
    shadow.forget()
    yield
    get_settings.cache_clear()
    catalog.forget()
    licence.unlink()
    if not any(licence.parent.iterdir()):
        licence.parent.rmdir()


def _versions() -> tuple[str, str]:
    a, b = f"sw5a-{RUN}", f"sw5b-{RUN}"
    for v, m in ((a, f"sw5-a-{RUN}"), (b, f"sw5-b-{RUN}")):
        ops.register(actor="test", version=v, model_id=m, revision="stub", backend="stub")
    return a, b


def _new(_: str) -> str:
    doc, _blocks = _doc_with_ocr(_rx())
    return doc


def _extract(doc: str) -> Any:
    return extract_document(doc, store=MemoryStore())


# ------------------------------------------------------------------ AC1


def test_ac1_shadow_mode_leaves_the_real_result_as_it_is(rollout, licensed, monkeypatch):  # noqa: F811
    a, b = _versions()
    candidate = Slow(f"sw5-b-{RUN}", extra={"advice": ["CANDIDATE ONLY - never shown"]})
    with served(Slow(f"sw5-a-{RUN}"), a) as url_a, served(candidate, b) as url_b:
        ops.deploy(a, url_a, actor="test")
        lines = _rx()
        plain, _ = _doc_with_ocr(lines)
        _extract(plain)  # no shadow
        for k, v in {
            "CDI_SHADOW_EXTRACTION": f"sw5-b-{RUN}",
            "CDI_SHADOW_URL": url_b,
            "CDI_SHADOW_INLINE": "true",
        }.items():
            monkeypatch.setenv(k, v)
        get_settings.cache_clear()
        shadow.forget()
        shadowed, _ = _doc_with_ocr(lines)
        _extract(shadowed)

    def facts(doc: str) -> list[tuple[Any, ...]]:
        with session_scope() as c:
            return sorted(
                (f["fact_type"], f["local_text"], f["value_text"], f["value_num"])
                for f in repo.get_facts_for_document(c, doc)
            )

    assert facts(shadowed) == facts(plain)  # the real result: the same, shadow or not
    with session_scope() as c:
        rows = (
            c.execute(
                text(
                    "SELECT document_id::text, model_id, answer, error, agreement "
                    "FROM shadow_result WHERE document_id IN (:p, :s)"
                ),
                {"p": plain, "s": shadowed},
            )
            .mappings()
            .all()
        )
        model = c.execute(
            text("SELECT model_name FROM extraction WHERE document_id = :d"), {"d": shadowed}
        ).scalar_one()
        stored = json.dumps(
            [
                dict(r)
                for r in c.execute(
                    text("SELECT payload FROM extraction WHERE document_id = :d"), {"d": shadowed}
                ).mappings()
            ],
            default=str,
        )
    assert model == f"sw5-a-{RUN}"
    assert [r["document_id"] for r in rows] == [shadowed]  # only the shadowed one, once
    row = rows[0]
    assert row["model_id"] == f"sw5-b-{RUN}" and row["error"] is None
    assert row["answer"]["advice"] == ["CANDIDATE ONLY - never shown"]
    assert row["agreement"]["compared"] > 0
    assert "CANDIDATE ONLY" not in stored  # the candidate's words are in no real record
    with session_scope() as c:
        assert shadow.report(c, f"sw5-b-{RUN}")[0]["documents"] == 1


# ------------------------------------------------------------------ AC2, AC3: the recorded drill


def test_ac2_ac3_the_swap_drill(rollout, licensed, monkeypatch):  # noqa: F811
    monkeypatch.setenv("CDI_MODEL_ROUTE_TTL_S", "2")
    get_settings.cache_clear()
    a, b = _versions()
    with (
        served(Slow(f"sw5-a-{RUN}", delay=3.0), a) as url_a,
        served(Slow(f"sw5-b-{RUN}"), b) as url_b,
    ):
        ops.deploy(a, url_a, actor="test")
        record = drill.run(
            a,
            b,
            url_a,
            url_b,
            _new,
            _extract,
            actor="drill",
            running=5,
            wait_s=60,
            start_timeout_s=30,
        )
        # AC3 for the OTHER workers: a cached route is re-read within CDI_MODEL_ROUTE_TTL_S
        stale = router.live()
        assert stale.state is not None and stale.state.primary_version == a
    failed = [c for c in record["checks"] if not c["ok"]]
    assert not failed, failed
    names = [c["check"] for c in record["checks"]]
    assert "switch: no document lost" in names and "switch: no document duplicated" in names
    assert record["steps"][2]["seconds"] < 60
    with session_scope() as c:
        actions = [
            r[0]
            for r in c.execute(
                text("SELECT action FROM model_rollout ORDER BY id DESC LIMIT 2")
            ).all()
        ]
        audit = c.execute(
            text(
                "SELECT detail FROM audit_log WHERE entity = 'model_rollout' "
                "AND detail->>'action' = 'switch' ORDER BY at DESC LIMIT 1"
            )
        ).scalar_one()
    assert actions == ["rollback", "switch"]
    assert audit["from"] == a and audit["forced"] is True and audit["keep_previous_until"]
    if os.environ.get("CDI_WRITE_EVIDENCE"):
        out = ROOT / "docs" / "evidence" / "SW-S5"
        out.mkdir(parents=True, exist_ok=True)
        (out / "drill-local-stub-models.json").write_text(
            json.dumps(
                {
                    **record,
                    "where": "integration test: stub models on loopback gateways, "
                    "PostgreSQL 16; not a GPU drill",
                },
                indent=1,
                default=str,
            )
            + "\n",
            "utf-8",
        )


def test_a_cached_route_follows_a_rollback_within_the_ttl(rollout, licensed, monkeypatch):  # noqa: F811
    """AC3 for a worker that did not run the command: it still holds the old route, and moves
    within CDI_MODEL_ROUTE_TTL_S without a restart."""
    a, b = _versions()
    with served(Slow(f"sw5-a-{RUN}"), a) as url_a, served(Slow(f"sw5-b-{RUN}"), b) as url_b:
        ops.deploy(a, url_a, actor="test")
        ops.switch(b, url_b, actor="test", force=True, reason="ttl test", wait_s=5)
        monkeypatch.setenv("CDI_MODEL_ROUTE_TTL_S", "1")
        get_settings.cache_clear()
        router.reset()
        assert router.route("doc").version == b  # this worker now caches B
        held = router._cache
        assert held is not None
        ops.rollback(actor="test", reason="ttl test")  # the command resets ITS process only
        router._cache = (time.monotonic(), held[1])  # ... the other worker: cached just now
        assert router.route("doc").version == b  # still B: not re-read yet
        time.sleep(1.2)
        assert router.route("doc").version == a  # re-read: A again, no restart


def test_a_switch_is_refused_without_a_contest_won_or_a_licence(rollout, licensed, monkeypatch):  # noqa: F811
    a, b = _versions()
    with served(Slow(f"sw5-a-{RUN}"), a) as url_a, served(Slow(f"sw5-b-{RUN}"), b) as url_b:
        ops.deploy(a, url_a, actor="test")
        with pytest.raises(ops.RolloutError, match="has not won a binding contest"):
            ops.switch(b, url_b, actor="test", contest_index=Path("no-such-index.jsonl"))
        with pytest.raises(ops.RolloutError, match="--force needs a --reason"):
            ops.switch(b, url_b, actor="test", force=True)
        monkeypatch.setenv("CDI_MODEL_REGISTRY_PATH", str(ROOT / "models" / "registry.json"))
        get_settings.cache_clear()
        catalog.forget()
        with pytest.raises(ops.RolloutError, match="has no licence entry"):
            ops.switch(b, url_b, actor="test", force=True, reason="no licence")
        with session_scope() as c:
            assert ops.registry.current(c).primary_version == a  # nothing changed
