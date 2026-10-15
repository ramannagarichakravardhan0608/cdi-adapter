"""SW-S3 "Fine-tune any replacement model with the same data and one command".

* AC1  a new base model named in the training command: the smoke test finishes, or fails
       with the reason (format, memory or licence);
* AC2  the same data version and seed twice: the settings and results are saved and
       comparable;
* AC3  the run ends: the report shows seconds per step and GPU memory;
* AC4  a base model with no licence entry: training is refused.

No GPU here: the real adapters (Qwen2-VL, TrOCR) are checked for the format they write, and
the training loop runs with a stand-in adapter that does no learning. The data is drawn by
``finetune.synthetic`` from made-up words. The real smoke test on two base models is the last
test, marked as not done yet.
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

import pytest

from cdi_adapter.config import get_settings
from cdi_adapter.finetune import adapters, dataset, run, synthetic
from cdi_adapter.finetune.__main__ import main
from cdi_adapter.modelops import catalog

ROOT = Path(__file__).resolve().parents[1]
QWEN = "Qwen/Qwen2.5-VL-7B-Instruct"
TROCR = "microsoft/trocr-base-handwritten"
REV = "c" * 40


class Stand_in:  # noqa: N801 - reads as what it is
    """An adapter that does no learning: a loss from the seed, a fixed 'GPU' peak."""

    family = "stand-in"
    roles = ("extraction", "line_reader")

    def __init__(self, peak: int | None = 3 * 2**30, fail_at: str = "", error: Any = None):
        self.peak, self.fail_at, self.error = peak, fail_at, error
        self.calls: list[str] = []

    def handles(self, model_id: str) -> bool:
        return True

    def render(self, example: dataset.Example) -> dict[str, Any]:
        self.calls.append("render")
        return {"id": example.id}

    def prepare(self, cfg: Any) -> None:
        self.calls.append("prepare")
        self.prepared = cfg
        if self.fail_at == "prepare":
            raise self.error
        self.rng = random.Random(cfg.seed)

    def step(self, batch: list[dataset.Example]) -> float:
        self.calls.append("step")
        if self.fail_at == "step":
            raise self.error
        return 2.0 * self.rng.random()

    def peak_memory(self) -> int | None:
        return self.peak

    def save(self, folder: Path) -> None:
        (folder / "adapter_model.safetensors").write_bytes(b"stand-in")


def _clock(step: float = 0.25):
    t = [0.0]

    def tick() -> float:
        t[0] += step
        return t[0]

    return tick


@pytest.fixture
def registry(tmp_path, monkeypatch):
    """A registry with two licensed models and one without a licence text; settings point at
    it. The licence text is a test copy, removed afterwards."""
    licence = ROOT / "licences" / "models" / "test-sw-s3-apache.txt"
    licence.parent.mkdir(parents=True, exist_ok=True)
    licence.write_text("Apache License, Version 2.0 (test copy)\n", encoding="utf-8")
    text = "licences/models/test-sw-s3-apache.txt"

    def entry(id_: str, model_id: str, text_file: str | None, roles: list[str]) -> dict[str, Any]:
        return {
            "id": id_,
            "roles": roles,
            "model_id": model_id,
            "revision": REV,
            "status": "candidate",
            "licence": {"name": "Apache-2.0", "text_file": text_file},
        }

    path = tmp_path / "registry.json"
    models = [
        entry("qwen", QWEN, text, ["extraction"]),
        entry("trocr", TROCR, text, ["handwriting"]),
        entry("brand-new", "Org/Brand-New-Model", text, ["extraction"]),
        entry("unlicensed", "Org/No-Licence-Text", None, ["extraction"]),
    ]
    path.write_text(json.dumps({"models": models}), encoding="utf-8")
    monkeypatch.setenv("CDI_MODEL_REGISTRY_PATH", str(path))
    get_settings.cache_clear()
    catalog.forget()
    yield path
    get_settings.cache_clear()
    catalog.forget()
    licence.unlink()
    if not any(licence.parent.iterdir()):
        licence.parent.rmdir()


@pytest.fixture(scope="module")
def data(tmp_path_factory) -> Path:
    return synthetic.make(tmp_path_factory.mktemp("sw3") / "synthetic-smoke", pages=60, lines=60)


def _cfg(data: Path, base: str = QWEN, role: str = "extraction", **kw: Any) -> run.RunConfig:
    return run.RunConfig(base=base, role=role, data=str(data), **kw)


# ------------------------------------------------------------------ AC1: finishes, or says why


def test_ac1_the_smoke_test_is_50_examples_and_20_steps_and_finishes(registry, data, tmp_path):
    cfg = _cfg(data).as_smoke()
    assert (cfg.smoke, cfg.examples, cfg.steps) == (True, 50, 20)
    report = run.run(cfg, tmp_path, adapter=Stand_in(), clock=_clock())
    assert report["outcome"] == "finished", report["detail"]
    assert (report["reason"], report["steps_done"], report["examples"]) == (None, 20, 50)
    assert report["revision"] == REV  # the registry's pin, since none was named
    assert report["kind"] == "smoke test"
    assert not list(tmp_path.glob("*/adapter"))  # a smoke test keeps no model files


def test_ac1_every_example_is_written_in_the_model_format_before_the_model_is_loaded(
    registry, data, tmp_path
):
    stand_in = Stand_in()
    run.run(_cfg(data).as_smoke(), tmp_path, adapter=stand_in, clock=_clock())
    assert stand_in.calls[:50] == ["render"] * 50
    assert stand_in.calls[50] == "prepare"


def test_ac1_a_base_model_of_a_family_no_adapter_knows_fails_with_the_reason_format(
    registry, data, tmp_path
):
    report = run.run(_cfg(data, base="Org/Brand-New-Model").as_smoke(), tmp_path)
    assert (report["outcome"], report["reason"]) == ("failed", "format")
    assert "no adapter knows the model family of 'Org/Brand-New-Model'" in report["detail"]
    assert "known: qwen2-vl, trocr" in report["detail"]


def test_ac1_a_model_asked_for_a_role_it_cannot_do_fails_with_the_reason_format(
    registry, data, tmp_path
):
    report = run.run(_cfg(data, base=TROCR, role="extraction").as_smoke(), tmp_path)
    assert report["reason"] == "format"
    assert "the trocr adapter trains line_reader, not extraction" in report["detail"]


def test_ac1_an_answer_that_does_not_fit_the_schema_fails_with_the_reason_format(
    registry, data, tmp_path
):
    bad = tmp_path / "bad"
    synthetic.make(bad, pages=3, lines=0)
    rows = (bad / "examples.jsonl").read_text("utf-8").splitlines()
    first = json.loads(rows[0])
    first["target"] = {"medications": [{"drug_name": "Paracetamol"}], "confidence": 1.0}
    (bad / "examples.jsonl").write_text(
        "\n".join([json.dumps(first), *rows[1:]]) + "\n", encoding="utf-8"
    )
    report = run.run(_cfg(bad).as_smoke(), tmp_path / "runs", adapter=Stand_in())
    assert report["reason"] == "format"
    assert "line 1: the answer does not fit prescription.v3" in report["detail"]
    assert "'confidence' is a required property" in report["detail"]


@pytest.mark.parametrize(
    "error",
    [
        type("OutOfMemoryError", (RuntimeError,), {})("CUDA out of memory. Tried to allocate"),
        RuntimeError("CUDA error: out of memory"),
    ],
)
@pytest.mark.parametrize("where", ["prepare", "step"])
def test_ac1_running_out_of_gpu_memory_fails_with_the_reason_memory(
    registry, data, tmp_path, error, where
):
    report = run.run(_cfg(data).as_smoke(), tmp_path, adapter=Stand_in(fail_at=where, error=error))
    assert (report["outcome"], report["reason"]) == ("failed", "memory")
    assert "out of memory" in report["detail"]
    assert report["gpu_memory"]["peak_gib"] == 3.0  # what it reached before it failed


def test_ac1_any_other_failure_is_reported_not_raised(registry, data, tmp_path):
    report = run.run(
        _cfg(data).as_smoke(),
        tmp_path,
        adapter=Stand_in(fail_at="step", error=ValueError("loss is nan")),
    )
    assert (report["reason"], report["detail"]) == ("other", "ValueError: loss is nan")


def test_ac1_pictures_of_identified_patients_or_data_without_rights_are_refused(registry, tmp_path):
    for change, said in [
        ({"data_class": "identified"}, "pictures of identified patients are never trained on"),
        ({"rights": {"may_train": False, "source": "a hospital"}}, "does not record a right"),
        ({"rights": {"may_train": True}}, "does not record a right"),
    ]:
        folder = synthetic.make(tmp_path / f"d{len(said)}{len(change)}", pages=2, lines=2)
        manifest = json.loads((folder / "manifest.json").read_text("utf-8"))
        (folder / "manifest.json").write_text(json.dumps({**manifest, **change}), "utf-8")
        report = run.run(_cfg(folder).as_smoke(), tmp_path / "runs", adapter=Stand_in())
        assert report["reason"] == "data"
        assert said in report["detail"]


def test_ac1_the_qwen_adapter_writes_the_production_prompt_and_the_answer_as_a_chat(data):
    ds = dataset.load(data)
    page = next(e for e in ds.examples if e.role == "extraction")
    line = next(e for e in ds.examples if e.role == "line_reader")
    qwen = adapters.for_model(QWEN, "extraction")
    assert qwen.family == "qwen2-vl"
    user, answer = qwen.render(page)["messages"]
    assert user["content"][0] == {"type": "image", "image": str(page.image)}
    from cdi_adapter.extract.prompt import build_extraction_prompt

    assert user["content"][1]["text"] == build_extraction_prompt("prescription", [])[0]
    assert json.loads(answer["content"][0]["text"]) == page.target
    assert qwen.render(line)["messages"][1]["content"][0]["text"] == line.target
    trocr = adapters.for_model(TROCR, "line_reader")
    assert trocr.render(line) == {"image": str(line.image), "text": line.target}
    with pytest.raises(adapters.FormatError):
        trocr.render(page)


def test_ac1_a_new_family_is_one_adapter_and_one_register(registry, data, tmp_path):
    class NewFamily(Stand_in):
        family = "brand-new"

        def handles(self, model_id: str) -> bool:
            return model_id.startswith("Org/Brand-New")

    adapters.register(NewFamily)
    try:
        report = run.run(_cfg(data, base="Org/Brand-New-Model").as_smoke(), tmp_path)
    finally:
        adapters.unregister(NewFamily)
    assert (report["outcome"], report["adapter"]) == ("finished", "brand-new")


def test_ac1_the_command_runs_the_smoke_test_and_stops_when_it_fails(
    registry, data, tmp_path, capsys
):
    out = tmp_path / "runs"
    code = main(
        [
            "train",
            "--base",
            "Org/Brand-New-Model",
            "--role",
            "extraction",
            "--data",
            str(data),
            "--out",
            str(out),
            "--steps",
            "500",
        ]
    )
    assert code == 1
    printed = capsys.readouterr().out
    assert "# Smoke test: Org/Brand-New-Model (extraction)" in printed
    assert "reason **format**" in printed
    assert [p.name.split("-")[1] for p in out.iterdir()] == ["smoke"]  # no training run after


# ------------------------------------------------------------------ AC2: repeatable, comparable


def test_ac2_the_same_data_and_seed_twice_give_comparable_saved_runs(registry, data, tmp_path):
    first = run.run(_cfg(data, steps=30, seed=11), tmp_path, adapter=Stand_in(), clock=_clock())
    second = run.run(_cfg(data, steps=30, seed=11), tmp_path, adapter=Stand_in(), clock=_clock())
    assert first["run_id"] != second["run_id"]  # the second never writes over the first
    for r in (first, second):
        folder = tmp_path / r["run_id"]
        assert json.loads((folder / "settings.json").read_text("utf-8")) == r["settings"]
        assert json.loads((folder / "report.json").read_text("utf-8")) == r
        assert (folder / "adapter" / "adapter_model.safetensors").is_file()
    assert first["data_version"] == second["data_version"] == dataset.load(data).version
    assert first["environment"]["code"] == second["environment"]["code"]
    found = run.compare(first, second)
    assert found["comparable"] is True and found["differences"] == []
    assert found["same_losses"] is True and len(first["losses"]) == 30


def test_ac2_a_different_seed_or_setting_is_named_as_the_difference(registry, data, tmp_path):
    a = run.run(_cfg(data, steps=5, seed=1), tmp_path, adapter=Stand_in())
    b = run.run(_cfg(data, steps=5, seed=2, rank=8), tmp_path, adapter=Stand_in())
    found = run.compare(a, b)
    assert found["comparable"] is False
    assert "seed: 1 / 2" in found["differences"]
    assert "settings.rank: 16 / 8" in found["differences"]


def test_ac2_the_data_version_changes_when_one_picture_changes(tmp_path):
    a = synthetic.make(tmp_path / "one" / "set", pages=3, lines=3)
    b = synthetic.make(tmp_path / "two" / "set", pages=3, lines=3)  # a byte-for-byte copy
    assert dataset.load(a).version == dataset.load(b).version  # the same data, the same version
    (b / "images" / "line-001.png").write_bytes(
        (b / "images" / "line-001.png").read_bytes() + b"\0"
    )
    assert dataset.load(a).version != dataset.load(b).version


def test_ac2_the_examples_chosen_depend_only_on_the_data_and_the_seed(data):
    ds = dataset.load(data)
    pick = [e.id for e in dataset.select(ds, "extraction", 50, 7)]
    assert pick == [e.id for e in dataset.select(dataset.load(data), "extraction", 50, 7)]
    assert pick != [e.id for e in dataset.select(ds, "extraction", 50, 8)]


def test_ac2_the_compare_command_says_whether_two_runs_are_comparable(registry, data, tmp_path):
    a = run.run(_cfg(data, steps=3), tmp_path, adapter=Stand_in())
    b = run.run(_cfg(data, steps=3), tmp_path, adapter=Stand_in())
    c = run.run(_cfg(data, steps=3, seed=99), tmp_path, adapter=Stand_in())
    path = lambda r: str(tmp_path / r["run_id"] / "report.json")  # noqa: E731
    assert main(["compare", path(a), path(b)]) == 0
    assert main(["compare", path(a), path(c)]) == 1


# ------------------------------------------------------------------ AC3: seconds/step, GPU memory


def test_ac3_the_report_shows_seconds_per_step_and_gpu_memory(registry, data, tmp_path):
    report = run.run(_cfg(data).as_smoke(), tmp_path, adapter=Stand_in(), clock=_clock(0.5))
    sps = report["seconds_per_step"]
    assert (sps["mean"], sps["median"], sps["max"], sps["steps"]) == (0.5, 0.5, 0.5, 20)
    assert report["gpu_memory"] == {"peak_bytes": 3 * 2**30, "peak_gib": 3.0}
    written = (tmp_path / report["run_id"] / "report.md").read_text("utf-8")
    assert "- **Seconds per step:** 0.5 on average, 0.5 median, 0.5 at most" in written
    assert "- **GPU memory:** 3.0 GiB at peak" in written
    assert report["prompt_version"] and report["answer_schema"] == "prescription.v3"


def test_ac3_without_a_gpu_the_report_says_memory_was_not_measured(registry, data, tmp_path):
    report = run.run(_cfg(data).as_smoke(), tmp_path, adapter=Stand_in(peak=None))
    assert report["gpu_memory"]["note"] == "not measured: no GPU"
    assert "(not measured: no GPU)" in run.as_markdown(report)


def test_ac3_the_first_step_is_shown_apart_from_the_average(registry, data, tmp_path):
    times = iter([0.0, 9.0] + [x for i in range(19) for x in (10.0 + i, 10.5 + i)])
    report = run.run(_cfg(data).as_smoke(), tmp_path, adapter=Stand_in(), clock=lambda: next(times))
    assert report["seconds_per_step"]["first_step"] == 9.0
    assert report["seconds_per_step"]["mean"] == 0.5


# ------------------------------------------------------------------ AC4: no licence, no training


def test_ac4_a_base_model_with_no_licence_entry_is_refused_before_loading(registry, data, tmp_path):
    stand_in = Stand_in()
    report = run.run(_cfg(data, base="Org/Never-Registered").as_smoke(), tmp_path, adapter=stand_in)
    assert (report["outcome"], report["reason"]) == ("failed", "licence")
    assert "Org/Never-Registered has no licence entry" in report["detail"]
    assert stand_in.calls == []  # nothing rendered, nothing loaded


def test_ac4_a_registered_model_without_its_licence_text_saved_is_refused(registry, data, tmp_path):
    stand_in = Stand_in()
    report = run.run(_cfg(data, base="Org/No-Licence-Text"), tmp_path, adapter=stand_in)
    assert report["reason"] == "licence"
    assert "has no licence text saved" in report["detail"]
    assert stand_in.calls == []


def test_ac4_the_command_refuses_with_exit_code_1(registry, data, tmp_path, capsys):
    code = main(
        [
            "smoke",
            "--base",
            "Org/Never-Registered",
            "--role",
            "line_reader",
            "--data",
            str(data),
            "--out",
            str(tmp_path),
        ]
    )
    assert code == 1
    assert "reason **licence**" in capsys.readouterr().out


def test_ac4_today_every_model_in_the_real_registry_is_refused_for_want_of_its_licence_text():
    """The real registry: no licence text is saved for any model yet (SW-S2), so none may be
    trained. This is the rule working, and the reason the real smoke test below waits."""
    get_settings.cache_clear()
    catalog.forget()
    for e in catalog.entries():
        with pytest.raises(run.Refused) as refused:
            run.licence_check(e.model_id)
        assert refused.value.reason == "licence"


# ------------------------------------------------------------------ PROVE IT: not done yet


@pytest.mark.xfail(
    strict=True,
    reason="not run yet: needs a GPU host, peft installed, the licence texts saved in "
    "models/registry.json, and the smoke test run on two base models there with the reports "
    "kept in docs/evidence/SW-S3/",
)
def test_proof_the_smoke_test_has_run_on_two_real_base_models():
    reports = [
        json.loads(p.read_text("utf-8"))
        for p in sorted((ROOT / "docs" / "evidence" / "SW-S3").glob("*/report.json"))
    ]
    finished = {
        r["base"] for r in reports if r["kind"] == "smoke test" and r["outcome"] == "finished"
    }
    measured = [r for r in reports if (r.get("gpu_memory") or {}).get("peak_gib")]
    assert len(finished) >= 2 and measured
