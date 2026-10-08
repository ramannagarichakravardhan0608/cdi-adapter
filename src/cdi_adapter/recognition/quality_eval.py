"""The false-reject rate of the picture check, on an answer-key set (backlog IM-S1 AC3).

    python scripts/quality_answer_key.py --key answer_key.json
    python scripts/quality_answer_key.py --key answer_key.json --json

"A readable picture must not be rejected" is IM-S1's own rule, and every limit in
``recognition/quality.py`` trades against it: set a limit higher and more bad pictures are
caught, and more good ones are turned away. This module puts a number on that trade. Given
pictures a **person** has labelled readable or not, it runs the check on each one and reports:

* **false-reject rate** - of the pictures a person can read, the share the check would hold.
  This is the number the story asks for.
* **false-accept rate** - of the pictures a person cannot read, the share the check lets
  through. Reported beside it because either rate alone can be made zero by a silly limit.
* **why** - the reasons the readable pictures were held for, most common first, and which
  pictures they were, so a limit can be looked at with the pictures in hand.
* **the thresholds used** - every limit the run was judged with, in the report itself: a rate
  without its limits cannot be compared with the next run.

The answer key is a JSON list, one entry per picture, with paths relative to the key file::

    [{"image": "photos/rx_001.jpg", "readable": true,  "doc_type": "prescription"},
     {"image": "photos/rx_002.jpg", "readable": false, "doc_type": "prescription"}]

A multi-page file (PDF, TIFF) is one entry and is held if any page of it is held, exactly as the
upload job treats it. The run always judges - it does not matter whether ``CDI_QUALITY_CHECK`` is
on - and it reads pictures only: no database, nothing stored.

**There is no answer-key set of real photographs in the repository yet** (ENT-S3). The sample key
beside the sample scans (``data/samples/quality_answer_key.sample.json``) exists to show the
format and to prove the runner; three clean scans are not evidence about phone photographs, and
a rate from them means nothing about the limits.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from cdi_adapter.recognition import quality


def judge(raw: bytes, mime: str, settings: Any) -> quality.Verdict:
    """The check's verdict on one file, with holding forced on: an evaluation always judges."""
    forced = settings.model_copy(update={"quality_check": True})
    return quality.assess(raw, mime, settings=forced)


def evaluate(entries: list[dict[str, Any]], *, root: Path, settings: Any = None) -> dict[str, Any]:
    """Run the check over an answer key and count. ``entries`` is the parsed key; ``root`` is
    the folder its image paths are relative to."""
    from cdi_adapter.config import get_settings
    from cdi_adapter.ingest.pages import sniff_mime

    s = settings or get_settings()
    rows = []
    for entry in entries:
        path = root / entry["image"]
        raw = path.read_bytes()
        verdict = judge(raw, sniff_mime(raw, path.name), s)
        rows.append({"image": entry["image"], "readable": bool(entry["readable"]),
                     "doc_type": entry.get("doc_type") or "unknown",
                     "held": not verdict.ok, "code": verdict.code})  # fmt: skip
    return summarise(rows, quality.limits(s))


def _rates(rows: list[dict[str, Any]]) -> dict[str, Any]:
    readable = [r for r in rows if r["readable"]]
    unreadable = [r for r in rows if not r["readable"]]
    false_rejects = [r for r in readable if r["held"]]
    false_accepts = [r for r in unreadable if not r["held"]]
    return {
        "pictures": len(rows),
        "readable": len(readable),
        "false_rejects": len(false_rejects),
        "false_reject_rate": round(len(false_rejects) / len(readable), 4) if readable else None,
        "unreadable": len(unreadable),
        "false_accepts": len(false_accepts),
        "false_accept_rate": (
            round(len(false_accepts) / len(unreadable), 4) if unreadable else None
        ),  # fmt: skip
    }


def summarise(rows: list[dict[str, Any]], thresholds: dict[str, Any]) -> dict[str, Any]:
    """The counting, apart from the pictures so it can be checked on its own. Each row is
    ``{"image", "readable", "doc_type", "held", "code"}``."""
    wrongly_held = [r for r in rows if r["readable"] and r["held"]]
    reasons: dict[str, int] = {}
    for r in wrongly_held:
        reasons[r["code"] or "unknown"] = reasons.get(r["code"] or "unknown", 0) + 1
    by_type = {t: _rates([r for r in rows if r["doc_type"] == t])
               for t in sorted({r["doc_type"] for r in rows})}  # fmt: skip
    return {
        **_rates(rows),
        "thresholds": dict(thresholds),
        "false_reject_reasons": [
            {"code": c, "count": n}
            for c, n in sorted(reasons.items(), key=lambda kv: (-kv[1], kv[0]))
        ],
        "false_rejected": [{"image": r["image"], "code": r["code"]} for r in wrongly_held],
        "false_accepted": [r["image"] for r in rows if not r["readable"] and not r["held"]],
        "by_doc_type": by_type,
    }


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{100 * value:.1f} %"


def as_text(report: dict[str, Any]) -> str:
    """The report as a person reads it."""
    lines = [
        f"Picture check on the answer key: {report['pictures']} picture(s)",
        "",
        f"  readable pictures          {report['readable']}",
        f"  held by the check          {report['false_rejects']}",
        f"  FALSE-REJECT RATE          {_pct(report['false_reject_rate'])}",
        "",
        f"  unreadable pictures        {report['unreadable']}",
        f"  let through by the check   {report['false_accepts']}",
        f"  false-accept rate          {_pct(report['false_accept_rate'])}",
        "",
        "  Thresholds used",
    ]
    lines += [f"    {name:<24}{value}" for name, value in report["thresholds"].items()]
    if report["false_reject_reasons"]:
        lines += ["", "  Why readable pictures were held"]
        lines += [f"    {r['code']:<24}{r['count']}" for r in report["false_reject_reasons"]]
        lines += ["", "  Which ones"]
        lines += [f"    {r['image']}  ({r['code']})" for r in report["false_rejected"]]
    if report["false_accepted"]:
        lines += ["", "  Unreadable pictures let through"]
        lines += [f"    {name}" for name in report["false_accepted"]]
    if len(report["by_doc_type"]) > 1:
        lines += ["", "  By document type            readable  held   false-reject rate"]
        for t, r in report["by_doc_type"].items():
            rate = _pct(r["false_reject_rate"])
            lines.append(f"    {t:<26}{r['readable']:>8}{r['false_rejects']:>6}   {rate}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="quality_answer_key",
                                 description=__doc__.split("\n\n")[0])  # fmt: skip
    ap.add_argument("--key", type=Path, required=True, help="the answer-key JSON file")
    ap.add_argument("--json", action="store_true", help="print the report as JSON")
    args = ap.parse_args(argv)
    entries = json.loads(args.key.read_text(encoding="utf-8"))
    report = evaluate(entries, root=args.key.parent)
    print(json.dumps(report, indent=2) if args.json else as_text(report))
    return 0
