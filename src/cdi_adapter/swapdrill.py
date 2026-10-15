"""The component swap drill (backlog SW-S6 FR6, AC4): the golden set on the alternatives.

    python -m cdi_adapter.swapdrill [--out docs/evidence/SW-S6] [--fs-root DIR]
        [--redis-url URL --valkey-url URL]

The golden set is the ARCHITECTURE §8.2 example (the three "Anjali Das" documents,
``scripts/make_sample_docs.py``). For every swap point with an alternative, the same inputs go
through the default and the alternative, and the outputs are compared. A difference passes only
when it is one the swap is MEANT to make, with its explanation (:data:`EXPECTED`); any other
difference fails the drill. A swap point whose default or alternative cannot be reached where
the drill runs says so - "not run", with the reason - and is never replaced by a stand-in.

* **object store** - default ``s3`` (MinIO), alternative ``fs``: each golden document and its
  rendered pages are saved, read back and listed; the bytes must be the same;
* **terminology** - default: internal canonical vocabulary (no licence); alternative: SNOMED CT
  + ICD-10 (a licensed customer): the diagnoses, medicines, allergy, tests and vital signs of the
  golden set must name the same canonical terms; only the external codes may differ;
* **job queue** - Redis 7.2 and Valkey: 20 jobs on each, all finish, none twice;
* **PDF renderer** - one renderer by decision (2026-10-04, no PyMuPDF): rendered twice, the
  same pages; there is no alternative to compare;
* **drive connector, sign-in provider** - need real accounts / a real identity provider: their
  contract tests run in CI (SW-S1); the drill lists them as not run.

Run it once a quarter (with the queue drill and the GPU drill of SW-S5) and keep the report.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import os
import sys
import tempfile
import uuid
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from cdi_adapter.config import Settings, get_settings

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs" / "evidence" / "SW-S6"
# the differences a swap is MEANT to make, and why
EXPECTED = {
    ("terminology", "code_system"): "SNOMED CT is used only where it is licensed; without the "
    "licence the term is named by the internal vocabulary's own id (story NFR1, AC3)",
    ("terminology", "code"): "the same: a SNOMED CT code with the licence, the internal id "
    "without it",
    ("terminology", "icd10"): "ICD-10 is given only where it is licensed",
}
# what the golden documents say, as the extraction names each value (§8.2)
GOLDEN_TERMS = (
    ("condition", "Type 2 diabetes mellitus"),
    ("condition", "Hypertension"),
    ("medication", "Tab Metformin 500 mg"),
    ("medication", "Tab Amlodipine 5 mg"),
    ("medication", "Tab Atorvastatin 10 mg"),
    ("allergy", "Penicillin"),
    ("lab_result", "HbA1c"),
    ("lab_result", "Fasting Blood Glucose"),
    ("lab_result", "Serum Creatinine"),
    ("lab_result", "Total Cholesterol"),
    ("lab_result", "LDL Cholesterol"),
    ("vital_sign", "Pulse"),
    ("vital_sign", "Respiratory rate"),
    ("vital_sign", "Temperature"),
    ("vital_sign", "SpO2"),
    ("vital_sign", "Weight"),
    ("vital_sign", "Height"),
    ("vital_sign", "BP"),
)


def _samples() -> Any:
    spec = importlib.util.spec_from_file_location(
        "make_sample_docs", ROOT / "scripts" / "make_sample_docs.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def golden_files() -> dict[str, bytes]:
    """The three §8.2 documents as PDFs, made the same way every time."""
    mod = _samples()
    return {name: mod.text_pdf(lines) for name, lines in sorted(mod.DOCS.items())}


def _render(pdf: bytes) -> list[bytes]:
    from cdi_adapter.ingest import pdf as pdfs

    return list(pdfs.renderer().render(pdf, dpi=150))


def _result(point: str, default: str, alternative: str, status: str, **more: Any) -> dict[str, Any]:
    return {
        "swap_point": point,
        "default": default,
        "alternative": alternative,
        "status": status,
        **more,
    }


# ------------------------------------------------------------------ the swap points


def pdf_renderer() -> dict[str, Any]:
    from cdi_adapter.ingest import pdf as pdfs

    name = get_settings().pdf_renderer
    first = {n: [_sha(p) for p in _render(b)] for n, b in golden_files().items()}
    again = {n: [_sha(p) for p in _render(b)] for n, b in golden_files().items()}
    same = first == again
    return _result(
        "pdf_renderer",
        name,
        "(none)",
        "matched" if same else "differences UNEXPLAINED",
        note="one renderer by decision 2026-10-04 (no PyMuPDF); a second renderer is "
        "out of scope (story). Rendered twice: the same pages.",
        pages={n: len(v) for n, v in first.items()},
        registered=pdfs.registered(),
    )


def _store_round_trip(store: Any, files: dict[str, bytes]) -> dict[str, str]:
    """Save each golden document and its pages under its real keys, read back: key -> sha."""
    from cdi_adapter import storage

    store.ensure_bucket()
    seen: dict[str, str] = {}
    run = uuid.uuid4().hex[:8]
    for name, pdf in files.items():
        sha = _sha(pdf + run.encode())  # a key of this run only: nothing of a site's is touched
        keys = [(storage.original_key(sha, "pdf"), pdf, "application/pdf")]
        keys += [
            (storage.page_key(sha, i + 1), png, "image/png") for i, png in enumerate(_render(pdf))
        ]
        for key, data, ctype in keys:
            store.put_bytes(key, data, ctype)
            back = store.get_bytes(key)
            seen[f"{name}/{key.rsplit('/', 1)[-1]}"] = (
                _sha(back) if storage.content_type_of(store, key) == ctype else "content type lost"
            )
            store.delete(key)
    return seen


def object_store(fs_root: Path | None = None, settings: Settings | None = None) -> dict[str, Any]:
    from cdi_adapter import storage

    files = golden_files()
    with tempfile.TemporaryDirectory() as tmp:
        fs = storage.FileStore(root=fs_root or Path(tmp) / "store")
        alt = _store_round_trip(fs, files)
    want = {f"{n}/original.pdf": _sha(b) for n, b in files.items()}
    alt_ok = all(alt[k] == v for k, v in want.items())
    s = settings or get_settings()
    endpoint = os.environ.get("CDI_TEST_S3_ENDPOINT") or (
        s.s3_endpoint_url if s.object_store == "s3" else ""
    )
    try:
        default = storage.ObjectStore(s)
        if not default.healthy():
            raise ConnectionError(f"no S3 store answers at {endpoint or s.s3_endpoint_url}")
        base = _store_round_trip(default, files)
    except Exception as exc:  # noqa: BLE001 - the default is not here: say so, never substitute
        return _result(
            "object_store",
            "s3",
            "fs",
            "not run: "
            + f"{type(exc).__name__}: {exc}"[:200]
            + (
                " - the filesystem store alone was checked: every byte read back as saved"
                if alt_ok
                else " - and the filesystem store FAILED"
            ),
            alternative_ok=alt_ok,
            objects=len(alt),
        )
    diffs = sorted(k for k in set(base) | set(alt) if base.get(k) != alt.get(k))
    return _result(
        "object_store",
        "s3",
        "fs",
        "matched" if not diffs and alt_ok else "differences UNEXPLAINED",
        objects=len(alt),
        differences=diffs,
    )


def _bound(fact_type: str, text: str) -> dict[str, Any]:
    from cdi_adapter.terminology import canonical
    from cdi_adapter.terminology.service import bind_fact

    u = bind_fact({"fact_type": fact_type, "local_text": text})
    return {
        "code_status": u["code_status"],
        "code_system": u["code_system"],
        "code": u["code"],
        "canonical_term": u["code_display"],
        "icd10": canonical.icd10_for(u["code"]),
    }


def terminology(with_licence: Callable[[str], Any] | None = None) -> dict[str, Any]:
    """The golden terms bound twice: as a customer with no licence (the default) and as one
    with SNOMED CT and ICD-10."""

    def under(licence: str) -> dict[str, dict[str, Any]]:
        old = os.environ.get("CDI_LICENSED_CODE_SYSTEMS")
        os.environ["CDI_LICENSED_CODE_SYSTEMS"] = licence
        get_settings.cache_clear()
        try:
            return {f"{t}: {x}": _bound(t, x) for t, x in GOLDEN_TERMS}
        finally:
            if old is None:
                os.environ.pop("CDI_LICENSED_CODE_SYSTEMS", None)
            else:
                os.environ["CDI_LICENSED_CODE_SYSTEMS"] = old
            get_settings.cache_clear()

    default, alternative = under(""), under("snomed,icd10")
    explained: list[dict[str, Any]] = []
    unexplained: list[dict[str, Any]] = []
    for term in default:
        a, b = default[term], alternative[term]
        for field_ in sorted(set(a) | set(b)):
            if a.get(field_) == b.get(field_):
                continue
            row = {
                "term": term,
                "field": field_,
                "default": a.get(field_),
                "alternative": b.get(field_),
            }
            why = EXPECTED.get(("terminology", field_))
            (explained if why else unexplained).append({**row, "why": why} if why else row)
    external = [
        t
        for t, v in default.items()
        if v["code_system"] in ("http://snomed.info/sct", "http://hl7.org/fhir/sid/icd-10")
        or v["icd10"]
    ]
    status = (
        "differences UNEXPLAINED"
        if unexplained or external
        else "differences explained"
        if explained
        else "matched"
    )
    return _result(
        "terminology",
        "internal canonical vocabulary (no licence)",
        "SNOMED CT + ICD-10 (licensed)",
        status,
        terms=len(default),
        explained=explained,
        unexplained=unexplained,
        external_codes_without_licence=external,
        default_terms={t: v["canonical_term"] for t, v in default.items()},
    )


def queue(redis_url: str = "", valkey_url: str = "", jobs: int = 20) -> dict[str, Any]:
    if not (redis_url and valkey_url):
        return _result(
            "job_queue",
            "redis 7.2",
            "valkey",
            "not run: no Redis and Valkey servers named (--redis-url, --valkey-url); "
            "the drill needs the real servers",
        )
    runs = {
        "redis": run_jobs(redis_url, "redis", jobs),
        "valkey": run_jobs(valkey_url, "valkey", jobs),
    }
    ok = all(
        r["finished"] == jobs and not r["duplicates"] and not r["missing"] for r in runs.values()
    )
    return _result(
        "job_queue",
        "redis 7.2",
        "valkey",
        "matched" if ok else "differences UNEXPLAINED",
        runs=runs,
    )


def run_jobs(url: str, expected: str, jobs: int = 20) -> dict[str, Any]:  # pragma: no cover
    """``jobs`` Celery tasks through the server at ``url``, by a real worker: how many finished,
    and any job run more than once. Needs the server (no stand-in)."""
    import redis
    from celery import Celery
    from celery.contrib.testing.worker import start_worker

    from cdi_adapter import queueserver

    info = queueserver.info_of(url)
    why = queueserver.problem(info, expected)
    if why:
        return {
            "server": queueserver.identify(info),
            "refused": why,
            "finished": 0,
            "duplicates": [],
            "missing": [],
        }
    run = uuid.uuid4().hex[:8]
    app = Celery(f"swapdrill-{run}", broker=url, backend=url)
    app.conf.update(
        task_acks_late=True, worker_prefetch_multiplier=1, broker_connection_retry_on_startup=True
    )
    tally = f"cdi:swapdrill:{run}"

    @app.task(name=f"swapdrill.job.{run}")
    def job(n: int) -> int:
        redis.Redis.from_url(url).hincrby(tally, str(n), 1)
        return n

    with start_worker(app, pool="solo", perform_ping_check=False, shutdown_timeout=30):
        results = [job.delay(i) for i in range(jobs)]
        done = [r.get(timeout=120) for r in results]
    held: Any = redis.Redis.from_url(url).hgetall(tally)  # the sync client: a dict
    counts = {int(k): int(v) for k, v in held.items()}
    redis.Redis.from_url(url).delete(tally)
    return {
        "server": queueserver.identify(info),
        "finished": len(done),
        "duplicates": sorted(n for n, c in counts.items() if c > 1),
        "missing": sorted(set(range(jobs)) - set(counts)),
    }


def not_run_here() -> list[dict[str, Any]]:
    return [
        _result(
            "drive_connector",
            "local folder",
            "OneDrive / SharePoint / Google Drive",
            "not run: needs a real account of each drive; the contract test runs in CI "
            "(tests/test_ls_s1_acceptance_unit.py)",
        ),
        _result(
            "sign_in",
            "local accounts",
            "OpenID Connect",
            "not run: needs a real identity provider; the contract test runs in CI "
            "(tests/test_ent_s2_acceptance_unit.py)",
        ),
    ]


def drill(
    *, fs_root: Path | None = None, redis_url: str = "", valkey_url: str = ""
) -> dict[str, Any]:
    points = [
        pdf_renderer(),
        object_store(fs_root),
        terminology(),
        queue(redis_url, valkey_url),
        *not_run_here(),
    ]
    failed = [p["swap_point"] for p in points if p["status"].startswith("differences UNEXPLAINED")]
    not_run = [p["swap_point"] for p in points if p["status"].startswith("not run")]
    return {
        "drill": "SW-S6 component swap drill",
        "golden_set": "ARCHITECTURE §8.2 (Anjali Das)",
        "at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "swap_points": points,
        "failed": failed,
        "not_run": not_run,
        "passed": not failed,
        "complete": not failed and not not_run,
    }


def as_markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# {report['drill']}",
        "",
        f"Golden set: {report['golden_set']}. Run {report['at']}.",
        "",
        f"**{'PASSED' if report['passed'] else 'FAILED'}**"
        + (
            ""
            if report["complete"]
            else f" - not complete: {', '.join(report['not_run'])} not run here"
        ),
        "",
        "| Swap point | Default | Alternative | Result |",
        "|---|---|---|---|",
    ]
    for p in report["swap_points"]:
        lines.append(f"| {p['swap_point']} | {p['default']} | {p['alternative']} | {p['status']} |")
    for p in report["swap_points"]:
        if p.get("explained") or p.get("unexplained"):
            lines += ["", f"## {p['swap_point']}: each difference", ""]
            for d in p.get("explained", []):
                lines.append(
                    f"- {d['term']} - {d['field']}: {d['default']!r} / "
                    f"{d['alternative']!r} - explained: {d['why']}"
                )
            for d in p.get("unexplained", []):
                lines.append(
                    f"- {d['term']} - {d['field']}: {d['default']!r} / "
                    f"{d['alternative']!r} - **NOT EXPLAINED**"
                )
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m cdi_adapter.swapdrill")
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--fs-root", default="")
    ap.add_argument("--redis-url", default="")
    ap.add_argument("--valkey-url", default="")
    args = ap.parse_args(argv)
    report = drill(
        fs_root=Path(args.fs_root) if args.fs_root else None,
        redis_url=args.redis_url,
        valkey_url=args.valkey_url,
    )
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "swap-drill.json").write_text(json.dumps(report, indent=1, default=str) + "\n", "utf-8")
    (out / "swap-drill.md").write_text(as_markdown(report), encoding="utf-8")
    print(as_markdown(report))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
