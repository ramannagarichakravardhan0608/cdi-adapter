"""Shadow mode: a candidate answers real pages, and nobody sees it (backlog SW-S5 AC1).

With ``CDI_SHADOW_EXTRACTION`` naming a registered candidate and ``CDI_SHADOW_URL`` its own
gateway, every extraction the champion finishes is asked again of the candidate - the same
page, the same words, the same answer format - AFTER the real result is saved:

* the candidate's answer goes to ``shadow_result`` and nowhere else: not into a fact, a result,
  a review screen or an export. The real result is the champion's, unchanged;
* a candidate that fails, is slow or is down costs nothing but a row saying so: no error
  reaches the document;
* ``CDI_SHADOW_PERCENT`` sends a share of documents (the same document always the same way);
* the candidate must be in the model registry with its licence text saved (as for a contest,
  SW-S4), and its gateway must say it serves that model - otherwise shadow mode stays off and
  says why, once;
* each row says how far the candidate agreed with the champion, field by field; ``report``
  sums it up per candidate (``python -m cdi_adapter.modelops shadow-report``).

The shadow call runs on a thread of its own, so the real document never waits for it
(``CDI_SHADOW_INLINE=true`` runs it in place: tests, a replay of old documents).
"""

from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import text

from cdi_adapter import metrics
from cdi_adapter.config import get_settings
from cdi_adapter.logging import get_logger

log = get_logger(__name__)

IGNORED = ("confidence", "evidence", "bbox", "observation_ids", "quote")


@dataclass(frozen=True)
class Shadow:
    model_id: str
    revision: str | None
    registered_as: str
    url: str
    percent: int


_lock = threading.Lock()
_said: set[str] = set()
_pool: ThreadPoolExecutor | None = None


def _once(key: str, **kw: Any) -> None:
    with _lock:
        if key in _said:
            return
        _said.add(key)
    log.warning("shadow_off", **kw)


def forget() -> None:
    """Forget what was said and checked (tests; a settings change in the same process)."""
    with _lock:
        _said.clear()


def configured() -> Shadow | None:
    """The shadow candidate, when shadow mode is on and allowed; else None (and why, once)."""
    s = get_settings()
    name = s.shadow_extraction.strip()
    if not name or s.shadow_percent <= 0:
        return None
    if not s.shadow_url.strip():
        _once(
            "no-url",
            candidate=name,
            why="CDI_SHADOW_URL is empty: the candidate needs its own gateway",
        )
        return None
    from cdi_adapter.modelops import catalog
    from cdi_adapter.modelops.contest import licence_refusal

    why = licence_refusal(name, "extraction")
    if why:
        _once(f"licence:{name}", candidate=name, why=why)
        return None
    entry = catalog.find(name)
    assert entry is not None
    return Shadow(
        entry.model_id, entry.revision, entry.id, s.shadow_url.strip().rstrip("/"), s.shadow_percent
    )


def chosen(document_id: UUID | str, shadow: Shadow) -> bool:
    from cdi_adapter.modelops.router import bucket

    return shadow.percent >= 100 or bucket(document_id) < shadow.percent


def _client(shadow: Shadow) -> Any:
    """The candidate's own gateway - with NO fallback: an answer from another model would be
    saved under the candidate's name."""
    from cdi_adapter.ml.client import HttpMLClient

    client = HttpMLClient(shadow.url)
    client.fallbacks = []
    client.model = shadow.model_id
    return client


def serving(shadow: Shadow) -> str | None:
    """Why the gateway is not the candidate's, or None. A gateway serving another model must
    not be recorded as the candidate."""
    from cdi_adapter.modelops.ops import gateway_health

    h = gateway_health(shadow.url)
    if h is None:
        return f"{shadow.url} does not answer"
    if h.get("model") != shadow.model_id:
        return f"{shadow.url} serves {h.get('model')}, not {shadow.model_id}"
    return None


def _leaves(node: Any, path: str = "") -> dict[str, Any]:
    if isinstance(node, dict):
        out: dict[str, Any] = {}
        for k, v in node.items():
            if k.startswith("_") or k in IGNORED:
                continue
            out.update(_leaves(v, f"{path}.{k}" if path else k))
        return out
    if isinstance(node, list):
        out = {}
        for i, v in enumerate(node):
            out.update(_leaves(v, f"{path}[{i}]"))
        return out
    return {path: " ".join(str(node).split()).lower() if isinstance(node, str) else node}


def agreement(champion: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    """Field by field: of the values the champion gave, how many the candidate gave the same;
    and what the candidate gave that the champion did not. Confidence and evidence aside."""
    a, b = _leaves(champion), _leaves(candidate)
    given = {k for k, v in a.items() if v not in (None, "")}
    same = sorted(k for k in given if b.get(k) == a[k])
    differ = sorted(k for k in given if b.get(k) != a[k])
    extra = sorted(k for k, v in b.items() if v not in (None, "") and k not in given)
    return {
        "compared": len(given),
        "same": len(same),
        "rate": round(len(same) / len(given), 4) if given else None,
        "differ": differ[:30],
        "only_candidate": extra[:30],
    }


def save(row: dict[str, Any]) -> None:
    from cdi_adapter.db import session_scope

    with session_scope() as conn:
        conn.execute(
            text(
                "INSERT INTO shadow_result (document_id, role, model_id, revision, registered_as, "
                "gateway, champion_model, champion_version, prompt_version, answer, error, "
                "seconds, agreement) VALUES (:document_id, 'extraction', :model_id, :revision, "
                ":registered_as, :gateway, :champion_model, :champion_version, :prompt_version, "
                "CAST(:answer AS jsonb), :error, :seconds, CAST(:agreement AS jsonb))"
            ),
            {
                **row,
                "answer": None if row["answer"] is None else json.dumps(row["answer"]),
                "agreement": None if row["agreement"] is None else json.dumps(row["agreement"]),
            },
        )


def answer(
    shadow: Shadow,
    document_id: UUID | str,
    request: Any,
    champion_answer: dict[str, Any],
    champion_model: str | None,
    champion_version: str | None,
    *,
    client: Any = None,
) -> dict[str, Any]:
    """Ask the candidate, compare, save. Never raises: a failure is a row with its error."""
    from cdi_adapter.extract.prompt import prompt_version

    t0 = time.perf_counter()
    row: dict[str, Any] = {
        "document_id": str(document_id),
        "model_id": shadow.model_id,
        "revision": shadow.revision,
        "registered_as": shadow.registered_as,
        "gateway": shadow.url,
        "champion_model": champion_model,
        "champion_version": champion_version,
        "prompt_version": prompt_version("prescription"),
        "answer": None,
        "error": None,
        "seconds": None,
        "agreement": None,
    }
    try:
        if client is None:
            wrong = serving(shadow)
            if wrong:
                raise RuntimeError(wrong)
            client = _client(shadow)
        got = client.vlm_json(
            request.image_png, request.prompt, request.schema, max_tokens=request.max_tokens
        )
        got = dict(got)
        got.pop("_raw_answer", None)
        row["answer"] = got
        row["agreement"] = agreement(champion_answer, got)
        outcome = "answered"
    except Exception as exc:  # noqa: BLE001 - the candidate's failure is the candidate's only
        row["error"] = f"{type(exc).__name__}: {exc}"[:500]
        outcome = "failed"
    row["seconds"] = round(time.perf_counter() - t0, 3)
    try:
        save(row)
    except Exception as exc:  # noqa: BLE001 - never the real document's problem
        log.warning("shadow_not_saved", document_id=str(document_id), error=str(exc))
        outcome = "not_saved"
    metrics.SHADOW_ANSWERS.labels(outcome).inc()
    return row


def after_extraction(
    document_id: UUID | str,
    request: Any,
    champion_answer: dict[str, Any],
    champion_model: str | None,
    champion_version: str | None,
) -> bool:
    """Called once the real result is saved. True when the candidate was asked (or will be)."""
    try:
        shadow = configured()
        if shadow is None or request is None or not chosen(document_id, shadow):
            return False
        args = (
            shadow,
            document_id,
            request,
            dict(champion_answer),
            champion_model,
            champion_version,
        )
        if get_settings().shadow_inline:
            answer(*args)
        else:
            _executor().submit(answer, *args)
        return True
    except Exception as exc:  # noqa: BLE001 - shadow mode never touches the real document
        log.warning("shadow_skipped", document_id=str(document_id), error=str(exc))
        return False


def _executor() -> ThreadPoolExecutor:
    global _pool
    with _lock:
        if _pool is None:
            _pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="shadow")
        return _pool


def report(conn: Any, model_id: str | None = None) -> list[dict[str, Any]]:
    """Per candidate: how many documents, how many failed, how far it agreed, how fast."""
    rows = (
        conn.execute(
            text(
                "SELECT model_id, registered_as, count(*) AS documents, "
                "count(*) FILTER (WHERE error IS NOT NULL) AS failed, "
                "avg((agreement->>'rate')::float) AS agreement, avg(seconds) AS seconds, "
                "min(created_at) AS first, max(created_at) AS last FROM shadow_result "
                "WHERE (CAST(:m AS text) IS NULL OR model_id = :m) "
                "GROUP BY model_id, registered_as ORDER BY model_id"
            ),
            {"m": model_id},
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]
