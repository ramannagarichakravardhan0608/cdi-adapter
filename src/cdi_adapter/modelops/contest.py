"""A fair contest: a candidate model against the current champion (backlog SW-S4).

    python -m cdi_adapter.modelops.contest --candidate <model> [--champion <model>] \\
        --set evaluation/<sealed set> [--guard evaluation/<guard suite>] \\
        --rules models/contest_rules.json \\
        (--candidate-results DIR --champion-results DIR | --candidate-host URL --champion-host URL)

Both models are read on the same answer key and the same forgetting-guard suite, on the same
machine with the same settings, scored by the same scorer (ENT-S3), and judged by rules
written down BEFORE the run. The order is fixed:

1. **refusals** - before anything is run or opened: the candidate (and the champion) must be in
   the model registry with its licence text saved (AC4); the rules must name a margin, the
   metrics guarded per slice and the precision claim; the set must be fit to use;
2. **fairness** - both runs on the same answer-key version, machine and settings, and each
   result made by the model it is said to be (when the result names its model);
3. **the report** (AC1) - every slice with the same metrics for both, side by side; seconds per
   document, GPU memory and rent per prescription for both;
4. **the verdict** - ``keep champion`` when any slice drops by more than the margin, naming the
   slice (AC2), or when speed, cost or memory is over its limit, or when the candidate is not
   better; ``need more data`` when the sample is too small to prove the precision claim (AC3);
   otherwise ``promote``. It is binding only on a sealed set with agreed rules.

The models never see the answer key: a host is sent only the pictures. Every contest report is
saved beside the model registry (``models/contests/``), with one line in its index.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import re
import statistics
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from cdi_adapter.accuracy import gate, keys, score, stats
from cdi_adapter.config import get_settings
from cdi_adapter.modelops import catalog

ROOT = Path(__file__).resolve().parents[3]
CONTESTS = ROOT / "models" / "contests"
RULES = ROOT / "models" / "contest_rules.json"
RULES_VERSION = "contest-rules.v1"
REPORT_VERSION = "contest.v1"
DECISIONS = ("promote", "keep champion", "need more data")
GROUPS = (*keys.GROUPS, "all")
# what the report shows for every group of every slice, for both models
GROUP_METRICS = (
    "precision_accepted",
    "precision_accepted_lower_95",
    "coverage",
    "field_accuracy",
    "f1",
    "invented_rate",
)
SLICE_METRICS = (
    ("all", "strict_whole_prescription_rate"),
    ("context", "prep_to_test.error_rate"),
    ("context", "test_to_reason.error_rate"),
)
LOWER_IS_BETTER = ("invented_rate", "error_rate", "calibration_gap")


class ContestRefused(Exception):
    """The contest may not start (licence, rules, set) or its runs are not a fair comparison."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason, self.detail = reason, detail


# ------------------------------------------------------------------ 1. refusals


def licence_refusal(value: str, role: str) -> str | None:
    """Why this model may not enter a contest for this role, or None."""
    entry = catalog.find(value)
    if entry is None:
        return f"{value} has no licence entry: it is not in the model registry"
    if role not in entry.roles:
        return f"{value} is registered for {', '.join(entry.roles) or 'no role'}, not for {role}"
    why = catalog.licence_problem(entry)  # SW-S7: the same gate as a champion
    if why == "has no licence text saved":
        return f"{value} has no licence text saved in the model registry"
    if why:
        return f"{value}: {why}"
    if not entry.revision or entry.revision in ("main", "latest", "master"):
        return f"{value} has no exact revision pinned in the model registry"
    return None


def rules_problems(rules: dict[str, Any]) -> list[str]:
    """What is missing from the rules for a verdict to be possible at all."""
    found = []
    if rules.get("schema_version") != RULES_VERSION:
        found.append(f"the rules are not {RULES_VERSION}")
    margin = rules.get("margin")
    if not isinstance(margin, int | float) or margin < 0:
        found.append("no margin written down: how far may a slice drop?")
    if not rules.get("slice_metrics"):
        found.append("no metrics named to guard in every slice (slice_metrics)")
    if not (rules.get("precision") or {}).get("groups"):
        found.append("no precision claim named (precision.groups)")
    return found


def agreed(rules: dict[str, Any]) -> bool:
    a = rules.get("agreed") or {}
    return bool(a.get("owner") and a.get("clinician") and a.get("date"))


def set_version(folder: Path) -> str:
    """The answer key's version: every answer and every picture, byte for byte."""
    h = hashlib.sha256()
    for path in sorted(p for p in folder.rglob("*") if p.is_file()):
        rel = path.relative_to(folder).as_posix()
        if rel.split("/")[-1] == "answer.json" or keys.IMAGE_NAMES.match(path.name):
            h.update(rel.encode())
            h.update(hashlib.sha256(path.read_bytes()).digest())
    return h.hexdigest()[:16]


def _open_set(folder: Path, why: str, by: str, at: str) -> dict[str, Any]:
    fit = keys.check_set(folder)
    if not fit["usable"]:
        raise ContestRefused(
            "set", f"{folder.name} may not be used: {len(fit['problems'])} case(s) with problems"
        )
    if fit["sealed"]:  # every opening of a sealed set is written down (ENT-S3)
        with (folder / "openings.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"at": at, "change": why, "by": by}) + "\n")
    _, answers = keys.load_set(folder)
    return {
        "name": folder.name,
        "version": set_version(folder),
        "sealed": bool(fit["sealed"]),
        "cases": len(answers),
        "answers": answers,
    }


# ------------------------------------------------------------------ 2. the runs, and fairness


def read_run(folder: Path, cases: Sequence[str]) -> dict[str, Any]:
    """One model's run: ``run.json`` (model, machine, settings, seconds per case, GPU memory)
    and ``<case>.json`` for every case it produced a result for."""
    meta_path = folder / "run.json"
    meta = json.loads(meta_path.read_text("utf-8")) if meta_path.is_file() else {}
    results = {}
    for case in cases:
        path = folder / f"{case}.json"
        results[case] = json.loads(path.read_text("utf-8")) if path.is_file() else None
    return {"meta": meta, "results": results}


def _made_by(result: dict[str, Any] | None) -> tuple[str, str | None] | None:
    for e in (result or {}).get("engines") or []:
        if e.get("stage") == "extract" and e.get("role") == "vision-language model":
            return str(e.get("name")), e.get("version")
    return None


def fairness(
    entries: dict[str, Any], runs: dict[str, dict[str, Any]], versions: dict[str, str]
) -> list[str]:
    """Why these two runs are not a fair comparison (empty: they are)."""
    found = []
    meta = {side: runs[side]["meta"] for side in runs}
    for what in ("machine", "settings"):
        if meta["champion"].get(what) != meta["candidate"].get(what):
            found.append(
                f"the two runs differ in {what}: {meta['champion'].get(what)!r} / "
                f"{meta['candidate'].get(what)!r}"
            )
    for side, run in runs.items():
        for set_name, version in versions.items():
            said = (run["meta"].get("set_versions") or {}).get(set_name)
            if said is not None and said != version:
                found.append(
                    f"the {side} was run on another version of {set_name} ({said}, now {version})"
                )
        entry = entries[side]
        for case, result in run["results"].items():
            made = _made_by(result)
            if made and (
                made[0] not in (entry.model_id, entry.id)
                or (made[1] and entry.revision and made[1] != entry.revision)
            ):
                found.append(
                    f"{case}: the {side} result was made by {made[0]}@{made[1]}, "
                    f"not {entry.model_id}@{entry.revision}"
                )
                break
    return found


# ------------------------------------------------------------------ 3. the report


def _better(metric: str) -> int:
    return -1 if metric.split(".")[-1] in LOWER_IS_BETTER else 1


def _outcome(champion: Any, candidate: Any, metric: str, tie: float) -> str | None:
    if champion is None or candidate is None:
        return None if champion is None and candidate is None else "unmeasured"
    gain = (candidate - champion) * _better(metric)
    return "win" if gain > tie + 1e-12 else ("loss" if gain < -tie - 1e-12 else "tie")


def _rows(cards: dict[str, dict[str, Any]], slice_name: str, tie: float) -> list[dict[str, Any]]:
    pairs = [(g, m) for g in GROUPS for m in GROUP_METRICS] + list(SLICE_METRICS)
    rows = []
    for group, metric in pairs:
        ch = gate.value_of(cards["champion"], group, metric, slice_name)
        ca = gate.value_of(cards["candidate"], group, metric, slice_name)
        rows.append(
            {
                "group": group,
                "metric": metric,
                "champion": ch,
                "candidate": ca,
                "delta": None if ch is None or ca is None else ca - ch,
                "outcome": _outcome(ch, ca, metric, tie),
            }
        )
    return rows


def _speed(meta: dict[str, Any], cases: Sequence[str], rules: dict[str, Any]) -> dict[str, Any]:
    seconds = [float(s) for c, s in (meta.get("seconds") or {}).items() if c in cases and s]
    mean = statistics.fmean(seconds) if seconds else None
    per_hour = (rules.get("cost") or {}).get("per_hour")
    ordered = sorted(seconds)
    return {
        "documents_timed": len(seconds),
        "seconds_per_document": None if mean is None else round(mean, 3),
        "seconds_p95": ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)] if ordered else None,
        "gpu_memory_gib": meta.get("gpu_memory_gib"),
        "rent_per_prescription": (
            None if mean is None or per_hour is None else round(mean * float(per_hour) / 3600.0, 4)
        ),
        "currency": (rules.get("cost") or {}).get("currency"),
    }


# ------------------------------------------------------------------ 4. the verdict


def _limits(speed: dict[str, dict[str, Any]], rules: dict[str, Any]) -> tuple[list[str], list[str]]:
    """(over a limit, not measured although limited)."""
    over, unmeasured = [], []
    ch, ca = speed["champion"], speed["candidate"]
    speed, cost = rules.get("speed") or {}, rules.get("cost") or {}
    checks = [
        (
            "seconds per document",
            "seconds_per_document",
            speed.get("max_seconds_per_document"),
            speed.get("max_ratio"),
        ),
        (
            "rent per prescription",
            "rent_per_prescription",
            cost.get("max_per_prescription"),
            cost.get("max_ratio"),
        ),
    ]
    for label, key, most, ratio in checks:
        if most is None and ratio is None:
            continue
        if ca[key] is None or (ratio is not None and ch[key] is None):
            unmeasured.append(f"{label} not measured, and it has a limit")
            continue
        if most is not None and ca[key] > most:
            over.append(f"{label}: {ca[key]} is over the limit {most}")
        if ratio is not None and ch[key] and ca[key] > ch[key] * ratio:
            over.append(f"{label}: {ca[key]} is more than {ratio} x the champion's {ch[key]}")
    most = rules.get("gpu_memory_gib_max")
    if most is not None:
        if ca["gpu_memory_gib"] is None:
            unmeasured.append("GPU memory not measured, and it has a limit")
        elif ca["gpu_memory_gib"] > most:
            over.append(f"GPU memory: {ca['gpu_memory_gib']} GiB is over the limit {most} GiB")
    return over, unmeasured


def _precision(cards: dict[str, dict[str, Any]], rules: dict[str, Any]) -> list[dict[str, Any]]:
    """The precision claim (FR3): the candidate's lower bound of accepted-value precision is not
    below the champion's, and meets the target when one is set."""
    claim = rules.get("precision") or {}
    target = claim.get("target")
    out = []
    for group in claim.get("groups") or []:
        sl = claim.get("slice", "overall")
        side: dict[str, dict[str, Any]] = {}
        for who, card in cards.items():
            n = int(gate.value_of(card, group, "accepted", sl) or 0)
            k = int(gate.value_of(card, group, "accepted_correct", sl) or 0)
            side[who] = {
                "n": n,
                "correct": k,
                "observed": k / n if n else None,
                "lower_95": stats.wilson_lower(k, n),
            }
        ch, ca = side["champion"], side["candidate"]
        row: dict[str, Any] = {
            "group": group,
            "slice": sl,
            "target": target,
            "champion": ch,
            "candidate": ca,
        }
        if ca["n"] == 0:
            row["finding"], row["why"] = "too small", "the candidate accepted nothing"
        elif target is not None and (ca["lower_95"] or 0) < target:
            proof = stats.proves(ca["correct"], ca["n"], float(target))
            if ca["observed"] >= target:
                row["finding"] = "too small"
                row["why"] = (
                    f"{ca['correct']} right of {ca['n']} accepted: lower bound "
                    f"{ca['lower_95']:.4f} does not prove {target} - at least "
                    f"{proof['needed_with_no_errors']} accepted values are needed"
                )
            else:
                row["finding"] = "lower"
                row["why"] = f"observed {ca['observed']:.4f} is below the target {target}"
        elif ch["lower_95"] is not None and ca["lower_95"] < ch["lower_95"] - 1e-12:
            if ca["observed"] >= ch["observed"]:
                row["finding"] = "too small"
                row["why"] = (
                    f"lower bound {ca['lower_95']:.4f} is below the champion's "
                    f"{ch['lower_95']:.4f} although the observed precision is not "
                    f"lower ({ca['correct']} of {ca['n']} accepted)"
                )
            else:
                row["finding"] = "lower"
                row["why"] = (
                    f"{ca['observed']:.4f} (lower bound {ca['lower_95']:.4f}) against "
                    f"the champion's {ch['observed']:.4f} ({ch['lower_95']:.4f})"
                )
        else:
            row["finding"], row["why"] = "holds", ""
        out.append(row)
    return out


def verdict(report: dict[str, Any], rules: dict[str, Any]) -> dict[str, Any]:
    margin = float(rules["margin"])
    keep: list[str] = []
    more: list[str] = []
    guarded = [(g["group"], g["metric"]) for g in rules["slice_metrics"]]
    for sl in report["slices"]:
        for row in sl["rows"]:
            if (row["group"], row["metric"]) not in guarded or row["outcome"] is None:
                continue
            if row["outcome"] == "unmeasured":
                keep.append(
                    f"slice {sl['name']}: {row['group']}.{row['metric']} could not be "
                    "measured for one of the models"
                )
                continue
            drop = (row["champion"] - row["candidate"]) * _better(row["metric"])
            if drop > margin + 1e-12:
                keep.append(
                    f"slice {sl['name']}: {row['group']}.{row['metric']} dropped from "
                    f"{row['champion']:.4f} to {row['candidate']:.4f}, more than the "
                    f"margin {margin}"
                )
        least = rules.get("min_cases_per_slice")
        if least and sl["cases"] < least:
            more.append(f"slice {sl['name']} has {sl['cases']} case(s); the rules ask for {least}")
    over, unmeasured = _limits(report["speed"], rules)
    keep += over
    more += unmeasured
    for row in report["precision"]:
        if row["finding"] == "lower":
            keep.append(
                f"precision of accepted {row['group']} values [{row['slice']}]: {row['why']}"
            )
        elif row["finding"] == "too small":
            more.append(
                f"precision of accepted {row['group']} values [{row['slice']}] is not "
                f"proven: {row['why']}"
            )
    first = guarded[0]
    overall = next(r for r in report["slices"][0]["rows"] if (r["group"], r["metric"]) == first)
    better = overall["outcome"] == "win"
    if not keep and not more and not better:
        keep.append(
            f"the candidate is not better overall on {first[0]}.{first[1]} "
            f"({overall['candidate']} against {overall['champion']})"
        )
    decision = "keep champion" if keep else ("need more data" if more else "promote")
    notes = []
    if not report["set"]["sealed"]:
        notes.append("the answer key is not sealed: a rehearsal, not a decision")
    if not agreed(rules):
        notes.append("the rules are not agreed by the product owner and a clinician")
    return {
        "decision": decision,
        "keep_champion_because": keep,
        "need_more_data_because": more,
        "better_overall": better,
        "binding": not notes,
        "notes": notes,
    }


# ------------------------------------------------------------------ the contest


def contest(
    candidate: str,
    champion: str | None,
    role: str,
    sets: Sequence[Path],
    rules_path: Path,
    runs: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]],
    *,
    by: str = "",
    out: Path | None = None,
    now: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
) -> dict[str, Any]:
    """Refuse, run, score, judge, save. ``runs(entries, opened)`` gives each side's run
    (``{"champion": run, "candidate": run}``) as :func:`read_run` does."""
    started = now().isoformat(timespec="seconds")
    champion = champion or catalog.champion_setting(role, get_settings())
    if not champion:
        raise ContestRefused("champion", f"no champion is set for {role}")
    for side, name in (("candidate", candidate), ("champion", champion)):
        why = licence_refusal(name, role)
        if why:
            raise ContestRefused("licence", f"the {side} {why}; the contest does not start")
    entries: dict[str, catalog.Entry] = {}
    for side, name in (("champion", champion), ("candidate", candidate)):
        entry = catalog.find(name)
        assert entry is not None  # licence_refusal said it is registered
        entries[side] = entry
    if entries["champion"] == entries["candidate"]:
        raise ContestRefused("models", "the candidate is the champion")
    rules_text = rules_path.read_text(encoding="utf-8")
    rules = json.loads(rules_text)
    found = rules_problems(rules)
    if found:
        raise ContestRefused("rules", "the rules are not written down: " + "; ".join(found))
    why = f"contest {entries['candidate'].id} vs {entries['champion'].id}"
    opened = [_open_set(Path(s), why, by, started) for s in sets]
    the_runs = runs(entries, {"sets": opened})
    versions = {o["name"]: o["version"] for o in opened}
    unfair = fairness(entries, the_runs, versions)
    if unfair:
        raise ContestRefused("unfair", "; ".join(unfair))
    cards = {}
    for side in ("champion", "candidate"):
        merged: dict[str, Any] = {"slices": {}, "cases": 0, "cases_without_a_result": []}
        for i, o in enumerate(opened):
            card = score.scorecard(
                o["answers"],
                {c: the_runs[side]["results"].get(c) for c in o["answers"]},
                set_name=o["name"],
            )
            prefix = "" if i == 0 else f"guard {o['name']}: "
            ordered = ["overall", *sorted(k for k in card["slices"] if k != "overall")]
            merged["slices"].update({prefix + k: card["slices"][k] for k in ordered})
            merged["cases"] += card["cases"]
            merged["cases_without_a_result"] += card["cases_without_a_result"]
        cards[side] = merged
    tie = float(rules.get("tie_band") or 0.0)
    names = list(cards["champion"]["slices"])
    cases = [c for o in opened for c in o["answers"]]
    report: dict[str, Any] = {
        "schema_version": REPORT_VERSION,
        "started": started,
        "role": role,
        "by": by or None,
        "set": {k: v for k, v in opened[0].items() if k != "answers"},
        "guard": [{k: v for k, v in o.items() if k != "answers"} for o in opened[1:]],
        "rules": {
            "path": _shown(rules_path),
            "digest": hashlib.sha256(rules_text.encode()).hexdigest()[:16],
            "agreed": rules.get("agreed"),
            "margin": rules["margin"],
            "slice_metrics": rules["slice_metrics"],
            "precision": rules["precision"],
        },
        "models": {
            side: {
                "id": e.id,
                "model_id": e.model_id,
                "revision": e.revision,
                "prompt_version": e.prompt_version,
                "licence": e.licence.get("name"),
                "machine": the_runs[side]["meta"].get("machine"),
                "settings": the_runs[side]["meta"].get("settings"),
                "cases_without_a_result": cards[side]["cases_without_a_result"],
            }
            for side, e in entries.items()
        },
        "slices": [
            {
                "name": n,
                "cases": cards["champion"]["slices"][n]["cases"],
                "rows": _rows(cards, n, tie),
            }
            for n in names
        ],
        "speed": {side: _speed(the_runs[side]["meta"], cases, rules) for side in entries},
    }
    for sl in report["slices"]:
        guarded = [(g["group"], g["metric"]) for g in rules["slice_metrics"]]
        tally = [r["outcome"] for r in sl["rows"] if (r["group"], r["metric"]) in guarded]
        sl["tally"] = {o: tally.count(o) for o in ("win", "tie", "loss", "unmeasured")}
    report["precision"] = _precision(cards, rules)
    report["verdict"] = verdict(report, rules)
    report["contest_id"] = "-".join(
        [
            started.replace(":", "").replace("-", "")[:15],
            _slug(entries["candidate"].id),
            "vs",
            _slug(entries["champion"].id),
        ]
    )
    save(report, out or CONTESTS)
    return report


def _shown(path: Path) -> str:
    """A path as the report shows it: inside the repository, relative to it; else its name."""
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.name


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40]


def save(report: dict[str, Any], out: Path) -> Path:
    """Beside the model registry: the report, its plain reading, and a line in the index."""
    folder = out / report["contest_id"]
    n = 1
    while folder.exists():
        n += 1
        folder = out / f"{report['contest_id']}-{n}"
    folder.mkdir(parents=True)
    report["saved_in"] = _shown(folder)
    (folder / "report.json").write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    (folder / "report.md").write_text(as_markdown(report), encoding="utf-8")
    v = report["verdict"]
    line = {
        "contest_id": folder.name,
        "started": report["started"],
        "role": report["role"],
        "candidate": report["models"]["candidate"]["id"],
        "champion": report["models"]["champion"]["id"],
        "set": report["set"]["name"],
        "set_version": report["set"]["version"],
        "decision": v["decision"],
        "binding": v["binding"],
    }
    with (out / "index.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(line) + "\n")
    return folder


def _f(v: Any) -> str:
    return "-" if v is None else (f"{v:.4f}" if isinstance(v, float) else str(v))


def as_markdown(report: dict[str, Any]) -> str:
    v, m = report["verdict"], report["models"]
    out = [
        f"# Contest: {m['candidate']['id']} (candidate) against {m['champion']['id']} (champion)",
        "",
        f"**Verdict: {v['decision'].upper()}**"
        + ("" if v["binding"] else " - not binding: " + "; ".join(v["notes"])),
        "",
    ]
    for why in v["keep_champion_because"]:
        out.append(f"- keep champion: {why}")
    for why in v["need_more_data_because"]:
        out.append(f"- need more data: {why}")
    s = report["set"]
    out += [
        "",
        f"Answer key {s['name']} version {s['version']} ({s['cases']} cases, "
        f"{'sealed' if s['sealed'] else 'NOT sealed'}); rules {report['rules']['digest']}, "
        f"margin {report['rules']['margin']}.",
        "",
        "## Models",
        "",
        "| | champion | candidate |",
        "|---|---|---|",
    ]
    for key in ("model_id", "revision", "prompt_version", "licence", "machine"):
        out.append(f"| {key} | {_f(m['champion'].get(key))} | {_f(m['candidate'].get(key))} |")
    sp = report["speed"]
    out += ["", "## Speed and cost", "", "| | champion | candidate |", "|---|---|---|"]
    for key in (
        "seconds_per_document",
        "seconds_p95",
        "gpu_memory_gib",
        "rent_per_prescription",
        "documents_timed",
    ):
        out.append(f"| {key} | {_f(sp['champion'][key])} | {_f(sp['candidate'][key])} |")
    out += ["", "## Precision of accepted values", ""]
    for p in report["precision"]:
        ch, ca = p["champion"], p["candidate"]
        out.append(
            f"- {p['group']} [{p['slice']}]: champion {ch['correct']}/{ch['n']} "
            f"(lower bound {_f(ch['lower_95'])}), candidate {ca['correct']}/{ca['n']} "
            f"(lower bound {_f(ca['lower_95'])}) - {p['finding']}"
            + (f": {p['why']}" if p["why"] else "")
        )
    out += ["", "## Slices", ""]
    for sl in report["slices"]:
        t = sl["tally"]
        out += [
            f"### {sl['name']} ({sl['cases']} cases) - {t['win']} won, {t['tie']} tied, "
            f"{t['loss']} lost",
            "",
            "| group | metric | champion | candidate | change | |",
            "|---|---|---|---|---|---|",
        ]
        for r in sl["rows"]:
            out.append(
                f"| {r['group']} | {r['metric']} | {_f(r['champion'])} | "
                f"{_f(r['candidate'])} | {_f(r['delta'])} | {r['outcome'] or '-'} |"
            )
        out.append("")
    return "\n".join(out)


# ------------------------------------------------------------------ running both models


def collect(
    folder: Path, host: str, out: Path, *, user: str = "", wait_s: float = 600.0, machine: str = ""
) -> None:  # pragma: no cover - needs a running system
    """Send every case's PICTURES (never the answer) to a running system serving one model; save
    each result and how long it took, and the GPU memory seen on this machine."""
    import os
    import subprocess
    import threading
    import time

    import httpx

    out.mkdir(parents=True, exist_ok=True)
    peak: list[float] = []
    stop = threading.Event()

    def sample() -> None:
        while not stop.is_set():
            try:
                used = subprocess.run(
                    ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                ).stdout.split()
                peak.append(max(float(u) for u in used) / 1024.0)
            except (OSError, ValueError, subprocess.SubprocessError):
                return
            stop.wait(1.0)

    sampler = threading.Thread(target=sample, daemon=True)
    sampler.start()
    seconds: dict[str, float] = {}
    host = host.rstrip("/")
    with httpx.Client(base_url=host, timeout=120.0, headers={"Origin": host}) as http:
        if user:
            http.post(
                "/login",
                data={"username": user, "password": os.environ.get("CDI_LOAD_PASSWORD", "")},
            )
        components = http.get("/api/components").json()
        for answer in sorted(folder.glob("*/answer.json")):
            pictures = sorted(p for p in answer.parent.iterdir() if keys.IMAGE_NAMES.match(p.name))
            if not pictures:
                continue
            t0 = time.monotonic()
            files = [
                ("files", (p.name, p.read_bytes(), "application/octet-stream")) for p in pictures
            ]
            job = http.post("/api/jobs", files=files).json()["job_id"]
            state: dict[str, Any] = {}
            while time.monotonic() - t0 < wait_s:
                state = http.get(f"/api/jobs/{job}").json()
                if state.get("state") in ("review", "error", "mismatch", "done", "approved"):
                    break
                time.sleep(1)
            seconds[answer.parent.name] = round(time.monotonic() - t0, 3)
            docs = [d for d in state.get("documents") or [] if d.get("document_id")]
            if docs:
                body = http.get(f"/api/documents/{docs[0]['document_id']}/result.json")
                if body.status_code == 200:
                    (out / f"{answer.parent.name}.json").write_text(body.text, encoding="utf-8")
    stop.set()
    sampler.join(timeout=5)
    swaps = {c["name"]: c.get("choice") for c in components.get("swap_points") or []}
    meta = {
        "machine": machine or None,
        "settings": swaps,
        "seconds": seconds,
        "gpu_memory_gib": round(max(peak), 2) if peak else None,
        "set_versions": {folder.name: set_version(folder)},
    }
    (out / "run.json").write_text(json.dumps(meta, indent=1) + "\n", encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m cdi_adapter.modelops.contest")
    ap.add_argument("--candidate", required=True)
    ap.add_argument("--champion", default="", help="default: the champion setting of the role")
    ap.add_argument("--role", default="extraction", choices=catalog.ROLES)
    ap.add_argument("--set", required=True, help="the answer-key set")
    ap.add_argument("--guard", action="append", default=[], help="a forgetting-guard suite")
    ap.add_argument("--rules", default=str(RULES))
    ap.add_argument("--candidate-results", default="")
    ap.add_argument("--champion-results", default="")
    ap.add_argument("--candidate-host", default="")
    ap.add_argument("--champion-host", default="")
    ap.add_argument("--user", default="")
    ap.add_argument("--machine", default="", help="a name for the machine both models run on")
    ap.add_argument("--by", default="")
    ap.add_argument("--out", default="")
    args = ap.parse_args(argv)
    sets = [Path(args.set), *map(Path, args.guard)]

    def runs(entries: dict[str, Any], opened: dict[str, Any]) -> dict[str, Any]:
        cases = [c for o in opened["sets"] for c in o["answers"]]
        found = {}
        for side in ("champion", "candidate"):
            folder = Path(getattr(args, f"{side}_results") or "")
            host = getattr(args, f"{side}_host")
            if host:  # pragma: no cover - needs a running system
                folder = Path(args.out or CONTESTS) / "runs" / f"{side}-{entries[side].id}"
                for s in sets:
                    collect(s, host, folder, user=args.user, machine=args.machine)
            elif not getattr(args, f"{side}_results"):
                raise ContestRefused("runs", f"no results and no host given for the {side}")
            found[side] = read_run(folder, cases)
        return found

    try:
        report = contest(
            args.candidate,
            args.champion or None,
            args.role,
            sets,
            Path(args.rules),
            runs,
            by=args.by,
            out=Path(args.out) if args.out else None,
        )
    except ContestRefused as refused:
        print(f"contest refused ({refused.reason}): {refused.detail}", file=sys.stderr)
        return 2
    print(as_markdown(report))
    return {"promote": 0, "keep champion": 1, "need more data": 3}[report["verdict"]["decision"]]


if __name__ == "__main__":
    sys.exit(main())
