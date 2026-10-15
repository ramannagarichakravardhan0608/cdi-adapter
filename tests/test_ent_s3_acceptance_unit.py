"""ENT-S3 "Prove the accuracy: an answer-key set and an automatic quality gate".

* AC1  precision and coverage are reported for patient, doctor, tests and preparation;
* AC2  a change that lowers a score below its limit fails the automatic check;
* AC3  a claimed target is shown with its 95% lower bound and sample size, and passes only
       when the lower bound meets the target;
* AC4  wrong test-to-reason and preparation-to-test links are counted apart from reading errors;
* AC5  two labellers' keys are compared, their agreement reported, and every disagreement
       settled before the key is used;
* AC6  no picture of the set shows a readable patient or clinic identity.

The scorer is exercised on small made-up prescriptions built here, and on the ten of
``evaluation/synthetic-10``. NOTHING in this file measures the product: no picture is read.
The tests at the end say which criteria wait for the real set.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from cdi_adapter import result as result_module
from cdi_adapter.accuracy import __main__ as cli
from cdi_adapter.accuracy import gate, keys, score, stats

ROOT = Path(__file__).resolve().parents[1]
SYNTHETIC = ROOT / "evaluation" / "synthetic-10"


def V(value: Any, status: str = "accepted", conf: float = 0.97) -> dict[str, Any]:
    return {"value": value, "status": status, "confidence": conf}


def answer(case: str = "rx-1", **over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema_version": "answer.v1",
        "case_id": case,
        "slices": {
            "script": "printed",
            "doctor": "known",
            "doctor_group": "clinic-a",
            "suite": "regression",
        },
        "patient": {
            "name": "Asha Testwala",
            "age_text": "45 Y",
            "sex": "female",
            "dob": None,
            "phone": "9876543210",
            "address": None,
        },
        "doctor": {
            "name": "Dr. Meera Placeholder",
            "registration_no": "70001",
            "qualification": "MBBS, MD",
            "department": None,
        },
        "tests": [
            {
                "as_written": "HbA1c",
                "standard_code": "4548-4",
                "reasons": ["Type 2 diabetes mellitus"],
            },
            {"as_written": "Lipid profile", "standard_code": "LT0012", "reasons": []},
        ],
        "preparation": [
            {
                "text": "fasting 12 hrs",
                "type": "fasting",
                "amount": 12,
                "unit": "h",
                "applies_to": ["Lipid profile"],
            }
        ],
        "labelling": {"labellers": ["A", "B"], "disagreements": 0},
        "identity": {
            "method": "synthetic",
            "checked_by": "C",
            "checked_on": "2026-10-13",
            "readable_identity": False,
        },
    }
    base.update(over)
    return base


def perfect(key: dict[str, Any]) -> dict[str, Any]:
    """What a system that read everything right, and accepted it, would produce."""
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


def card_of(*pairs: tuple[dict[str, Any], dict[str, Any] | None]) -> dict[str, Any]:
    answers = {a["case_id"]: a for a, _ in pairs}
    return score.scorecard(answers, {a["case_id"]: r for a, r in pairs})


def group(card: dict[str, Any], name: str, slice_name: str = "overall") -> dict[str, Any]:
    return card["slices"][slice_name]["groups"][name]


# ------------------------------------------------------------------ AC1: precision and coverage


def test_ac1_precision_and_coverage_are_reported_for_each_of_the_four_groups():
    key = answer()
    card = card_of((key, perfect(key)))
    for name in ("patient", "doctor", "tests", "preparation"):
        m = group(card, name)
        assert m["precision_accepted"] == 1.0 and m["coverage"] == 1.0, name
        assert m["accepted"] == m["accepted_correct"] > 0
    assert set(card["slices"]["overall"]["groups"]) == set(keys.GROUPS)


def test_ac1_precision_is_about_what_was_accepted_and_coverage_about_how_much_was():
    key = answer()
    got = perfect(key)
    got["patient"]["phone"] = V("9876543219")  # wrong, and accepted by itself
    got["patient"]["name"] = V("Asha Testwala", "needs_check", 0.6)  # right, but held
    m = group(card_of((key, got)), "patient")
    assert (m["accepted"], m["accepted_correct"]) == (3, 2)  # age, sex, phone accepted
    assert m["precision_accepted"] == pytest.approx(2 / 3)
    assert m["coverage"] == pytest.approx(3 / 4)  # 4 values produced, 3 accepted by itself
    assert m["field_accuracy"] == pytest.approx(3 / 4)  # the held name is still right


def test_ac1_a_value_held_for_a_person_never_counts_against_precision():
    key = answer()
    got = perfect(key)
    got["patient"]["phone"] = V("0000000000", "needs_check", 0.4)  # wrong, but NOT accepted
    m = group(card_of((key, got)), "patient")
    assert m["precision_accepted"] == 1.0 and m["wrong"] == 1 and m["coverage"] == 0.75


def test_ac1_every_outcome_is_told_apart():
    key = answer()
    got = perfect(key)
    del got["doctor"]["registration_no"]  # missed
    got["patient"]["address"] = V("12 Nowhere Lane")  # invented: the key says there is none
    got["doctor"]["name"] = V("Dr. Someone Else")  # wrong
    card = card_of((key, got))
    assert (group(card, "doctor")["missed"], group(card, "doctor")["wrong"]) == (1, 1)
    p = group(card, "patient")
    assert p["invented"] == 1 and p["blank"] == 1  # dob rightly blank, address invented
    assert p["blank_kept"] == 0.5 and p["invented_rate"] == pytest.approx(1 / 5)


def test_ac1_leaving_an_absent_field_blank_is_a_right_answer():
    key = answer()
    m = group(card_of((key, perfect(key))), "patient")
    assert m["blank"] == 2 and m["blank_kept"] == 1.0 and m["invented"] == 0


def test_ac1_test_names_are_matched_as_a_list_and_named_as_a_standard_test():
    key = answer()
    got = perfect(key)
    got["lab_tests"][0]["standard"]["code"] = "9999-9"  # read right, NAMED wrong
    got["lab_tests"].append(
        {
            "as_written": "Vitamin D",
            "match": "matched",
            "standard": None,
            "value": V("Vitamin D"),
            "context": [],
        }
    )
    card = card_of((key, got))
    t = group(card, "tests")
    assert (t["correct"], t["invented"], t["missed"]) == (2, 1, 0)
    assert t["list_precision"] == pytest.approx(2 / 3) and t["recall"] == 1.0
    assert t["f1"] == pytest.approx(0.8) and t["precision_accepted"] == pytest.approx(2 / 3)
    assert card["slices"]["overall"]["standard_name_accuracy"] == 0.5


def test_ac1_a_test_written_another_way_is_matched_by_its_standard_code():
    key = answer()
    got = perfect(key)
    got["lab_tests"][0]["as_written"] = "Glycated haemoglobin"
    assert group(card_of((key, got)), "tests")["correct"] == 2


def test_ac1_a_lab_result_written_on_the_page_is_not_an_ordered_test():
    key = answer()
    got = perfect(key)
    got["lab_tests"].append(
        {"as_written": "Hb", "kind": "test", "value": V("Hb"), "result": V(11.2)}
    )
    assert group(card_of((key, got)), "tests")["invented"] == 0


def test_ac1_preparation_is_matched_by_its_words_then_its_type_amount_and_unit():
    key = answer()
    got = perfect(key)
    got["preparation"][0]["amount"] = 2  # "12" read as "2"
    card = card_of((key, got))
    assert group(card, "preparation")["wrong"] == 1
    assert card["slices"]["overall"]["preparation_amount_exact"] == 0.0
    reworded = perfect(key)
    reworded["preparation"][0]["text"] = "12 hours fasting"  # other words, the same instruction
    assert group(card_of((key, reworded)), "preparation")["correct"] == 1


def test_ac1_digits_are_compared_digit_for_digit():
    assert keys.same("phone", "9876543210", "+91 98765 43210")
    assert not keys.same("phone", "9876543210", "9876543219")
    assert keys.same("registration_no", "71112", "Reg. No. 71112 (WBMC)")  # the same digits
    assert not keys.same("registration_no", "71112", "71113")
    assert keys.same("sex", "female", "F") and keys.same("name", "Asha  Testwala", "asha testwala.")


def test_ac1_the_slices_are_reported_too():
    a = answer("rx-1")
    b = answer(
        "rx-2",
        slices={
            "script": "handwritten",
            "doctor": "unknown",
            "doctor_group": "clinic-b",
            "suite": "capability",
        },
    )
    bad = perfect(b)
    bad["patient"]["phone"] = V("1111111111")
    card = card_of((a, perfect(a)), (b, bad))
    assert {
        "overall",
        "script=printed",
        "script=handwritten",
        "doctor=known",
        "doctor=unknown",
        "suite=regression",
        "suite=capability",
    } <= set(card["slices"])
    assert group(card, "patient", "script=printed")["precision_accepted"] == 1.0
    assert group(card, "patient", "script=handwritten")["precision_accepted"] == 0.75
    assert group(card, "patient")["precision_accepted"] == pytest.approx(7 / 8)


def test_ac1_a_case_with_no_result_is_scored_as_nothing_read_not_skipped():
    key = answer()
    card = card_of((key, None))
    assert card["cases_without_a_result"] == ["rx-1"]
    assert group(card, "patient")["missed"] == 4 and group(card, "tests")["missed"] == 2
    assert group(card, "patient")["precision_accepted"] is None  # nothing was accepted
    assert not gate.evaluate(card, {})["passed"]


def test_ac1_the_strict_rate_and_the_calibration_gap_are_there():
    key = answer()
    got = perfect(key)
    got["patient"]["phone"] = V("9876543219", conf=0.99)
    card = card_of((key, perfect(key)), (answer("rx-2"), got))
    assert card["slices"]["overall"]["strict_whole_prescription_rate"] == 0.5
    m = group(card, "patient")
    assert m["calibration_gap"] > 0 and m["calibration_bands"][0]["n"] == 8
    assert m["digits_exact"] == 0.5


def test_ac1_the_scorers_own_decisions_can_be_read():
    key = answer()
    got = perfect(key)
    got["patient"]["phone"] = V("9876543219")
    lines = score.transcript(card_of((key, got)))
    assert lines[0].startswith("wrong     accepted rx-1 patient.phone: key='9876543210'")


def test_ac1_the_report_names_all_four_groups_with_both_numbers():
    key = answer()
    card = card_of((key, perfect(key)))
    page = gate.as_markdown(card, gate.evaluate(card, {}))
    assert "| Group | Accepted | Right | Precision of accepted | 95% lower bound | Coverage" in page
    for name in keys.GROUPS:
        assert f"| {name} | " in page


# ------------------------------------------------------------------ AC2: below the limit fails


def _limits(**more: Any) -> dict[str, Any]:
    return {
        "limits": [
            {"group": "patient", "metric": "precision_accepted", "min": 0.9},
            {"group": "tests", "metric": "coverage", "min": 0.9},
        ],
        **more,
    }


def test_ac2_a_score_at_or_above_its_limit_passes():
    key = answer()
    verdict = gate.evaluate(card_of((key, perfect(key))), _limits())
    assert verdict["passed"] and verdict["failures"] == []


def test_ac2_a_score_below_its_limit_fails_the_change():
    key = answer()
    got = perfect(key)
    got["patient"]["phone"] = V("9876543219")  # precision 3/4 = 0.75, the limit is 0.9
    verdict = gate.evaluate(card_of((key, got)), _limits())
    assert not verdict["passed"]
    assert verdict["failures"] == [
        "patient.precision_accepted [overall]: 0.7500 is below its limit 0.9"
    ]


def test_ac2_an_error_rate_above_its_limit_fails_too():
    key = answer()
    got = perfect(key)
    got["preparation"][0]["applies_to"] = ["HbA1c"]
    limits = {"limits": [{"group": "context", "metric": "prep_to_test.error_rate", "max": 0.0}]}
    verdict = gate.evaluate(card_of((key, got)), limits)
    assert not verdict["passed"] and "is above its limit 0.0" in verdict["failures"][0]


def test_ac2_a_score_that_could_not_be_measured_is_not_a_pass():
    key = answer(preparation=[])
    limits = {"limits": [{"group": "preparation", "metric": "precision_accepted", "min": 0.9}]}
    verdict = gate.evaluate(card_of((key, perfect(key))), limits)
    assert not verdict["passed"] and "not measured" in verdict["failures"][0]


def test_ac2_a_drop_against_the_last_accepted_run_fails_even_above_the_limit():
    a, b = answer("rx-1"), answer("rx-2")
    before = card_of((a, perfect(a)), (b, perfect(b)))
    worse = perfect(b)
    worse["patient"]["phone"] = V("9876543219")  # 7/8 = 0.875: above a 0.8 limit, but a drop
    after = card_of((a, perfect(a)), (b, worse))
    limits = {
        "limits": [{"group": "patient", "metric": "precision_accepted", "min": 0.8}],
        "allowed_drop": 0.01,
    }
    assert gate.evaluate(after, limits)["passed"], "above the limit"
    verdict = gate.evaluate(after, limits, baseline=before)
    assert not verdict["passed"] and "fell from 1.0000 to 0.8750" in verdict["failures"][0]
    assert gate.evaluate(after, {**limits, "allowed_drop": 0.2}, baseline=before)["passed"]


def test_ac2_a_limit_can_be_set_for_one_slice():
    a = answer("rx-1")
    b = answer(
        "rx-2",
        slices={
            "script": "handwritten",
            "doctor": "known",
            "doctor_group": "clinic-a",
            "suite": "capability",
        },
    )
    bad = perfect(b)
    bad["patient"]["name"] = V("Someone Else")
    limits = {
        "limits": [
            {
                "group": "patient",
                "metric": "precision_accepted",
                "min": 0.9,
                "slice": "script=handwritten",
            }
        ]
    }
    verdict = gate.evaluate(card_of((a, perfect(a)), (b, bad)), limits)
    assert not verdict["passed"] and "[script=handwritten]" in verdict["failures"][0]


def test_ac2_the_command_exits_1_when_the_gate_fails_and_0_when_it_passes(tmp_path):
    assert (
        cli.main(
            [
                "score",
                str(SYNTHETIC),
                "--thresholds",
                str(SYNTHETIC / "thresholds.json"),
                "--baseline",
                str(SYNTHETIC / "baseline.json"),
            ]
        )
        == 0
    )
    strict = json.loads((SYNTHETIC / "thresholds.json").read_text(encoding="utf-8"))
    strict["limits"][0]["min"] = 0.999
    path = tmp_path / "strict.json"
    path.write_text(json.dumps(strict), encoding="utf-8")
    assert cli.main(["score", str(SYNTHETIC), "--thresholds", str(path)]) == 1


def test_ac2_a_change_that_makes_an_output_worse_fails_the_stored_gate(tmp_path):
    """The automatic check as CI runs it: the synthetic set, its limits, its baseline - and one
    result made worse, as a change to the product would."""
    import shutil

    work = tmp_path / "set"
    shutil.copytree(SYNTHETIC, work)
    res = json.loads((work / "syn-02" / "result.json").read_text(encoding="utf-8"))
    res["doctor"]["name"]["value"] = "Dr. Misread Name"
    (work / "syn-02" / "result.json").write_text(json.dumps(res), encoding="utf-8")
    assert (
        cli.main(
            [
                "score",
                str(work),
                "--thresholds",
                str(work / "thresholds.json"),
                "--baseline",
                str(work / "baseline.json"),
            ]
        )
        == 1
    )


def test_ac2_the_build_runs_the_check_on_every_change():
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    job = ci.split("  accuracy-gate:")[1].split("\n  integration-gate:")[0]
    assert "python -m cdi_adapter.accuracy score evaluation/synthetic-10" in job
    assert "--baseline evaluation/synthetic-10/baseline.json" in job
    assert "vars.CDI_EVAL_HOST" in job, "the real set runs against a host once one is named"
    assert ci.split("on:")[1].split("permissions:")[0].split() == ["push:", "pull_request:"]


def test_ac2_each_run_is_kept_and_the_history_can_be_drawn(tmp_path):
    key = answer()
    hist = tmp_path / "history.jsonl"
    for change, phone in (("c1", "9876543210"), ("c2", "9876543219"), ("c3", "9876543210")):
        got = perfect(key)
        got["patient"]["phone"] = V(phone)
        card = card_of((key, got))
        gate.append_history(
            hist, card, gate.evaluate(card, _limits()), at=f"2026-10-1{change[1]}", change=change
        )
    history = gate.read_history(hist)
    assert [h["passed"] for h in history] == [True, False, True]
    assert [h["patient"]["precision_accepted"] for h in history] == [1.0, 0.75, 1.0]
    page = gate.chart_html(history)
    assert page.count("<polyline") == 8 and "<script" not in page and "http" not in page
    assert "Precision of accepted values" in page and ">FAIL<" in page


# ------------------------------------------------------------------ AC3: the lower bound decides


@pytest.mark.parametrize(
    ("correct", "n", "bound", "proven"),
    [
        (300, 300, 0.9910605512394408, True),
        (299, 300, 0.9851973891171308, False),
        (995, 1000, 0.9897640447178245, False),
        (990, 1000, 0.9833422702488035, False),
        (1990, 2000, 0.9916535745484144, True),
        (4975, 5000, 0.9930701024191743, True),
    ],
)
def test_ac3_the_lower_bound_is_the_accuracy_plans_own(correct, n, bound, proven):
    """The six rows of the Accuracy Plan's proof calculator, to the last digit shown there."""
    proof = stats.proves(correct, n, 0.99)
    assert proof["lower_bound_95"] == pytest.approx(bound, abs=1e-12)
    assert proof["proven"] is proven and proof["n"] == n


def test_ac3_a_perfect_run_needs_299_accepted_values_to_prove_99_percent():
    assert stats.needed_with_no_errors(0.99) == 299  # the Accuracy Plan's number
    assert stats.proves(299, 299, 0.99)["proven"] and not stats.proves(298, 298, 0.99)["proven"]
    assert stats.wilson_lower(0, 0) is None and stats.exact_lower(0, 0) is None


def test_ac3_an_observed_99_percent_is_not_a_proven_99_percent():
    proof = stats.proves(99, 100, 0.99)
    assert proof["observed"] == 0.99 and proof["lower_bound_95"] < 0.96 and not proof["proven"]


def test_ac3_the_report_shows_the_bound_and_the_sample_size_and_decides_by_the_bound():
    key = answer()
    card = card_of((key, perfect(key)))
    verdict = gate.evaluate(card, {"claims": [{"group": "patient", "target": 0.99}]})
    claim = verdict["claims"][0]
    assert (claim["n"], claim["correct"], claim["observed"]) == (4, 4, 1.0)
    assert claim["lower_bound_95"] == pytest.approx(0.5965, abs=1e-3) and not claim["proven"]
    assert not verdict["passed"], "4 right of 4 is 100% observed and proves nothing"
    assert verdict["failures"] == [
        "claim patient precision >= 0.99 [overall]: NOT proven - "
        "4 right of 4 accepted, lower bound 0.5965"
    ]
    page = gate.as_markdown(card, verdict)
    assert (
        "| patient precision of accepted | overall | 4 | 4 | 100.0% | 59.6% | 99.0% | NOT YET |"
        in page
    )


def test_ac3_with_enough_right_values_the_claim_passes():
    pairs = []
    for i in range(80):  # 80 prescriptions x 4 accepted patient values = 320, all right
        key = answer(f"rx-{i}")
        pairs.append((key, perfect(key)))
    verdict = gate.evaluate(card_of(*pairs), {"claims": [{"group": "patient", "target": 0.99}]})
    claim = verdict["claims"][0]
    assert claim["n"] == 320 and claim["proven"] and verdict["passed"]
    assert claim["lower_bound_95"] >= 0.99 and claim["lower_bound_95_exact"] >= 0.99


def test_ac3_a_claim_is_made_for_a_slice_and_judged_on_that_slices_own_sample():
    a = answer("rx-1")
    b = answer(
        "rx-2",
        slices={
            "script": "handwritten",
            "doctor": "known",
            "doctor_group": "clinic-a",
            "suite": "capability",
        },
    )
    claims = {"claims": [{"group": "doctor", "target": 0.5, "slice": "script=handwritten"}]}
    claim = gate.evaluate(card_of((a, perfect(a)), (b, perfect(b))), claims)["claims"][0]
    assert claim["slice"] == "script=handwritten" and claim["n"] == 3  # not the 6 of both


def test_ac3_nothing_accepted_proves_nothing():
    key = answer()
    held = perfect(key)
    for v in held["patient"].values():
        v["status"] = "needs_check"
    verdict = gate.evaluate(card_of((key, held)), {"claims": [{"group": "patient", "target": 0.9}]})
    assert verdict["claims"][0]["n"] == 0 and "nothing was accepted" in verdict["failures"][0]


def test_ac3_the_storys_target_is_in_the_product_thresholds_as_a_claim_to_confirm():
    product = json.loads((ROOT / "evaluation/thresholds.json").read_text(encoding="utf-8"))
    assert {c["group"]: c["target"] for c in product["claims"]} == dict.fromkeys(keys.GROUPS, 0.99)
    assert product["limits"] == [], "no limit was set by guess"
    assert not gate.agreed(product), "nobody has agreed them yet"


def test_ac3_a_release_refuses_thresholds_nobody_agreed():
    key = answer()
    card = card_of((key, perfect(key)))
    assert gate.evaluate(card, {"limits": []})["passed"]
    refused = gate.evaluate(card, {"limits": []}, require_agreed=True)
    assert not refused["passed"] and "not agreed" in refused["failures"][0]
    signed = {"agreed": {"owner": "O", "clinician": "C", "date": "2026-11-01"}}
    assert gate.evaluate(card, signed, require_agreed=True)["passed"]


# ------------------------------------------------------------------ AC4: context apart from reading


def test_ac4_a_note_read_right_but_tied_to_the_wrong_test_is_a_context_error_only():
    key = answer()
    got = perfect(key)
    got["preparation"][0]["applies_to"] = ["HbA1c"]  # the key says Lipid profile
    card = card_of((key, got))
    for name in keys.GROUPS:  # every reading number is still perfect
        assert group(card, name)["wrong"] == 0 and group(card, name)["field_accuracy"] == 1.0
    link = card["slices"]["overall"]["context"]["prep_to_test"]
    assert (link["in_key"], link["wrong"], link["missing"], link["error_rate"]) == (1, 1, 1, 1.0)
    assert card["slices"]["overall"]["strict_whole_prescription_rate"] == 0.0


def test_ac4_a_test_tied_to_a_reason_that_is_not_its_own_is_counted_separately():
    key = answer()
    got = perfect(key)
    got["lab_tests"][1]["context"] = [{"text": "Type 2 diabetes mellitus"}]  # not in the key
    context = card_of((key, got))["slices"]["overall"]["context"]
    assert context["test_to_reason"]["wrong"] == 1 and context["test_to_reason"]["right"] == 1
    assert context["prep_to_test"]["wrong"] == 0, "the two kinds of link are counted apart"
    assert context["test_to_reason"]["link_precision"] == 0.5


def test_ac4_a_missing_link_shows_in_recall():
    key = answer()
    got = perfect(key)
    got["lab_tests"][0]["context"] = []
    link = card_of((key, got))["slices"]["overall"]["context"]["test_to_reason"]
    assert (link["missing"], link["wrong"], link["link_recall"], link["error_rate"]) == (1, 0, 0, 0)


def test_ac4_a_reading_error_is_never_counted_as_a_link_error_as_well():
    key = answer()
    got = perfect(key)
    got["lab_tests"] = got["lab_tests"][:1]  # "Lipid profile" was not read at all
    card = card_of((key, got))
    assert group(card, "tests")["missed"] == 1  # the reading error
    link = card["slices"]["overall"]["context"]["prep_to_test"]
    assert (link["in_key"], link["wrong"], link["missing"]) == (0, 0, 0)  # no link to judge


def test_ac4_applies_to_all_and_unclear_are_understood():
    key = answer()
    key["preparation"][0]["applies_to"] = "all"
    got = perfect(key)
    link = card_of((key, got))["slices"]["overall"]["context"]["prep_to_test"]
    assert (link["in_key"], link["right"]) == (2, 2)
    got["preparation"][0]["applies_to"] = "unclear"  # the system linked it to nothing
    link = card_of((key, got))["slices"]["overall"]["context"]["prep_to_test"]
    assert (link["given"], link["missing"], link["wrong"]) == (0, 2, 0)


def test_ac4_a_value_put_in_the_wrong_field_is_a_context_error():
    key = answer()
    got = perfect(key)
    got["patient"]["name"] = V("Dr. Meera Placeholder", "needs_check", 0.6)
    card = card_of((key, got))
    assert card["slices"]["overall"]["context"]["misplaced_values"] == 1
    assert card["links"]["rx-1"]["misplaced"] == ["doctor's name given as the patient's"]


def test_ac4_the_report_has_its_own_table_for_context():
    key = answer()
    got = perfect(key)
    got["preparation"][0]["applies_to"] = ["HbA1c"]
    card = card_of((key, got))
    page = gate.as_markdown(card, gate.evaluate(card, {}))
    assert "**Context errors (counted apart from reading errors):**" in page
    assert "| preparation -> test | 1 | 1 | 0 | 1 | 1 | 100.0% |" in page
    assert "| test -> reason | 1 | 1 | 1 | 0 | 0 | 0.0% |" in page


# ------------------------------------------------------------------ AC5: two labellers


def test_ac5_agreement_is_reported_overall_and_per_group():
    first = {f"rx-{i}": answer(f"rx-{i}") for i in range(50)}
    second = copy.deepcopy(first)
    second["rx-3"]["patient"]["phone"] = "9876543219"
    second["rx-7"]["preparation"][0]["amount"] = 10
    second["rx-9"]["tests"].append({"as_written": "TSH", "standard_code": "3016-3", "reasons": []})
    report = keys.agreement(first, second)
    assert report["cases_compared"] == 50 and report["only_one_labeller"] == []
    assert report["by_group"]["patient"] == {
        "items": 300,
        "agreed": 299,
        "agreement": pytest.approx(299 / 300),
    }
    assert report["by_group"]["doctor"]["agreement"] == 1.0
    assert 0.99 < report["agreement"] < 1.0 and not report["all_settled"]


def test_ac5_every_disagreement_is_listed_to_be_settled():
    first = {"rx-1": answer()}
    second = copy.deepcopy(first)
    second["rx-1"]["patient"]["phone"] = "9876543219"
    second["rx-1"]["doctor"]["department"] = "Medicine"  # one wrote it down, the other left it
    found = keys.agreement(first, second)["disagreements"]
    assert [(d["item"], d["first"], d["second"]) for d in found] == [
        ("doctor.department", None, "medicine"),
        ("patient.phone", "9876543210", "9876543219"),
    ]


def test_ac5_a_key_is_not_usable_until_it_is_settled():
    assert keys.settled(answer()) is None
    assert keys.settled(answer(labelling={"labellers": ["A"]})) == "fewer than two labellers"
    assert keys.settled(answer(labelling={"labellers": ["A", "A"], "disagreements": 0})) == (
        "fewer than two labellers"
    )
    open_ = answer(labelling={"labellers": ["A", "B"], "disagreements": 2})
    assert keys.settled(open_) == "2 disagreement(s) not adjudicated"
    assert "never compared" in str(keys.settled(answer(labelling={"labellers": ["A", "B"]})))
    done = answer(
        labelling={
            "labellers": ["A", "B"],
            "disagreements": 2,
            "adjudicated_by": "C",
            "adjudicated_on": "2026-11-02",
        }
    )
    assert keys.settled(done) is None


def _write_set(
    folder: Path,
    *answers: dict[str, Any],
    manifest: dict[str, Any] | None = None,
    picture: bool = True,
) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "manifest.json").write_text(json.dumps(manifest or {"name": "t"}), encoding="utf-8")
    for a in answers:
        case = folder / a["case_id"]
        case.mkdir()
        (case / "answer.json").write_text(json.dumps(a), encoding="utf-8")
        (case / "result.json").write_text(json.dumps(perfect(a)), encoding="utf-8")
        if picture:
            (case / "image.png").write_bytes(b"\x89PNG")
    return folder


def test_ac5_the_scorer_refuses_a_set_with_an_unsettled_key(tmp_path, capsys):
    unsettled = answer("rx-2", labelling={"labellers": ["A", "B"], "disagreements": 1})
    folder = _write_set(tmp_path / "set", answer("rx-1"), unsettled)
    assert cli.main(["score", str(folder)]) == 2
    assert "rx-2: 1 disagreement(s) not adjudicated" in capsys.readouterr().err


def test_ac5_each_labellers_own_key_is_kept_beside_the_settled_one():
    a = keys.labeller_keys(SYNTHETIC, "labeller-a")
    b = keys.labeller_keys(SYNTHETIC, "labeller-b")
    assert len(a) == len(b) == 10
    report = keys.agreement(a, b)
    assert report["items"] == 152 and len(report["disagreements"]) == 2
    settled = {c: json.loads((SYNTHETIC / c / "answer.json").read_text("utf-8")) for c in a}
    assert sum(k["labelling"]["disagreements"] for k in settled.values()) == 2
    assert all(keys.settled(k) is None for k in settled.values())


# ------------------------------------------------------------------ AC6: no readable identity


def test_ac6_a_case_must_record_that_its_identity_is_removed_and_who_looked():
    assert keys.identity_removed(answer()) is None
    for broken, why in (
        ({}, "identity.method"),
        (
            {
                "method": "deidentified",
                "checked_by": "C",
                "checked_on": "d",
                "readable_identity": False,
            },
            "identity.tool",
        ),
        ({"method": "synthetic", "readable_identity": False}, "nobody is recorded"),
        ({"method": "synthetic", "checked_by": "C", "checked_on": "d"}, "readable_identity"),
        (
            {
                "method": "synthetic",
                "checked_by": "C",
                "checked_on": "d",
                "readable_identity": True,
            },
            "readable_identity",
        ),
    ):
        assert why in str(keys.identity_removed(answer(identity=broken)))


def test_ac6_a_set_with_one_case_not_cleared_may_not_be_used(tmp_path, capsys):
    shows = answer(
        "rx-2",
        identity={
            "method": "deidentified",
            "tool": "cdi_adapter.deid",
            "checked_by": "C",
            "checked_on": "2026-11-02",
            "readable_identity": True,
        },
    )
    folder = _write_set(tmp_path / "set", answer("rx-1"), shows)
    report = keys.check_set(folder)
    assert not report["usable"] and list(report["problems"]) == ["rx-2"]
    assert cli.main(["score", str(folder)]) == 2


def test_ac6_a_real_page_must_have_its_picture_in_the_set(tmp_path):
    real = answer(
        identity={
            "method": "deidentified",
            "tool": "cdi_adapter.deid",
            "checked_by": "C",
            "checked_on": "2026-11-02",
            "readable_identity": False,
        }
    )
    report = keys.check_set(_write_set(tmp_path / "set", real, picture=False))
    assert report["problems"]["rx-1"] == ["no picture (image.png / page-N.png)"]


def test_ac6_the_synthetic_set_has_no_picture_and_no_real_person():
    report = keys.check_set(SYNTHETIC)
    assert report["usable"] and report["cases"] == 10 and report["fields_blank"] == 20
    assert not [p for p in SYNTHETIC.rglob("*") if keys.IMAGE_NAMES.match(p.name)]
    manifest = json.loads((SYNTHETIC / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["real_patient_data"] is False and "NOT a measurement" in manifest["about"]
    for path in SYNTHETIC.glob("*/answer.json"):
        key = json.loads(path.read_text(encoding="utf-8"))
        assert key["identity"]["method"] == "synthetic"
        assert key["patient"]["name"].split()[-1] in (
            "Testwala",
            "Samplekar",
            "Dummyani",
            "Mockerjee",
            "Fakenath",
        )


# ------------------------------------------------------------------ the set, and the rest


def test_set_a_key_must_state_every_field_null_when_absent():
    key = answer()
    del key["patient"]["address"]
    assert "patient.address is not stated (write null when it is absent)" in keys.problems_of(key)
    assert keys.problems_of(answer()) == []
    bad = answer(slices={"script": "typed", "doctor": "known"})
    assert len(keys.problems_of(bad)) == 2


def test_set_a_set_that_never_leaves_a_field_blank_is_called_out(tmp_path):
    full = answer()
    full["patient"].update(dob="1980-01-01", address="1 Example Road")
    full["doctor"]["department"] = "Medicine"
    report = keys.check_set(_write_set(tmp_path / "set", full))
    assert report["notes"] == ["no field is absent in any key: 'leave it blank' is not tested"]


def test_set_test_and_training_data_are_kept_apart_by_doctor_and_clinic(tmp_path):
    other = answer(
        "rx-2",
        slices={
            "script": "printed",
            "doctor": "unknown",
            "doctor_group": "clinic-z",
            "suite": "capability",
        },
    )
    folder = _write_set(tmp_path / "set", answer("rx-1"), other)
    assert keys.split_leaks(folder, {"clinic-a", "clinic-q"}) == ["rx-1"]
    assert keys.split_leaks(folder, {"clinic-q"}) == []


def test_set_a_sealed_set_is_opened_only_for_a_release_and_every_opening_is_written(tmp_path):
    folder = _write_set(tmp_path / "sealed", answer(), manifest={"name": "final", "sealed": True})
    assert cli.main(["score", str(folder)]) == 2 and not (folder / "openings.jsonl").exists()
    signed = tmp_path / "t.json"
    signed.write_text(json.dumps({"agreed": {"owner": "O", "clinician": "C", "date": "d"}}))
    assert (
        cli.main(
            [
                "score",
                str(folder),
                "--release",
                "--thresholds",
                str(signed),
                "--change",
                "abc123",
                "--by",
                "release manager",
            ]
        )
        == 0
    )
    opened = json.loads((folder / "openings.jsonl").read_text(encoding="utf-8"))
    assert opened["change"] == "abc123" and opened["by"] == "release manager"


def test_set_the_ablation_says_what_a_stage_adds():
    key = answer()
    worse = perfect(key)
    worse["patient"]["phone"] = V("9876543219")
    report = cli.ablation(card_of((key, worse)), card_of((key, perfect(key))), "second opinion")
    row = next(
        r for r in report["rows"] if (r["group"], r["metric"]) == ("patient", "precision_accepted")
    )
    assert row["delta"] == pytest.approx(0.25) and report["any_measured_gain"]
    same = cli.ablation(card_of((key, perfect(key))), card_of((key, perfect(key))), "trocr")
    assert not same["any_measured_gain"], "a stage with no measured gain is not kept"


def test_set_the_synthetic_outputs_are_real_result_documents():
    for path in sorted(SYNTHETIC.glob("*/result.json")):
        assert result_module.problems(json.loads(path.read_text(encoding="utf-8"))) == [], path


def test_set_the_scorer_finds_every_mistake_planted_in_the_synthetic_set():
    _, answers = keys.load_set(SYNTHETIC)
    results = {c: json.loads((SYNTHETIC / c / "result.json").read_text("utf-8")) for c in answers}
    card = score.scorecard(answers, results, set_name="synthetic-10")
    found = {
        (r["case"], r["group"], r["field"], r["outcome"])
        for r in card["records"]
        if r["outcome"] not in ("correct", "blank")
    }
    assert found == {
        ("syn-01", "patient", "phone", "wrong"),  # a digit wrong, accepted
        ("syn-02", "patient", "address", "invented"),  # not on the page
        ("syn-02", "preparation", "item", "wrong"),  # 12 hours read as 2
        ("syn-03", "tests", "name", "invented"),  # a test that is not written
        ("syn-06", "doctor", "registration_no", "missed"),
        ("syn-07", "patient", "name", "wrong"),  # the doctor's name
        ("syn-08", "preparation", "item", "invented"),
    }
    overall = card["slices"]["overall"]
    assert overall["context"]["prep_to_test"]["wrong"] == 1  # syn-04: the wrong test
    assert overall["context"]["test_to_reason"]["wrong"] == 1  # syn-05: a reason not written
    assert overall["context"]["misplaced_values"] == 1  # syn-07
    assert overall["standard_name_accuracy"] == pytest.approx(18 / 19)  # syn-05: named wrong
    assert overall["strict_whole_prescription_rate"] == pytest.approx(0.2)
    assert card["slices"]["suite=adversarial"]["strict_whole_prescription_rate"] == 1.0


# ------------------------------------------------------------------ what waits for people


@pytest.mark.xfail(
    strict=True,
    reason="AC1 on the product: there is no real answer-key set. "
    "Every number above comes from made-up outputs; no picture has been read "
    "and scored. Needs 30-50 de-identified prescriptions to start (300+ later).",
)
def test_the_product_has_been_measured_on_a_real_set():
    real = [
        p
        for p in (ROOT / "evaluation").iterdir()
        if p.is_dir() and p.name != "synthetic-10" and list(p.glob("*/answer.json"))
    ]
    assert real


@pytest.mark.xfail(
    strict=True,
    reason="AC3 / NFR3: the thresholds are not agreed - no owner, "
    "clinician and date are recorded, and the 99% target is still 'to confirm'.",
)
def test_the_thresholds_are_agreed_by_the_owner_and_a_clinician():
    assert gate.agreed(json.loads((ROOT / "evaluation/thresholds.json").read_text("utf-8")))


@pytest.mark.xfail(
    strict=True,
    reason="AC5 / AC6: no clinician has labelled anything and no "
    "real picture has been checked. The two 'labellers' of the synthetic set "
    "are made up, and it has no pictures.",
)
def test_two_clinicians_have_labelled_fifty_real_prescriptions():
    for folder in (ROOT / "evaluation").iterdir():
        if folder.is_dir() and folder.name != "synthetic-10":
            if keys.check_set(folder)["usable"] and keys.check_set(folder)["cases"] >= 50:
                return
    raise AssertionError("no settled, identity-cleared set of 50 real prescriptions")
