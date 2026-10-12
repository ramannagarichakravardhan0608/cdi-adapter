"""EX-S5 - Say why each test was ordered (context): the acceptance criteria, one group each.

Every prescription here is made up. A fact is given the page and the OCR line it was read
from, as the pipeline records them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from cdi_adapter import result
from cdi_adapter.config import Settings
from cdi_adapter.extract import context as C
from cdi_adapter.terminology.service import bind_fact
from cdi_adapter.validate import rules
from cdi_adapter.validate.service import gate

ROOT = Path(__file__).resolve().parents[1]


def _fact(kind: str, written: str, line: str, page: int = 1, **more: Any) -> dict[str, Any]:
    row = {"id": f"{kind}:{written}", "fact_type": kind, "local_text": written,
           "review_state": "auto_accepted", "confidence_overall": 0.97, "_line": line,
           "_page": page, **more}  # fmt: skip
    bound = bind_fact(row)
    return {**row, "code": bound["code"], "code_system": bound["code_system"],
            "code_display": bound["code_display"]}  # fmt: skip


def _build(monkeypatch, facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """``lab_tests`` as ``result.build`` builds them, context linked."""
    by_id = {f["id"]: f for f in facts}

    def evidence(conn: Any, fact_id: Any) -> dict[str, Any] | None:
        f = by_id[fact_id]
        if f["_line"] is None:
            return None
        return {"page": f["_page"], "bbox": [10, 10, 300, 30], "observation_ids": [f["_line"]]}

    monkeypatch.setattr(result, "_evidence", evidence)
    tests = result._ordered_tests(None, facts)  # type: ignore[arg-type]
    result._link_context(None, tests, facts)  # type: ignore[arg-type]
    return tests


# =====================================================================================
# AC1  GIVEN 'HbA1c' and 'T2DM' are on the same prescription WHEN it is read
#      THEN the test shows 'T2DM' as its context with proof
# =====================================================================================

AC1 = [_fact("condition", "T2DM", "u3"), _fact("lab_order", "HbA1c", "u9")]


def test_ac1_the_test_shows_t2dm_as_its_context(monkeypatch) -> None:
    (test,) = _build(monkeypatch, AC1)
    (reason,) = test["context"]
    assert (reason["type"], reason["text"]) == ("diagnosis", "T2DM")
    assert "check" not in test


def test_ac1_with_proof(monkeypatch) -> None:
    (test,) = _build(monkeypatch, AC1)
    (reason,) = test["context"]
    assert reason["quote"] == "T2DM"  # the text it rests on, exactly as written
    assert reason["evidence"] == {"page": 1, "bbox": [10, 10, 300, 30],
                                  "observation_ids": ["u3"]}  # fmt: skip


def test_ac1_a_complaint_and_a_finding_are_reasons_too(monkeypatch) -> None:
    facts = [_fact("symptom", "Fever", "u2"), _fact("finding", "Pallor", "u4"),
             _fact("lab_order", "CBC", "u9")]  # fmt: skip
    (test,) = _build(monkeypatch, facts)
    assert [(r["type"], r["text"]) for r in test["context"]] == [("complaint", "Fever"),
                                                                 ("note", "Pallor")]  # fmt: skip


def test_ac1_the_visits_follow_up_is_given_when_it_is_written(monkeypatch) -> None:
    """FR2. The follow-up as written, with its own proof and status."""
    proof = {"page": 1, "bbox": [1, 2, 3, 4], "observation_ids": ["u12"]}
    monkeypatch.setattr(result, "_evidence", lambda conn, fact_id: proof)
    facts = [{"id": 1, "fact_type": "advice", "local_text": "Review after 2 weeks",
              "review_state": "auto_accepted", "confidence_overall": 0.9},
             {"id": 2, "fact_type": "advice", "local_text": "Drink fluids",
              "review_state": "auto_accepted", "confidence_overall": 0.9}]  # fmt: skip

    class NoVisitDate:
        def execute(self, *a: Any, **k: Any) -> Any:
            return self

        def scalar_one_or_none(self) -> None:
            return None

    visit = result._visit(NoVisitDate(), facts)  # type: ignore[arg-type]
    assert [v["value"] for v in visit["follow_up"]] == ["Review after 2 weeks"]
    assert visit["follow_up"][0]["evidence"] == proof
    assert "follow_up" not in result._visit(NoVisitDate(), [facts[1]])  # type: ignore[arg-type]


# =====================================================================================
# AC2  GIVEN a test has no written reason WHEN it is read
#      THEN context is empty and no reason is invented
# =====================================================================================


@pytest.mark.parametrize("test", ["HbA1c", "TSH", "Lipid profile", "CBC", "XQZ profile"])
def test_ac2_no_written_reason_is_an_empty_context(monkeypatch, test: str) -> None:
    (row,) = _build(monkeypatch, [_fact("lab_order", test, "u9")])
    assert row["context"] == [] and "check" not in row


def test_ac2_no_reason_is_invented_from_what_the_test_is() -> None:
    """HbA1c with nothing written: not "diabetes", not anything."""
    (link,) = C.link_context([{"as_written": "HbA1c", "page": 1, "blocks": ["u9"]}], [])
    assert link == {"context": [], "fits": None}
    assert C.fit("HbA1c", []) is None  # not judged: nothing to judge it against


def test_ac2_a_medicine_or_an_advice_line_is_not_a_reason(monkeypatch) -> None:
    facts = [_fact("medication", "Tab. Metformin 500", "u5"),
             _fact("advice", "Diabetic diet", "u7"), _fact("lab_order", "HbA1c", "u9")]  # fmt: skip
    (row,) = _build(monkeypatch, facts)
    assert row["context"] == []  # a medicine suggests a reason; it does not write one


def test_ac2_a_reason_with_no_proof_on_the_page_gives_no_link(monkeypatch) -> None:
    """FR5. A link rests on a quote; a reason nothing on the page supports is removed."""
    facts = [_fact("condition", "T2DM", None), _fact("lab_order", "HbA1c", "u9")]  # type: ignore[arg-type]
    (row,) = _build(monkeypatch, facts)
    assert row["context"] == []


def test_ac2_a_rejected_diagnosis_is_not_a_reason(monkeypatch) -> None:
    facts = [_fact("condition", "T2DM", "u3", review_state="rejected"),
             _fact("lab_order", "HbA1c", "u9")]  # fmt: skip
    (row,) = _build(monkeypatch, facts)
    assert row["context"] == []


# =====================================================================================
# AC3  GIVEN a test does not fit any written reason WHEN it is read
#      THEN it is flagged 'check: does not fit' and not changed
# =====================================================================================

AC3 = [_fact("condition", "T2DM", "u3"), _fact("lab_order", "TSH", "u9")]


def test_ac3_a_test_that_goes_with_none_of_the_reasons_is_flagged(monkeypatch) -> None:
    (row,) = _build(monkeypatch, AC3)
    assert row["check"] == "check: does not fit"


def test_ac3_and_not_changed(monkeypatch) -> None:
    (row,) = _build(monkeypatch, AC3)
    assert row["as_written"] == "TSH" and row["standard"]["code"] == "3016-3"
    assert row["match"] == "matched" and row["kind"] == "test"  # exactly as without the flag
    (plain,) = _build(monkeypatch, [_fact("lab_order", "TSH", "u9")])
    for key in ("as_written", "standard", "match", "kind", "specimen", "value"):
        assert row[key] == plain[key]


def test_ac3_it_goes_to_review() -> None:
    found = rules.check_context({"fact_type": "lab_order", "local_text": "TSH"}, ["T2DM"])
    assert [(f.severity, f.code) for f in found] == [("blocker", "context-does-not-fit")]
    assert "check: does not fit" in found[0].message and "'T2DM'" in found[0].message
    assert gate("lab_order", 0.99, found, False, Settings()) == "in_review"


def test_ac3_one_reason_it_goes_with_is_enough(monkeypatch) -> None:
    facts = [_fact("condition", "T2DM", "u3"), _fact("condition", "Hypothyroidism", "u4"),
             _fact("lab_order", "TSH", "u9"), _fact("lab_order", "HbA1c", "u10")]  # fmt: skip
    tsh, a1c = _build(monkeypatch, facts)
    assert "check" not in tsh and "check" not in a1c
    assert [r["text"] for r in tsh["context"]] == ["Hypothyroidism"]  # its own reason only
    assert [r["text"] for r in a1c["context"]] == ["T2DM"]


@pytest.mark.parametrize(("test", "reasons"), [
    ("TSH", ["Fracture left radius"]),           # a reason the list does not know
    ("TSH", ["T2DM", "Something unreadable"]),   # one known, one not: cannot say "none"
    ("XQZ profile", ["T2DM"]),                   # a test the list does not know
    ("Liver functon test", ["T2DM"]),            # a doubtful test name judges nothing
    ("CBC", ["T2DM"]),                           # a test that fits any reason
    ("TSH", []),                                 # no reason written
])  # fmt: skip
def test_ac3_what_cannot_be_judged_is_not_flagged(test: str, reasons: list[str]) -> None:
    assert C.fit(test, reasons) is None
    assert rules.check_context({"fact_type": "lab_order", "local_text": test}, reasons) == []


def test_ac3_only_an_ordered_test_is_checked() -> None:
    for kind in ("medication", "condition", "lab_result", "advice"):
        assert rules.check_context({"fact_type": kind, "local_text": "TSH"}, ["T2DM"]) == []


# =====================================================================================
# AC4  NEW v3: GIVEN a test and a diagnosis are on the same page but no line connects them
#      WHEN links are built THEN the link is shown as 'same page' only, or omitted,
#      never as 'ordered because'
# =====================================================================================


def test_ac4_on_one_page_with_no_line_between_them_is_same_page_only(monkeypatch) -> None:
    (row,) = _build(monkeypatch, AC1)  # the diagnosis on line u3, the test on line u9
    assert [r["relation"] for r in row["context"]] == ["same_page"]


def test_ac4_written_on_the_tests_own_line_is_same_line(monkeypatch) -> None:
    facts = [_fact("condition", "T2DM", "u9"), _fact("lab_order", "HbA1c", "u9")]
    (row,) = _build(monkeypatch, facts)
    assert [r["relation"] for r in row["context"]] == ["same_line"]


def test_ac4_on_another_page_of_the_prescription_is_other_page(monkeypatch) -> None:
    facts = [_fact("condition", "T2DM", "u3", page=1), _fact("lab_order", "HbA1c", "u40", page=2)]
    (row,) = _build(monkeypatch, facts)
    assert [r["relation"] for r in row["context"]] == ["other_page"]


def test_ac4_a_reason_known_not_to_be_the_tests_is_omitted(monkeypatch) -> None:
    facts = [_fact("condition", "Hypothyroidism", "u3"), _fact("condition", "T2DM", "u4"),
             _fact("lab_order", "HbA1c", "u9")]  # fmt: skip
    (row,) = _build(monkeypatch, facts)
    assert [r["text"] for r in row["context"]] == ["T2DM"]


def test_ac4_nothing_ever_says_ordered_because(monkeypatch) -> None:
    relations = {C.SAME_LINE, C.SAME_PAGE, C.OTHER_PAGE}
    assert relations == {"same_line", "same_page", "other_page"}
    for name in ("extract/context.py", "result.py", "webapp/reviewer_page.py"):
        source = (ROOT / "src" / "cdi_adapter" / name).read_text(encoding="utf-8")
        code = "\n".join(line.split("#")[0].split("//")[0] for line in source.splitlines()
                         if not line.strip().startswith(('"', "*", "-")))  # fmt: skip
        for claim in ("ordered_because", "ordered_for", "indication_for", "because_of"):
            assert claim not in code, (name, claim)
    (row,) = _build(monkeypatch, AC1)
    assert set(row["context"][0]) == {"type", "text", "relation", "quote", "evidence", "fits"}


def test_ac4_the_reviewer_words_it_the_same_way() -> None:
    from cdi_adapter.webapp.reviewer_page import SCRIPT

    assert "same_line: 'same line', same_page: 'same page', other_page: 'another page'" in SCRIPT
    assert "['Written reasons (context)', e => contextText(e.context)]" in SCRIPT
    assert "no reason written" in SCRIPT


# =====================================================================================
# the requirements behind the criteria
# =====================================================================================


def test_nfr1_context_never_changes_a_value() -> None:
    tests = [{"as_written": "TSH", "page": 1, "blocks": ["u9"]}]
    before = [dict(t) for t in tests]
    C.link_context(tests, [{"type": "diagnosis", "text": "T2DM", "quote": "T2DM", "page": 1,
                            "blocks": ["u3"]}])  # fmt: skip
    assert tests == before  # the tests it was given are untouched


def test_nfr3_the_list_of_pairs_is_plain_data_a_clinician_can_read() -> None:
    bank = C.bank()
    assert set(bank) == {"about", "version", "review_status", "fits_any_reason", "groups"}
    assert bank["review_status"].startswith("unreviewed")
    for group in bank["groups"]:
        assert set(group) == {"name", "reasons", "tests"} and group["reasons"] and group["tests"]


def test_every_test_named_in_the_list_is_one_the_lab_master_knows() -> None:
    bank = C.bank()
    named = [t for g in bank["groups"] for t in g["tests"]] + bank["fits_any_reason"]
    assert [t for t in named if C._test_id(t) is None] == []


def test_the_result_says_which_version_of_the_rules_linked_it(monkeypatch) -> None:
    monkeypatch.setattr(result, "_evidence", lambda conn, fact_id: None)
    tests = result._ordered_tests(None, [_fact("lab_order", "HbA1c", "u9")])  # type: ignore[arg-type]
    assert result._link_context(None, tests, []) == C.version()  # type: ignore[arg-type]
    assert result._link_context(None, [], []) is None  # type: ignore[arg-type]
    assert "lab_tests[].context" not in result.ABSENT


def test_fr6_a_diagnosis_keeps_its_raw_text_and_our_own_term(monkeypatch) -> None:
    monkeypatch.setattr(result, "_evidence", lambda conn, fact_id: None)
    (row,) = result._diagnoses(None, [_fact("condition", "T2DM", "u3")])  # type: ignore[arg-type]
    assert row["raw_text"] == "T2DM" and row["canonical_term"] == "Type 2 diabetes mellitus"


@pytest.mark.xfail(strict=True, reason=(
    "EX-S5 (open question): which test / reason pairs are acceptable must be approved by a "
    "clinician. The list was written by the engineer and is marked unreviewed; the 'does not "
    "fit' flag rests on it."))  # fmt: skip
def test_the_list_of_pairs_is_approved_by_a_clinician() -> None:
    assert C.bank()["review_status"].startswith("approved")


@pytest.mark.xfail(strict=True, reason=(
    "EX-S5 FR6: an external SNOMED CT / ICD code is still written into diagnoses[] whenever "
    "the built-in list has one. Showing it only for customers who enabled that adapter is "
    "SW-S6, which is not built."))  # fmt: skip
def test_fr6_external_codes_only_when_the_adapter_is_enabled(monkeypatch) -> None:
    monkeypatch.setattr(result, "_evidence", lambda conn, fact_id: None)
    (row,) = result._diagnoses(None, [_fact("condition", "T2DM", "u3")])  # type: ignore[arg-type]
    assert "external_codes" not in row
