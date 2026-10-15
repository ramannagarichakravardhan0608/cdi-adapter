"""``python -m cdi_adapter.finetune`` - one command for any base model (SW-S3).

    python -m cdi_adapter.finetune smoke --base Qwen/Qwen2.5-VL-7B-Instruct --role extraction \\
        --data data/train/rx-v1 --out runs/
    python -m cdi_adapter.finetune train --base Qwen/Qwen2.5-VL-7B-Instruct --role extraction \\
        --data data/train/rx-v1 --out runs/ --seed 7 --steps 2000 [--qlora]
    python -m cdi_adapter.finetune compare runs/<run a>/report.json runs/<run b>/report.json

``train`` runs the smoke test first and stops if it fails. Exit codes: 0 finished; 1 failed
(the report says why: licence, data, format, memory or other).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from cdi_adapter.finetune.run import RunConfig, as_markdown, compare, run


def _config(args: argparse.Namespace) -> RunConfig:
    return RunConfig(
        base=args.base,
        role=args.role,
        data=args.data,
        seed=args.seed,
        revision=args.revision or None,
        steps=args.steps,
        examples=args.examples or None,
        batch=args.batch,
        lr=args.lr,
        rank=args.rank,
        alpha=args.alpha,
        dropout=args.dropout,
        qlora=args.qlora,
    )


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m cdi_adapter.finetune")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("smoke", "train"):
        p = sub.add_parser(name)
        p.add_argument(
            "--base", required=True, help="the base model id, as in models/registry.json"
        )
        p.add_argument("--role", required=True, choices=("extraction", "line_reader"))
        p.add_argument("--data", required=True, help="the dataset folder")
        p.add_argument("--out", default="runs")
        p.add_argument("--revision", default="")
        p.add_argument("--seed", type=int, default=7)
        p.add_argument("--steps", type=int, default=200)
        p.add_argument("--examples", type=int, default=0)
        p.add_argument("--batch", type=int, default=1)
        p.add_argument("--lr", type=float, default=1e-4)
        p.add_argument("--rank", type=int, default=16)
        p.add_argument("--alpha", type=int, default=32)
        p.add_argument("--dropout", type=float, default=0.05)
        p.add_argument("--qlora", action="store_true")
    c = sub.add_parser("compare")
    c.add_argument("first")
    c.add_argument("second")
    args = ap.parse_args(argv)
    if args.cmd == "compare":
        report = compare(
            json.loads(Path(args.first).read_text("utf-8")),
            json.loads(Path(args.second).read_text("utf-8")),
        )
        print(json.dumps(report, indent=1))
        return 0 if report["comparable"] else 1
    cfg = _config(args)
    out = Path(args.out)
    smoke = run(cfg.as_smoke(), out)
    print(as_markdown(smoke))
    if smoke["outcome"] != "finished" or args.cmd == "smoke":
        return 0 if smoke["outcome"] == "finished" else 1
    full = run(cfg, out)
    print(as_markdown(full))
    return 0 if full["outcome"] == "finished" else 1


if __name__ == "__main__":
    sys.exit(main())
