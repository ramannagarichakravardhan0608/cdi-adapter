"""ENT-S1: the licence audit and the licence gate. NOT LEGAL ADVICE - it prepares the evidence;
counsel decides.

    python scripts/licence_audit.py check             # the gate, every build (exit 1 = blocked)
    python scripts/licence_audit.py check --release   # + nothing unreviewed, every decision
                                                      #   applied or owned and dated, signed off
    python scripts/licence_audit.py check --report D  # also writes D/licence-report.{json,md}
    python scripts/licence_audit.py record            # add what is installed HERE to
                                                      #   licences/packages.json (run on the pod)
    python scripts/licence_audit.py notice            # (re)write the NOTICE file
    python scripts/licence_audit.py bundle --out DIR  # NOTICE + licence texts + SBOM
    python scripts/licence_audit.py inspect PATH      # is an installed package complete?
    python scripts/licence_audit.py sbom --out FILE   # what is installed HERE (deployed image)
    python scripts/licence_audit.py swap-gate         # SW-S7: what THIS change adds or alters
                                                      #   to the build must pass (exit 1 = no)
    python scripts/licence_audit.py swap-gate --accept --by NAME   # record the new baseline

What is audited - found in the build files, never typed from memory:

* every Python package named in ``pyproject.toml`` and the three pod lock files;
* every server and binary pinned in ``infra/*/versions.env`` and the compose file;
* every model the configuration names; the data sets and code lists in the register.

Where the facts live (``licences/``): ``policy.json`` (the written policy), ``packages.json``
(each package's licence as READ from its metadata, with where and when), ``register.json``
(everything that is not a Python package, the decisions, the sign-off) and
``nvidia_redistributable.json`` (the files an installed package may carry).

The rules:

* a licence is never guessed: no evidence -> ``UNKNOWN`` -> blocked;
* a component of the build with no entry -> blocked (AC4);
* a licence on the review list (GPL, AGPL, SSPL, non-commercial, ...) or one the policy does
  not name -> blocked until a decision is recorded with who and when (AC2);
* a decision is APPLIED when its check passes on the repository; one that is not must carry
  an owner and a date (AC3) - checked by ``--release``;
* an entry whose licence was not read from its source is ``unreviewed`` (AC1, ``--release``).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import subprocess
import sys
import tarfile
import zipfile
from datetime import date
from importlib import metadata
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
LOCKS = (
    "infra/runpod/requirements.lock",
    "infra/runpod/requirements-vllm.lock",
    "infra/runpod/requirements-quant.lock",
)
# extras that are tools of the developer, not parts of what is sold
TOOL_EXTRAS = ("dev", "load")
# SW-S7: what a licence field means is read in ONE place, shared with the service's swap gate
# (a champion, a contest, a switch). Run from a checkout with nothing installed (CI): src/ is
# put on the path first.
sys.path.insert(0, str(ROOT / "src"))
from cdi_adapter.licences import (  # noqa: E402
    ALLOWED,
    FAMILIES,
    NOT_IN_POLICY,
    NOTICES,
    REVIEW,
    UNKNOWN,
    families,
    verdict_of,
)

__all__ = ["ALLOWED", "FAMILIES", "NOTICES", "NOT_IN_POLICY", "REVIEW", "UNKNOWN", "families",
           "verdict_of"]  # fmt: skip


def norm(name: str) -> str:
    """A package name as PyPI compares names: ``Foo_Bar.baz`` -> ``foo-bar-baz``."""
    return re.sub(r"[-_.]+", "-", name.strip()).lower()


# ------------------------------------------------------------------ what is in the build


def _requirement(line: str) -> str | None:
    m = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9_.\-]*)", line)
    return norm(m.group(1)) if m else None


def build_packages(root: Path = ROOT) -> dict[str, dict[str, Any]]:
    """Every Python package the build names: ``{name: {"version", "from": [...], "tool"}}``.
    ``tool`` is True for a package only a developer extra asks for."""
    import tomllib

    found: dict[str, dict[str, Any]] = {}

    def add(name: str | None, where: str, version: str | None = None) -> None:
        if not name:
            return
        row = found.setdefault(name, {"version": None, "from": []})
        row["from"].append(where)
        row["version"] = row["version"] or version

    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    for dep in project.get("dependencies", []):
        add(_requirement(dep), "pyproject.toml")
    for extra, deps in (project.get("optional-dependencies") or {}).items():
        for dep in deps:
            add(_requirement(dep), f"pyproject.toml [{extra}]")
    for rel in LOCKS:
        path = root / rel
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^([A-Za-z0-9][A-Za-z0-9_.\-]*)==([^\s\\;]+)", line)
            if m:
                add(norm(m.group(1)), rel, m.group(2))
    for row in found.values():
        row["tool"] = all(any(f"[{e}]" in w for e in TOOL_EXTRAS) for w in row["from"])
    return dict(sorted(found.items()))


_MODEL_FIELD = re.compile(r"^\s*([a-z_]*model[a-z_]*)\s*:\s*str\s*=\s*\"([^\"\s]+)\"", re.M)


def build_models(root: Path = ROOT) -> dict[str, str]:
    """Every model the configuration names: ``{model id: where}`` - the defaults in
    ``config.py`` and any ``*MODEL*=`` line of an env or shell file under ``infra/``."""
    found: dict[str, str] = {}
    config = root / "src/cdi_adapter/config.py"
    if config.is_file():
        for field, value in _MODEL_FIELD.findall(config.read_text(encoding="utf-8")):
            # a pinned revision (SW-S2) is a model's version, not another model
            if value and not value.startswith(("http", "/")) and not field.endswith("revision"):
                found.setdefault(value, f"config.py {field}")
    infra = root / "infra"
    for path in sorted(infra.rglob("*")) if infra.is_dir() else []:
        if path.suffix not in (".env", ".sh", ".yml", ".yaml") or not path.is_file():
            continue
        for key, value in re.findall(
            r"^\s*(?:export\s+)?(CDI_[A-Z_]*MODEL[A-Z_]*)=[\"']?"
            r"([A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+)",
            path.read_text(encoding="utf-8", errors="replace"),
            re.M,
        ):
            found.setdefault(value, f"{path.relative_to(root).as_posix()} {key}")
    return dict(sorted(found.items()))


def build_servers(root: Path = ROOT) -> dict[str, dict[str, str]]:
    """Every server or binary the build pins: ``{name: {"version", "from"}}`` - the
    ``*_VERSION`` / ``*_TAG`` keys and the apt pins of ``versions.env``, the compose images."""
    found: dict[str, dict[str, str]] = {}

    def add(name: str, version: str, where: str) -> None:
        found.setdefault(name.lower(), {"version": version, "from": where})

    for rel in ("infra/runpod/versions.env", "infra/ingress/versions.env"):
        path = root / rel
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for key, value in re.findall(r"^([A-Z0-9_]+?)_(?:VERSION|TAG)=([^\s#]+)", text, re.M):
            add(key.replace("_", "-"), value.strip("\"'"), rel)
        pins = re.search(r"^APT_PINS=\"([^\"]*)\"", text, re.M)
        for pin in pins.group(1).split() if pins else []:
            name, _, version = pin.partition("=")
            add(f"apt:{name}", version, rel)
    compose = root / "infra/compose/docker-compose.yml"
    if compose.is_file():
        for image in re.findall(r"^\s*image:\s*([^\s#]+)", compose.read_text("utf-8"), re.M):
            name, _, tag = image.partition(":")
            if not tag.startswith("local"):
                add(f"image:{name}", tag or "latest", "infra/compose/docker-compose.yml")
    return dict(sorted(found.items()))


# ------------------------------------------------------------------ the decisions' checks


def _read(root: Path, rel: str) -> str:
    path = root / rel
    return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""


def _grep(root: Path, pattern: str, *dirs: str, suffixes: tuple[str, ...] = (".py",)) -> list[str]:
    hits = []
    for d in dirs:
        base = root / d
        paths = [base] if base.is_file() else sorted(base.rglob("*")) if base.is_dir() else []
        for path in paths:
            if not path.is_file() or path.suffix not in suffixes or "__pycache__" in path.parts:
                continue
            for n, line in enumerate(path.read_text("utf-8", errors="replace").splitlines(), 1):
                if re.search(pattern, line):
                    hits.append(f"{path.relative_to(root).as_posix()}:{n}")
    return hits


def _check_pymupdf(root: Path) -> list[str]:
    names = build_packages(root)
    hits = [f"{n} in {names[n]['from'][0]}" for n in ("pymupdf", "pymupdfb", "fitz") if n in names]
    return hits + _grep(root, r"^\s*(import|from)\s+(fitz|pymupdf)\b", "src", "scripts")


def _check_qwen_3b(root: Path) -> list[str]:
    hits = _grep(root, r"^\s*vlm_fallback_model_id\s*:", "src/cdi_adapter/config.py")
    # the model id itself, anywhere it is SET (a line that is only a comment does not count)
    named = r"^(?!\s*#).*Qwen/Qwen2\.5-VL-3B"
    kinds = (".py", ".env", ".sh", ".yml", ".example", ".runpod")
    places = ("src", "scripts", "infra", ".env.example", ".env.runpod")
    return hits + _grep(root, named, *places, suffixes=kinds)


def _check_iam(root: Path) -> list[str]:
    return _grep(
        root,
        r"fki\.tic|iam[-_ ]?(handwriting|database|dataset)|load_dataset\([\"'][^\"']*iam",
        "src",
        "scripts",
    )


def _check_object_store(root: Path) -> list[str]:
    text = _read(root, "src/cdi_adapter/storage.py")
    other = re.findall(r"^class (\w*(?:File|Local|Disk|Folder)\w*Store)\b", text, re.M)
    return (
        []
        if other
        else [
            "src/cdi_adapter/storage.py: the only store is the S3 client "
            "(any S3 service works; there is no filesystem store)"
        ]
    )


def _check_redis(root: Path) -> list[str]:
    hits = []
    for name, row in build_servers(root).items():
        if "redis" not in name:
            continue
        m = re.search(r"(?:^|:)(\d+)(?:\.(\d+))?", row["version"])
        major, minor = (int(m.group(1)), m.group(2)) if m else (99, None)
        if major > 7 or (major == 7 and (minor is None or int(minor) > 2)):
            hits.append(f"{row['from']}: {name} {row['version']} is not pinned to 7.2 or older")
    return hits


def _check_grafana(root: Path) -> list[str]:
    hits = [
        f"{row['from']}: {name} {row['version']}"
        for name, row in build_servers(root).items()
        if "grafana" in name
    ]
    if (root / "infra/monitoring/grafana").is_dir():
        hits.append("infra/monitoring/grafana/ (provisioning shipped with the product)")
    return hits


def _check_opencv(root: Path) -> list[str]:
    names = build_packages(root)
    return [
        f"{n} in {', '.join(names[n]['from'])}"
        for n in ("opencv-python", "opencv-contrib-python")
        if n in names
    ]


def _check_onemg(root: Path) -> list[str]:
    return _grep(root, r"(?i)\b1mg\b", "src")[:5]


def _check_notice(root: Path) -> list[str]:
    hits = [] if (root / "NOTICE").is_file() else ["NOTICE is missing"]
    if "NOTICE" not in _read(root, "pyproject.toml"):
        hits.append("pyproject.toml does not put NOTICE into the built package")
    return hits


def _check_codes(root: Path) -> list[str]:
    return _grep(
        root,
        r"(?i)\"(?:ICD-?10|SNOMED|http://snomed\.info/sct|http://hl7\.org/fhir/sid/icd-10)\"",
        "src/cdi_adapter/terminology",
    )[:5]


CHECKS = {
    "pymupdf-replaced": _check_pymupdf,
    "qwen-3b-removed": _check_qwen_3b,
    "iam-not-used-for-training": _check_iam,
    "object-store-pluggable": _check_object_store,
    "redis-pinned": _check_redis,
    "grafana-not-bundled": _check_grafana,
    "opencv-headless-only": _check_opencv,
    "onemg-not-used": _check_onemg,
    "notice-shipped": _check_notice,
    "external-codes-optional": _check_codes,
}


# ------------------------------------------------------------------ the audit


def load(root: Path = ROOT) -> dict[str, Any]:
    base = root / "licences"
    return {
        name: json.loads((base / f"{name}.json").read_text(encoding="utf-8"))
        for name in ("policy", "register", "packages", "nvidia_redistributable")
    }


def _decided(decision: dict[str, Any] | None) -> bool:
    return bool(
        decision and decision.get("decision") and decision.get("by") and decision.get("date")
    )


def _row(
    kind: str,
    name: str,
    version: Any,
    entry: dict[str, Any] | None,
    facts: dict[str, Any],
    where: str,
) -> dict[str, Any]:
    """One component of the report with its verdict and whether it blocks."""
    policy, register = facts["policy"], facts["register"]
    row: dict[str, Any] = {
        "kind": kind,
        "name": name,
        "version": version,
        "from": where,
        "licence": None,
        "verdict": UNKNOWN,
        "status": "blocked",
        "why": None,
        "reviewed": False,
        "decision": None,
    }
    if entry is None:
        row["why"] = "not in the register: add an entry with its licence and where it was read"
        return row
    row["licence"] = entry.get("licence")
    row["evidence"] = entry.get("source")
    row["reviewed"] = entry.get("reviewed", True) is not False and bool(entry.get("source"))
    row["verdict"] = verdict_of(entry.get("licence"), policy)
    decision = entry.get("decision") if isinstance(entry.get("decision"), dict) else None
    if decision is None and isinstance(entry.get("decision"), str):
        decision = (register.get("decisions") or {}).get(entry["decision"])
        row["decision"] = entry["decision"]
    blanket = register.get("blanket") or {}
    if (
        decision is None
        and row["verdict"] == NOT_IN_POLICY
        and kind == "python package"
        and entry.get("category") in (blanket.get("covers_categories") or [])
    ):
        decision, row["decision"] = blanket, blanket.get("id")
    if row["verdict"] in (ALLOWED, NOTICES):
        row["status"] = "ok"
    elif row["verdict"] == UNKNOWN:
        row["why"] = "licence UNKNOWN: read it from the source and record it (never guessed)"
    elif _decided(decision) and (decision or {}).get("outcome") == "do not use":
        row["decision"] = row["decision"] or (decision or {}).get("id")
        row["why"] = "the recorded decision is NOT to use it, and it is in the build"
    elif _decided(decision):
        row["status"] = "ok"
        row["decision"] = row["decision"] or (decision or {}).get("id") or "recorded"
    else:
        row["why"] = (
            f"{row['verdict']}: a decision must be recorded (what, who, when) "
            "before this may be built"
        )
    return row


def audit(root: Path = ROOT, facts: dict[str, Any] | None = None) -> dict[str, Any]:
    """The licence report: every component with its verdict, every decision with whether it is
    applied, and the problems that block a build (``blockers``) or a release (``release``)."""
    facts = facts or load(root)
    register, packages = facts["register"], facts["packages"]
    rows = []
    for name, info in build_packages(root).items():
        row = _row(
            "python package",
            name,
            info["version"],
            packages.get(name),
            facts,
            ", ".join(dict.fromkeys(info["from"])),
        )
        row["tool"] = info["tool"]
        rows.append(row)
    for name, info in build_servers(root).items():
        entry = (register.get("components") or {}).get(name)
        python_name = (register.get("aliases") or {}).get(name, name)
        if entry is None and python_name in packages:  # a pinned Python package
            entry = packages[python_name]
        rows.append(_row("server or binary", name, info["version"], entry, facts, info["from"]))
    models = register.get("models") or {}
    for name, where in build_models(root).items():
        rows.append(
            _row(
                "model",
                name,
                (models.get(name) or {}).get("revision"),
                models.get(name),
                facts,
                where,
            )
        )
    seen = {r["name"] for r in rows}
    for kind, key in (
        ("model", "models"),
        ("data set", "datasets"),
        ("code list", "code_lists"),
        ("other", "other"),
    ):
        for name, entry in (register.get(key) or {}).items():
            if name not in seen:
                rows.append(
                    _row(
                        kind,
                        name,
                        entry.get("revision") or entry.get("version"),
                        entry,
                        facts,
                        "licences/register.json",
                    )
                )
    decisions = []
    for key, d in (register.get("decisions") or {}).items():
        check = CHECKS.get(d.get("check") or "")
        found = check(root) if check else None
        applied = None if found is None else not found
        owned = bool(d.get("owner") and d.get("due"))
        decisions.append(
            {
                "id": key,
                "about": d.get("about"),
                "decision": d.get("decision"),
                "applied": applied,
                "found": found or [],
                "owner": d.get("owner"),
                "due": d.get("due"),
                "status": "APPLIED"
                if applied
                else "OPEN - owner and date recorded"
                if owned
                else "OPEN - NEEDS AN OWNER AND A DATE"
                if applied is False
                else "NOT CHECKED BY THE BUILD - NEEDS AN OWNER AND A DATE",
            }
        )
    blockers = [f"{r['kind']} {r['name']}: {r['why']}" for r in rows if r["status"] != "ok"]
    release = list(blockers)
    release += [
        f"{r['kind']} {r['name']}: unreviewed - its licence was not read from its source"
        for r in rows
        if r["status"] == "ok" and not r["reviewed"]
    ]
    release += [
        f"decision {d['id']}: {d['status']}"
        for d in decisions
        if not d["applied"] and "NEEDS" in d["status"]
    ]
    release += [
        f"model {name}: no {field} recorded"
        for name, m in (register.get("models") or {}).items()
        for field in ("revision", "sha256", "licence_text_copy")
        if not m.get(field)
    ]
    release += [
        f"NOTICE lacks a line a model licence requires - {m}; run "
        "`python scripts/licence_audit.py notice`"
        for m in missing_notices(root)
    ]
    signoff = register.get("signoff") or {}
    if not (signoff.get("by") and signoff.get("date")):
        release.append("sign-off: no person with legal authority has signed this release off")
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    return {
        "generated": str(date.today()),
        "not_legal_advice": True,
        "components": rows,
        "decisions": decisions,
        "counts": dict(sorted(counts.items())),
        "unreviewed": sum(1 for r in rows if not r["reviewed"]),
        "blockers": blockers,
        "release_blockers": release,
        "passed": not blockers,
        "release_ready": not release,
    }


def as_markdown(report: dict[str, Any]) -> str:
    out = [
        "# Licence report",
        "",
        f"Generated {report['generated']} by `scripts/licence_audit.py`. **Not legal advice** "
        "- it lists the evidence; counsel decides.",
        "",
        f"- components: {len(report['components'])}  |  "
        + "  |  ".join(f"{k}: {v}" for k, v in report["counts"].items()),
        f"- unreviewed: {report['unreviewed']}",
        f"- build gate: {'PASS' if report['passed'] else 'BLOCKED'} "
        f"({len(report['blockers'])} blockers)",
        f"- release gate: {'PASS' if report['release_ready'] else 'BLOCKED'} "
        f"({len(report['release_blockers'])} blockers)",
        "",
        "## Decisions",
        "",
        "| Decision | Status | Owner | Date | Found in the repository |",
        "|---|---|---|---|---|",
    ]
    for d in report["decisions"]:
        out.append(
            f"| {d['about']}: {d['decision']} | {d['status']} | {d['owner'] or '-'} | "
            f"{d['due'] or '-'} | {'; '.join(d['found'][:4]) or '-'} |"
        )
    out += ["", "## Blocked", ""] + [f"- {b}" for b in report["blockers"] or ["nothing"]]
    out += [
        "",
        "## Components",
        "",
        "| Kind | Name | Version | Licence | Verdict | Status |",
        "|---|---|---|---|---|---|",
    ]
    for r in report["components"]:
        licence = str(r["licence"] or "-").replace("|", "/")[:80]
        status = (
            r["status"]
            + (f" ({r['decision']})" if r["decision"] else "")
            + ("" if r["reviewed"] else ", unreviewed")
        )
        out.append(
            f"| {r['kind']} | {r['name']} | {r['version'] or '-'} | {licence} | "
            f"{r['verdict']} | {status} |"
        )
    return "\n".join(out) + "\n"


# ------------------------------------------------------------------ record, notice, bundle


def licence_of(dist: metadata.Distribution) -> str:
    """The licence a package states about itself: its SPDX expression, else its licence
    field (when that is a name and not the whole text), else its classifiers."""
    meta = dist.metadata
    said = (meta.get("License-Expression") or "").strip()
    if said:
        return said
    field = (meta.get("License") or "").strip()
    if field and len(field) <= 120 and "\n" not in field and field.upper() != "UNKNOWN":
        return field
    classes = [
        c.split("::")[-1].strip()
        for c in meta.get_all("Classifier") or []
        if c.startswith("License ::")
    ]
    return "; ".join(dict.fromkeys(classes))


def installed() -> dict[str, metadata.Distribution]:
    return {norm(d.metadata["Name"]): d for d in metadata.distributions() if d.metadata["Name"]}


def record(root: Path = ROOT, *, everything: bool = False) -> list[str]:
    """Add to ``licences/packages.json`` the licence of every build package that is installed
    HERE and has no entry yet (``everything``: refresh the ones that have). Never invents one:
    a package that states no licence is recorded as empty, which the gate reads as UNKNOWN."""
    path = root / "licences/packages.json"
    packages = json.loads(path.read_text(encoding="utf-8"))
    have, added = installed(), []
    where = f"installed metadata, {platform.system()} {platform.machine()}, {date.today()}"
    for name in build_packages(root):
        if name not in have or (name in packages and not everything):
            continue
        packages[name] = {
            "version": have[name].version,
            "licence": licence_of(have[name]),
            "source": where,
        }
        added.append(name)
    path.write_text(json.dumps(dict(sorted(packages.items())), indent=1) + "\n", "utf-8")
    return added


def model_notices(root: Path = ROOT) -> list[tuple[str, list[str]]]:
    """SW-S7 FR5: the notice / attribution lines each model's licence requires, as recorded in
    ``models/registry.json`` (``licence.notice``) - for every model that is not retired."""
    path = root / "models" / "registry.json"
    if not path.is_file():
        return []
    out = []
    for m in json.loads(path.read_text(encoding="utf-8")).get("models") or []:
        lines = (m.get("licence") or {}).get("notice") or []
        if isinstance(lines, str):
            lines = [lines]
        if lines and m.get("status") != "retired":
            out.append((str(m.get("id")), [str(x) for x in lines]))
    return out


def missing_notices(root: Path = ROOT) -> list[str]:
    """Each required model notice line the NOTICE file does not carry."""
    have = (root / "NOTICE").read_text(encoding="utf-8") if (root / "NOTICE").is_file() else ""
    return [f"{model}: {line}" for model, lines in model_notices(root) for line in lines
            if line not in have]  # fmt: skip


def notice_text(report: dict[str, Any], register: dict[str, Any],
                notices: list[tuple[str, list[str]]] | None = None) -> str:  # fmt: skip
    lines = [
        register.get("product", "CDI-Adapter"),
        register.get("copyright", ""),
        "",
        "This product includes third-party software, models and data. Each is listed "
        "below with the licence it is used under; the licence texts are in "
        "THIRD_PARTY_LICENSES/.",
        "",
    ]
    lines += [line for line in register.get("notice_lines") or []] + [""]
    if notices:  # SW-S7: the lines the models' licences require, word for word
        lines.append("== notices required by model licences ==")
        for model, required in notices:
            lines += [f"{line}" for line in required]
            lines.append(f"  (required by the licence of {model})")
        lines.append("")
    for kind in ("model", "code list", "server or binary", "python package"):
        rows = [
            r
            for r in report["components"]
            if r["kind"] == kind and not r.get("tool") and r["licence"]
        ]
        if rows:
            lines.append(f"== {kind} ==")
            lines += [
                f"{r['name']}{' ' + str(r['version']) if r['version'] else ''} - {r['licence']}"
                for r in rows
            ]
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


_LICENCE_FILE = re.compile(
    r"(?i)(^|/)(licen[cs]es/.+|(licen[cs]e|copying|notice|authors|copyright)[^/]*)$"
)


def bundle(out: Path, root: Path = ROOT) -> dict[str, Any]:
    """What an installed version must carry: NOTICE, the licence texts of every Python
    package that is shipped (copied from the package itself), and the SBOM. Says which
    packages are not installed here, so their texts could not be collected."""
    report = audit(root)
    facts = load(root)
    out.mkdir(parents=True, exist_ok=True)
    (out / "NOTICE").write_text(notice_text(report, facts["register"], model_notices(root)),
                                encoding="utf-8")  # fmt: skip
    have, missing, shipped = installed(), [], []
    for r in report["components"]:
        if r["kind"] != "python package" or r.get("tool"):
            continue
        shipped.append(r["name"])
        dist = have.get(r["name"])
        texts = (
            [f for f in (dist.files or []) if _LICENCE_FILE.search(str(f).replace("\\", "/"))]
            if dist
            else []
        )
        if not texts:
            missing.append(r["name"])
            continue
        target = out / "THIRD_PARTY_LICENSES" / r["name"]
        target.mkdir(parents=True, exist_ok=True)
        for f in texts:
            try:
                name = str(f).replace("\\", "/").split(".dist-info/", 1)[-1].replace("/", "__")
                (target / name).write_bytes(Path(str(dist.locate_file(f))).read_bytes())
            except OSError:
                continue
    manifest = {
        "generated": report["generated"],
        "shipped_python_packages": shipped,
        "licence_texts_missing": missing,
        "components": [
            {k: r[k] for k in ("kind", "name", "version", "licence", "verdict")}
            for r in report["components"]
            if not r.get("tool")
        ],
    }
    (out / "sbom.json").write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    return manifest


# ------------------------------------------------------------------ inspect a built package

NVIDIA_FILE = re.compile(
    r"(?i)(^|/)(nvidia[-_][^/]*|lib(cu(da|dnn|blas|fft|rand|solver|sparse|pti)|nv|npp|nccl)[^/]*"
    r"\.so[^/]*|cu(da|dnn)[^/]*\.(dll|so|deb|whl|run)|nv[a-z]*\d*_?\d*\.dll)$"
)


def _names(path: Path) -> list[str]:
    if path.is_dir():
        return sorted(p.relative_to(path).as_posix() for p in path.rglob("*") if p.is_file())
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            return sorted(n for n in z.namelist() if not n.endswith("/"))
    if tarfile.is_tarfile(path):
        with tarfile.open(path) as t:
            return sorted(m.name for m in t.getmembers() if m.isfile())
    raise ValueError(f"{path} is not a folder, a zip / wheel or a tar file")


def _text(path: Path, name: str) -> str:
    if path.is_dir():
        return (path / name).read_text(encoding="utf-8", errors="replace")
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            return z.read(name).decode("utf-8", "replace")
    with tarfile.open(path) as t:
        member = t.extractfile(name)
        return member.read().decode("utf-8", "replace") if member else ""


def inspect(path: Path, redistributable: list[str] | None = None) -> list[str]:
    """What is wrong with an installed-version package (AC5); empty when nothing is:

    * a NOTICE file, with something in it;
    * a licence text under THIRD_PARTY_LICENSES/ for every Python package its ``sbom.json``
      says is shipped;
    * no NVIDIA file that is not on the redistributable list. With an empty list - today,
      because the EULA's Attachment A has not been read in full - NO NVIDIA file may be in it."""
    names = _names(path)
    problems = []
    notice = next((n for n in names if n.rsplit("/", 1)[-1] == "NOTICE"), None)
    if notice is None:
        problems.append("no NOTICE file")
    elif not _text(path, notice).strip():
        problems.append("the NOTICE file is empty")
    sbom = next((n for n in names if n.rsplit("/", 1)[-1] == "sbom.json"), None)
    if sbom is None:
        problems.append("no sbom.json: what is shipped is not stated")
    else:
        have = {
            n.split("THIRD_PARTY_LICENSES/", 1)[1].split("/", 1)[0]
            for n in names
            if "THIRD_PARTY_LICENSES/" in n and n.count("/") >= 2
        }
        for name in json.loads(_text(path, sbom)).get("shipped_python_packages") or []:
            if name not in have:
                problems.append(f"no licence text for {name}")
    if not any("THIRD_PARTY_LICENSES/" in n for n in names):
        problems.append("no THIRD_PARTY_LICENSES folder")
    allowed = {a.lower() for a in redistributable or []}
    for n in names:
        if NVIDIA_FILE.search(n) and n.rsplit("/", 1)[-1].lower() not in allowed:
            problems.append(f"NVIDIA file not on the redistributable list: {n}")
    return problems


# ------------------------------------------------------------------ SBOM of the deployed image


def sbom_here() -> dict[str, Any]:
    """What is installed on THIS machine - meant to be run inside the deployed image: every
    Python package with the licence it states, every system package, the NVIDIA libraries."""
    out: dict[str, Any] = {
        "read_on": str(date.today()),
        "host": platform.platform(),
        "python": sys.version.split()[0],
        "python_packages": [],
        "system_packages": [],
        "nvidia_libraries": [],
    }
    for name, dist in sorted(installed().items()):
        out["python_packages"].append(
            {"name": name, "version": dist.version, "licence": licence_of(dist)}
        )
    try:
        listed = subprocess.run(
            ["dpkg-query", "-W", "-f", "${Package}\t${Version}\n"],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        ).stdout
        out["system_packages"] = [
            dict(zip(("name", "version"), line.split("\t"), strict=False))
            for line in listed.splitlines()
            if line
        ]
    except (OSError, subprocess.SubprocessError):
        out["system_packages_note"] = "dpkg-query is not available here (not a Debian image)"
    for base in ("/usr/local/cuda", "/usr/lib/x86_64-linux-gnu"):
        root = Path(base)
        if root.is_dir():
            for p in sorted(root.rglob("*")):
                if p.is_file() and NVIDIA_FILE.search(p.as_posix()):
                    out["nvidia_libraries"].append(
                        {
                            "path": p.as_posix(),
                            "bytes": p.stat().st_size,
                            "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                        }
                    )
    return out


# ------------------------------------------------------------------ command line


# ------------------------------------------------------------------ the swap gate (SW-S7)

BASELINE = "licences/swap-baseline.json"


def snapshot(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Every component of the build (the SBOM, with each licence and verdict), by kind and name."""
    return {f"{r['kind']}: {r['name']}": {k: r.get(k) for k in
                                         ("version", "licence", "verdict", "status", "decision")}
            for r in report["components"] if not r.get("tool")}  # fmt: skip


def swap_gate(report: dict[str, Any], baseline: dict[str, Any] | None) -> dict[str, Any]:
    """The SBOM before (the baseline) and after (now): every component added or changed must be
    allowed, or carry a recorded decision. Unknown is blocked. What was already in the build is
    the full gate's business (``check``); this gate stops a swap from adding a NEW problem."""
    now = snapshot(report)
    if baseline is None:
        return {"passed": False, "added": [], "changed": [], "removed": [],
                "blocking": [f"no baseline ({BASELINE}): record one with --accept"]}  # fmt: skip
    before = baseline.get("components") or {}
    added = sorted(set(now) - set(before))
    removed = sorted(set(before) - set(now))
    changed = sorted(
        k
        for k in set(now) & set(before)
        if any(now[k].get(f) != before[k].get(f) for f in ("version", "licence"))
    )
    rows = {f"{r['kind']}: {r['name']}": r for r in report["components"]}
    blocking = [f"{k} ({'new' if k in added else 'changed'}, {now[k]['licence'] or 'no licence'}): "
                f"{rows[k]['why']}"
                for k in added + changed if now[k]["status"] != "ok"]  # fmt: skip
    return {"passed": not blocking, "added": added, "changed": changed, "removed": removed,
            "blocking": blocking}  # fmt: skip


def accept(report: dict[str, Any], by: str, root: Path = ROOT) -> Path:
    """Record the build as it is now as the swap gate's baseline - after the gate passed."""
    path = root / BASELINE
    body = {
        "about": "SW-S7: the build's components when the swap gate last passed. NOT an approval: "
        "what was already blocked stays blocked in `check`; this file only lets the swap "
        "gate see what a change ADDS or alters.",
        "accepted_by": by,
        "accepted_on": str(date.today()),
        "components": snapshot(report),
    }
    path.write_text(json.dumps(body, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="ENT-S1 licence audit and gate (not legal advice)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--release", action="store_true")
    c.add_argument("--report", default="")
    r = sub.add_parser("record")
    r.add_argument("--all", action="store_true")
    sub.add_parser("notice")
    b = sub.add_parser("bundle")
    b.add_argument("--out", required=True)
    i = sub.add_parser("inspect")
    i.add_argument("path")
    s = sub.add_parser("sbom")
    s.add_argument("--out", default="")
    g = sub.add_parser("swap-gate")
    g.add_argument("--baseline", default=BASELINE)
    g.add_argument("--accept", action="store_true")
    g.add_argument("--by", default="")
    args = ap.parse_args(argv)
    if args.cmd == "swap-gate":
        report = audit()
        path = ROOT / args.baseline
        base = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
        got = swap_gate(report, base)
        for k in got["added"]:
            print(f"  added    {k}")
        for k in got["changed"]:
            print(f"  changed  {k}")
        for k in got["removed"]:
            print(f"  removed  {k}")
        for line in got["blocking"]:
            print(f"  BLOCKED  {line}")
        if args.accept:
            if not args.by:
                print("--accept needs --by (who records the baseline)")
                return 2
            if base is not None and not got["passed"]:
                print("the baseline is not moved past a swap the gate blocks")
                return 1
            print(f"baseline recorded: {accept(report, args.by)}")
            return 0
        verdict = "PASS" if got["passed"] else f"BLOCKED ({len(got['blocking'])})"
        print(f"swap gate: {verdict}")
        return 0 if got["passed"] else 1
    if args.cmd == "record":
        added = record(everything=args.all)
        print(f"recorded {len(added)} package(s): {', '.join(added) or '-'}")
        return 0
    if args.cmd == "notice":
        facts = load()
        (ROOT / "NOTICE").write_text(
            notice_text(audit(facts=facts), facts["register"], model_notices()), "utf-8"
        )
        print("NOTICE written")
        return 0
    if args.cmd == "bundle":
        made = bundle(Path(args.out))
        print(
            f"bundle in {args.out}: {len(made['shipped_python_packages'])} packages, "
            f"{len(made['licence_texts_missing'])} without a licence text collected here"
        )
        return 0
    if args.cmd == "inspect":
        found = inspect(Path(args.path), load()["nvidia_redistributable"].get("files"))
        for problem in found:
            print(f"  FAIL  {problem}")
        print("the package is complete" if not found else f"{len(found)} problem(s)")
        return 1 if found else 0
    if args.cmd == "sbom":
        text = json.dumps(sbom_here(), indent=1)
        if args.out:
            Path(args.out).write_text(text + "\n", encoding="utf-8")
        else:
            print(text)
        return 0
    report = audit()
    if args.report:
        out = Path(args.report)
        out.mkdir(parents=True, exist_ok=True)
        (out / "licence-report.json").write_text(json.dumps(report, indent=1) + "\n", "utf-8")
        (out / "licence-report.md").write_text(as_markdown(report), encoding="utf-8")
    print(
        f"components: {len(report['components'])}  "
        + "  ".join(f"{k}: {v}" for k, v in report["counts"].items())
        + f"  unreviewed: {report['unreviewed']}"
    )
    for d in report["decisions"]:
        print(f"  decision  {d['status']:44} {d['about']}")
    blocked = report["release_blockers"] if args.release else report["blockers"]
    for line in blocked:
        print(f"  BLOCKED  {line}")
    gate = "release gate" if args.release else "licence gate"
    print(f"{gate}: {'PASS' if not blocked else f'BLOCKED ({len(blocked)})'}")
    return 1 if blocked else 0


if __name__ == "__main__":
    raise SystemExit(main())
