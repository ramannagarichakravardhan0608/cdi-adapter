"""EX-S3 - Read the lab tests that were ordered and give each a standard name.

The acceptance criteria, one group each. Every prescription here is made up.
"""

from __future__ import annotations

import csv
import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

from cdi_adapter import result
from cdi_adapter.config import Settings
from cdi_adapter.extract.facts import facts_from_payload
from cdi_adapter.terminology import lab_master as L
from cdi_adapter.terminology.service import bind_fact, bind_text
from cdi_adapter.validate import rules
from cdi_adapter.validate.service import gate

ROOT = Path(__file__).resolve().parents[1]
KEY = ROOT / "docs" / "evidence" / "EX-S3" / "answer_key.csv"
LOINC = "http://loinc.org"


def _orders(*written: str) -> list[Any]:
    payload = {"confidence": 0.9, "medications": [],
               "investigations_advised": [{"text": w, "evidence": [f"b{i}"], "confidence": 0.9}
                                          for i, w in enumerate(written, 1)]}  # fmt: skip
    return [f for f in facts_from_payload("prescription", payload) if f.fact_type == "lab_order"]


def _fact(written: str, **more: Any) -> dict[str, Any]:
    fact = {"id": written, "fact_type": "lab_order", "local_text": written,
            "review_state": "in_review", "confidence_overall": 0.9, **more}  # fmt: skip
    bound = bind_fact(fact)
    return {**fact, **{k: bound[k] for k in ("code_system", "code", "code_display",
                                             "code_status")}}  # fmt: skip


def _lab_tests(monkeypatch, *written: str) -> list[dict[str, Any]]:
    proof = {"page": 1, "bbox": [10, 10, 200, 30], "observation_ids": ["u1"]}
    monkeypatch.setattr(result, "_evidence", lambda conn, fact_id: proof)
    return result._ordered_tests(None, [_fact(w) for w in written])  # type: ignore[arg-type]


# =====================================================================================
# AC1  GIVEN 'CBC' and 'Lipid profile' are written WHEN they are read
#      THEN both are listed as written, matched to standard names/codes, marked as panels
# =====================================================================================


def test_ac1_both_are_listed_as_written() -> None:
    assert [f.local_text for f in _orders("CBC", "Lipid profile")] == ["CBC", "Lipid profile"]


def test_ac1_both_are_matched_to_a_standard_name_and_code() -> None:
    cbc, lipid = bind_text("lab_order", "CBC"), bind_text("lab_order", "Lipid profile")
    assert cbc and (cbc.concept.system, cbc.concept.code) == (LOINC, "58410-2")
    assert cbc.concept.display == "CBC panel - Blood by Automated count"
    assert lipid and (lipid.concept.system, lipid.concept.code) == (LOINC, "57698-3")
    assert lipid.concept.display.startswith("Lipid panel")


def test_ac1_both_are_marked_as_panels(monkeypatch) -> None:
    cbc, lipid = _lab_tests(monkeypatch, "CBC", "Lipid profile")
    assert (cbc["as_written"], cbc["kind"], cbc["match"]) == ("CBC", "panel", "matched")
    assert cbc["standard"] == {"name": "CBC panel - Blood by Automated count",
                               "code_system": LOINC, "code": "58410-2"}  # fmt: skip
    assert (lipid["kind"], lipid["standard"]["code"]) == ("panel", "57698-3")
    assert cbc["specimen"] == "blood" and lipid["specimen"] == "serum"
    assert cbc["value"]["evidence"]["bbox"]  # with the part of the picture it was read from


def test_ac1_a_single_test_is_marked_a_test_and_a_study_a_study(monkeypatch) -> None:
    kinds = [r["kind"] for r in _lab_tests(monkeypatch, "HbA1c", "ECG", "Thyroid profile")]
    assert kinds == ["test", "study", "panel"]


@pytest.mark.parametrize(("written", "how"), [
    ("Lipid panel with direct LDL - Serum or Plasma", "exact"), ("lipid profile", "alias"),
    ("S. Creatinine", "alias"), ("Sr. TSH", "normalised"), ("TSH level", "normalised"),
    ("Urine R/E", "normalised"), ("C.B.C", "alias"), ("Hb%", "normalised"),
])  # fmt: skip
def test_ac1_matching_goes_exact_then_alias_then_normalised(written: str, how: str) -> None:
    m = L.match(written)
    assert (m.status, m.how) == (L.MATCHED, how)


# =====================================================================================
# AC2  GIVEN an unknown abbreviation is written WHEN it is read
#      THEN it is listed as written with 'not matched' and goes to review
# =====================================================================================


@pytest.mark.parametrize("written", ["TSML", "PTA", "XQZ profile", "ABCD"])
def test_ac2_an_unknown_abbreviation_is_listed_as_written_and_not_matched(
        monkeypatch, written: str) -> None:  # fmt: skip
    assert [f.local_text for f in _orders(written)] == [written]
    assert bind_text("lab_order", written) is None  # never forced to a name
    (row,) = _lab_tests(monkeypatch, written)
    assert (row["as_written"], row["match"], row["standard"]) == (written, "not_matched", None)
    assert row["kind"] is None  # not known: not guessed to be a test or a panel


def test_ac2_it_goes_to_review_whatever_the_confidence() -> None:
    fact = _fact("TSML")
    assert fact["code_status"] == "local_only" and fact["code"] is None
    found = rules.check_lab_order(fact)
    assert [(f.severity, f.code) for f in found] == [("blocker", "test-not-matched")]
    assert gate("lab_order", 0.99, found, False, Settings()) == "in_review"


def test_ac2_a_test_that_is_matched_is_not_held_by_this_rule() -> None:
    assert rules.check_lab_order(_fact("CBC")) == []
    assert gate("lab_order", 0.99, [], False, Settings()) == "auto_accepted"


def test_ac2_a_short_abbreviation_is_never_matched_by_likeness() -> None:
    """NFR1. "TSML" is one letter from "TSH" plus one: it is NOT read as TSH."""
    for written in ("TSML", "TSB", "FT5", "CBD", "RFA"):
        assert L.match(written).status == L.NOT_MATCHED


def test_ac2_a_number_is_never_close_to_another_number() -> None:
    """NFR1. A wrong digit is another test: T3 / T4, B12 / B6, a 24-hour / 12-hour sample."""
    assert L.match("T3").entry != L.match("T4").entry
    assert L.match("Vitamin B6").status == L.NOT_MATCHED  # not "vitamin b12"
    assert L.match("12 hour urine protein").status == L.NOT_MATCHED  # not the 24-hour one


def test_ac2_a_doubtful_match_goes_to_review_with_what_it_is_thought_to_be() -> None:
    m = L.match("Liver functon test")  # a spelling slip of a long name
    assert (m.status, m.how) == (L.DOUBTFUL, "fuzzy") and m.entry and "Hepatic" in m.entry.name
    found = rules.check_lab_order(_fact("Liver functon test"))
    assert [(f.severity, f.code) for f in found] == [("blocker", "test-match-doubtful")]


def test_ac2_the_reviewer_is_offered_candidates_or_none_of_these(monkeypatch) -> None:
    """FR6. The closest rows of the list are offered; an empty list means "none of these"."""
    (near,) = _lab_tests(monkeypatch, "Lipid prof.")
    assert near["match"] == "doubtful" and near["candidates"][0]["name"].startswith("Lipid panel")
    assert len(near["candidates"]) <= L.CANDIDATES
    (none,) = _lab_tests(monkeypatch, "XQZ")
    assert none["match"] == "not_matched" and "candidates" not in none
    from cdi_adapter.webapp.reviewer_page import SCRIPT

    assert "could be: " in SCRIPT and "none of the list is close" in SCRIPT


def test_ac2_a_name_a_person_has_chosen_answers_the_question() -> None:
    chosen = {**_fact("TSML"), "code": "3016-3"}  # the reviewer picked TSH
    assert rules.check_lab_order(chosen) == []
    assert L.describe("TSML", "3016-3")["standard"]["name"].startswith("Thyrotropin")


# =====================================================================================
# AC3  GIVEN a panel is read WHEN it is saved THEN it is not expanded into its tests
# =====================================================================================

PANELS = ("CBC", "Lipid profile", "LFT", "KFT", "Thyroid profile", "Iron studies",
          "Urine R/M", "Viral markers")  # fmt: skip


def test_ac3_a_panel_is_one_fact() -> None:
    orders = _orders(*PANELS)
    assert [f.local_text for f in orders] == list(PANELS)  # eight panels, eight facts


def test_ac3_a_panel_is_one_item_in_the_result(monkeypatch) -> None:
    rows = _lab_tests(monkeypatch, *PANELS)
    assert len(rows) == len(PANELS) and {r["kind"] for r in rows} == {"panel"}
    for r in rows:
        assert set(r) <= {"as_written", "kind", "specimen", "urgency", "standard", "match",
                          "matched_by", "value", "candidates"}  # fmt: skip
        assert "tests" not in r and "components" not in r and "members" not in r


def test_ac3_the_master_does_not_say_what_a_panel_is_made_of() -> None:
    """Nothing to expand a panel WITH: the list has no column for a panel's tests."""
    header = next(csv.reader(line for line in L.FILE.read_text(encoding="utf-8").splitlines()
                             if not line.startswith("#")))  # fmt: skip
    assert header == ["internal_id", "canonical_name", "kind", "specimen", "code_system",
                      "code", "aliases", "review_status", "source"]  # fmt: skip
    source = Path(L.__file__).read_text(encoding="utf-8")
    assert "expand" not in source.split('"""', 2)[2]


def test_ac3_a_panel_and_its_tests_written_together_stay_what_was_written() -> None:
    orders = _orders("CBC", "Hb", "TLC")
    assert [f.local_text for f in orders] == ["CBC", "Hb", "TLC"]  # nothing added or merged


# =====================================================================================
# AC4  GIVEN the answer-key set is run WHEN coverage is counted
#      THEN the share of tests matched and the share correct are reported
# =====================================================================================


def _script() -> Any:
    spec = importlib.util.spec_from_file_location("lab_coverage",
                                                  ROOT / "scripts" / "lab_coverage.py")  # fmt: skip
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_ac4_the_two_shares_are_counted() -> None:
    rows = [{"as_written": "CBC", "expected": "LT0001"},      # matched, and right
            {"as_written": "Lipid profile", "expected": "LT0001"},  # matched, to the wrong row
            {"as_written": "XQZ", "expected": "LT0001"},      # not matched
            {"as_written": "Lipid prof.", "expected": "LT0004"},  # doubtful: not a match
            {"as_written": "Review after 5 days", "expected": ""},  # not a test, and not named
            {"as_written": "HbA1c", "expected": ""}]          # should not be named, but is
    rep = L.coverage(rows)
    assert (rep["with_a_right_answer"], rep["matched"], rep["correct"], rep["doubtful"]) == (
        4, 2, 1, 1)  # fmt: skip
    assert (rep["share_matched"], rep["share_correct"]) == (0.5, 0.25)
    assert rep["wrong"] == [{"as_written": "Lipid profile", "expected": "LT0001",
                             "got": "57698-3"}]  # fmt: skip
    assert rep["not_matched"] == ["XQZ", "Lipid prof."]
    assert rep["named_but_should_not_be"] == ["HbA1c"]
    assert rep["master_version"] == L.version()


def test_ac4_the_answer_key_is_fifty_prescriptions_keyed_to_rows_that_exist() -> None:
    rows = _script().read_key(KEY)
    assert len({r["prescription"] for r in rows}) == 50 and len(rows) >= 140
    for r in rows:
        assert not r["expected"] or L.by_code(r["expected"]) is not None, r


def test_ac4_the_report_states_both_shares_and_what_it_was_measured_with() -> None:
    script = _script()
    rows = script.read_key(KEY)
    rep = L.coverage(rows)
    page = script.render(rep, KEY.name, 50)
    assert "**MEASURED**" in page and L.version() in page and "50 prescriptions" in page
    assert f"**{100 * rep['share_matched']:.1f}%**" in page
    assert f"**{100 * rep['share_correct']:.1f}%**" in page
    assert "It is not a sample of a real lab's orders" in page  # what it does not show


def test_ac4_the_saved_report_is_the_one_the_current_master_gives() -> None:
    saved = json.loads((KEY.parent / "report.json").read_text(encoding="utf-8"))
    now = L.coverage(_script().read_key(KEY))
    for figure in ("master_version", "with_a_right_answer", "matched", "correct", "doubtful",
                   "share_matched", "share_correct"):  # fmt: skip
        assert saved[figure] == now[figure], figure
    assert now["wrong"] == []  # no text of the key is given the WRONG name


# =====================================================================================
# the requirements behind the criteria
# =====================================================================================


def test_fr3_urgency_is_read_only_when_it_is_written(monkeypatch) -> None:
    urgent, plain = _lab_tests(monkeypatch, "CBC urgent", "CBC")
    assert (urgent["urgency"], urgent["standard"]["code"]) == ("urgent", "58410-2")
    assert plain["urgency"] is None  # "routine" is not assumed


def test_fr3_a_study_keeps_where_it_is_to_be_done(monkeypatch) -> None:
    (mri,) = _lab_tests(monkeypatch, "MRI brain")
    assert (mri["as_written"], mri["kind"], mri["standard"]["name"]) == (
        "MRI brain", "study", "MRI scan")  # fmt: skip
    assert L.match("T3 T4 TSH").entry.name == "Thyroid profile"  # a test's name is not a prefix


def test_fr4_a_test_is_never_invented() -> None:
    assert _orders() == []
    payload = {"confidence": 0.9, "medications": [], "advice": [{"text": "Rest for 3 days"}]}
    assert [f for f in facts_from_payload("prescription", payload)
            if f.fact_type == "lab_order"] == []  # fmt: skip


def test_fr7_the_master_is_versioned_and_the_version_is_saved_with_the_run() -> None:
    assert L.version() == "lab-master.2026-10-12.1"
    service = (ROOT / "src" / "cdi_adapter" / "terminology" / "service.py").read_text(
        encoding="utf-8")  # fmt: skip
    assert "model_version=lab_master.version()" in service
    built = (ROOT / "src" / "cdi_adapter" / "result.py").read_text(encoding="utf-8")
    assert '"lab_master_version": _lab_master_version(conn, doc["id"])' in built


def test_fr8_a_test_without_a_code_is_named_in_our_own_vocabulary_never_a_made_up_code() -> None:
    named = bind_text("lab_order", "Thyroid profile")
    assert named and named.concept.system == L.INTERNAL_SYSTEM
    assert named.concept.code.startswith("LT")
    entries = L.load()[1]
    coded = [e for e in entries if e.code]
    assert all(e.code_system for e in coded)
    # every code in the file was already in the project's seed list: none was added for EX-S3
    from cdi_adapter.terminology import seed

    known = {c.code for c in (*seed.LAB_PANELS, *seed.LABS, *seed.ORDERED_STUDIES,
                              *seed.PROCEDURES)}  # fmt: skip
    assert {e.code for e in coded} <= known


def test_nfr2_the_size_of_the_list_is_measured() -> None:
    entries = L.load()[1]
    kinds = {k: sum(1 for e in entries if e.kind == k) for k in L.KINDS}
    assert len(entries) == 133 and kinds == {"test": 104, "panel": 12, "study": 17}
    assert sum(len(e.aliases) for e in entries) >= 350


def test_one_way_of_writing_never_means_two_tests(tmp_path) -> None:
    clash = tmp_path / "lab_tests.csv"
    clash.write_text("# version: t.1\n"
                     "internal_id,canonical_name,kind,specimen,code_system,code,aliases,"
                     "review_status,source\n"
                     "LT1,Alpha,test,,,,abc|alpha test,unreviewed,x\n"
                     "LT2,Beta,test,,,,abc,unreviewed,x\n", encoding="utf-8")  # fmt: skip
    with pytest.raises(ValueError, match="listed for LT1 and LT2"):
        L.load(clash)


@pytest.mark.xfail(strict=True, reason=(
    "EX-S3 TR2: no row of the lab-test master has been reviewed by a clinician, and the rows "
    "added for this story carry no LOINC code - one must be taken from LOINC by a reviewer, "
    "never guessed. The codes the list does carry came from the project's seed list, which was "
    "never checked against a LOINC release."))  # fmt: skip
def test_tr2_the_master_is_reviewed_by_a_clinician() -> None:
    assert all(e.reviewed for e in L.load()[1])


@pytest.mark.xfail(strict=True, reason=(
    "EX-S3 FR6: the candidates are offered to the human reviewer. The reading model is not "
    "yet asked to pick from that list or say 'none of these' for a doubtful test."))  # fmt: skip
def test_fr6_the_reading_model_picks_from_the_candidate_list() -> None:
    assert importlib.util.find_spec("cdi_adapter.terminology.lab_choice") is not None
