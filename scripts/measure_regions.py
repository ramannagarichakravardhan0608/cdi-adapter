"""Measure line finding and printed/handwritten labelling on real pages (backlog IM-S3, AC3).

Two ways to run it, from the repo root:

    python scripts/measure_regions.py --key answer_key.json          # recall + label accuracy
    python scripts/measure_regions.py page1.png page2.jpg --draw out # what was found, per page

**With an answer key** it compares the lines the module finds with hand-drawn ones and prints
line recall and label accuracy by document type - the table the story's acceptance asks for. The
key is a JSON list, one entry per page:

    [{"image": "pages/rx_001.png", "doc_type": "prescription",
      "lines": [{"bbox": [x0, y0, x1, y1], "label": "printed"},
                {"bbox": [x0, y0, x1, y1], "label": "handwritten"}]}]

``image`` is relative to the key file; ``label`` is ``printed``, ``handwritten`` or ``mixed``.
A found line counts for a drawn one when they overlap by ``--iou`` (default 0.5).

**Without a key** it prints, for each picture, how many lines of each kind were found and how
long it took, and with ``--draw DIR`` saves a copy with every line boxed (green printed, red
handwritten, magenta mixed, amber uncertain) so a person can check it by eye.

It reads pictures and writes only to ``--draw``; it touches no database and no stored document.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import cv2

from cdi_adapter.recognition import regions

COLOURS = {"printed": (0, 160, 0), "handwritten": (0, 0, 220), "mixed": (200, 0, 200),
           "uncertain": (0, 160, 230)}  # fmt: skip


def _find(path: Path, draw: Path | None) -> tuple[list[dict[str, Any]], float]:
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None:
        raise SystemExit(f"cannot read {path}")
    started = time.perf_counter()
    found = regions.detect_regions(img)
    seconds = time.perf_counter() - started
    if draw is not None:
        draw.mkdir(parents=True, exist_ok=True)
        for r in found:
            cv2.rectangle(img, r.bbox[:2], r.bbox[2:], COLOURS[r.label], 2)
        cv2.imwrite(str(draw / f"{path.stem}.regions.png"), img)
    return [r.record() for r in found], seconds


def _pct(value: float | None) -> str:
    return "    -" if value is None else f"{100 * value:5.1f}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("images", nargs="*", type=Path, help="page pictures to look at (no key)")
    ap.add_argument("--key", type=Path, help="answer key JSON (see the module docstring)")
    ap.add_argument("--iou", type=float, default=0.5, help="overlap that counts as the same line")
    ap.add_argument("--draw", type=Path, help="directory for copies with the lines boxed")
    ap.add_argument("--json", action="store_true", help="print the result as JSON")
    args = ap.parse_args(argv)
    if not args.key and not args.images:
        ap.error("give page pictures, or --key")

    if not args.key:
        for path in args.images:
            found, seconds = _find(path, args.draw)
            s = {label: sum(1 for f in found if f["label"] == label) for label in regions.LABELS}
            print(f"{path.name}: {len(found)} lines in {seconds:.2f} s - "
                  + ", ".join(f"{n} {label}" for label, n in s.items()))  # fmt: skip
        return 0

    pages = []
    for entry in json.loads(args.key.read_text(encoding="utf-8")):
        found, _ = _find(args.key.parent / entry["image"], args.draw)
        pages.append({"doc_type": entry.get("doc_type"), "found": found, "truth": entry["lines"]})
    result = regions.score_by_type(pages, min_iou=args.iou)
    if args.json:
        json.dump(result, sys.stdout, indent=2)
        print()
        return 0
    print(f"{'document type':<22}{'pages':>6}{'lines':>7}{'recall %':>10}{'label %':>9}"
          f"{'right':>7}{'both':>6}{'wrong':>7}{'extra':>7}")  # fmt: skip
    for doc_type, r in sorted(result.items()):
        print(f"{doc_type:<22}{r['pages']:>6}{r['truth_lines']:>7}{_pct(r['recall']):>10}"
              f"{_pct(r['label_accuracy']):>9}{r['label_right']:>7}{r['label_safe']:>6}"
              f"{r['label_wrong']:>7}{r['extra_lines']:>7}")  # fmt: skip
    print("\nright = the drawn label; both = sent to both readers (safe, not right);")
    print("wrong = sent to the one reader that cannot read it; extra = found, not drawn.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
