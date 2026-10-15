"""SW-S2 "Model registry: every model recorded, pinned and chosen by settings".

* AC1  a champion that is not in the registry: the service refuses to start, saying so;
* AC2  the extraction champion changed by a setting: the result shows the new model id,
       revision and prompt version;
* AC3  a registered model with no licence text saved cannot be set as champion;
* AC4  the files change on the hub but the pinned revision does not: what is loaded still
       matches the registry's checksum.

The registries used here are written into a temporary folder, with made-up models, so each
rule can be shown alone. The real registry (``models/registry.json``) is checked at the end.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from cdi_adapter import result
from cdi_adapter.config import Settings, get_settings
from cdi_adapter.extract import prompt
from cdi_adapter.extract import service as X
from cdi_adapter.modelops import catalog

ROOT = Path(__file__).resolve().parents[1]
REV_A = "a" * 40
REV_B = "b" * 40
PROMPT_NOW = prompt.prompt_version("prescription")


def _entry(
    id_: str,
    model_id: str,
    revision: str = REV_A,
    *,
    roles: tuple[str, ...] = ("extraction",),
    licence: str | None = "licences/models/test-apache.txt",
    prompt_version: str | None = PROMPT_NOW,
    status: str = "candidate",
    checksum: str | None = None,
) -> dict[str, Any]:
    return {
        "id": id_,
        "roles": list(roles),
        "model_id": model_id,
        "revision": revision,
        "checksum": checksum,
        "status": status,
        "trained_on": "made up for a test",
        "served_by": "test",
        "gpu_memory_gb": 1,
        "schema_version": "prescription.v3",
        "prompt_version": prompt_version,
        "licence": {"name": "Apache-2.0", "text_file": licence, "checked_on": "2026-10-14"},
    }


@pytest.fixture
def registry(tmp_path, monkeypatch):
    """Write a registry; point the settings at it; give back a helper that sets champions."""
    licence = ROOT / "licences" / "models" / "test-apache.txt"
    made_licence = not licence.exists()
    if made_licence:
        licence.parent.mkdir(parents=True, exist_ok=True)
        licence.write_text("Apache License, Version 2.0 (test copy)\n", encoding="utf-8")
    path = tmp_path / "registry.json"

    def write(*models: dict[str, Any]) -> Path:
        path.write_text(json.dumps({"models": list(models)}), encoding="utf-8")
        catalog.forget()
        return path

    def settings(**champions: str) -> Settings:
        return Settings(
            model_registry_path=str(path), **{f"champion_{k}": v for k, v in champions.items()}
        )

    yield SimpleNamespace(write=write, settings=settings, path=path)
    catalog.forget()
    if made_licence:
        licence.unlink()
        if not any(licence.parent.iterdir()):
            licence.parent.rmdir()


# ------------------------------------------------------------------ AC1: not registered, refused


def test_ac1_a_champion_that_is_not_registered_is_refused_at_start(registry):
    registry.write(_entry("qwen-7b", "Qwen/Qwen2.5-VL-7B-Instruct"))
    s = registry.settings(extraction="Some/Unlisted-Model")
    with pytest.raises(SystemExit) as stopped:
        catalog.check(s)
    said = str(stopped.value)
    assert said.startswith("refusing to start: CDI_CHAMPION_EXTRACTION='Some/Unlisted-Model'")
    assert (
        "the model is not registered in registry.json (registered for extraction: qwen-7b)" in said
    )


def test_ac1_a_registered_model_at_a_revision_that_is_not_registered_is_refused(registry):
    registry.write(_entry("qwen-7b", "Qwen/Qwen2.5-VL-7B-Instruct", REV_A))
    s = registry.settings(extraction=f"Qwen/Qwen2.5-VL-7B-Instruct@{REV_B}")
    assert "is not registered" in str(catalog.refusal("extraction", s))
    assert (
        catalog.refusal(
            "extraction", registry.settings(extraction=f"Qwen/Qwen2.5-VL-7B-Instruct@{REV_A}")
        )
        is None
    )


def test_ac1_a_model_registered_for_another_role_is_refused(registry):
    registry.write(_entry("trocr", "microsoft/trocr-base-handwritten", roles=("handwriting",)))
    found = catalog.refusal("extraction", registry.settings(extraction="trocr"))
    assert found == (
        "CDI_CHAMPION_EXTRACTION='trocr': trocr is registered for handwriting, not for extraction"
    )


def test_ac1_a_retired_model_or_one_without_an_exact_revision_is_refused(registry):
    registry.write(
        _entry("old", "Org/Old", status="retired"),
        _entry("floating", "Org/Floating", revision="main"),
    )
    assert "is retired" in str(catalog.refusal("extraction", registry.settings(extraction="old")))
    assert "no exact revision pinned (never 'latest')" in str(
        catalog.refusal("extraction", registry.settings(extraction="floating"))
    )


def test_ac1_with_no_champion_set_the_service_starts_as_it_always_has(registry):
    registry.write(_entry("qwen-7b", "Qwen/Qwen2.5-VL-7B-Instruct"))
    s = registry.settings()
    catalog.check(s)
    before = (s.vlm_model_id, s.vlm_model_revision, s.trocr_model_id)
    assert (catalog.apply(s).vlm_model_id, s.vlm_model_revision, s.trocr_model_id) == before
    assert catalog.identity("extraction", s)["chosen_by"] == "model settings (no champion set)"


@pytest.mark.parametrize(
    "module",
    ["cdi_adapter.webapp.app", "cdi_adapter.mlserve.__main__", "cdi_adapter.ocrhost.__main__"],
)
def test_ac1_every_service_checks_before_it_serves(module):
    import importlib

    source = Path(importlib.util.find_spec(module).origin).read_text(encoding="utf-8")  # type: ignore[arg-type,union-attr]
    main = source.split("def main() -> None:")[1]
    assert main.index("catalog.check(") < main.index("catalog.apply(") < main.index("uvicorn.run(")


def test_ac1_the_real_model_server_start_refuses_by_name(tmp_path):
    """The model server's own entry point, in a process of its own, with an unlisted champion."""
    done = subprocess.run(
        [sys.executable, "-c", "from cdi_adapter.mlserve.__main__ import main; main()"],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=tmp_path,
        env={
            **__import__("os").environ,
            "CDI_CHAMPION_EXTRACTION": "Some/Unlisted-Model",
            "PYTHONPATH": str(ROOT / "src"),
        },
    )
    assert done.returncode != 0
    assert (
        "refusing to start: CDI_CHAMPION_EXTRACTION='Some/Unlisted-Model': the model is not "
        "registered"
    ) in done.stderr + done.stdout


# ------------------------------------------------------------------ AC2: the champion in the result


def test_ac2_the_champion_becomes_the_model_the_service_loads(registry):
    registry.write(
        _entry("qwen-7b", "Qwen/Qwen2.5-VL-7B-Instruct", REV_A),
        _entry("qwen-next", "Qwen/Next-VL-Model", REV_B),
    )
    s = catalog.apply(registry.settings(extraction="qwen-next"))
    assert (s.vlm_model_id, s.vlm_model_revision) == ("Qwen/Next-VL-Model", REV_B)
    assert catalog.identity("extraction", s) == {
        "model_id": "Qwen/Next-VL-Model",
        "revision": REV_B,
        "registered_as": "qwen-next",
        "chosen_by": "CDI_CHAMPION_EXTRACTION",
    }


def test_ac2_the_extraction_records_the_new_model_id_revision_and_prompt(registry, monkeypatch):
    registry.write(_entry("qwen-next", "Qwen/Next-VL-Model", REV_B))
    monkeypatch.setenv("CDI_MODEL_REGISTRY_PATH", str(registry.path))
    monkeypatch.setenv("CDI_CHAMPION_EXTRACTION", "qwen-next")
    get_settings.cache_clear()
    try:
        catalog.apply(get_settings())
        stack = {
            "extractor": "Qwen/Next-VL-Model",
            **X.versions("prescription"),
            "extractor_revision": X.revision_of("Qwen/Next-VL-Model"),
            "extractor_registered_as": X.registered_as("Qwen/Next-VL-Model"),
        }
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
    assert stack["extractor_revision"] == REV_B and stack["extractor_registered_as"] == "qwen-next"
    assert stack["prompt_version"] == PROMPT_NOW
    source = Path(X.__file__).read_text(encoding="utf-8")
    assert '"extractor_revision": revision_of(vlm.model),' in source, "written with every value"


class _Db:
    def __init__(self, runs: list[Any], stack: dict[str, Any]) -> None:
        self.runs, self.stack = runs, stack

    def execute(self, statement: Any, params: Any = None) -> Any:
        sql = str(statement)
        rows: list[Any] = (
            self.runs
            if "pipeline_run" in sql
            else []
            if "ocr_observation" in sql
            else [{"model_stack": self.stack}]
        )
        return SimpleNamespace(
            mappings=lambda: SimpleNamespace(
                all=lambda: rows, first=lambda: rows[0] if rows else None
            )
        )


def test_ac2_the_result_shows_the_model_id_revision_and_prompt_version():
    stack = {
        "extractor": "Qwen/Next-VL-Model",
        "extractor_revision": REV_B,
        "prompt_version": PROMPT_NOW,
        "schema": "prescription.v3",
    }
    runs = [{"stage": "extract", "model_name": "Qwen/Next-VL-Model", "model_version": None}]
    engines = {
        (e["stage"], e["name"]): e["version"] for e in result._engines(_Db(runs, stack), "d")
    }
    assert engines[("extract", "Qwen/Next-VL-Model")] == REV_B  # the model id and its revision
    assert engines[("extract", "prompt_version")] == PROMPT_NOW  # the prompt version
    assert ("extract", "prescription.v3") in engines  # and the answer format


def test_ac2_switching_needs_no_code_change_only_the_setting():
    assert {
        "champion_extraction",
        "champion_handwriting",
        "champion_printed",
        "model_registry_path",
    } <= set(Settings.model_fields)
    assert catalog.SETTING == {
        "extraction": "CDI_CHAMPION_EXTRACTION",
        "handwriting": "CDI_CHAMPION_HANDWRITING",
        "printed": "CDI_CHAMPION_PRINTED",
    }


def test_ac2_the_handwriting_champion_is_loaded_at_its_pinned_revision(registry, monkeypatch):
    registry.write(
        _entry("trocr-x", "org/trocr-x", REV_B, roles=("handwriting",), prompt_version=None)
    )
    from cdi_adapter.recognition import engines

    monkeypatch.setenv("CDI_MODEL_REGISTRY_PATH", str(registry.path))
    monkeypatch.setenv("CDI_CHAMPION_HANDWRITING", "trocr-x")
    get_settings.cache_clear()
    try:
        catalog.apply(get_settings())
        assert engines.pinned_revision("org/trocr-x") == REV_B
        assert engines.pinned_revision("someone/else") is None
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()


# -------------------------------------------------------- AC3: no licence text, no champion


def test_ac3_a_model_with_no_licence_text_saved_cannot_be_champion(registry):
    registry.write(_entry("no-licence", "Org/NoLicence", licence=None))
    found = catalog.refusal("extraction", registry.settings(extraction="no-licence"))
    assert found == (
        "CDI_CHAMPION_EXTRACTION='no-licence': no-licence has no licence text saved; "
        "a model without one cannot be champion (save it and name it in "
        "licence.text_file)"
    )
    with pytest.raises(SystemExit):
        catalog.check(registry.settings(extraction="no-licence"))


def test_ac3_a_licence_file_that_is_named_but_missing_or_empty_is_no_licence(registry, tmp_path):
    empty = ROOT / "licences" / "models" / "test-empty.txt"
    empty.write_text("   \n", encoding="utf-8")
    try:
        registry.write(
            _entry("gone", "Org/Gone", licence="licences/models/not-there.txt"),
            _entry("blank", "Org/Blank", licence="licences/models/test-empty.txt"),
        )
        for name in ("gone", "blank"):
            assert "no licence text saved" in str(
                catalog.refusal("extraction", registry.settings(extraction=name))
            )
    finally:
        empty.unlink()


def test_ac3_with_the_licence_text_saved_it_can_be_champion(registry):
    registry.write(_entry("ok", "Org/Ok"))
    assert catalog.refusal("extraction", registry.settings(extraction="ok")) is None


def test_ac3_a_prompt_changed_since_registration_must_be_registered_again(registry):
    registry.write(_entry("old-prompt", "Org/Model", prompt_version="000000000000"))
    found = str(catalog.refusal("extraction", registry.settings(extraction="old-prompt")))
    assert "the prompt has changed since old-prompt was registered" in found
    assert f"now {PROMPT_NOW}" in found


# ------------------------------------------------------------------ AC4: the pinned files, checked


def _snapshot(cache: Path, model_id: str, revision: str, files: dict[str, bytes]) -> Path:
    folder = catalog.snapshot(cache, model_id, revision)
    for name, body in files.items():
        (folder / name).parent.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(body)
    return folder


WEIGHTS = {
    "config.json": b'{"model_type": "made-up"}',
    "model.safetensors": b"\x00" * 4096,
    "tokenizer/vocab.json": b'{"a": 1}',
}


def test_ac4_the_hub_changing_under_another_revision_does_not_touch_the_pinned_files(tmp_path):
    pinned = _snapshot(tmp_path, "Org/Model", REV_A, WEIGHTS)
    entry = catalog.Entry(
        "m",
        ("extraction",),
        "Org/Model",
        REV_A,
        catalog.checksum(pinned),
        {},
        None,
        None,
        None,
        None,
        None,
        "candidate",
    )
    # the maker pushes new files: on the hub they are a NEW revision, beside the pinned one
    _snapshot(tmp_path, "Org/Model", REV_B, {**WEIGHTS, "model.safetensors": b"\x01" * 4096})
    loaded = catalog.snapshot(tmp_path, "Org/Model", entry.revision or "")
    assert loaded == pinned and catalog.verify(entry, loaded) == entry.checksum


def test_ac4_files_changed_under_the_same_revision_are_refused(tmp_path):
    folder = _snapshot(tmp_path, "Org/Model", REV_A, WEIGHTS)
    entry = catalog.Entry(
        "m",
        ("extraction",),
        "Org/Model",
        REV_A,
        catalog.checksum(folder),
        {},
        None,
        None,
        None,
        None,
        None,
        "candidate",
    )
    (folder / "model.safetensors").write_bytes(b"\x02" * 4096)
    with pytest.raises(catalog.ModelMismatch, match="the files do not match the registry"):
        catalog.verify(entry, folder)


def test_ac4_the_checksum_sees_a_changed_added_removed_or_renamed_file(tmp_path):
    base = catalog.checksum(_snapshot(tmp_path / "a", "O/M", REV_A, WEIGHTS))
    assert base == catalog.checksum(_snapshot(tmp_path / "same", "O/M", REV_A, WEIGHTS))
    for n, files in enumerate(
        (
            {**WEIGHTS, "config.json": b"{}"},
            {**WEIGHTS, "extra.py": b"print('remote code')"},
            {k: v for k, v in WEIGHTS.items() if k != "tokenizer/vocab.json"},
            {
                ("weights.safetensors" if k == "model.safetensors" else k): v
                for k, v in WEIGHTS.items()
            },
        )
    ):
        assert catalog.checksum(_snapshot(tmp_path / f"v{n}", "O/M", REV_A, files)) != base


def test_ac4_with_no_checksum_registered_yet_the_one_found_is_given_to_record(tmp_path):
    folder = _snapshot(tmp_path, "Org/Model", REV_A, WEIGHTS)
    entry = catalog.Entry(
        "m",
        ("extraction",),
        "Org/Model",
        REV_A,
        None,
        {},
        None,
        None,
        None,
        None,
        None,
        "candidate",
    )
    assert catalog.verify(entry, folder) == catalog.checksum(folder)


def test_ac4_models_are_fetched_at_the_exact_revision_safely():
    assert catalog.load_kwargs(REV_A) == {
        "revision": REV_A,
        "trust_remote_code": False,
        "use_safetensors": True,
    }
    for floating in (None, "", "main", "latest"):
        with pytest.raises(catalog.ModelMismatch, match="never 'latest'"):
            catalog.load_kwargs(floating)
    backends = (ROOT / "src/cdi_adapter/mlserve/backends.py").read_text(encoding="utf-8")
    assert '"trust_remote_code": False,' in backends and '"use_safetensors": True,' in backends
    assert "catalog.verify_loaded(model_id, rev, settings)" in backends, "checked once loaded"
    engines = (ROOT / "src/cdi_adapter/recognition/engines.py").read_text(encoding="utf-8")
    assert "load_trocr(model_id, pinned_revision(model_id))" in engines
    assert '"trust_remote_code": False' in engines


def test_ac4_trocr_is_pinned_to_the_revision_the_pod_holds():
    assert Settings.model_fields["trocr_model_revision"].default == (
        "eaacaf452b06415df8f10bb6fad3a4c11e609406"
    )


# ------------------------------------------------------------------ the real registry


def test_fr1_every_model_has_the_fields_the_story_asks_for():
    data = json.loads((ROOT / "models" / "registry.json").read_text(encoding="utf-8"))
    for m in data["models"]:
        for field in (
            "id",
            "roles",
            "model_id",
            "revision",
            "checksum",
            "licence",
            "trained_on",
            "served_by",
            "gpu_memory_gb",
            "prompt_version",
            "schema_version",
            "status",
        ):
            assert field in m, (m["id"], field)
        assert m["status"] in catalog.STATUSES and set(m["roles"]) <= set(catalog.ROLES)
        assert {"name", "text_file", "checked_on"} <= set(m["licence"])
    ids = {m["model_id"]: m for m in data["models"]}
    assert ids["Qwen/Qwen2.5-VL-7B-Instruct"]["revision"] == (
        "cc594898137f460bfe9f0759e9844b3ce807cfb5"
    )
    assert ids["Qwen/Qwen2.5-VL-7B-Instruct"]["prompt_version"] == PROMPT_NOW


def test_fr1_the_registry_says_what_is_missing_and_guesses_nothing():
    listed = {d["id"]: d for d in catalog.describe(Settings())}
    assert set(listed) == {
        "qwen2.5-vl-7b-instruct",
        "trocr-base-handwritten",
        "rapidocr-onnxruntime",
    }
    for d in listed.values():
        assert d["checksum"] is None and d["licence_text_saved"] is False
        assert d["status"] == "candidate" and d["champion_for"] == []


def test_fr4_the_models_are_shown_with_the_swap_points():
    from cdi_adapter.webapp import app as web

    body = web.components_view()
    assert {m["id"] for m in body["models"]} == {
        "qwen2.5-vl-7b-instruct",
        "trocr-base-handwritten",
        "rapidocr-onnxruntime",
    }


@pytest.mark.xfail(
    strict=True,
    reason="No model can be made champion yet: none of the three "
    "has a licence text saved (Qwen's repository has no LICENSE file - Alibaba is "
    "to confirm in writing; TrOCR waits for counsel on the IAM terms) and none has "
    "a checksum recorded (the files are on the pod). The service runs on the model "
    "settings, as before, until they are.",
)
def test_a_model_is_ready_to_be_champion():
    assert any(d["licence_text_saved"] and d["checksum"] for d in catalog.describe(Settings()))
