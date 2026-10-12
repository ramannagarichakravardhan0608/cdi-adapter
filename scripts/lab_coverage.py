"""Coverage of the lab-test master on an answer-key set (backlog EX-S3, AC4).

    python scripts/lab_coverage.py docs/evidence/EX-S3/answer_key.csv
    python scripts/lab_coverage.py docs/evidence/EX-S3/answer_key.csv --out docs/evidence/EX-S3

The answer key is a CSV with a header: ``prescription`` (any label), ``as_written`` (the test as
a doctor wrote it) and ``expected`` - the master's internal id (``LT0001``) or code for the test
it should be named as, or **empty** when it should NOT be named (an unknown abbreviation,
something that is not a test).

The report gives, of the texts that have a right answer:

* **share matched** - how many were given a standard name at all;
* **share correct** - how many were given the RIGHT one.

A doubtful (fuzzy) match is not a match: it is counted apart, as it goes to a person. Texts that
should not be named and were are listed too. Every figure is tagged with the master's version.

This measures the list and the matching rules on the texts in the key. It says nothing about
how well a page is READ: a test misread by the reader never reaches this step as written.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any


def read_key(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        data = [line for line in fh if not line.startswith("#")]
        rows = [{k.strip().lower(): (v or "").strip() for k, v in r.items()}
                for r in csv.DictReader(data)]  # fmt: skip
    return [r for r in rows if r.get("as_written")]


def render(rep: dict[str, Any], key_name: str, prescriptions: int) -> str:
    def pct(x: float | None) -> str:
        return "n/a" if x is None else f"{100 * x:.1f}%"

    n = rep["with_a_right_answer"]
    unmatched = len(rep["not_matched"]) - rep["doubtful"]

    def share(count: int) -> float | None:
        return count / n if n else None

    def row(label: str, count: int, part: float | None = None, bold: bool = False) -> str:
        said = "" if part is None and not bold else pct(part)
        return f"| {label} | {count} | {'**' + said + '**' if bold else said} |"

    lines = [
        "# EX-S3 - coverage of the lab-test master",
        "",
        f"**MEASURED** on {date.today().isoformat()} with master `{rep['master_version']}` "
        f"({rep['master_rows']} rows), on the answer key `{key_name}`: {prescriptions} "
        f"prescriptions, {rep['texts']} ordered tests as written.",
        "",
        "| | Count | Share |",
        "|---|---|---|",
        row("Tests with a right answer", n),
        row("**Matched** (given a standard name)", rep["matched"], rep["share_matched"], True),
        row("**Correct** (given the right name)", rep["correct"], rep["share_correct"], True),
        row("Matched to the wrong test", len(rep["wrong"]), share(len(rep["wrong"]))),
        row("Doubtful (sent to a person)", rep["doubtful"], share(rep["doubtful"])),
        row("Not matched", unmatched, share(unmatched)),
        row("Texts that should NOT be named", rep["should_not_match"]),
        row("...of which were named anyway", len(rep["named_but_should_not_be"])),
        "",
    ]  # fmt: skip
    if rep["wrong"]:
        lines += ["## Matched to the wrong test", ""]
        lines += [f"- `{w['as_written']}` -> {w['got']} (expected {w['expected']})"
                  for w in rep["wrong"]]  # fmt: skip
        lines.append("")
    if rep["not_matched"]:
        lines += ["## Not matched, or doubtful", ""]
        lines += [f"- `{t}`" for t in rep["not_matched"]]
        lines.append("")
    if rep["named_but_should_not_be"]:
        lines += ["## Named, but should not have been", ""]
        lines += [f"- `{t}`" for t in rep["named_but_should_not_be"]]
        lines.append("")
    lines += ["## What this does and does not show", "",
              "- The answer key is synthetic: test names written the way prescriptions write "
              "them, keyed by hand. It is not a sample of a real lab's orders.",
              "- It measures the list and the matching rules only - not how well a page is read.",
              "- No row of the master has been reviewed by a clinician yet, and rows without a "
              "code have none assigned: 'correct' means the right ROW, not a verified LOINC code.",
              ""]  # fmt: skip
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("answer_key", type=Path)
    ap.add_argument("--out", type=Path, help="folder for report.md and report.json")
    args = ap.parse_args(argv)
    from cdi_adapter.terminology import lab_master

    rows = read_key(args.answer_key)
    if not rows:
        print("the answer key has no rows", file=sys.stderr)
        return 1
    rep = lab_master.coverage(rows)
    prescriptions = len({r.get("prescription") for r in rows})
    page = render(rep, args.answer_key.name, prescriptions)
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "report.md").write_bytes(page.encode("utf-8"))
        (args.out / "report.json").write_bytes(
            (json.dumps(rep, indent=1, ensure_ascii=False) + "\n").encode("utf-8"))
    print(page)
    return 0


if __name__ == "__main__":
    sys.exit(main())
