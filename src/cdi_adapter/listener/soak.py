"""Proving the listener under load: every dropped file ends in exactly one place (LS-S10).

A soak run drops fake prescriptions into the watched folder at a steady rate and writes down
each one as it goes (``drops.jsonl``: its name, the fingerprint of its bytes, when). Afterwards
:func:`reconcile` puts three things side by side for every dropped file:

* **the folder** - which of inbox, processing, success, error, quarantine and rescan it is in;
* **the listener's table** - the state it was left in, the job that read it, the document it
  became, and how long it took from the drop to its result;
* **the jobs** - how many times it was handed to the pipeline.

and says, file by file, whether it is in exactly one place, was read no more than once, and is
still byte-for-byte what was dropped. :func:`render` is the page a person reads; the same
figures are kept as JSON, as evidence for the release.

Nothing here talks to a drive or a database: the script (``scripts/listener_soak.py``) gathers
the folder, the rows and the jobs, so the arithmetic can be tested on its own.
"""

from __future__ import annotations

import hashlib
import json
import os
import statistics
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

FINAL = ("success", "error", "quarantine")  # where the story says every file must end
PLACES = ("inbox", "processing", *FINAL, "rescan")


@dataclass(frozen=True)
class Drop:
    name: str
    sha256: str
    at: float  # seconds since the epoch, on the dropping machine's clock


def offsets(count: int, over_seconds: float) -> list[float]:
    """When each of ``count`` files is dropped, spread evenly over the window."""
    if count <= 1:
        return [0.0] * count
    return [round(i * over_seconds / count, 3) for i in range(count)]


def place(inbox: Path, name: str, data: bytes) -> Drop:
    """Put one file in the inbox the way a sync client does: written under another name, then
    renamed, so the listener never sees half of it."""
    inbox.mkdir(parents=True, exist_ok=True)
    part = inbox / f".{name}.part"
    part.write_bytes(data)
    os.replace(part, inbox / name)
    return Drop(name, hashlib.sha256(data).hexdigest(), (inbox / name).stat().st_mtime)


def note(results: Path, drop: Drop) -> None:
    results.mkdir(parents=True, exist_ok=True)
    with (results / "drops.jsonl").open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(asdict(drop)) + "\n")


def drops(results: Path) -> list[Drop]:
    path = results / "drops.jsonl"
    if not path.exists():
        return []
    return [Drop(**json.loads(line)) for line in path.read_text("utf-8").splitlines() if line]


def folders(root: Path, names: Mapping[str, str]) -> dict[str, dict[str, str]]:
    """What is in each place now: ``{place: {file name: sha256}}``. ``names`` maps a place to
    its folder under ``root`` ("" = the root itself, where the inbox may be)."""
    out: dict[str, dict[str, str]] = {}
    for where in PLACES:
        folder = root / names[where] if names.get(where) else root
        out[where] = {}
        if where not in names or not folder.is_dir():
            continue
        for path in sorted(folder.iterdir()):
            if path.is_file() and not path.name.startswith("."):
                out[where][path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


@dataclass
class FileResult:
    name: str
    places: list[str]
    state: str | None = None  # the listener's table
    jobs: int = 0  # times it was read to a result (a run that failed and was retried is not one)
    runs: int = 0  # every time it was handed to the pipeline, failed runs included
    document_id: str | None = None
    seconds: float | None = None  # from the drop to its result
    unchanged: bool | None = None  # the file where it ended is byte-for-byte what was dropped
    problems: list[str] = field(default_factory=list)


def _handed(job: Mapping[str, Any]) -> dict[str, bool]:
    """The files one job was handed: ``{file name: it ended in a result}``. A run that failed
    (and was retried as another job) is a run, not a reading. With no record of how the job's
    documents ended, every file it was handed counts as read."""

    def rows(value: Any) -> list[dict[str, Any]]:
        value = json.loads(value) if isinstance(value, str) else value
        return [v for v in (value or []) if isinstance(v, dict)]

    out = {str(f["filename"]): True for f in rows(job.get("files")) if f.get("filename")}
    for doc in rows(job.get("docs")):
        if doc.get("filename") in out:
            out[str(doc["filename"])] = doc.get("status") not in ("error", "failed")
    return out


def _seconds(history: Any) -> float | None:
    """The time of the file's last result, from its history in the listener's table."""
    events = history if isinstance(history, list) else json.loads(history or "[]")
    timed = [e["seconds_from_drop"] for e in events
             if isinstance(e, dict) and e.get("seconds_from_drop") is not None]  # fmt: skip
    return float(timed[-1]) if timed else None


def reconcile(dropped: Sequence[Drop], tree: Mapping[str, Mapping[str, str]],
              rows: Iterable[Mapping[str, Any]], jobs: Iterable[Mapping[str, Any]],
              ) -> dict[str, Any]:  # fmt: skip
    """Every dropped file against the folder, the listener's table and the jobs."""
    by_name = {str(r["name"]): r for r in rows}
    by_original = {str(r["original_name"]): r for r in by_name.values() if r.get("original_name")}
    handed = [_handed(j) for j in jobs]
    results: list[FileResult] = []
    for d in dropped:
        row = by_name.get(d.name) or by_original.get(d.name)
        kept_as = str(row["name"]) if row else d.name  # the listener may have renamed it
        places = [w for w in PLACES if d.name in tree.get(w, {}) or kept_as in tree.get(w, {})]
        r = FileResult(d.name, places)
        if row:
            r.state = row.get("state")
            r.document_id = str(row["document_id"]) if row.get("document_id") else None
            r.seconds = _seconds(row.get("history"))
        mine = [h.get(d.name, h.get(kept_as)) for h in handed if d.name in h or kept_as in h]
        r.runs, r.jobs = len(mine), sum(1 for ended in mine if ended)
        if not places:
            r.problems.append("missing: in no folder")
        elif len(places) > 1:
            r.problems.append(f"duplicated: in {' and '.join(places)}")
        else:
            where = tree[places[0]]
            r.unchanged = where.get(kept_as, where.get(d.name)) == d.sha256
            if r.unchanged is False and places[0] in FINAL:
                r.problems.append("changed: not the bytes that were dropped")
            if places[0] not in FINAL:
                r.problems.append(f"not finished: still in {places[0]}")
        if r.jobs > 1:
            r.problems.append(f"read {r.jobs} times")
        if places == ["success"] and r.state != "succeeded":
            r.problems.append(f"in success but the table says {r.state}")
        results.append(r)

    counts = Counter(r.places[0] if len(r.places) == 1 else
                     "missing" if not r.places else "duplicated" for r in results)  # fmt: skip
    known = {d.name for d in dropped} | {str(r["name"]) for r in by_name.values()}
    strangers = sorted(n for w in FINAL for n in tree.get(w, {}) if n not in known)
    documents = Counter(r.document_id for r in results if r.document_id)
    final = sum(counts[w] for w in FINAL)
    times = sorted(r.seconds for r in results if r.seconds is not None and r.places == ["success"])
    return {
        "dropped": len(dropped),
        "counts": {w: counts[w] for w in (*PLACES, "missing", "duplicated")},
        "final": final,
        "adds_up": final == len(dropped),
        "none_missing": counts["missing"] == 0,
        "none_duplicated": counts["duplicated"] == 0,
        "read_more_than_once": sorted(r.name for r in results if r.jobs > 1),
        "documents_shared": sorted(d for d, n in documents.items() if n > 1),
        "changed": sorted(r.name for r in results if r.unchanged is False),
        "files_nobody_dropped": strangers,
        "seconds_from_drop": timing(times),
        "problems": {r.name: r.problems for r in results if r.problems},
        "files": [asdict(r) for r in results],
    }


def timing(seconds: Sequence[float]) -> dict[str, Any]:
    """Seconds from the drop to the saved result, over the files that succeeded."""
    if not seconds:
        return {"files": 0}
    ordered = sorted(seconds)
    p95 = ordered[min(len(ordered) - 1, round(0.95 * (len(ordered) - 1)))]
    return {"files": len(ordered), "median": round(statistics.median(ordered), 1),
            "p95": round(p95, 1), "min": round(ordered[0], 1),
            "max": round(ordered[-1], 1)}  # fmt: skip


def passed(rep: Mapping[str, Any]) -> bool:
    """The story's AC3: success + error + quarantine equals what was dropped, none missing,
    none duplicated - and none read twice."""
    return bool(rep["adds_up"] and rep["none_missing"] and rep["none_duplicated"]
                and not rep["read_more_than_once"] and not rep["files_nobody_dropped"])  # fmt: skip


def render(rep: Mapping[str, Any], *, title: str, notes: Sequence[str] = ()) -> str:
    c, t = rep["counts"], rep["seconds_from_drop"]
    out = [f"# {title}", "", f"**{'PASS' if passed(rep) else 'FAIL'}** - {rep['dropped']} files "
           f"dropped; success + error + quarantine = {rep['final']}.", "",
           "| Where the files ended | Files |", "|---|---|"]  # fmt: skip
    out += [f"| {where} | {c[where]} |" for where in (*PLACES, "missing", "duplicated")]
    out += ["", "| Check | Result |", "|---|---|",
            f"| success + error + quarantine equals files dropped | {_yes(rep['adds_up'])} |",
            f"| none missing | {_yes(rep['none_missing'])} |",
            f"| none in two places | {_yes(rep['none_duplicated'])} |",
            f"| none read more than once | {_yes(not rep['read_more_than_once'])} |",
            f"| no two files became one document | {_yes(not rep['documents_shared'])} |",
            f"| every file is still what was dropped | {_yes(not rep['changed'])} |",
            f"| no file appeared that nobody dropped | {_yes(not rep['files_nobody_dropped'])} |",
            ""]  # fmt: skip
    if t["files"]:
        out += [f"Seconds from drop to saved, over {t['files']} files that succeeded: median "
                f"{t['median']}, 95th percentile {t['p95']}, fastest {t['min']}, slowest "
                f"{t['max']}.", ""]  # fmt: skip
    if rep["problems"]:
        out += ["| File | Problem |", "|---|---|"]
        out += [
            f"| {name} | {'; '.join(why)} |" for name, why in list(rep["problems"].items())[:40]
        ]
        out.append("")
    out += [f"- {line}" for line in notes]
    return "\n".join(out) + "\n"


def _yes(ok: Any) -> str:
    return "yes" if ok else "NO"
