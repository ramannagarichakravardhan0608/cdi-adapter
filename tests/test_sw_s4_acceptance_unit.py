"""SW-S4 "A fair contest: a new model against the current champion".

* AC1  a candidate and the champion run: the report shows both with the same slices, metrics,
       speed and cost;
* AC2  the candidate better overall but one slice drops more than the margin: 'keep champion',
       naming the slice;
* AC3  the sample too small to prove the precision claim: 'need more data';
* AC4  a candidate with no licence entry: the contest refuses to start.

The answer key is the made-up ``evaluation/synthetic-10`` set (ten made-up prescriptions, no
picture, no patient). The two "models" are made-up result sets: the champion gets two fields
wrong on every prescription; each candidate is made to win or lose where a test needs it. The
registry is written into a temporary folder. Nothing here measures a real model.
"""

from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from cdi_adapter.config import get_settings
from cdi_adapter.modelops import catalog, contest

ROOT = Path(__file__).resolve().parents[1]
SYNTHETIC = ROOT / "evaluation" / "synthetic-10"
HANDWRITTEN = ("syn-01", "syn-04", "syn-07", "syn-10")
REV = {"champ": "a" * 40, "cand": "b" * 40}


def V(value: Any) -> dict[str, Any]:
    return {"value": value, "status": "accepted", "confidence": 0.97}


def perfect(key: dict[str, Any]) -> dict[str, Any]:
    """What a model that read everything right, and accepted it, would produce."""
    return {
        "patient": {k: V(v) for k, v in key["patient"].items() if v is not None},
        "doctor": {k: V(v) for k, v in key["doctor"].items() if v is not None},
        "lab_tests": [
            {
                "as_written": t["as_written"],
                "match": "matched",
                "standard": {"code": t.get("standard_code")},
                "value": V(t["as_written"]),
                "context": [{"text": r} for r in t.get("reasons") or []],
            }
            for t in key["tests"]
        ],
        "preparation": [
            {
                **V(p["text"]),
                "text": p["text"],
                "type": p["type"],
                "amount": p["amount"],
                "unit": p["unit"],
                "applies_to": p["applies_to"],
            }
            for p in key["preparation"]
        ],
    }


def wrong(key: dict[str, Any], n: int) -> dict[str, Any]:
    """A perfect reading with ``n`` patient/doctor values read wrong (and accepted)."""
    out = perfect(key)
    fields = [(g, f) for g in ("patient", "doctor") for f in out[g]]
    for g, f in fields[:n]:
        out[g][f] = V(f"misread {out[g][f]['value']}")
    return out


@pytest.fixture
def key_set(tmp_path) -> Path:
    """The made-up answer key, without the made-up results that sit beside it."""
    folder = tmp_path / "synthetic-10"
    shutil.copytree(SYNTHETIC, folder, ignore=shutil.ignore_patterns("result.json", "*.jsonl"))
    return folder


@pytest.fixture
def answers(key_set) -> dict[str, dict[str, Any]]:
    return {
        p.parent.name: json.loads(p.read_text("utf-8"))
        for p in sorted(key_set.glob("*/answer.json"))
    }


@pytest.fixture
def registry(tmp_path, monkeypatch):
    licence = ROOT / "licences" / "models" / "test-sw-s4-apache.txt"
    licence.parent.mkdir(parents=True, exist_ok=True)
    licence.write_text("Apache License, Version 2.0 (test copy)\n", encoding="utf-8")
    text = "licences/models/test-sw-s4-apache.txt"

    def entry(
        id_: str,
        model_id: str,
        rev: str,
        text_file: str | None = text,
        roles: tuple[str, ...] = ("extraction",),
    ) -> dict[str, Any]:
        return {
            "id": id_,
            "roles": list(roles),
            "model_id": model_id,
            "revision": rev,
            "status": "candidate",
            "licence": {"name": "Apache-2.0", "text_file": text_file},
        }

    path = tmp_path / "registry.json"
    path.write_text(
        json.dumps(
            {
                "models": [
                    entry("champ", "Org/Champion-7B", REV["champ"]),
                    entry("cand", "Org/Candidate-7B", REV["cand"]),
                    entry("cand-unlicensed", "Org/No-Licence-Text", "c" * 40, text_file=None),
                    entry("reader", "Org/Line-Reader", "d" * 40, roles=("handwriting",)),
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CDI_MODEL_REGISTRY_PATH", str(path))
    monkeypatch.setenv("CDI_CHAMPION_EXTRACTION", "champ")
    get_settings.cache_clear()
    catalog.forget()
    yield path
    get_settings.cache_clear()
    catalog.forget()
    licence.unlink()
    if not any(licence.parent.iterdir()):
        licence.parent.rmdir()


def rules(tmp_path: Path, **over: Any) -> Path:
    body: dict[str, Any] = {
        "schema_version": "contest-rules.v1",
        "agreed": {
            "owner": "made-up owner",
            "clinician": "made-up clinician",
            "date": "2026-10-14",
        },
        "margin": 0.01,
        "slice_metrics": [
            {"group": "all", "metric": "field_accuracy"},
            {"group": "patient", "metric": "precision_accepted"},
        ],
        "precision": {"groups": ["patient", "doctor"], "slice": "overall", "target": None},
        "speed": {"max_seconds_per_document": None, "max_ratio": 1.5},
        "cost": {
            "per_hour": 90.0,
            "currency": "INR",
            "max_per_prescription": None,
            "max_ratio": 1.5,
        },
        "gpu_memory_gib_max": 40,
    }
    for k, v in over.items():
        body[k] = v
    path = tmp_path / f"rules-{len(list(tmp_path.glob('rules-*')))}.json"
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


def write_run(
    folder: Path,
    model: str,
    rev: str,
    results: dict[str, dict[str, Any]],
    seconds: float = 20.0,
    gpu: float | None = 18.0,
    machine: str = "1x L4",
    set_versions: dict[str, str] | None = None,
) -> Path:
    folder.mkdir(parents=True)
    for case, r in results.items():
        made = {"stage": "extract", "role": "vision-language model", "name": model, "version": rev}
        (folder / f"{case}.json").write_text(json.dumps({**r, "engines": [made]}), "utf-8")
    meta = {
        "machine": machine,
        "settings": {"ocr": "rapidocr", "two_readers": False},
        "seconds": {c: seconds for c in results},
        "gpu_memory_gib": gpu,
        "set_versions": set_versions or {},
    }
    (folder / "run.json").write_text(json.dumps(meta), "utf-8")
    return folder


class Runs:
    """Gives the contest the two runs written into folders; remembers whether it was asked."""

    def __init__(self, champion: Path, candidate: Path) -> None:
        self.folders = {"champion": champion, "candidate": candidate}
        self.asked = False

    def __call__(self, entries: dict[str, Any], opened: dict[str, Any]) -> dict[str, Any]:
        self.asked = True
        cases = [c for o in opened["sets"] for c in o["answers"]]
        return {side: contest.read_run(f, cases) for side, f in self.folders.items()}


def setup(
    tmp_path,
    answers,
    candidate: dict[str, dict[str, Any]],
    *,
    champion=None,
    cand_kw: dict[str, Any] | None = None,
    champ_kw: dict[str, Any] | None = None,
) -> Runs:
    champion = champion or {c: wrong(a, 2) for c, a in answers.items()}
    return Runs(
        write_run(
            tmp_path / "run-champ", "Org/Champion-7B", REV["champ"], champion, **(champ_kw or {})
        ),
        write_run(
            tmp_path / "run-cand", "Org/Candidate-7B", REV["cand"], candidate, **(cand_kw or {})
        ),
    )


def saved(tmp_path: Path, report: dict[str, Any]) -> Path:
    return tmp_path / "contests" / report["saved_in"]


def go(tmp_path, key_set, runs, rules_path, candidate="cand", **kw: Any) -> dict[str, Any]:
    return contest.contest(
        candidate,
        kw.pop("champion", None),
        "extraction",
        [key_set],
        rules_path,
        runs,
        out=tmp_path / "contests",
        **kw,
    )


# ------------------------------------------------------------------ AC1: the same report for both


def test_ac1_both_models_appear_with_the_same_slices_and_metrics(
    registry, key_set, answers, tmp_path
):
    runs = setup(tmp_path, answers, {c: perfect(a) for c, a in answers.items()})
    report = go(tmp_path, key_set, runs, rules(tmp_path))
    names = [s["name"] for s in report["slices"]]
    assert names[0] == "overall"
    assert {
        "script=handwritten",
        "script=printed",
        "doctor=known",
        "doctor=unknown",
        "suite=regression",
        "suite=capability",
        "suite=adversarial",
    } <= set(names)
    expected = [(g, m) for g in contest.GROUPS for m in contest.GROUP_METRICS] + list(
        contest.SLICE_METRICS
    )
    for sl in report["slices"]:
        assert [(r["group"], r["metric"]) for r in sl["rows"]] == expected
        assert all(set(r) >= {"champion", "candidate", "delta", "outcome"} for r in sl["rows"])
    overall = {(r["group"], r["metric"]): r for r in report["slices"][0]["rows"]}
    assert overall[("all", "field_accuracy")]["candidate"] == 1.0
    assert overall[("all", "field_accuracy")]["champion"] < 1.0
    assert report["models"]["champion"]["model_id"] == "Org/Champion-7B"
    assert report["models"]["candidate"]["revision"] == REV["cand"]


def test_ac1_both_appear_with_speed_gpu_memory_and_rent_per_prescription(
    registry, key_set, answers, tmp_path
):
    runs = setup(
        tmp_path,
        answers,
        {c: perfect(a) for c, a in answers.items()},
        cand_kw={"seconds": 24.0, "gpu": 21.5},
    )
    report = go(tmp_path, key_set, runs, rules(tmp_path))
    ch, ca = report["speed"]["champion"], report["speed"]["candidate"]
    assert (ch["seconds_per_document"], ca["seconds_per_document"]) == (20.0, 24.0)
    assert (ch["gpu_memory_gib"], ca["gpu_memory_gib"]) == (18.0, 21.5)
    assert ch["rent_per_prescription"] == 0.5  # 20 s at 90 an hour
    assert ca["rent_per_prescription"] == 0.6
    assert ch["documents_timed"] == ca["documents_timed"] == 10
    md = (saved(tmp_path, report) / "report.md").read_text("utf-8")
    assert "| rent_per_prescription | 0.5000 | 0.6000 |" in md
    assert "| seconds_per_document | 20.0000 | 24.0000 |" in md


def test_ac1_wins_ties_and_losses_are_counted_per_slice(registry, key_set, answers, tmp_path):
    runs = setup(tmp_path, answers, {c: perfect(a) for c, a in answers.items()})
    report = go(tmp_path, key_set, runs, rules(tmp_path))
    assert report["slices"][0]["tally"] == {"win": 2, "tie": 0, "loss": 0, "unmeasured": 0}
    md = (saved(tmp_path, report) / "report.md").read_text("utf-8")
    assert "### overall (10 cases) - 2 won, 0 tied, 0 lost" in md


def test_ac1_every_contest_is_saved_beside_the_registry_with_a_line_in_the_index(
    registry, key_set, answers, tmp_path
):
    runs = setup(tmp_path, answers, {c: perfect(a) for c, a in answers.items()})
    first = go(tmp_path, key_set, runs, rules(tmp_path))
    second = go(tmp_path, key_set, runs, rules(tmp_path))
    assert first["saved_in"] != second["saved_in"]
    index = [
        json.loads(x) for x in (tmp_path / "contests" / "index.jsonl").read_text().splitlines()
    ]
    assert [i["decision"] for i in index] == ["promote", "promote"]
    assert index[0]["candidate"] == "cand" and index[0]["champion"] == "champ"
    assert index[0]["set_version"] == contest.set_version(key_set)
    kept = json.loads((saved(tmp_path, first) / "report.json").read_text("utf-8"))
    assert kept["verdict"] == first["verdict"]


def test_ac1_the_guard_suite_is_scored_in_the_same_report(registry, key_set, answers, tmp_path):
    guard = tmp_path / "guard-set"  # the same cases under other names, copied (not renamed:
    guard.mkdir()  # Windows can refuse to rename a folder it has just copied)
    shutil.copy(key_set / "manifest.json", guard / "manifest.json")
    for case in answers:
        shutil.copytree(key_set / case, guard / f"g-{case}")
        (guard / f"g-{case}" / "answer.json").write_text(
            json.dumps({**answers[case], "case_id": f"g-{case}"}), "utf-8"
        )
    both = {**answers, **{f"g-{c}": a for c, a in answers.items()}}
    runs = setup(
        tmp_path,
        both,
        {c: perfect(a) for c, a in both.items()},
        champion={c: wrong(a, 2) for c, a in both.items()},
    )
    report = contest.contest(
        "cand",
        None,
        "extraction",
        [key_set, guard],
        rules(tmp_path),
        runs,
        out=tmp_path / "contests",
    )
    names = [s["name"] for s in report["slices"]]
    assert "guard guard-set: overall" in names and "guard guard-set: script=printed" in names
    assert report["guard"][0]["name"] == "guard-set"


# ------------------------------------------------------------------ AC2: a slice drop


def test_ac2_better_overall_but_one_slice_drops_beyond_the_margin_keeps_the_champion(
    registry, key_set, answers, tmp_path
):
    # printed: the candidate is perfect; handwritten: it gets four fields wrong, not two
    candidate = {c: (wrong(a, 4) if c in HANDWRITTEN else perfect(a)) for c, a in answers.items()}
    runs = setup(tmp_path, answers, candidate)
    report = go(tmp_path, key_set, runs, rules(tmp_path))
    overall = {(r["group"], r["metric"]): r for r in report["slices"][0]["rows"]}
    assert overall[("all", "field_accuracy")]["outcome"] == "win"  # better overall
    v = report["verdict"]
    assert v["decision"] == "keep champion"
    assert v["better_overall"] is True
    named = " | ".join(v["keep_champion_because"])
    assert "slice script=handwritten: all.field_accuracy dropped from" in named
    assert "more than the margin 0.01" in named
    assert "script=printed" not in named
    md = (saved(tmp_path, report) / "report.md").read_text("utf-8")
    assert "**Verdict: KEEP CHAMPION**" in md
    assert "- keep champion: slice script=handwritten:" in md


def test_ac2_a_drop_within_the_margin_does_not_keep_the_champion(
    registry, key_set, answers, tmp_path
):
    candidate = {c: (wrong(a, 4) if c in HANDWRITTEN else perfect(a)) for c, a in answers.items()}
    runs = setup(tmp_path, answers, candidate)
    claim = {"groups": ["patient"], "slice": "overall", "target": None}
    report = go(tmp_path, key_set, runs, rules(tmp_path, margin=0.5, precision=claim))
    assert report["verdict"]["decision"] == "promote", report["verdict"]


def test_ac2_speed_cost_or_memory_over_its_limit_keeps_the_champion(
    registry, key_set, answers, tmp_path
):
    runs = setup(
        tmp_path,
        answers,
        {c: perfect(a) for c, a in answers.items()},
        cand_kw={"seconds": 45.0, "gpu": 44.0},
    )
    v = go(tmp_path, key_set, runs, rules(tmp_path))["verdict"]
    assert v["decision"] == "keep champion"
    said = " | ".join(v["keep_champion_because"])
    assert "seconds per document: 45.0 is more than 1.5 x the champion's 20.0" in said
    assert "rent per prescription: 1.125 is more than 1.5 x the champion's 0.5" in said
    assert "GPU memory: 44.0 GiB is over the limit 40 GiB" in said


def test_ac2_a_candidate_that_is_not_better_keeps_the_champion(
    registry, key_set, answers, tmp_path
):
    same = {c: wrong(a, 2) for c, a in answers.items()}
    v = go(tmp_path, key_set, setup(tmp_path, answers, same), rules(tmp_path))["verdict"]
    assert v["decision"] == "keep champion"
    assert v["keep_champion_because"][0].startswith("the candidate is not better overall")


def test_ac2_a_candidate_better_everywhere_within_every_limit_is_promoted(
    registry, key_set, answers, tmp_path
):
    runs = setup(tmp_path, answers, {c: perfect(a) for c, a in answers.items()})
    v = go(tmp_path, key_set, runs, rules(tmp_path))["verdict"]
    assert (v["decision"], v["keep_champion_because"], v["need_more_data_because"]) == (
        "promote",
        [],
        [],
    )
    assert v["binding"] is False  # the made-up key is not sealed: a rehearsal
    assert v["notes"] == ["the answer key is not sealed: a rehearsal, not a decision"]


# ------------------------------------------------------------------ AC3: too small to prove


def test_ac3_a_sample_too_small_to_prove_the_precision_target_needs_more_data(
    registry, key_set, answers, tmp_path
):
    runs = setup(tmp_path, answers, {c: perfect(a) for c, a in answers.items()})
    claim = {"groups": ["patient", "doctor"], "slice": "overall", "target": 0.99}
    report = go(tmp_path, key_set, runs, rules(tmp_path, precision=claim))
    v = report["verdict"]
    assert v["decision"] == "need more data"
    assert v["keep_champion_because"] == []
    said = " | ".join(v["need_more_data_because"])
    assert "precision of accepted patient values [overall] is not proven" in said
    assert "does not prove 0.99 - at least 299 accepted values are needed" in said
    patient = report["precision"][0]
    assert patient["candidate"]["observed"] == 1.0 and patient["candidate"]["lower_95"] < 0.99


def test_ac3_a_lower_bound_below_the_champions_on_fewer_values_needs_more_data(
    registry, key_set, answers, tmp_path
):
    """The candidate is right on all it accepts, but accepts too few values for its lower bound
    to reach the champion's: the numbers do not yet show it is as precise."""
    champion = {c: perfect(a) for c, a in answers.items()}
    candidate = {}
    for c, a in answers.items():
        r = perfect(a)
        held = list(r["patient"])[: 3 if c != "syn-01" else 0]
        for f in held:
            r["patient"][f]["status"] = "needs_check"
        candidate[c] = r
    runs = setup(tmp_path, answers, candidate, champion=champion)
    rule = rules(
        tmp_path,
        precision={"groups": ["patient"], "slice": "overall", "target": None},
        slice_metrics=[{"group": "patient", "metric": "field_accuracy"}],
    )
    found = go(tmp_path, key_set, runs, rule)
    row = found["precision"][0]
    assert row["finding"] == "too small"
    assert row["candidate"]["n"] < row["champion"]["n"]
    assert found["verdict"]["decision"] in ("need more data", "keep champion")
    assert any("is not proven" in w for w in found["verdict"]["need_more_data_because"])


def test_ac3_a_limited_number_that_was_not_measured_needs_more_data(
    registry, key_set, answers, tmp_path
):
    runs = setup(
        tmp_path, answers, {c: perfect(a) for c, a in answers.items()}, cand_kw={"gpu": None}
    )
    v = go(tmp_path, key_set, runs, rules(tmp_path))["verdict"]
    assert v["decision"] == "need more data"
    assert v["need_more_data_because"] == ["GPU memory not measured, and it has a limit"]


def test_ac3_a_slice_with_fewer_cases_than_the_rules_ask_needs_more_data(
    registry, key_set, answers, tmp_path
):
    runs = setup(tmp_path, answers, {c: perfect(a) for c, a in answers.items()})
    v = go(tmp_path, key_set, runs, rules(tmp_path, min_cases_per_slice=3))["verdict"]
    assert v["decision"] == "need more data"
    assert (
        "slice suite=adversarial has 1 case(s); the rules ask for 3" in v["need_more_data_because"]
    )


def test_the_proof_calculator_numbers_are_the_accuracy_plans():
    from cdi_adapter.accuracy import stats

    assert stats.needed_with_no_errors(0.99) == 299
    assert round(stats.wilson_lower(300, 300) or 0, 5) == 0.99106


# ------------------------------------------------------------------ AC4: no licence, no contest


def test_ac4_a_candidate_with_no_licence_entry_is_refused_before_anything_runs(
    registry, key_set, answers, tmp_path
):
    (key_set / "manifest.json").write_text(json.dumps({"name": "k", "sealed": True}), "utf-8")
    runs = setup(tmp_path, answers, {c: perfect(a) for c, a in answers.items()})
    with pytest.raises(contest.ContestRefused) as refused:
        go(tmp_path, key_set, runs, rules(tmp_path), candidate="Org/Never-Registered")
    assert refused.value.reason == "licence"
    assert refused.value.detail == (
        "the candidate Org/Never-Registered has no licence entry: it is not in the model "
        "registry; the contest does not start"
    )
    assert runs.asked is False  # no model was run
    assert not (key_set / "openings.jsonl").exists()  # the sealed key was not opened
    assert not (tmp_path / "contests").exists()


def test_ac4_a_candidate_without_its_licence_text_or_for_another_role_is_refused(
    registry, key_set, answers, tmp_path
):
    runs = setup(tmp_path, answers, {c: perfect(a) for c, a in answers.items()})
    for name, said in [
        ("cand-unlicensed", "has no licence text saved in the model registry"),
        ("reader", "is registered for handwriting, not for extraction"),
    ]:
        with pytest.raises(contest.ContestRefused) as refused:
            go(tmp_path, key_set, runs, rules(tmp_path), candidate=name)
        assert refused.value.reason == "licence" and said in refused.value.detail
    assert runs.asked is False


def test_ac4_the_command_refuses_with_exit_code_2(registry, key_set, tmp_path, capsys):
    code = contest.main(
        [
            "--candidate",
            "Org/Never-Registered",
            "--set",
            str(key_set),
            "--rules",
            str(rules(tmp_path)),
            "--candidate-results",
            str(tmp_path),
            "--champion-results",
            str(tmp_path),
            "--out",
            str(tmp_path / "c"),
        ]
    )
    assert code == 2
    assert "contest refused (licence): the candidate Org/Never-Registered has no licence entry" in (
        capsys.readouterr().err
    )


# ------------------------------------------------------------------ the rules and fairness


def test_rules_not_written_down_refuse_the_contest(registry, key_set, answers, tmp_path):
    runs = setup(tmp_path, answers, {c: perfect(a) for c, a in answers.items()})
    with pytest.raises(contest.ContestRefused) as refused:
        go(tmp_path, key_set, runs, rules(tmp_path, margin=None, slice_metrics=[]))
    assert refused.value.reason == "rules"
    assert "no margin written down" in refused.value.detail
    assert "no metrics named to guard in every slice" in refused.value.detail
    assert runs.asked is False


def test_the_real_rules_file_is_not_written_down_yet_so_no_real_contest_can_start():
    found = contest.rules_problems(json.loads(contest.RULES.read_text("utf-8")))
    assert "no margin written down: how far may a slice drop?" in found
    assert not contest.agreed(json.loads(contest.RULES.read_text("utf-8")))


@pytest.mark.parametrize(
    ("cand_kw", "said"),
    [
        ({"machine": "1x A100"}, "the two runs differ in machine: '1x L4' / '1x A100'"),
        ({"set_versions": {"synthetic-10": "0000"}}, "the candidate was run on another version"),
    ],
)
def test_runs_on_another_machine_or_answer_key_are_not_a_fair_contest(
    registry, key_set, answers, tmp_path, cand_kw, said
):
    runs = setup(tmp_path, answers, {c: perfect(a) for c, a in answers.items()}, cand_kw=cand_kw)
    with pytest.raises(contest.ContestRefused) as refused:
        go(tmp_path, key_set, runs, rules(tmp_path))
    assert refused.value.reason == "unfair" and said in refused.value.detail


def test_a_result_made_by_another_model_is_not_a_fair_contest(registry, key_set, answers, tmp_path):
    runs = setup(tmp_path, answers, {c: perfect(a) for c, a in answers.items()})
    case = runs.folders["candidate"] / "syn-03.json"
    body = json.loads(case.read_text("utf-8"))
    body["engines"][0]["name"] = "Org/Champion-7B"
    case.write_text(json.dumps(body), "utf-8")
    with pytest.raises(contest.ContestRefused) as refused:
        go(tmp_path, key_set, runs, rules(tmp_path))
    assert "syn-03: the candidate result was made by Org/Champion-7B" in refused.value.detail


def test_the_champion_defaults_to_the_setting_and_cannot_meet_itself(
    registry, key_set, answers, tmp_path
):
    runs = setup(tmp_path, answers, {c: perfect(a) for c, a in answers.items()})
    report = go(tmp_path, key_set, runs, rules(tmp_path))
    assert report["models"]["champion"]["id"] == "champ"  # CDI_CHAMPION_EXTRACTION
    with pytest.raises(contest.ContestRefused, match="the candidate is the champion"):
        go(tmp_path, key_set, runs, rules(tmp_path), candidate="champ")


def test_a_sealed_key_is_opened_for_a_contest_and_the_opening_written_down(
    registry, key_set, answers, tmp_path
):
    (key_set / "manifest.json").write_text(json.dumps({"name": "k", "sealed": True}), "utf-8")
    runs = setup(tmp_path, answers, {c: perfect(a) for c, a in answers.items()})
    report = go(tmp_path, key_set, runs, rules(tmp_path), by="made-up owner")
    opening = json.loads((key_set / "openings.jsonl").read_text("utf-8"))
    assert opening["change"] == "contest cand vs champ" and opening["by"] == "made-up owner"
    assert report["verdict"]["binding"] is True  # sealed key, agreed rules


def test_the_models_are_sent_pictures_only_never_the_answer():
    import inspect

    source = inspect.getsource(contest.collect)
    assert "keys.IMAGE_NAMES.match" in source and 'answer.json")' not in source.split("files =")[1]


def test_the_answer_key_is_never_changed_by_a_contest(registry, key_set, answers, tmp_path):
    before = contest.set_version(key_set)
    copy_of = copy.deepcopy(answers)
    runs = setup(tmp_path, answers, {c: perfect(a) for c, a in answers.items()})
    go(tmp_path, key_set, runs, rules(tmp_path))
    assert contest.set_version(key_set) == before
    assert {
        p.parent.name: json.loads(p.read_text("utf-8")) for p in key_set.glob("*/answer.json")
    } == copy_of


# ------------------------------------------------------------------ not done yet


@pytest.mark.xfail(
    strict=True,
    reason="not done yet: the margins and limits in models/contest_rules.json are not written "
    "down or agreed by the product owner and a clinician (story NFR3)",
)
def test_the_rules_are_agreed_before_the_first_real_contest():
    real = json.loads(contest.RULES.read_text("utf-8"))
    assert contest.agreed(real) and not contest.rules_problems(real)


@pytest.mark.xfail(
    strict=True,
    reason="not done yet: no real contest has run - it needs the sealed answer key (ENT-S3), the "
    "guard suite (EV-S4), a second registered model with its licence text saved, and a GPU host",
)
def test_a_real_contest_has_been_run_on_the_sealed_key():
    index = contest.CONTESTS / "index.jsonl"
    lines = (
        [json.loads(x) for x in index.read_text("utf-8").splitlines()] if index.is_file() else []
    )
    assert any(line["binding"] for line in lines)
