"""Burst test (backlog RD-S5): N single-page prescriptions sent in the same second, on one GPU.

Run ON the GPU host, with the app tier's settings, so the documents go through the real pipeline:

    cd /workspace/cdi && set -a && . ./.env && set +a
    CDI_SECRETS_DIR=/run/cdi-secrets .venv/bin/python scripts/burst_test.py \\
        --count 10 --price 0.366 --label "hf, serial" --out /workspace/rd-s5/hf-10

    ... --count 20                         # overload: the later ones must show "waiting"
    ... --kill-gpu-worker-after 20         # a GPU worker killed mid-run, started again 5 s later

Every document is a fake prescription (``listener_soak.fake_prescription``: a made-up patient,
each one different), submitted as its own upload in the same second. The run watches the jobs
until each has finished, samples GPU memory every half second, and writes ``report.md`` and
``report.json``: seconds from upload to saved result for each document, how many within 60 s,
whether each was seen "waiting", none lost, none doubled, GPU memory, and the cost per document
at ``--price``. Every figure is tagged MEASURED with the GPU's name and the date.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from listener_soak import fake_prescription  # noqa: E402

from cdi_adapter.loadtest_burst import Burst, judge, render  # noqa: E402

ACTOR = "burst-test"


def gpu_sampler(stop: threading.Event, out: dict[str, Any]) -> None:
    """GPU memory every half second, and the GPU's name."""
    used: list[int] = []
    while not stop.is_set():
        try:
            line = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.used,memory.total",
                                   "--format=csv,noheader,nounits"], capture_output=True,
                                  text=True, timeout=5).stdout.splitlines()[0]  # fmt: skip
            name, mem, total = (x.strip() for x in line.split(","))
            out.update(name=name, total_mib=int(total))
            used.append(int(mem))
        except Exception:  # noqa: BLE001 - a missed sample is a missed sample
            pass
        stop.wait(0.5)
    out["used_mib"] = used


def gpu_worker_pids() -> list[int]:
    # "--": a pattern starting with "-" is not an option (the first kill test killed nothing)
    look = ["pgrep", "-f", "--", "-Q cdi-gpu"]
    found = subprocess.run(look, capture_output=True, text=True).stdout
    return [int(p) for p in found.split()]


def kill_and_restart(after: float, record: dict[str, Any]) -> None:
    """Kill one GPU worker (SIGKILL: no goodbye) ``after`` seconds in, start it again 5 s later
    with the same command, environment and log - as a supervisor would."""
    time.sleep(after)
    pids = gpu_worker_pids()
    if not pids:
        record["kill"] = "no GPU worker was running"
        return
    pid = pids[0]
    cmd = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")[:-1]
    env = dict(e.split(b"=", 1) for e in Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
               if b"=" in e)  # fmt: skip
    cwd = os.readlink(f"/proc/{pid}/cwd")
    log = os.readlink(f"/proc/{pid}/fd/1")
    os.kill(pid, signal.SIGKILL)
    record.update(killed_pid=pid, killed_at=time.time())
    time.sleep(5)
    with open(log, "ab") as out:
        new = subprocess.Popen(cmd, env={k.decode(): v.decode() for k, v in env.items()}, cwd=cwd,
                               stdout=out, stderr=out, start_new_session=True)  # fmt: skip
    record.update(restarted_pid=new.pid, restarted_at=time.time())
    # the pid file the start scripts keep must name the new worker, or the next code sync starts
    # a second one beside it (it did, the first time this test ran)
    for pidfile in Path(os.environ.get("WS", "/workspace"), "run").glob("gpu-*.pid"):
        if pidfile.read_text().strip() == str(pid):
            pidfile.write_text(str(new.pid) + os.linesep)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--count", type=int, default=10)
    ap.add_argument("--price", type=float, required=True, help="the GPU host's $ per hour")
    ap.add_argument("--label", default="", help="what was running: the model server, settings")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--timeout", type=float, default=1200, help="seconds to wait for every job")
    ap.add_argument("--kill-gpu-worker-after", type=float, default=None)
    args = ap.parse_args(argv)

    from sqlalchemy import text

    from cdi_adapter.db import session_scope
    from cdi_adapter.webapp import jobstore

    run = datetime.now(UTC).strftime("%m%d%H%M%S")
    files = [fake_prescription(f"burst{run}", n, 7) for n in range(args.count)]
    gpu: dict[str, Any] = {}
    stop = threading.Event()
    sampler = threading.Thread(target=gpu_sampler, args=(stop, gpu), daemon=True)
    sampler.start()
    time.sleep(1.5)  # a baseline sample before anything is sent
    fault: dict[str, Any] = {}
    if args.kill_gpu_worker_after is not None:
        threading.Thread(target=kill_and_restart, args=(args.kill_gpu_worker_after, fault),
                         daemon=True).start()  # fmt: skip

    started = time.time()
    with ThreadPoolExecutor(max_workers=args.count) as pool:  # all in the same second
        jobs = list(pool.map(lambda f: jobstore.submit([(f[0], f[1], "image/png")], actor=ACTOR),
                             files))  # fmt: skip
    sent = time.time() - started
    ids = [j.id for j in jobs]
    waiting: dict[str, bool] = dict.fromkeys(ids, False)
    rows: dict[str, Any] = {}
    deadline = time.time() + args.timeout
    while time.time() < deadline:
        with session_scope() as c:
            got = c.execute(text(
                "SELECT id, state, created_at, finished_at, docs, attempts FROM web_job "
                "WHERE id = ANY(:i)"), {"i": ids}).mappings().all()  # fmt: skip
        for r in got:
            rows[r["id"]] = dict(r)
            if any((d or {}).get("step") == "waiting" for d in (r["docs"] or [])):
                waiting[r["id"]] = True
        if len(rows) == len(ids) and all(r["finished_at"] for r in rows.values()):
            break
        time.sleep(0.5)
    stop.set()
    sampler.join(timeout=5)

    doc_ids = [str((rows.get(i, {}).get("docs") or [{}])[0].get("document_id")) for i in ids]
    once = text(
        "SELECT d.id, (SELECT count(*) FROM extraction e WHERE e.document_id = d.id), "
        "(SELECT count(*) FROM clinical_fact f WHERE d.id = ANY(f.source_doc_ids) "
        "AND f.is_current), (SELECT count(*) FROM rv_item i WHERE i.source_document_id = d.id) "
        "FROM source_document d WHERE d.id::text = ANY(:docs)")  # fmt: skip
    with session_scope() as c:  # what each document became: once, and only once?
        per_doc = {str(r[0]): (int(r[1]), int(r[2]), int(r[3]))
                   for r in c.execute(once, {"docs": doc_ids}).all()}  # fmt: skip

    burst = Burst(label=args.label, count=args.count, sent_seconds=round(sent, 3),
                  price_per_hour=args.price, gpu=gpu, fault=fault,
                  date=datetime.now(UTC).strftime("%Y-%m-%d"))  # fmt: skip
    report = judge(burst, [rows.get(i) for i in ids], waiting=[waiting[i] for i in ids],
                   per_doc=per_doc)  # fmt: skip
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "report.json").write_text(json.dumps(report, indent=1, default=str),
                                          encoding="utf-8", newline="\n")  # fmt: skip
    page = render(report)
    (args.out / "report.md").write_text(page, encoding="utf-8", newline="\n")
    print(page)
    return 0


if __name__ == "__main__":
    sys.exit(main())
