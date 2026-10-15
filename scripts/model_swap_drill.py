"""The SW-S5 swap drill with real models on the GPU host - shadow, switch, roll back.

Runs ``cdi_adapter.modelops.drill.run`` (the same steps the integration test runs with stub
models) on synthetic prescriptions (no patient data), with two registered versions each in its
own gateway process, and keeps the record in ``docs/evidence/SW-S5/drill-gpu-<date>.json``.
Runbook: ``docs/drill.md``. Do it once a quarter (story NFR3).

    python scripts/model_swap_drill.py --model-id <A> --revision <sha> \\
        [--candidate-model-id <B> --candidate-revision <sha>] --running 5

Both models must be in ``models/registry.json`` with their licence text saved: the switch and
shadow mode refuse a model without one. Two 7B models do not fit one 24 GB L4 together; with no
second model, the drill registers the same weights twice (what is drilled is then the routing,
the record of which version answered, and the rollback - not a model difference). Needs
PostgreSQL + MinIO and the GPU free of other gateways.
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def _rollout_drill() -> Any:
    spec = importlib.util.spec_from_file_location(
        "model_rollout_drill", ROOT / "scripts" / "model_rollout_drill.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - GPU host
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--model-id", required=True)
    ap.add_argument("--revision", required=True)
    ap.add_argument("--candidate-model-id", default="")
    ap.add_argument("--candidate-revision", default="")
    ap.add_argument("--backend", default="hf", choices=("hf", "vllm"))
    ap.add_argument("--champion-url", default="http://127.0.0.1:8079")
    ap.add_argument("--candidate-url", default="http://127.0.0.1:8078")
    ap.add_argument("--running", type=int, default=5)
    args = ap.parse_args(argv)

    from cdi_adapter.classify.service import classify_document
    from cdi_adapter.extract.service import extract_document
    from cdi_adapter.ingest.service import ingest_bytes
    from cdi_adapter.modelops import drill, ops
    from cdi_adapter.modelops.registry import RolloutError
    from cdi_adapter.ocr.service import ocr_document

    helper = _rollout_drill()
    run = dt.datetime.now(dt.UTC).strftime("%Y%m%d-%H%M%S")
    a, b = f"swap-a-{run}", f"swap-b-{run}"
    ops.register(
        actor="drill",
        version=a,
        model_id=args.model_id,
        revision=args.revision,
        backend=args.backend,
        notes="SW-S5 swap drill: champion",
    )
    ops.register(
        actor="drill",
        version=b,
        model_id=args.candidate_model_id or args.model_id,
        revision=args.candidate_revision or args.revision,
        backend=args.backend,
        notes="SW-S5 swap drill: candidate",
    )
    counter = [0]

    def new_document(tag: str) -> str:
        counter[0] += 1
        pdf = helper._samples().text_pdf(helper.prescription(counter[0]) + [f"Ref: {run}-{tag}"])
        doc = str(ingest_bytes(pdf, f"swap-{run}-{tag}.pdf", source_channel="drill").document_id)
        ocr_document(doc, force_engine="rapidocr")
        classify_document(doc)
        return doc

    procs: list[subprocess.Popen[bytes]] = []
    record: dict[str, Any]
    try:
        ops.serve(a, args.champion_url, launch=lambda v, u: procs.append(ops.launch_gateway(v, u)))
        ops.serve(b, args.candidate_url, launch=lambda v, u: procs.append(ops.launch_gateway(v, u)))
        record = drill.run(
            a,
            b,
            args.champion_url,
            args.candidate_url,
            new_document,
            lambda d: extract_document(d),
            running=args.running,
        )
    except RolloutError as exc:
        record = {"drill": "SW-S5 swap drill", "passed": False, "refused": str(exc), "checks": []}
    finally:
        for p in procs:  # the drill's gateways only; the site's own are never touched
            p.terminate()
        for p in procs:
            try:
                p.wait(60)
            except subprocess.TimeoutExpired:
                p.kill()
    record.update(
        {
            "where": "GPU host",
            "model_id": args.model_id,
            "revision": args.revision,
            "candidate_model_id": args.candidate_model_id or args.model_id,
        }
    )
    out = ROOT / "docs" / "evidence" / "SW-S5"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"drill-gpu-{run}.json"
    path.write_text(json.dumps(record, indent=1, default=str) + "\n", encoding="utf-8")
    for c in record.get("checks", []):
        print(f"{'ok  ' if c['ok'] else 'FAIL'} {c['check']}")
    if record.get("refused"):
        print("REFUSED:", record["refused"])
    print("SWAP DRILL:", "PASS" if record["passed"] else "FAIL", "-", path)
    return 0 if record["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
