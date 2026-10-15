"""Accuracy CLI (backlog ENT-S3).

    python -m cdi_adapter.accuracy check-set SET              # is the answer-key set fit to use?
    python -m cdi_adapter.accuracy agreement SET anita ravi   # two labellers, item by item
    python -m cdi_adapter.accuracy collect SET --host URL --user U   # run the pictures through
                                                              # a host; saves each result.json
    python -m cdi_adapter.accuracy score SET --thresholds T.json [--baseline B.json]
           [--out DIR] [--history H.jsonl] [--change <commit>] [--release]
    python -m cdi_adapter.accuracy proof --target 0.99 [--correct 299 --n 300]
    python -m cdi_adapter.accuracy ablation WITHOUT.json WITH.json --stage trocr
    python -m cdi_adapter.accuracy history H.jsonl --html history.html

``score`` exits 0 when the gate passes, 1 when a limit, a claim or the allowed drop fails, and
2 when the set itself may not be used (a key not settled, an identity not recorded removed, a
sealed set opened without ``--release``). ``--release`` also demands agreed thresholds.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cdi_adapter.accuracy import gate, keys, score, stats

END_STATES = ("review", "error", "mismatch", "done", "approved")


def _results(folder: Path, results_dir: Path | None) -> dict[str, dict[str, Any] | None]:
    """Each case's result: ``<results>/<case>.json`` when a folder is named, else the
    ``result.json`` beside its answer. A case with none is scored as "nothing was read"."""
    out: dict[str, dict[str, Any] | None] = {}
    for answer in sorted(folder.glob("*/answer.json")):
        case = answer.parent.name
        path = results_dir / f"{case}.json" if results_dir else answer.parent / "result.json"
        out[case] = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
    return out


def cmd_check(args: argparse.Namespace) -> int:
    report = keys.check_set(Path(args.set))
    print(json.dumps(report, indent=1))
    return 0 if report["usable"] else 2


def cmd_agreement(args: argparse.Namespace) -> int:
    folder = Path(args.set)
    report = keys.agreement(
        keys.labeller_keys(folder, args.first), keys.labeller_keys(folder, args.second)
    )
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    said = "-" if report["agreement"] is None else f"{report['agreement']:.1%}"
    print(f"{report['cases_compared']} cases, {report['items']} items: agreement {said}")
    for group, g in report["by_group"].items():
        if g["items"]:
            print(f"  {group:12} {g['agreed']}/{g['items']} = {g['agreement']:.1%}")
    for d in report["disagreements"]:
        print(f"  SETTLE  {d['case']} {d['item']}: {d['first']!r} / {d['second']!r}")
    return 0 if report["all_settled"] and report["cases_compared"] else 1


def cmd_score(args: argparse.Namespace) -> int:
    folder = Path(args.set)
    fit = keys.check_set(folder)
    if not fit["usable"]:
        print(
            f"the set may not be used: {len(fit['problems'])} case(s) with problems",
            file=sys.stderr,
        )
        for case, found in list(fit["problems"].items())[:20]:
            print(f"  {case}: {'; '.join(found)}", file=sys.stderr)
        return 2
    if fit["sealed"] and not args.release:
        print(
            "this set is SEALED: it is opened only for a release decision (--release)",
            file=sys.stderr,
        )
        return 2
    now = args.at or datetime.now(UTC).isoformat(timespec="seconds")
    if fit["sealed"]:  # every opening of the sealed set is written down
        with (folder / "openings.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"at": now, "change": args.change, "by": args.by}) + "\n")
    _, answers = keys.load_set(folder)
    card = score.scorecard(
        answers,
        _results(folder, Path(args.results) if args.results else None),
        set_name=folder.name,
    )
    thresholds = json.loads(Path(args.thresholds).read_text("utf-8")) if args.thresholds else {}
    baseline = json.loads(Path(args.baseline).read_text("utf-8")) if args.baseline else None
    verdict = gate.evaluate(card, thresholds, baseline=baseline, require_agreed=args.release)
    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "scorecard.json").write_text(json.dumps(card, indent=1) + "\n", encoding="utf-8")
        (out / "verdict.json").write_text(json.dumps(verdict, indent=1) + "\n", encoding="utf-8")
        (out / "scorecard.md").write_text(gate.as_markdown(card, verdict), encoding="utf-8")
        (out / "transcript.txt").write_text("\n".join(score.transcript(card, 500)) + "\n", "utf-8")
    if args.history:
        gate.append_history(Path(args.history), card, verdict, at=now, change=args.change)
    overall = card["slices"]["overall"]["groups"]
    for group, m in overall.items():
        p, low, cov = (m["precision_accepted"], m["precision_accepted_lower_95"], m["coverage"])
        print(
            f"  {group:12} accepted {m['accepted_correct']}/{m['accepted']}"
            f"  precision {gate._pct(p)}  lower bound {gate._pct(low)}"
            f"  coverage {gate._pct(cov)}"
        )
    for failure in verdict["failures"]:
        print(f"  FAIL  {failure}")
    print(f"accuracy gate: {'PASS' if verdict['passed'] else 'FAIL'}")
    return 0 if verdict["passed"] else 1


def cmd_proof(args: argparse.Namespace) -> int:
    print(
        f"to prove {args.target:.2%} with no error at all: "
        f"{stats.needed_with_no_errors(args.target)} accepted values in the slice"
    )
    if args.n:
        p = stats.proves(args.correct, args.n, args.target)
        print(
            f"{args.correct} right of {args.n}: observed {p['observed']:.4f}, 95% lower bound "
            f"{p['lower_bound_95']:.4f} -> {'PROVEN' if p['proven'] else 'NOT YET'}"
        )
        return 0 if p["proven"] else 1
    return 0


def ablation(without: dict[str, Any], with_: dict[str, Any], stage: str) -> dict[str, Any]:
    """FR5: what one stage adds - each headline number with the stage minus without it. A
    stage is worth keeping only for a measured gain; time and cost come from the run notes."""
    rows = []
    for group in keys.GROUPS:
        for metric in ("precision_accepted", "coverage", "field_accuracy", "invented_rate"):
            a, b = gate.value_of(without, group, metric), gate.value_of(with_, group, metric)
            rows.append(
                {
                    "group": group,
                    "metric": metric,
                    "without": a,
                    "with": b,
                    "delta": None if a is None or b is None else b - a,
                }
            )
    gains = [r for r in rows if r["delta"] and r["metric"] != "invented_rate" and r["delta"] > 0]
    return {"stage": stage, "rows": rows, "any_measured_gain": bool(gains)}


def cmd_ablation(args: argparse.Namespace) -> int:
    report = ablation(
        json.loads(Path(args.without).read_text("utf-8")),
        json.loads(Path(args.with_).read_text("utf-8")),
        args.stage,
    )
    for r in report["rows"]:
        if r["delta"] is not None:
            print(
                f"  {r['group']:12} {r['metric']:20} {r['without']:.4f} -> {r['with']:.4f}"
                f"  ({r['delta']:+.4f})"
            )
    print(
        f"stage {args.stage!r}: {'a measured gain' if report['any_measured_gain'] else 'NO gain'}"
    )
    return 0


def cmd_history(args: argparse.Namespace) -> int:
    history = gate.read_history(Path(args.file))
    if args.html:
        Path(args.html).write_text(gate.chart_html(history), encoding="utf-8")
    print(f"{len(history)} run(s)" + (f"; chart written to {args.html}" if args.html else ""))
    return 0


def cmd_collect(args: argparse.Namespace) -> int:
    """Send every case's picture to a running system and save what it produced. The password
    is read from CDI_LOAD_PASSWORD, never from the command line."""
    import httpx

    folder = Path(args.set)
    host = args.host.rstrip("/")
    with httpx.Client(base_url=host, timeout=120.0, headers={"Origin": host}) as http:
        if args.user:
            r = http.post(
                "/login",
                data={"username": args.user, "password": os.environ.get("CDI_LOAD_PASSWORD", "")},
            )
            if r.status_code != 303:
                print(f"sign-in as {args.user!r} failed (HTTP {r.status_code})", file=sys.stderr)
                return 2
        done = 0
        for answer in sorted(folder.glob("*/answer.json")):
            pictures = sorted(p for p in answer.parent.iterdir() if keys.IMAGE_NAMES.match(p.name))
            if not pictures:
                continue
            files = [
                ("files", (p.name, p.read_bytes(), "application/octet-stream")) for p in pictures
            ]
            job = http.post("/api/jobs", files=files).json()["job_id"]
            deadline = time.monotonic() + args.wait_s
            state: dict[str, Any] = {}
            while time.monotonic() < deadline:
                state = http.get(f"/api/jobs/{job}").json()
                if state.get("state") in END_STATES:
                    break
                time.sleep(2)
            docs = [d for d in state.get("documents") or [] if d.get("document_id")]
            if docs:
                body = http.get(f"/api/documents/{docs[0]['document_id']}/result.json")
                if body.status_code == 200:
                    (answer.parent / "result.json").write_text(body.text, encoding="utf-8")
                    done += 1
        print(f"{done} result(s) saved")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="cdi_adapter.accuracy", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("check-set")
    p.add_argument("set")
    p.set_defaults(fn=cmd_check)
    p = sub.add_parser("agreement")
    p.add_argument("set")
    p.add_argument("first")
    p.add_argument("second")
    p.add_argument("--out", default="")
    p.set_defaults(fn=cmd_agreement)
    p = sub.add_parser("score")
    p.add_argument("set")
    p.add_argument("--results", default="")
    p.add_argument("--thresholds", default="")
    p.add_argument("--baseline", default="")
    p.add_argument("--out", default="")
    p.add_argument("--history", default="")
    p.add_argument("--change", default="")
    p.add_argument("--by", default="")
    p.add_argument("--at", default="")
    p.add_argument("--release", action="store_true")
    p.set_defaults(fn=cmd_score)
    p = sub.add_parser("proof")
    p.add_argument("--target", type=float, default=0.99)
    p.add_argument("--correct", type=int, default=0)
    p.add_argument("--n", type=int, default=0)
    p.set_defaults(fn=cmd_proof)
    p = sub.add_parser("ablation")
    p.add_argument("without")
    p.add_argument("with_")
    p.add_argument("--stage", required=True)
    p.set_defaults(fn=cmd_ablation)
    p = sub.add_parser("history")
    p.add_argument("file")
    p.add_argument("--html", default="")
    p.set_defaults(fn=cmd_history)
    p = sub.add_parser("collect")
    p.add_argument("set")
    p.add_argument("--host", required=True)
    p.add_argument("--user", default="")
    p.add_argument("--wait-s", type=float, default=600.0)
    p.set_defaults(fn=cmd_collect)
    args = ap.parse_args(argv)
    return int(args.fn(args))


if __name__ == "__main__":
    raise SystemExit(main())
