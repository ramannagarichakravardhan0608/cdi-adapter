"""Soak-test the file listener (backlog LS-S10; Pilot & Load Test sheet LT-1 to LT-4).

    # drop 200 fake prescriptions into the watched folder over two hours
    python scripts/listener_soak.py drop --count 200 --over 7200 --out results/soak

    # afterwards: where did every file end up, and how long did each take?
    python scripts/listener_soak.py report --out results/soak --title "Soak, 200 files"

The folder is the one the listener's settings name (``CDI_LISTENER_LOCAL_ROOT`` and the folder
names), so run it with the listener's own settings. Every file is a fake prescription made by
``make_sample_docs.py``'s scan maker - a made-up patient, and each one different, so no two are
the same document. ``drop`` writes each file down as it goes; ``report`` reads the folder, the
listener's table and the jobs, and writes ``report.md`` and ``report.json`` beside the drops.
Breaking things on purpose (killing the listener, stopping the model server) is done from
outside while ``drop`` runs; ``report`` judges the result the same way whatever happened.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from make_sample_docs import scanify  # noqa: E402

from cdi_adapter.config import get_settings  # noqa: E402
from cdi_adapter.listener import soak  # noqa: E402

FIRST = ("Asha", "Ravi", "Meera", "Arjun", "Kavya", "Suresh", "Nisha", "Vikram", "Lata", "Imran")
LAST = ("Soaktest", "Dummy", "Sample", "Fictional")
DRUGS = (("Metformin", 500), ("Amlodipine", 5), ("Atorvastatin", 10), ("Pantoprazole", 40),
         ("Paracetamol", 650), ("Cetirizine", 10), ("Telmisartan", 40),
         ("Azithromycin", 500))  # fmt: skip


def fake_prescription(run: str, n: int, seed: int) -> tuple[str, bytes]:
    """One fake prescription as a scanned-looking PNG. The reference line makes it unique."""
    rnd = random.Random(f"{seed}:{run}:{n}")
    lines = ["CareFlow Polyclinic - Rx   (FAKE - SOAK TEST)", f"Ref: SOAK-{run}-{n:04d}",
             f"Name: {rnd.choice(FIRST)} {rnd.choice(LAST)}   Age/Sex: {rnd.randint(21, 79)} Y / "
             f"{rnd.choice('MF')}", f"Date: {rnd.randint(1, 28):02d}/09/2026", "Rx"]  # fmt: skip
    for drug, mg in rnd.sample(DRUGS, rnd.randint(2, 4)):
        lines.append(f"Tab {drug} {mg} mg {rnd.choice(('1-0-1', '1-0-0', '0-0-1'))} "
                     f"x {rnd.choice((5, 7, 10, 30))} days")  # fmt: skip
    lines.append("Review after 2 weeks")
    return f"soak-{run}-{n:04d}.png", scanify(lines, seed=rnd.randrange(10**6))


def _names() -> tuple[Path, dict[str, str]]:
    s = get_settings()
    names = {"inbox": s.listener_inbox, "processing": s.listener_processing,
             "success": s.listener_success, "error": s.listener_error,
             "quarantine": s.listener_quarantine, "rescan": "rescan"}  # fmt: skip
    return Path(s.listener_local_root), names


def drop(args: argparse.Namespace) -> int:
    root, names = _names()
    inbox = root / names["inbox"] if names["inbox"] else root
    run = args.run or time.strftime("%m%d%H%M")
    started = time.time()
    for n, offset in enumerate(soak.offsets(args.count, args.over)):
        wait = started + offset - time.time()
        if wait > 0:
            time.sleep(wait)
        name, data = fake_prescription(run, n, args.seed)
        soak.note(args.out, soak.place(inbox, name, data))
        print(f"{time.strftime('%H:%M:%S')} dropped {n + 1}/{args.count} {name}", flush=True)
    return 0


def report(args: argparse.Namespace) -> int:
    from sqlalchemy import text

    from cdi_adapter.db import session_scope
    from cdi_adapter.listener.connectors import LocalConnector

    root, names = _names()
    dropped = soak.drops(args.out)
    source = LocalConnector.from_settings(get_settings()).source_id
    first = min((d.at for d in dropped), default=time.time())
    with session_scope() as c:
        rows = [dict(r) for r in c.execute(text(
            "SELECT name, original_name, state, document_id, history FROM listener_file "
            "WHERE source_id = :s"), {"s": source}).mappings()]  # fmt: skip
        jobs = [dict(r) for r in c.execute(text(
            "SELECT id, files, docs FROM web_job WHERE created_at >= to_timestamp(:t) - interval "
            "'10 minutes'"), {"t": first}).mappings()]  # fmt: skip
    rep = soak.reconcile(dropped, soak.folders(root, names), rows, jobs)
    notes = [f"watched folder: {root}", f"listener source: {source}", *args.note]
    page = soak.render(rep, title=args.title, notes=notes)
    (args.out / "report.md").write_text(page, encoding="utf-8", newline="\n")
    (args.out / "report.json").write_text(json.dumps(rep, indent=1, default=str),
                                          encoding="utf-8", newline="\n")  # fmt: skip
    print(page)
    return 0 if soak.passed(rep) else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)
    d = sub.add_parser("drop", help="drop fake prescriptions at a steady rate")
    d.add_argument("--count", type=int, required=True)
    d.add_argument("--over", type=float, required=True, help="seconds to spread the drops over")
    d.add_argument("--out", type=Path, required=True, help="results folder (drops.jsonl)")
    d.add_argument("--run", default="", help="a name for this run, used in the file names")
    d.add_argument("--seed", type=int, default=7)
    r = sub.add_parser("report", help="where every dropped file ended, and how long it took")
    r.add_argument("--out", type=Path, required=True, help="the results folder of the drop")
    r.add_argument("--title", default="Listener soak test")
    r.add_argument("--note", action="append", default=[], help="a line for the report")
    args = ap.parse_args(argv)
    return drop(args) if args.command == "drop" else report(args)


if __name__ == "__main__":
    sys.exit(main())
