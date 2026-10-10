"""Benchmark the handwriting readers (backlog RD-S2): TrOCR alone, Qwen alone, and both.

    python scripts/bench_htr.py --synthetic 200 --out bench/htr          # proves the harness
    python scripts/bench_htr.py --manifest lines.jsonl --deidentified --out bench/htr

``lines.jsonl`` has one labelled line crop per row: ``{"image": "crops/0001.png", "text": "Tab
Paracetamol 500 mg 1-0-1"}``, paths relative to the manifest. The lines must be de-identified
(``--deidentified`` says you have checked): they are read by models and their text is written
to the report's JSON.

Writes ``report.md`` (the checkpoint, the three set-ups' error rates, the decision) and
``report.json`` (the same, with every line's readings). A run on synthetic lines makes no
decision about TrOCR: see ``cdi_adapter/recognition/htr_bench.py``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from cdi_adapter.config import get_settings
from cdi_adapter.recognition import engines, htr_bench


def load_manifest(path: Path) -> list[htr_bench.Line]:
    lines = []
    for n, row in enumerate(path.read_text(encoding="utf-8").splitlines()):
        if not row.strip():
            continue
        item = json.loads(row)
        image = (path.parent / item["image"]).read_bytes()
        lines.append(htr_bench.Line(str(item.get("id") or f"line-{n:04d}"), image, item["text"]))
    return lines


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--synthetic", type=int, metavar="N", help="N typeset lines (not handwriting)")
    src.add_argument("--manifest", type=Path, help="JSONL of labelled, de-identified line crops")
    ap.add_argument("--deidentified", action="store_true",
                    help="you have checked that the manifest's lines carry no patient")  # fmt: skip
    ap.add_argument("--out", type=Path, required=True, help="folder for report.md and report.json")
    ap.add_argument("--no-trocr", action="store_true", help="skip TrOCR (set-ups B and C not run)")
    ap.add_argument("--no-qwen", action="store_true", help="skip Qwen (set-ups A and C not run)")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args(argv)

    settings = get_settings()
    if args.manifest:
        if not args.deidentified:
            print("refused: pass --deidentified once you have checked the lines", file=sys.stderr)
            return 2
        lines, data = load_manifest(args.manifest), "real"
        source = f"manifest {args.manifest.name}, declared de-identified"
    else:
        lines, data = htr_bench.synthetic_lines(args.synthetic, seed=args.seed), "synthetic"
        source = f"typeset text with jitter and noise, fictional content, seed {args.seed}"

    trocr = None if args.no_trocr else engines.TrOCREngine(
        settings.trocr_model_id, url=settings.ocr_host_url, timeout=120.0)  # fmt: skip
    qwen = None
    qwen_model = "not run"
    if not args.no_qwen:
        from cdi_adapter.ml.client import get_client

        client = get_client()
        qwen, qwen_model = engines.QwenLineEngine(client), f"{client.model} ({client.backend})"

    reads = htr_bench.run(lines, trocr, qwen)
    rep = htr_bench.report(reads, checkpoint=settings.trocr_model_id, qwen_model=qwen_model,
                           data=data, source=source)  # fmt: skip
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "report.md").write_text(htr_bench.render(rep), encoding="utf-8", newline="\n")
    (args.out / "report.json").write_text(
        json.dumps({**rep, "readings": [vars(r) for r in reads]}, indent=1), encoding="utf-8",
        newline="\n")  # fmt: skip
    print(htr_bench.render(rep))
    return 0


if __name__ == "__main__":
    sys.exit(main())
