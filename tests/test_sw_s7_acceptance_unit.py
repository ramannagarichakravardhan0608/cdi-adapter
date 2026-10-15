"""SW-S7 "A licence and safety gate in front of every swap". NOT LEGAL ADVICE.

* AC1  a model with a research-only licence set as champion: the gate refuses - and the same
       gate stands in front of a contest, a switch, shadow mode and fine-tuning;
* AC2  a swap that adds a package with a strong copyleft licence: the build fails until a
       decision is recorded (the SBOM before and after: ``licence_audit.py swap-gate``);
* AC3  a model licence that requires a notice line: the line is in the NOTICE file of a release;
* AC4  a model whose files do not match the registered checksum: the service refuses to load it.

The registries and repositories here are made up, in temporary folders; the real ones are
checked at the end.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from cdi_adapter import licences
from cdi_adapter.config import Settings, get_settings
from cdi_adapter.modelops import catalog
from tests.test_ent_s1_acceptance_unit import WHO, _facts, _repo, la

ROOT = Path(__file__).resolve().parents[1]
REV = "d" * 40
RESEARCH = "Research use only (the model card: not for commercial use)"


@pytest.fixture
def registry(tmp_path, monkeypatch):
    """Write a registry with any licence; a licence TEXT is saved for every model, so only what
    the licence SAYS can stop it."""
    text = ROOT / "licences" / "models" / "test-sw-s7-licence.txt"
    text.parent.mkdir(parents=True, exist_ok=True)
    text.write_text("A licence text (test copy)\n", encoding="utf-8")
    path = tmp_path / "registry.json"

    def write(name: str, decision: dict[str, Any] | None = None, **licence: Any) -> Settings:
        entry = {
            "id": "m",
            "roles": ["extraction"],
            "model_id": "Org/Model-7B",
            "revision": REV,
            "status": "candidate",
            "licence": {
                "name": name,
                "text_file": "licences/models/test-sw-s7-licence.txt",
                **({"decision": decision} if decision else {}),
                **licence,
            },
        }
        path.write_text(json.dumps({"models": [entry]}), encoding="utf-8")
        monkeypatch.setenv("CDI_MODEL_REGISTRY_PATH", str(path))
        get_settings.cache_clear()
        catalog.forget()
        return Settings(model_registry_path=str(path), champion_extraction="m")

    yield SimpleNamespace(write=write)
    get_settings.cache_clear()
    catalog.forget()
    text.unlink()
    if not any(text.parent.iterdir()):
        text.parent.rmdir()


# ------------------------------------------------------------------ AC1: research-only refused


@pytest.mark.parametrize(
    "name",
    [
        RESEARCH,
        "research-only licence",
        "Qwen RESEARCH LICENSE AGREEMENT",
        "CC-BY-NC-4.0",
        "academic use only",
        "GPL-3.0",
        "see the website",  # nothing recognisable: UNKNOWN, never guessed
    ],
)
def test_ac1_a_model_whose_licence_the_policy_blocks_cannot_be_champion(registry, name):
    s = registry.write(name)
    why = catalog.refusal("extraction", s)
    assert why is not None and why.startswith("CDI_CHAMPION_EXTRACTION='m': m cannot be champion:")
    with pytest.raises(SystemExit, match="refusing to start"):
        catalog.check(s)


def test_ac1_the_refusal_says_what_the_licence_is_and_what_would_lift_it(registry):
    why = catalog.refusal("extraction", registry.write(RESEARCH))
    assert f"its licence ({RESEARCH}) is REVIEW: NON-COMMERCIAL" in str(why)
    assert "blocked until a decision to use it is recorded (decision, by, date)" in str(why)


def test_ac1_an_allowed_licence_passes(registry):
    for name in ("Apache-2.0", "MIT", "BSD-3-Clause"):
        assert catalog.refusal("extraction", registry.write(name)) is None


def test_ac1_only_a_whole_decision_to_use_it_lifts_the_block(registry):
    full = {"decision": "used under a written licence from the vendor", **WHO}
    assert catalog.refusal("extraction", registry.write(RESEARCH, full)) is None
    for partial in (
        {"decision": "ok"},
        {"decision": "ok", "by": "X"},
        {**full, "outcome": "do not use"},
    ):
        assert catalog.refusal("extraction", registry.write(RESEARCH, partial)) is not None


def test_ac1_the_same_gate_stands_in_front_of_every_swap(registry, tmp_path):
    from cdi_adapter.finetune import run
    from cdi_adapter.modelops import contest

    registry.write(RESEARCH)
    entry = catalog.find("m")
    assert entry is not None and "REVIEW: NON-COMMERCIAL" in str(catalog.licence_problem(entry))
    assert "REVIEW: NON-COMMERCIAL" in str(contest.licence_refusal("m", "extraction"))  # contest,
    # switch (SW-S5) and shadow mode all ask contest.licence_refusal
    with pytest.raises(run.Refused) as refused:  # fine-tuning (SW-S3)
        run.licence_check("m")
    assert refused.value.reason == "licence" and "it may not be trained" in refused.value.detail


def test_ac1_trocr_as_its_licence_is_recorded_today_would_be_refused_even_with_its_text():
    real = {
        m["id"]: m for m in json.loads((ROOT / "models/registry.json").read_text("utf-8"))["models"]
    }
    trocr = real["trocr-base-handwritten"]["licence"]
    assert "REVIEW: NON-COMMERCIAL" in str(licences.model_problem(trocr))  # the IAM terms
    assert licences.model_problem(real["rapidocr-onnxruntime"]["licence"]) is None


# ------------------------------------------------------------------ AC2: copyleft, until decided


def _at(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _baseline(tmp_path: Path) -> dict[str, Any]:
    before = la.audit(_repo(_at(tmp_path / "before")), _facts())
    return {"components": la.snapshot(before)}


def test_ac2_a_swap_adding_a_strong_copyleft_package_fails_the_build(tmp_path):
    base = _baseline(tmp_path)
    gpl = {"gplthing": {"version": "2.0", "licence": "GPL-3.0-only", "source": "PyPI"}}
    after = la.audit(_repo(_at(tmp_path / "after"), deps=["alpha>=1", "gplthing>=2"]), _facts(gpl))
    got = la.swap_gate(after, base)
    assert not got["passed"]
    assert got["added"] == ["python package: gplthing"]
    assert got["blocking"] == [
        "python package: gplthing (new, GPL-3.0-only): REVIEW: a decision must be recorded "
        "(what, who, when) before this may be built"
    ]


def test_ac2_it_passes_once_the_decision_is_recorded(tmp_path):
    base = _baseline(tmp_path)
    gpl = {
        "gplthing": {
            "version": "2.0",
            "licence": "GPL-3.0-only",
            "source": "PyPI",
            "decision": {"decision": "separate process only", **WHO},
        }
    }
    after = la.audit(_repo(_at(tmp_path / "after"), deps=["alpha>=1", "gplthing>=2"]), _facts(gpl))
    assert la.swap_gate(after, base)["passed"]


def test_ac2_an_added_package_with_no_entry_is_unknown_and_blocked(tmp_path):
    base = _baseline(tmp_path)
    after = la.audit(_repo(_at(tmp_path / "after"), deps=["alpha>=1", "mystery>=1"]), _facts())
    got = la.swap_gate(after, base)
    assert got["blocking"] == [
        "python package: mystery (new, no licence): not in the register: add an entry with its "
        "licence and where it was read"
    ]


def test_ac2_a_package_whose_licence_changes_is_checked_again(tmp_path):
    base = _baseline(tmp_path)
    now_agpl = {"alpha": {"version": "1.0", "licence": "AGPL-3.0", "source": "PyPI"}}
    after = la.audit(_repo(_at(tmp_path / "after")), _facts(now_agpl))
    got = la.swap_gate(after, base)
    assert got["changed"] == ["python package: alpha"] and not got["passed"]


def test_ac2_what_was_already_there_is_the_full_gates_business_not_the_swaps(tmp_path):
    old = {"legacy": {"version": "1", "licence": "LGPL-2.1 or GPL-2.0", "source": "PyPI"}}
    before = la.audit(_repo(_at(tmp_path / "b"), deps=["alpha>=1", "mystery-old>=1"]), _facts(old))
    after = la.audit(_repo(_at(tmp_path / "a"), deps=["alpha>=1", "mystery-old>=1"]), _facts(old))
    assert not after["passed"]  # the full gate still blocks it ...
    assert la.swap_gate(after, {"components": la.snapshot(before)})["passed"]  # ... no swap did


def test_ac2_no_baseline_is_no_pass(tmp_path):
    got = la.swap_gate(la.audit(_repo(tmp_path), _facts()), None)
    assert not got["passed"] and "no baseline" in got["blocking"][0]


def test_ac2_the_gate_runs_in_ci_and_in_the_deploy_step():
    ci = (ROOT / ".github/workflows/ci.yml").read_text("utf-8")
    assert "swap-gate:" in ci and "python scripts/licence_audit.py swap-gate" in ci
    deploy = (ROOT / "infra/runpod/start_all.sh").read_text("utf-8")
    gate = deploy.index("scripts/licence_audit.py swap-gate")
    assert gate < deploy.index('bash "$HERE/start_mlserve.sh"')  # before any model is loaded
    assert "SWAP GATE BLOCKED" in deploy and "exit 1" in deploy[gate : gate + 300]


def test_ac2_this_repository_passes_its_swap_gate(capsys):
    assert la.main(["swap-gate"]) == 0
    assert "swap gate: PASS" in capsys.readouterr().out


# ------------------------------------------------------------------ AC3: the notice line


def _with_model(root: Path, notice: list[str], status: str = "candidate") -> Path:
    (root / "models").mkdir(parents=True, exist_ok=True)
    entry = {
        "id": "qwen-like",
        "model_id": "Org/Qwen-Like",
        "status": status,
        "licence": {"name": "Apache-2.0", "notice": notice},
    }
    (root / "models/registry.json").write_text(json.dumps({"models": [entry]}), "utf-8")
    return root


LINE = (
    "Org Qwen-Like is licensed under its licence agreement, Copyright (c) Org. All Rights Reserved."
)


def test_ac3_a_model_licences_notice_line_goes_into_the_notice_file(tmp_path):
    root = _with_model(_repo(tmp_path), [LINE])
    report = la.audit(root, _facts())
    text = la.notice_text(report, _facts()["register"], la.model_notices(root))
    assert LINE in text and "(required by the licence of qwen-like)" in text


def test_ac3_a_release_is_blocked_while_the_notice_file_lacks_the_line(tmp_path):
    root = _with_model(_repo(tmp_path), [LINE])
    report = la.audit(root, _facts())
    assert (
        f"NOTICE lacks a line a model licence requires - qwen-like: {LINE}; run "
        "`python scripts/licence_audit.py notice`" in report["release_blockers"]
    )
    (root / "NOTICE").write_text(
        la.notice_text(report, _facts()["register"], la.model_notices(root)), "utf-8"
    )
    assert not any("NOTICE lacks" in b for b in la.audit(root, _facts())["release_blockers"])


def test_ac3_a_retired_model_needs_no_notice(tmp_path):
    root = _with_model(_repo(tmp_path), [LINE], status="retired")
    assert la.model_notices(root) == []


def test_ac3_the_release_bundle_writes_the_line_itself():
    source = inspect.getsource(la.bundle)
    assert 'notice_text(report, facts["register"], model_notices(root))' in source


# ------------------------------------------------------------------ AC4: the checksum


def test_ac4_files_that_do_not_match_the_registered_checksum_are_refused(tmp_path, monkeypatch):
    good = tmp_path / "good"
    good.mkdir()
    (good / "model.safetensors").write_bytes(b"the weights that were reviewed")
    path = tmp_path / "registry.json"
    entry = {
        "id": "m",
        "roles": ["extraction"],
        "model_id": "Org/Model-7B",
        "revision": REV,
        "checksum": catalog.checksum(good),
        "licence": {"name": "MIT"},
    }
    path.write_text(json.dumps({"models": [entry]}), "utf-8")
    s = Settings(model_registry_path=str(path))
    catalog.forget()
    swapped = tmp_path / "swapped"
    swapped.mkdir()
    (swapped / "model.safetensors").write_bytes(b"other weights under the same revision")
    import huggingface_hub

    monkeypatch.setattr(huggingface_hub, "snapshot_download", lambda *a, **k: str(swapped))
    with pytest.raises(catalog.ModelMismatch, match="the files do not match the registry"):
        catalog.verify_loaded("Org/Model-7B", REV, s)
    monkeypatch.setattr(huggingface_hub, "snapshot_download", lambda *a, **k: str(good))
    assert catalog.verify_loaded("Org/Model-7B", REV, s) == entry["checksum"]
    catalog.forget()


def test_ac4_every_model_loader_checks_the_files_it_loaded():
    from cdi_adapter.mlserve import backends
    from cdi_adapter.recognition import engines

    for loader in (backends._default_loader, backends._default_engine_factory, engines.load_trocr):
        assert "catalog.verify_loaded(" in inspect.getsource(loader), loader.__name__
    assert "trust_remote_code=False" in inspect.getsource(backends._default_engine_factory)


# ------------------------------------------------------------------ the real registry


@pytest.mark.xfail(
    strict=True,
    reason="not done yet: no model in models/registry.json has its licence text saved, and "
    "TrOCR's recorded licence (IAM: non-commercial research only) needs counsel's decision",
)
def test_every_registered_model_passes_the_gate():
    catalog.forget()
    get_settings.cache_clear()
    assert all(catalog.licence_problem(e) is None for e in catalog.entries())
