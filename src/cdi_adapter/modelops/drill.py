"""The swap drill (backlog SW-S5 NFR3; ``docs/drill.md``): shadow, switch, roll back - recorded.

One function, run two ways: by the integration test with stub models on loopback gateways,
and on the GPU host with real models (``scripts/model_swap_drill.py``). Each step is checked,
and the record is what the quarterly drill keeps as evidence:

1. **shadow** - B answers in shadow while A is the champion: every real result is A's, and B's
   answers are saved apart, one per document (AC1);
2. **switch while documents run** - ``running`` documents are being read by A when B is made
   champion: each finishes once, on A, and says so; nothing is lost or doubled; the next
   document goes to B (AC2);
3. **roll back** - one command: new documents are A's again, and how long that took (AC3).

The previous model's gateway is never stopped by the drill: a rollback always has somewhere to
go back to.
"""

from __future__ import annotations

import datetime as dt
import os
import threading
import time
from collections.abc import Callable
from typing import Any

from sqlalchemy import text

from cdi_adapter.config import get_settings
from cdi_adapter.db import session_scope
from cdi_adapter.modelops import ops, registry, router, shadow

SHADOW_ENV = ("CDI_SHADOW_EXTRACTION", "CDI_SHADOW_URL", "CDI_SHADOW_PERCENT", "CDI_SHADOW_INLINE")


def _made_by(document_id: str) -> dict[str, Any]:
    """What the database holds for one document: its extractions, its extract runs, the model
    the result names."""
    from cdi_adapter.result import _engines

    with session_scope() as c:
        extractions = c.execute(
            text("SELECT model_version, model_name FROM extraction WHERE document_id = :d"),
            {"d": document_id},
        ).all()
        runs = c.execute(
            text(
                "SELECT status, model_version FROM pipeline_run WHERE document_id = :d "
                "AND stage = 'extract'"
            ),
            {"d": document_id},
        ).all()
        engines = _engines(c, document_id)
    named = [
        e["name"]
        for e in engines
        if e["stage"] == "extract" and e["role"] == "vision-language model"
    ]
    return {
        "extractions": [tuple(r) for r in extractions],
        "runs": [tuple(r) for r in runs],
        "result_names": named,
    }


def run(
    a: str,
    b: str,
    url_a: str,
    url_b: str,
    new_document: Callable[[str], str],
    extract: Callable[[str], Any],
    *,
    actor: str = "drill",
    running: int = 5,
    shadow_docs: int = 3,
    wait_s: float = 600.0,
    start_timeout_s: float = 120.0,
) -> dict[str, Any]:
    """The drill. ``a`` is (or becomes) the champion, ``b`` the candidate; both gateways must
    already serve them. ``new_document(tag)`` makes a document ready to extract."""
    checks: list[dict[str, Any]] = []
    steps: list[dict[str, Any]] = []

    def check(name: str, ok: bool, **seen: Any) -> None:
        checks.append({"check": name, "ok": bool(ok), **seen})

    with session_scope() as c:
        live = registry.current(c)
        va, vb = registry.get_version(c, a), registry.get_version(c, b)
    assert va is not None and vb is not None, "register both versions first"
    if live is not None and live.canary_version is not None:
        ops.rollback(actor=actor, reason="SW-S5 drill: no canary during the drill")
    if live is None or live.primary_version != a:
        ops.deploy(
            a,
            url_a,
            actor=actor,
            force=live is not None,
            reason="SW-S5 drill" if live is not None else None,
        )

    # 1. shadow ----------------------------------------------------------------------------
    saved = {k: os.environ.get(k) for k in SHADOW_ENV}
    os.environ.update(
        {
            "CDI_SHADOW_EXTRACTION": f"{vb['model_id']}@{vb['revision']}",
            "CDI_SHADOW_URL": url_b,
            "CDI_SHADOW_PERCENT": "100",
            "CDI_SHADOW_INLINE": "true",
        }
    )
    get_settings.cache_clear()
    shadow.forget()
    try:
        on = shadow.configured() is not None
        docs = [new_document(f"shadow-{i}") for i in range(shadow_docs)]
        for d in docs:
            extract(d)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        get_settings.cache_clear()
        shadow.forget()
    with session_scope() as c:
        rows = c.execute(
            text(
                "SELECT document_id::text, model_id, error FROM shadow_result "
                "WHERE document_id = ANY(CAST(:d AS uuid[]))"
            ),
            {"d": docs},
        ).all()
    made = {d: _made_by(d) for d in docs}
    check("shadow mode could start (candidate registered with its licence text)", on)
    check(
        "shadow: every real result is the champion's",
        all(m["extractions"] == [(a, va["model_id"])] for m in made.values()),
    )
    check(
        "shadow: the candidate's answer saved apart, once per document",
        sorted(r[0] for r in rows) == sorted(docs)
        and all(r[1] == vb["model_id"] and r[2] is None for r in rows),
        shadow_rows=len(rows),
    )
    steps.append({"step": "shadow", "documents": len(docs), "shadow_rows": len(rows)})

    # 2. switch while documents are running ------------------------------------------------
    docs = [new_document(f"switch-{i}") for i in range(running)]
    failed: dict[str, str] = {}

    def one(d: str) -> None:
        try:
            extract(d)
        except Exception as exc:  # noqa: BLE001 - recorded, checked below
            failed[d] = f"{type(exc).__name__}: {exc}"

    threads = [threading.Thread(target=one, args=(d,)) for d in docs]
    for t in threads:
        t.start()
    deadline = time.monotonic() + start_timeout_s
    started = 0
    while time.monotonic() < deadline:
        with session_scope() as c:
            started = len([r for r in ops.in_flight(c, a) if str(r["document_id"]) in docs])
        if started == running:
            break
        time.sleep(0.05)
    t0 = time.perf_counter()
    st, info = ops.switch(
        b,
        url_b,
        actor=actor,
        force=True,
        wait_s=wait_s,
        poll_s=0.2,
        reason="SW-S5 drill: switch while documents run",
    )
    switch_s = round(time.perf_counter() - t0, 3)
    for t in threads:
        t.join(wait_s)
    made = {d: _made_by(d) for d in docs}
    check(
        f"switch: {running} documents were being read by the old champion",
        started == running,
        running=started,
    )
    check(
        "switch: waited for every one still running on the old champion (drain)",
        info["drain"]["running_at_switch"] >= 1 and not info["drain"]["still_running"],
        drain=info["drain"],
    )
    check(
        "switch: no document lost",
        not failed and all(m["extractions"] for m in made.values()),
        failed=failed,
    )
    check(
        "switch: no document duplicated",
        all(
            len(m["extractions"]) == 1 and [r for r in m["runs"] if r[0] == "ok"] == [("ok", a)]
            for m in made.values()
        ),
    )
    check(
        "switch: each result names the model that made it",
        all(m["result_names"] == [va["model_id"]] for m in made.values()),
    )
    after = new_document("after-switch")
    extract(after)
    check(
        "switch: the next document goes to the new champion",
        _made_by(after)["extractions"] == [(b, vb["model_id"])],
    )
    steps.append(
        {
            "step": "switch",
            "to": st.primary_version,
            "seconds": switch_s,
            "previous_kept_until": info["keep_previous_until"],
        }
    )

    # 3. roll back -------------------------------------------------------------------------
    t0 = time.perf_counter()
    st = ops.rollback(actor=actor, reason="SW-S5 drill: one-step rollback")
    reached = None
    for i in range(10):
        d = new_document(f"rollback-{i}")
        extract(d)
        if _made_by(d)["extractions"] == [(a, va["model_id"])]:
            reached = round(time.perf_counter() - t0, 3)
            break
    check(
        "rollback: new documents use the previous model again within minutes",
        reached is not None and reached < 300,
        seconds=reached,
        other_workers_within_s=get_settings().model_route_ttl_s,
    )
    steps.append({"step": "rollback", "to": st.primary_version, "seconds": reached})
    router.reset()
    return {
        "drill": "SW-S5 swap drill",
        "at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "champion": a,
        "candidate": b,
        "steps": steps,
        "checks": checks,
        "passed": all(c["ok"] for c in checks),
    }
