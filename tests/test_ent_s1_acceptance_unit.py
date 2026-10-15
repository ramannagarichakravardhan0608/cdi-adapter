"""ENT-S1 "Commercial licence audit of every software, model, data set and code list".

* AC1  the licence report gives every component a verdict, and none is unreviewed;
* AC2  a review-list licence fails the build until a decision is recorded;
* AC3  each licence decision of the register is applied, or has an owner and a date;
* AC4  a model or package added to a build must have an entry in the register;
* AC5  an installed-version package holds the NOTICE file, the third-party licence texts and
       only redistributable NVIDIA files.

The gate (``scripts/licence_audit.py``) is run on small made-up repositories, where each rule
can be shown alone, and on THIS repository, where the report is what it is today - the
tests at the end say plainly which criteria the real repository does not meet yet.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import zipfile
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("licence_audit", ROOT / "scripts/licence_audit.py")
assert _spec and _spec.loader
la = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(la)

POLICY = json.loads((ROOT / "licences/policy.json").read_text(encoding="utf-8"))
WHO = {"by": "A. Counsel", "date": "2026-10-13"}


def _repo(
    tmp_path: Path,
    *,
    deps: list[str] | None = None,
    lock: str = "",
    model: str | None = None,
    versions: str = "",
    compose: str = "",
) -> Path:
    """A made-up repository with only the build files the gate reads."""
    listed = ", ".join(f'"{d}"' for d in deps or ["alpha>=1"])
    (tmp_path / "pyproject.toml").write_text(
        f'[project]\nname = "demo"\nversion = "1"\ndependencies = [{listed}]\n'
        '[project.optional-dependencies]\ndev = ["devtool>=1"]\n'
        '[tool.setuptools]\nlicense-files = ["NOTICE"]\n',
        encoding="utf-8",
    )
    (tmp_path / "NOTICE").write_text("demo\n", encoding="utf-8")
    (tmp_path / "infra/runpod").mkdir(parents=True)
    (tmp_path / "infra/runpod/requirements.lock").write_text(lock, encoding="utf-8")
    (tmp_path / "infra/runpod/versions.env").write_text(versions, encoding="utf-8")
    (tmp_path / "infra/compose").mkdir(parents=True)
    (tmp_path / "infra/compose/docker-compose.yml").write_text(compose, encoding="utf-8")
    (tmp_path / "src/cdi_adapter").mkdir(parents=True)
    (tmp_path / "src/cdi_adapter/config.py").write_text(
        f'class Settings:\n    vlm_model_id: str = "{model}"\n' if model else "", "utf-8"
    )
    return tmp_path


def _facts(packages: dict[str, Any] | None = None, **register: Any) -> dict[str, Any]:
    known = {
        "alpha": {"version": "1.0", "licence": "MIT", "source": "installed metadata"},
        "devtool": {"version": "1.0", "licence": "BSD-3-Clause", "source": "installed metadata"},
    }
    return {
        "policy": POLICY,
        "packages": {**known, **(packages or {})},
        "register": {"signoff": {**WHO}, **register},
        "nvidia_redistributable": {"files": []},
    }


def _row(report: dict[str, Any], name: str) -> dict[str, Any]:
    return next(r for r in report["components"] if r["name"] == name)


# ------------------------------------------------------------------ the policy


@pytest.mark.parametrize(
    ("said", "verdict"),
    [
        ("MIT", "ALLOWED"),
        ("BSD-3-Clause", "ALLOWED"),
        ("Apache Software License", "ALLOWED"),
        ("Apache-2.0 AND BSD-3-Clause", "ALLOWED"),
        ("ISC License (ISCL)", "ALLOWED"),
        ("PostgreSQL", "ALLOWED"),
        ("SIL Open Font License 1.1", "ALLOWED"),
        ("LGPL-3.0-only", "ALLOWED WITH NOTICES"),
        ("MIT AND LGPL-2.1", "ALLOWED WITH NOTICES"),
        ("MPL-2.0", "NOT IN POLICY"),
        ("PSF-2.0", "NOT IN POLICY"),
        ("MIT AND PSF-2.0", "NOT IN POLICY"),
        ("GPL-2+", "REVIEW"),
        ("GNU General Public License v3", "REVIEW"),
        ("AGPL-3.0", "REVIEW"),
        ("SSPL-1.0", "REVIEW"),
        ("RSALv2", "REVIEW"),
        ("CC-BY-NC-4.0", "REVIEW"),
        ("Qwen RESEARCH LICENSE (non-commercial)", "REVIEW"),
        ("Qwen LICENSE AGREEMENT", "REVIEW"),
        ("Dual Licensed - GNU AFFERO GPL 3.0 or Artifex Commercial License", "REVIEW"),
        ("MIT OR GPL-2.0", "ALLOWED"),  # the licensee may choose the MIT side
        ("Apache-2.0 AND GPL-2.0", "REVIEW"),  # both apply
        ("", "UNKNOWN"),
        (None, "UNKNOWN"),
        ("see the website", "UNKNOWN"),
    ],
)
def test_policy_each_licence_field_gets_the_verdict_the_policy_gives_it(said, verdict):
    assert la.verdict_of(said, POLICY) == verdict


def test_policy_lgpl_is_not_mistaken_for_gpl_nor_agpl_for_either():
    assert la.families("LGPL-3.0-only") == [{"LGPL"}]
    assert la.families("AGPL-3.0") == [{"AGPL"}]
    assert la.families("GPL-2+") == [{"GPL"}]


def test_policy_is_written_down_as_the_story_states_it():
    assert set(POLICY["allowed"]) >= {"MIT", "BSD", "APACHE", "ISC", "POSTGRESQL", "OFL"}
    assert POLICY["allowed_with_notices"] == ["LGPL"]
    assert set(POLICY["review"]) >= {"GPL", "AGPL", "SSPL", "NON-COMMERCIAL"}
    assert "AGPL tools are never bundled" in POLICY["rules"]["REVIEW"]
    assert any("NVIDIA" in line for line in POLICY["installed_version"])


# ------------------------------------------------------------------ AC1: every component, a verdict


def test_ac1_every_component_of_the_build_is_in_the_report_with_a_verdict(tmp_path):
    root = _repo(
        tmp_path,
        deps=["alpha>=1", "beta[extra]>=2"],
        lock="gamma==3.1 \\\n",
        model="org/reader",
        versions="CADDY_VERSION=2.11.4\n",
        compose="services:\n  db:\n    image: postgres:16\n",
    )
    report = la.audit(root, _facts())
    names = {r["name"]: r["kind"] for r in report["components"]}
    assert names == {
        "alpha": "python package",
        "beta": "python package",
        "gamma": "python package",
        "devtool": "python package",
        "caddy": "server or binary",
        "image:postgres": "server or binary",
        "org/reader": "model",
    }
    assert all(
        r["verdict"] in ("ALLOWED", "ALLOWED WITH NOTICES", "NOT IN POLICY", "REVIEW", "UNKNOWN")
        for r in report["components"]
    )
    assert _row(report, "gamma")["version"] == "3.1" and _row(report, "devtool")["tool"]


def test_ac1_a_clean_build_passes_with_nothing_unreviewed(tmp_path):
    report = la.audit(_repo(tmp_path), _facts())
    assert report["passed"] and report["release_ready"] and report["unreviewed"] == 0
    assert report["counts"] == {"ALLOWED": 2}


def test_ac1_a_licence_is_never_guessed_unknown_blocks(tmp_path):
    facts = _facts({"alpha": {"version": "1.0", "licence": "", "source": "installed metadata"}})
    report = la.audit(_repo(tmp_path), facts)
    assert _row(report, "alpha")["verdict"] == "UNKNOWN" and not report["passed"]
    assert "never guessed" in report["blockers"][0]


def test_ac1_an_entry_with_no_evidence_counts_as_unreviewed_and_blocks_a_release(tmp_path):
    facts = _facts({"alpha": {"version": "1.0", "licence": "MIT", "source": None}})
    report = la.audit(_repo(tmp_path), facts)
    assert report["passed"], "the licence is an allowed one"
    assert report["unreviewed"] == 1 and not report["release_ready"]
    assert any("unreviewed" in b for b in report["release_blockers"])


def test_ac1_the_report_is_made_by_the_build_as_json_and_as_a_page(tmp_path):
    report = la.audit(_repo(tmp_path), _facts())
    page = la.as_markdown(report)
    assert "# Licence report" in page and "Not legal advice" in page
    assert "| python package | alpha | - | MIT | ALLOWED | ok |" in page
    json.dumps(report)  # serialisable as it is


def test_ac1_the_build_runs_the_gate_and_keeps_the_report():
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    job = ci.split("  licence-gate:")[1].split("\n  integration-gate:")[0]
    assert "python scripts/licence_audit.py check --report licence-report" in job
    assert "upload-artifact" in job and "if: always()" in job
    assert "licences:" in (ROOT / "Makefile").read_text(encoding="utf-8")


# ------------------------------------------------------------------ AC2: review list


def test_ac2_a_review_list_licence_fails_the_build(tmp_path):
    facts = _facts({"alpha": {"version": "1.0", "licence": "AGPL-3.0", "source": "PyPI"}})
    report = la.audit(_repo(tmp_path), facts)
    assert not report["passed"]
    assert report["blockers"] == [
        "python package alpha: REVIEW: a decision must be recorded "
        "(what, who, when) before this may be built"
    ]


def test_ac2_it_passes_once_a_decision_is_recorded_with_who_and_when(tmp_path):
    entry = {
        "version": "1.0",
        "licence": "AGPL-3.0",
        "source": "PyPI",
        "decision": {"decision": "separate process only, after counsel's review", **WHO},
    }
    report = la.audit(_repo(tmp_path), _facts({"alpha": entry}))
    assert report["passed"] and _row(report, "alpha")["status"] == "ok"
    assert _row(report, "alpha")["verdict"] == "REVIEW", "the verdict is not hidden by it"


@pytest.mark.parametrize("missing", ["decision", "by", "date"])
def test_ac2_a_decision_without_what_who_or_when_is_not_a_decision(tmp_path, missing):
    decision = {"decision": "accepted", **WHO}
    decision[missing] = None
    entry = {"version": "1.0", "licence": "GPL-3.0", "source": "PyPI", "decision": decision}
    assert not la.audit(_repo(tmp_path), _facts({"alpha": entry}))["passed"]


def test_ac2_a_decision_not_to_use_it_keeps_blocking_while_it_is_in_the_build(tmp_path):
    entry = {"version": "1.0", "licence": "AGPL-3.0", "source": "PyPI", "decision": "drop"}
    decisions = {
        "drop": {"about": "alpha", "decision": "replace it", "outcome": "do not use", **WHO}
    }
    report = la.audit(_repo(tmp_path), _facts({"alpha": entry}, decisions=decisions))
    assert not report["passed"] and "NOT to use it" in report["blockers"][0]


def test_ac2_a_licence_the_policy_does_not_name_needs_a_decision_too(tmp_path):
    facts = _facts({"alpha": {"version": "1.0", "licence": "MPL-2.0", "source": "PyPI"}})
    assert not la.audit(_repo(tmp_path), facts)["passed"]
    covered = copy.deepcopy(facts)
    covered["packages"]["alpha"]["category"] = "2 WEAK COPYLEFT (file level)"
    covered["register"]["blanket"] = {
        "id": "REG",
        "decision": "accepted",
        **WHO,
        "covers_categories": ["2 WEAK COPYLEFT (file level)"],
    }
    report = la.audit(tmp_path, covered)
    assert report["passed"] and _row(report, "alpha")["decision"] == "REG"


def test_ac2_the_command_exits_1_when_blocked(monkeypatch, capsys):
    monkeypatch.setattr(
        la,
        "audit",
        lambda *a, **k: {
            "components": [],
            "counts": {},
            "unreviewed": 0,
            "decisions": [],
            "blockers": ["python package x: REVIEW"],
            "release_blockers": ["python package x: REVIEW"],
        },
    )
    assert la.main(["check"]) == 1 and "BLOCKED" in capsys.readouterr().out
    monkeypatch.setattr(
        la,
        "audit",
        lambda *a, **k: {
            "components": [],
            "counts": {},
            "unreviewed": 0,
            "decisions": [],
            "blockers": [],
            "release_blockers": ["sign-off: none"],
        },
    )
    assert la.main(["check"]) == 0 and la.main(["check", "--release"]) == 1


# ------------------------------------------------------------------ AC3: decisions applied


def _decision(check: str, **more: Any) -> dict[str, Any]:
    return {"d": {"about": "x", "decision": "do it", "check": check, **WHO, **more}}


def test_ac3_a_decision_is_applied_when_its_check_finds_nothing(tmp_path):
    root = _repo(
        tmp_path,
        versions='APT_PINS="redis-server=5:7.0.15-1ubuntu0.24.04.4"\n',
        compose="services:\n  redis:\n    image: redis:7.2-alpine\n",
    )
    components = {
        "apt:redis-server": {"licence": "BSD-3-Clause", "source": "sheet"},
        "image:redis": {"licence": "BSD-3-Clause", "source": "sheet"},
    }
    report = la.audit(root, _facts(decisions=_decision("redis-pinned"), components=components))
    assert report["decisions"][0]["status"] == "APPLIED" and report["release_ready"]


@pytest.mark.parametrize("image", ["redis:7-alpine", "redis:7.4", "redis:8.0", "redis:latest"])
def test_ac3_an_unpinned_or_newer_redis_is_found(tmp_path, image):
    root = _repo(tmp_path, compose=f"services:\n  redis:\n    image: {image}\n")
    assert la.CHECKS["redis-pinned"](root), image


def test_ac3_a_decision_not_applied_needs_an_owner_and_a_date(tmp_path):
    root = _repo(tmp_path, deps=["alpha>=1", "pymupdf>=1.24"])
    facts = _facts(
        {
            "pymupdf": {
                "version": "1.28",
                "licence": "AGPL-3.0",
                "source": "PyPI",
                "decision": {"decision": "being replaced", **WHO},
            }
        },
        decisions=_decision("pymupdf-replaced"),
    )
    report = la.audit(root, facts)
    d = report["decisions"][0]
    assert d["applied"] is False and d["status"] == "OPEN - NEEDS AN OWNER AND A DATE"
    assert d["found"] == ["pymupdf in pyproject.toml"]
    assert "decision d: OPEN - NEEDS AN OWNER AND A DATE" in report["release_blockers"]
    facts["register"]["decisions"]["d"].update(owner="R. Engineer", due="2026-11-01")
    owned = la.audit(root, facts)
    assert owned["decisions"][0]["status"] == "OPEN - owner and date recorded"
    assert owned["release_ready"], "open, but owned and dated: the report says so"


def test_ac3_each_register_decision_has_a_check_that_reads_the_repository(tmp_path):
    register = json.loads((ROOT / "licences/register.json").read_text(encoding="utf-8"))
    checked = {d["check"] for d in register["decisions"].values() if d.get("check")}
    assert (
        checked
        == set(la.CHECKS)
        >= {
            "pymupdf-replaced",
            "qwen-3b-removed",
            "object-store-pluggable",
            "redis-pinned",
            "grafana-not-bundled",
            "iam-not-used-for-training",
            "opencv-headless-only",
        }
    )
    root = _repo(tmp_path, deps=["opencv-python>=4", "opencv-python-headless>=4"])
    assert la.CHECKS["opencv-headless-only"](root) == ["opencv-python in pyproject.toml"]
    (root / "src/cdi_adapter/config.py").write_text(
        '    vlm_fallback_model_id: str = "Qwen/Qwen2.5-VL-3B-Instruct"\n', encoding="utf-8"
    )
    assert len(la.CHECKS["qwen-3b-removed"](root)) == 2


def test_ac3_the_report_shows_each_decision_with_its_status(tmp_path):
    report = la.audit(_repo(tmp_path), _facts(decisions=_decision("notice-shipped")))
    assert "| x: do it | APPLIED | - | - | - |" in la.as_markdown(report)


# ------------------------------------------------------------------ AC4: no entry, no build


def test_ac4_a_package_added_without_an_entry_fails_the_build(tmp_path):
    root = _repo(tmp_path, deps=["alpha>=1", "newthing>=0.1"])
    report = la.audit(root, _facts())
    assert not report["passed"]
    assert report["blockers"] == [
        "python package newthing: not in the register: add an entry "
        "with its licence and where it was read"
    ]


def test_ac4_a_package_added_only_to_a_lock_file_is_caught_too(tmp_path):
    root = _repo(tmp_path, lock="alpha==1.0 \\\n    --hash=sha256:00\nSneaky_Dep==2.0 \\\n")
    report = la.audit(root, _facts())
    assert [b.split(":")[0] for b in report["blockers"]] == ["python package sneaky-dep"]


def test_ac4_a_model_added_without_an_entry_fails_the_build(tmp_path):
    root = _repo(tmp_path, model="someone/new-model")
    report = la.audit(root, _facts())
    assert report["blockers"][0].startswith("model someone/new-model: not in the register")
    models = {
        "someone/new-model": {
            "licence": "Apache-2.0",
            "source": "model card 2026-10-13",
            "revision": "abc",
            "sha256": "00",
            "licence_text_copy": "x",
        }
    }
    assert la.audit(root, _facts(models=models))["release_ready"]


def test_ac4_a_model_named_in_an_env_file_is_seen_as_well(tmp_path):
    root = _repo(tmp_path)
    (root / "infra/runpod/pod.env").write_text("CDI_VLM_MODEL_ID=org/other-model\n", "utf-8")
    assert la.build_models(root) == {"org/other-model": "infra/runpod/pod.env CDI_VLM_MODEL_ID"}


def test_ac4_a_server_added_to_the_pins_without_an_entry_fails_the_build(tmp_path):
    root = _repo(tmp_path, versions="NEWDB_VERSION=1.2.3\n")
    assert la.audit(root, _facts())["blockers"][0].startswith("server or binary newdb: not in")


def test_ac4_a_model_needs_its_revision_checksum_and_licence_copy_for_a_release(tmp_path):
    models = {
        "org/reader": {
            "licence": "Apache-2.0",
            "source": "model card",
            "revision": "abc",
            "sha256": None,
            "licence_text_copy": None,
        }
    }
    report = la.audit(_repo(tmp_path, model="org/reader"), _facts(models=models))
    assert report["passed"] and not report["release_ready"]
    assert {
        "model org/reader: no sha256 recorded",
        "model org/reader: no licence_text_copy recorded",
    } <= set(report["release_blockers"])


def test_ac4_recording_reads_the_licence_from_the_package_never_invents_one(tmp_path):
    root = _repo(tmp_path, deps=["pytest>=8", "not-installed-anywhere>=1"])
    (root / "licences").mkdir()
    (root / "licences/packages.json").write_text("{}", encoding="utf-8")
    assert la.record(root) == ["pytest"]
    saved = json.loads((root / "licences/packages.json").read_text(encoding="utf-8"))
    assert (
        set(saved) == {"pytest"} and la.verdict_of(saved["pytest"]["licence"], POLICY) == "ALLOWED"
    )
    assert saved["pytest"]["source"].startswith("installed metadata")


# ------------------------------------------------------------------ AC5: the installed package


def _package(
    tmp_path: Path,
    *,
    notice: str | None = "CDI-Adapter\n",
    texts: tuple[str, ...] = ("alpha",),
    shipped: tuple[str, ...] = ("alpha",),
    extra: tuple[str, ...] = (),
) -> Path:
    out = tmp_path / "installed"
    out.mkdir()
    if notice is not None:
        (out / "NOTICE").write_text(notice, encoding="utf-8")
    (out / "sbom.json").write_text(json.dumps({"shipped_python_packages": list(shipped)}), "utf-8")
    for name in texts:
        (out / "THIRD_PARTY_LICENSES" / name).mkdir(parents=True)
        (out / "THIRD_PARTY_LICENSES" / name / "LICENSE").write_text("MIT ...", encoding="utf-8")
    for rel in extra:
        (out / rel).parent.mkdir(parents=True, exist_ok=True)
        (out / rel).write_bytes(b"\x7fELF")
    return out


def test_ac5_a_complete_package_passes(tmp_path):
    assert la.inspect(_package(tmp_path), []) == []


def test_ac5_a_missing_or_empty_notice_is_found(tmp_path):
    assert la.inspect(_package(tmp_path, notice=None), []) == ["no NOTICE file"]
    (tmp_path / "installed" / "NOTICE").write_text("  \n", encoding="utf-8")
    assert la.inspect(tmp_path / "installed", []) == ["the NOTICE file is empty"]


def test_ac5_a_shipped_package_without_its_licence_text_is_found(tmp_path):
    out = _package(tmp_path, shipped=("alpha", "beta"))
    assert la.inspect(out, []) == ["no licence text for beta"]


@pytest.mark.parametrize(
    "rel",
    [
        "lib/libcudnn.so.9",
        "lib/libcublas.so.12",
        "lib/libnvinfer.so",
        "wheels/nvidia_cudnn_cu12-9.1.0-py3-none-manylinux.whl",
        "debs/cuda-nvcc-12-8_12.8.93-1_amd64.deb",
        "bin/cudart64_12.dll",
    ],
)
def test_ac5_an_nvidia_file_that_is_not_on_the_list_is_found(tmp_path, rel):
    out = _package(tmp_path, extra=(rel,))
    assert la.inspect(out, []) == [f"NVIDIA file not on the redistributable list: {rel}"]


def test_ac5_an_nvidia_file_on_the_list_is_allowed_and_others_are_not(tmp_path):
    out = _package(tmp_path, extra=("lib/libcudnn.so.9", "lib/libcublas.so.12"))
    assert la.inspect(out, ["libcudnn.so.9"]) == [
        "NVIDIA file not on the redistributable list: lib/libcublas.so.12"
    ]


def test_ac5_ordinary_files_are_not_mistaken_for_nvidia_ones(tmp_path):
    out = _package(
        tmp_path,
        extra=("app/convert.py", "lib/libcurl.so.4", "lib/libnuma.so.1", "app/cuda_check.py"),
    )
    assert la.inspect(out, []) == []


def test_ac5_a_wheel_or_zip_is_inspected_the_same_way(tmp_path):
    out = _package(tmp_path, extra=("lib/libcudnn.so.9",))
    packed = tmp_path / "installed.zip"
    with zipfile.ZipFile(packed, "w") as z:
        for p in out.rglob("*"):
            if p.is_file():
                z.write(p, p.relative_to(out).as_posix())
    assert la.inspect(packed, []) == [
        "NVIDIA file not on the redistributable list: lib/libcudnn.so.9"
    ]


def test_ac5_the_notice_goes_into_every_built_package():
    project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'license-files = ["NOTICE"]' in project and (ROOT / "NOTICE").stat().st_size > 0


def test_ac5_the_bundle_collects_the_licence_texts_from_the_packages_themselves(tmp_path):
    made = la.bundle(tmp_path / "out")
    assert (tmp_path / "out/NOTICE").read_text(encoding="utf-8").startswith("CDI-Adapter")
    texts = tmp_path / "out/THIRD_PARTY_LICENSES"
    assert any((texts / "pypdfium2").iterdir()), "PDFium's licence files are shipped"
    assert any((texts / "psycopg").iterdir()), "the LGPL text is shipped"
    assert "pypdfium2" in made["shipped_python_packages"]
    assert "pytest" not in made["shipped_python_packages"], "a developer tool is not shipped"
    assert set(made["licence_texts_missing"]).isdisjoint({"pypdfium2", "psycopg"})


def test_ac5_today_no_nvidia_file_may_be_shipped_because_the_list_is_not_established():
    listed = json.loads((ROOT / "licences/nvidia_redistributable.json").read_text("utf-8"))
    assert listed["files"] == [] and listed["status"].startswith("NOT ESTABLISHED")


# ------------------------------------------------------------------ this repository, today


@pytest.fixture(scope="module")
def today() -> dict[str, Any]:
    return la.audit(ROOT)


def test_today_the_report_covers_packages_servers_models_data_and_code_lists(today):
    kinds = {r["kind"] for r in today["components"]}
    assert kinds == {
        "python package",
        "server or binary",
        "model",
        "data set",
        "code list",
        "other",
    }
    names = {r["name"] for r in today["components"]}
    assert {
        "pypdfium2",
        "psycopg",
        "opencv-python-headless",
        "minio",
        "apt:redis-server",
        "Qwen/Qwen2.5-VL-7B-Instruct",
        "microsoft/trocr-base-handwritten",
        "LOINC",
    } <= names
    assert len(today["components"]) > 200


def test_today_the_decisions_already_applied_are_reported_applied(today):
    status = {d["id"]: d["status"] for d in today["decisions"]}
    assert [status[k] for k in ("pymupdf", "iam", "redis", "notice")] == ["APPLIED"] * 4
    assert "pymupdf" not in {r["name"] for r in today["components"]}


def test_today_the_gate_finds_what_nobody_had_listed(today):
    """Things in the build that the Licence Register does not have: the gate blocks on them."""
    blocked = "\n".join(today["blockers"])
    for name in (
        "model obi/deid_roberta_i2b2: not in the register",
        "model en_core_web_lg: not in the register",
        "model microsoft/trocr-base-handwritten: REVIEW",
        "data set medicine catalogue (1mg-derived): the recorded decision is NOT",
    ):
        assert name in blocked


@pytest.mark.xfail(
    strict=True,
    reason="AC1 / AC2 on the real repository: components are still "
    "blocked - packages of the vLLM and quantisation locks with no licence "
    "recorded (run 'licence_audit.py record' on the pod), servers whose licence "
    "nobody has read, review-list items with no decision. People must decide.",
)
def test_today_ac1_every_component_has_a_verdict_and_none_is_unreviewed(today):
    assert today["passed"] and today["unreviewed"] == 0


@pytest.mark.xfail(
    strict=True,
    reason="AC3 on the real repository: six decisions are not "
    "applied (Qwen 3B in a drill script, object store, Grafana, the non-headless "
    "OpenCV wheel in the pod lock, the 1mg catalogue, ICD codes in the seed) and "
    "none of them has an owner and a date - nobody has been named.",
)
def test_today_ac3_every_decision_is_applied_or_owned_and_dated(today):
    assert all(d["applied"] or (d["owner"] and d["due"]) for d in today["decisions"])


@pytest.mark.xfail(
    strict=True,
    reason="AC5: no installed-version package is built by this "
    "repository yet, and the NVIDIA redistributable list has not been "
    "established (the EULA was not read in full).",
)
def test_today_ac5_an_installed_package_exists_and_passes():
    built = ROOT / "dist/installed"
    listed = json.loads((ROOT / "licences/nvidia_redistributable.json").read_text("utf-8"))
    assert built.is_dir() and listed["read_on"] and la.inspect(built, listed["files"]) == []
