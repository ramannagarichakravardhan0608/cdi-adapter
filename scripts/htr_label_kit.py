"""Make the real handwritten lines the benchmark needs (backlog RD-S2 AC4).

    # 1. cut the handwritten lines out of prescriptions you choose (images or PDFs)
    python scripts/htr_label_kit.py cut --out D:/htr-kit  D:/prescriptions/*.jpg

    # 2. open D:/htr-kit/label.html, type what each line says, press "Save labels.csv",
    #    and put the saved file in D:/htr-kit in place of the one there

    # 3. turn what was typed into the benchmark's lines
    python scripts/htr_label_kit.py build --kit D:/htr-kit --out D:/htr-lines

    # 4. run the benchmark on them
    python scripts/bench_htr.py --manifest D:/htr-lines/lines.jsonl --deidentified
        (add: --out D:/htr-report)

Printed lines are left out. A line with a name, a phone number or an ID on it is left out whole:
when it is cut (if the print reader can read it), when a person ticks "skip", and again on what
was typed. The kit and the lines are real patients' handwriting: both folders must be outside
the repository. The benchmark wants 500-1,000 lines; ``build`` says how many are still needed.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import asdict
from pathlib import Path

from cdi_adapter.recognition import htr_labels


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)
    cut = sub.add_parser("cut", help="cut handwritten lines out of prescriptions")
    cut.add_argument("files", nargs="+", type=Path, help="images or PDFs")
    cut.add_argument("--out", type=Path, required=True, help="the kit folder (outside the repo)")
    build = sub.add_parser("build", help="turn the typed sheet into the benchmark's lines")
    build.add_argument("--kit", type=Path, required=True)
    build.add_argument("--out", type=Path, required=True, help="folder for lines.jsonl and crops")
    args = ap.parse_args(argv)
    try:
        if args.command == "cut":
            done = htr_labels.cut(args.files, args.out)
            print(f"{done.written} new handwritten lines in {args.out} "
                  f"(open label.html there and type what each says)")  # fmt: skip
        else:
            done = htr_labels.build(args.kit, args.out)
            low, high = htr_labels.TARGET
            more = "" if done.enough else f": {done.still_needed} more needed"
            print(f"{done.kept} lines ready in {args.out / 'lines.jsonl'}; the benchmark wants "
                  f"{low}-{high}{more}")  # fmt: skip
    except ValueError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    for key, value in asdict(done).items():
        print(f"  {key.replace('_', ' ')}: {value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
