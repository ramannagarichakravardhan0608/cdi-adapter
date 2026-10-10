"""Judging a burst (backlog RD-S5): N prescriptions sent in the same second, on one GPU.

``scripts/burst_test.py`` sends them through the real pipeline on the GPU host and gathers what
happened; :func:`judge` turns that into the figures the story asks for and :func:`render` into
the page a person reads. Kept apart from the script so the arithmetic is tested on its own.

* **seconds per document** - from the upload being accepted to its result being saved (the job
  finished: ``web_job.created_at`` -> ``finished_at``, both on the database's clock);
* **within 60 s** - the share of documents saved within 60 seconds (AC1 asks for 95%);
* **lost** - a document whose job never finished, ended in error, or has no document;
* **doubled** - a document with more than one review item, or a job counted twice;
* **waiting** - whether the document was seen waiting for a model slot (AC2);
* **GPU memory** - the most used during the run, and before it;
* **cost per document** - the host's price for the run's wall time, divided by the documents.

Every figure is tagged MEASURED, with the GPU's name and the date: nothing here is a promise.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

WITHIN_S = 60.0
TARGET_SHARE = 0.95


@dataclass
class Burst:
    label: str
    count: int
    sent_seconds: float  # how long sending all of them took
    price_per_hour: float
    gpu: dict[str, Any] = field(default_factory=dict)  # name, total_mib, used_mib (samples)
    fault: dict[str, Any] = field(default_factory=dict)  # a worker killed, when, restarted
    date: str = ""


def _seconds(row: Mapping[str, Any] | None) -> float | None:
    if not row or not row.get("finished_at") or not row.get("created_at"):
        return None
    return round((row["finished_at"] - row["created_at"]).total_seconds(), 2)


def percentile(values: Sequence[float], share: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, math.ceil(share * len(ordered)) - 1)], 1)


def judge(burst: Burst, rows: Sequence[Mapping[str, Any] | None], *, waiting: Sequence[bool],
          per_doc: Mapping[str, tuple[int, int, int]]) -> dict[str, Any]:  # fmt: skip
    """``rows`` are the jobs (``web_job``), one per document sent, in order; ``per_doc`` maps a
    document id to (extractions, current facts, review items)."""
    docs: list[dict[str, Any]] = []
    for n, row in enumerate(rows):
        d = ((row or {}).get("docs") or [{}])[0] or {}
        doc_id = str(d.get("document_id")) if d.get("document_id") else None
        extractions, facts, items = per_doc.get(doc_id or "", (0, 0, 0))
        secs = _seconds(row)
        state = (row or {}).get("state") or "not found"
        problems = []
        if secs is None:
            problems.append("lost: never finished")
        elif state == "error" or d.get("status") == "error":
            problems.append(f"lost: ended in error ({d.get('error') or state})")
        elif not doc_id:
            problems.append("lost: no document")
        if items > 1:
            problems.append(f"doubled: {items} review items")
        docs.append({"n": n, "job": (row or {}).get("id"), "document_id": doc_id, "state": state,
                     "status": d.get("status"), "seconds": secs, "seen_waiting": waiting[n],
                     "attempts": (row or {}).get("attempts"), "extractions": extractions,
                     "facts": facts, "review_items": items, "problems": problems})  # fmt: skip
    done = [x["seconds"] for x in docs if x["seconds"] is not None and not x["problems"]]
    within = sum(1 for s in done if s <= WITHIN_S)
    used = list(burst.gpu.get("used_mib") or [])
    wall = max([x["seconds"] for x in docs if x["seconds"] is not None] or [0.0])
    cost = burst.price_per_hour * wall / 3600 / burst.count if burst.count else None
    jobs = [x["job"] for x in docs]
    return {
        "tag": "MEASURED",
        "date": burst.date,
        "gpu": burst.gpu.get("name"),
        "gpu_memory_total_mib": burst.gpu.get("total_mib"),
        "label": burst.label,
        "count": burst.count,
        "sent_in_seconds": burst.sent_seconds,
        "finished": len(done),
        "lost": sum(
            1 for x in docs if any(p.startswith("lost") for p in x["problems"])
        ),  # fmt: skip
        "doubled": sum(1 for x in docs if any(p.startswith("doubled") for p in x["problems"]))
        + (len(jobs) - len(set(jobs))),
        "within_60s": within,
        "within_60s_share": round(within / burst.count, 3) if burst.count else None,  # fmt: skip
        "meets_95_within_60": burst.count > 0 and within / burst.count >= TARGET_SHARE,
        "seconds": {
            "median": percentile(done, 0.5),
            "p95": percentile(done, 0.95),
            "fastest": min(done, default=None),
            "slowest": max(done, default=None),
        },
        "seen_waiting": sum(1 for x in docs if x["seen_waiting"]),
        "gpu_memory_mib": {"before": used[0] if used else None, "peak": max(used, default=None)},
        "wall_seconds": wall,
        "price_per_hour": burst.price_per_hour,
        "cost_per_document_usd": round(cost, 5) if cost is not None else None,
        "fault": burst.fault,
        "documents": docs,
    }


def render(rep: Mapping[str, Any]) -> str:
    s, m = rep["seconds"], rep["gpu_memory_mib"]
    verdict = "meets" if rep["meets_95_within_60"] else "does NOT meet"
    out = [f"# Burst: {rep['count']} prescriptions in the same second ({rep['label']})", "",
           f"**MEASURED** on {rep['gpu']} ({rep['gpu_memory_total_mib']} MiB), {rep['date']}.", "",
           f"- {rep['within_60s']} of {rep['count']} saved within 60 s "
           f"({rep['within_60s_share']:.0%}): {verdict} the 95% target.",
           f"- Seconds from upload to saved result: median {s['median']}, 95th percentile "
           f"{s['p95']}, fastest {s['fastest']}, slowest {s['slowest']}.",
           f"- Lost: {rep['lost']}. Doubled: {rep['doubled']}. "
           f"Seen waiting for the model: {rep['seen_waiting']} of {rep['count']}.",
           f"- GPU memory: {m['before']} MiB before, {m['peak']} MiB at the most.",
           f"- Cost per document: ${rep['cost_per_document_usd']} "
           f"(${rep['price_per_hour']}/hr for {rep['wall_seconds']} s).", ""]  # fmt: skip
    if rep.get("fault"):
        out += [f"- Fault: {rep['fault']}", ""]
    out += ["| # | Seconds | State | Waited | Attempts | Extractions | Facts | Review items | "
            "Problems |", "|---|---|---|---|---|---|---|---|---|"]  # fmt: skip
    for d in rep["documents"]:
        waited = "yes" if d["seen_waiting"] else ""
        out.append(f"| {d['n']} | {d['seconds']} | {d['state']} | {waited} | {d['attempts']} | "
                   f"{d['extractions']} | {d['facts']} | {d['review_items']} | "
                   f"{'; '.join(d['problems'])} |")  # fmt: skip
    return "\n".join(out) + "\n"


def as_dict(burst: Burst) -> dict[str, Any]:
    return asdict(burst)
