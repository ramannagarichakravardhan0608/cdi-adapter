"""OUT-S3 chaos rehearsal: send N made-up prescriptions, send them all AGAIN, stop the web app
in the middle, start it, and count - one result per upload, nothing lost, nothing doubled.

    python scripts/chaos_upload.py --host http://127.0.0.1:8888 --user intake1 \\
        --kill-cmd "<the command that stops the web app on this host>" \\
        --restart-cmd "bash infra/runpod/start_apptier.sh" --report chaos.json

The password is read from ``CDI_LOAD_PASSWORD`` (never an argument). ``--kill-cmd`` and
``--restart-cmd`` are run on THIS machine: run the script on the host whose web app is to be
stopped, or leave both out and it only checks the repeated sends. ``--corrupt K`` makes K of
the files unreadable, to see that the others finish and those K are reported.

Every page is drawn here from made-up names (``FIRST`` x ``LAST``): no real patient data.

What is counted (``verdict``), before the stop and after everything finished:

* every upload was accepted once: the second send of each answered with the FIRST job's id;
* no upload was lost: every job reached an end state after the restart;
* no document was doubled: one document id per readable file, and one result for each;
* a file that cannot be read is reported with its reason, and stops nothing else.

Exit code 0 when every count holds, 1 when one does not, 2 when the run could not be made.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import random
import subprocess
import sys
import time
from typing import Any

FIRST = ("Asha", "Bimal", "Chitra", "Dev", "Esha", "Farid", "Gita", "Hari", "Indu", "Jai")
LAST = ("Testwala", "Samplekar", "Dummyani", "Mockerjee", "Fakenath")
TESTS = ("CBC", "HbA1c", "Lipid profile", "TSH", "LFT", "KFT", "Urine routine", "FBS")
END_STATES = ("review", "error", "mismatch", "done", "approved")
CORRUPT = b"\x89PNG\r\n\x1a\n" + b"this is not a picture" * 20


def page(index: int, seed: int) -> bytes:
    """One made-up prescription as a PNG. Different for every index, the same for the same
    index and seed - so the second send is byte for byte the first."""
    from PIL import Image, ImageDraw

    rnd = random.Random(f"{seed}:{index}")
    name = f"{FIRST[index % len(FIRST)]} {LAST[(index // len(FIRST) + seed) % len(LAST)]}"
    lines = ["CITY TEST CLINIC  (rehearsal page - not a real patient)",
             f"Name: {name} CH{seed % 1000:03d}{index:03d}   Age: {rnd.randint(21, 79)} Y",
             "Diagnosis: Type 2 diabetes mellitus",
             "Adv: " + ", ".join(rnd.sample(TESTS, 3)),
             f"Review after {rnd.randint(1, 6)} weeks"]  # fmt: skip
    img = Image.new("RGB", (1240, 700), "white")
    draw = ImageDraw.Draw(img)
    for n, line in enumerate(lines):
        draw.text((60, 60 + 90 * n), line, fill="black")
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


def files_for(count: int, corrupt: int, seed: int) -> list[tuple[str, bytes]]:
    """``count`` files, the last ``corrupt`` of them unreadable (each differently, so they are
    different documents)."""
    made = []
    for i in range(count):
        bad = i >= count - corrupt
        raw = CORRUPT + f"{seed}:{i}".encode() if bad else page(i, seed)
        made.append((f"chaos_{seed}_{i:03d}{'_corrupt' if bad else ''}.png", raw))
    return made


def verdict(first: list[str], second: list[str], jobs: dict[str, dict[str, Any]],
            results: dict[str, bool], corrupt: int) -> dict[str, Any]:  # fmt: skip
    """The counts, and whether each holds. ``first`` / ``second``: the job id answered for each
    file on the first and on the second send, in file order; ``jobs``: job id -> its final
    state as ``GET /api/jobs/<id>`` gives it; ``results``: document id -> a result was served.
    Pure: the same answers give the same verdict."""
    docs = [d for j in dict.fromkeys(first) for d in (jobs.get(j) or {}).get("documents") or []]
    read = [d for d in docs if d.get("status") not in ("error", "quality_hold", "skipped")]
    failed = [d for d in docs if d.get("status") == "error"]
    doc_ids = [d.get("document_id") for d in read if d.get("document_id")]
    unfinished = [j for j in dict.fromkeys(first)
                  if (jobs.get(j) or {}).get("state") not in END_STATES]  # fmt: skip
    counts = {
        "files_sent": len(first),
        "sends": len(first) + len(second),
        "jobs": len(set(first) | set(second)),
        "jobs_unfinished": len(unfinished),
        "documents_read": len(doc_ids),
        "documents_distinct": len(set(doc_ids)),
        "results": sum(1 for d in set(doc_ids) if results.get(d)),
        "reported_unreadable": len(failed),
        "unreadable_without_a_reason": sum(1 for d in failed if not d.get("error")),
    }
    checks = {
        "a repeated send is answered with the first job": first == second,
        "one job per upload": counts["jobs"] == len(first),
        "no job was lost: all reached an end": not unfinished,
        "no document is doubled": counts["documents_read"] == counts["documents_distinct"],
        "one result per readable file": counts["results"] == len(first) - corrupt
        == counts["documents_distinct"],
        "each unreadable file is reported, with its reason":
            counts["reported_unreadable"] == corrupt
            and counts["unreadable_without_a_reason"] == 0,
    }  # fmt: skip
    return {"counts": counts, "checks": checks, "passed": all(checks.values())}


class Site:
    """The upload screen's own API, signed in as an intake account."""

    def __init__(self, host: str, user: str, password: str, timeout: float = 60.0) -> None:
        import httpx

        self.host = host.rstrip("/")
        self.http = httpx.Client(base_url=self.host, timeout=timeout,
                                 headers={"Origin": self.host})  # fmt: skip
        self.user, self.password = user, password

    def sign_in(self) -> None:
        if not self.user:
            return
        r = self.http.post("/login", data={"username": self.user, "password": self.password})
        if r.status_code != 303 or "cdi_session" not in self.http.cookies:
            raise RuntimeError(f"sign-in as {self.user!r} failed (HTTP {r.status_code})")

    def send(self, name: str, raw: bytes) -> str:
        r = self.http.post("/api/jobs", files=[("files", (name, raw, "image/png"))])
        if r.status_code != 202:
            raise RuntimeError(f"upload of {name} refused (HTTP {r.status_code})")
        return str(r.json()["job_id"])

    def job(self, job_id: str) -> dict[str, Any]:
        r = self.http.get(f"/api/jobs/{job_id}")
        r.raise_for_status()
        return dict(r.json())

    def has_result(self, document_id: str) -> bool:
        r = self.http.get(f"/api/documents/{document_id}/result.json")
        return r.status_code == 200 and r.json().get("document_id") == document_id

    def up(self) -> bool:
        try:
            return self.http.get("/readyz", timeout=5.0).status_code == 200
        except Exception:  # noqa: BLE001 - down is an answer
            return False


def snapshot(site: Any, job_ids: list[str]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for job_id in dict.fromkeys(job_ids):
        try:
            out[job_id] = site.job(job_id)
        except Exception as exc:  # noqa: BLE001 - the web app may be down at this moment
            out[job_id] = {"state": "unreachable", "documents": [], "error": str(exc)}
    return out


def tally(jobs: dict[str, dict[str, Any]]) -> dict[str, int]:
    states: dict[str, int] = {}
    for j in jobs.values():
        states[j.get("state", "?")] = states.get(j.get("state", "?"), 0) + 1
    return dict(sorted(states.items()))


def rehearse(site: Any, files: list[tuple[str, bytes]], *, corrupt: int,
             stop: Any = None, start: Any = None, wait_s: float = 900.0, poll_s: float = 2.0,
             sleep: Any = time.sleep, say: Any = print) -> dict[str, Any]:  # fmt: skip
    """The rehearsal against ``site``. ``stop`` / ``start`` stop and start the web app (None:
    it is not stopped). Returns the report: counts before the stop, after, and the verdict."""
    site.sign_in()
    first = [site.send(name, raw) for name, raw in files]
    say(f"sent {len(files)} files: {len(set(first))} jobs")
    second = [site.send(name, raw) for name, raw in files]
    say(f"sent the same {len(files)} again: {len(set(first) | set(second))} jobs in all")
    before = snapshot(site, first)
    say(f"before the stop: {tally(before)}")
    stopped = False
    if stop is not None:
        stop()
        stopped = True
        say("web app stopped in the middle of the run")
        if start is not None:
            start()
        deadline = time.monotonic() + 180
        while not site.up() and time.monotonic() < deadline:
            sleep(poll_s)
        site.sign_in()  # a restart may have ended the session
        say("web app is back")
    deadline = time.monotonic() + wait_s
    jobs = snapshot(site, first)
    while time.monotonic() < deadline and any(
            j.get("state") not in END_STATES for j in jobs.values()):  # fmt: skip
        sleep(poll_s)
        jobs = snapshot(site, first)
    third = [site.send(name, raw) for name, raw in files] if stopped else second
    doc_ids = {d["document_id"] for j in jobs.values() for d in j.get("documents") or []
               if d.get("document_id") and d.get("status") != "error"}  # fmt: skip
    results = {d: site.has_result(d) for d in sorted(doc_ids)}
    out = verdict(first, second, jobs, results, corrupt)
    out["checks"]["a send after the restart is still answered with the first job"] = third == first
    out["passed"] = all(out["checks"].values())
    out.update(before_stop=tally(before), after=tally(jobs), stopped=stopped)
    say(f"after: {tally(jobs)}")
    return out


def _shell(command: str) -> Any:
    return lambda: subprocess.run(command, shell=True, check=True)  # noqa: S602 - operator's own


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--host", required=True)
    ap.add_argument("--user", default="", help="intake account; password in CDI_LOAD_PASSWORD")
    ap.add_argument("--files", type=int, default=20)
    ap.add_argument("--corrupt", type=int, default=1)
    ap.add_argument("--seed", type=int, default=20261013)
    ap.add_argument("--kill-cmd", default="", help="stops the web app (run on this machine)")
    ap.add_argument("--restart-cmd", default="", help="starts it again")
    ap.add_argument("--wait-s", type=float, default=900.0)
    ap.add_argument("--report", default="chaos_upload.json")
    args = ap.parse_args(argv)
    if not 0 <= args.corrupt < args.files:
        ap.error("need 0 <= --corrupt < --files")
    site = Site(args.host, args.user, os.environ.get("CDI_LOAD_PASSWORD", ""))
    try:
        out = rehearse(site, files_for(args.files, args.corrupt, args.seed),
                       corrupt=args.corrupt,
                       stop=_shell(args.kill_cmd) if args.kill_cmd else None,
                       start=_shell(args.restart_cmd) if args.restart_cmd else None,
                       wait_s=args.wait_s)  # fmt: skip
    except Exception as exc:  # noqa: BLE001 - said plainly, with exit code 2
        print(f"the rehearsal could not be run: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    with open(args.report, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, sort_keys=True)
    for name, value in out["counts"].items():
        print(f"  {name:32} {value}")
    for name, held in out["checks"].items():
        print(f"  {'PASS' if held else 'FAIL'}  {name}")
    print(f"report: {args.report}")
    return 0 if out["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
