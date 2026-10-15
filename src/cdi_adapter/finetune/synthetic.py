"""A made-up training set, for smoke tests (SW-S3).

Every picture is drawn here from made-up words: no patient, no real prescription, nothing that
needs usage rights. It proves the command, the formats and the GPU memory - it does not teach
the model anything useful. The real training set is the de-identified one (FT-S1, DS-S1).

    python -m cdi_adapter.finetune.synthetic data/train/synthetic-smoke --pages 60 --lines 60
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

DRUGS = (
    "Paracetamol",
    "Amoxicillin",
    "Cetirizine",
    "Pantoprazole",
    "Metformin",
    "Azithromycin",
    "Ibuprofen",
    "Atorvastatin",
    "Amlodipine",
    "Ondansetron",
)
STRENGTHS = ((500, "mg"), (250, "mg"), (10, "mg"), (40, "mg"), (5, "mg"))
FREQUENCIES = ("1-0-1", "1-1-1", "0-0-1", "1-0-0", "SOS")
DURATIONS = ("3 days", "5 days", "7 days", "1 month")


def _picture(lines: list[str], size: tuple[int, int]) -> Any:
    from PIL import Image, ImageDraw

    image = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(image)
    for i, line in enumerate(lines):
        draw.text((16, 12 + 22 * i), line, fill="black")
    return image


def _medicine(rng: random.Random) -> dict[str, Any]:
    value, unit = rng.choice(STRENGTHS)
    return {
        "drug_name": rng.choice(DRUGS),
        "strength": {"value": value, "unit": unit},
        "frequency": rng.choice(FREQUENCIES),
        "duration": rng.choice(DURATIONS),
        "confidence": 1.0,
    }


def _line(m: dict[str, Any]) -> str:
    s = m["strength"]
    return f"Tab {m['drug_name']} {s['value']}{s['unit']} {m['frequency']} x {m['duration']}"


def make(folder: Path, pages: int = 60, lines: int = 60, seed: int = 7) -> Path:
    """Write a dataset of ``pages`` extraction examples and ``lines`` line-reader examples."""
    rng = random.Random(seed)
    (folder / "images").mkdir(parents=True, exist_ok=True)
    rows = []
    for i in range(pages):
        meds = [_medicine(rng) for _ in range(rng.randint(1, 4))]
        name = f"images/page-{i:03d}.png"
        _picture(["Rx  (synthetic - not a patient)", *[_line(m) for m in meds]], (640, 160)).save(
            folder / name
        )
        rows.append(
            {
                "id": f"page-{i:03d}",
                "role": "extraction",
                "image": name,
                "target": {"medications": meds, "confidence": 1.0},
            }
        )
    for i in range(lines):
        text = _line(_medicine(rng))
        name = f"images/line-{i:03d}.png"
        _picture([text], (480, 40)).save(folder / name)
        rows.append({"id": f"line-{i:03d}", "role": "line_reader", "image": name, "target": text})
    (folder / "examples.jsonl").write_text(
        "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows), encoding="utf-8"
    )
    manifest = {
        "name": folder.name,
        "data_class": "synthetic",
        "schema": "prescription.v3",
        "rights": {
            "may_train": True,
            "source": "drawn by cdi_adapter.finetune.synthetic from made-up words",
        },
        "seed": seed,
    }
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    return folder


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m cdi_adapter.finetune.synthetic")
    ap.add_argument("folder")
    ap.add_argument("--pages", type=int, default=60)
    ap.add_argument("--lines", type=int, default=60)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args(argv)
    print(make(Path(args.folder), args.pages, args.lines, args.seed))
    return 0


if __name__ == "__main__":
    sys.exit(main())
