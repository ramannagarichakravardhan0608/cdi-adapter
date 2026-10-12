"""EX-S4 - Read the lab preparation instructions: the acceptance criteria, one group each.

Every prescription here is made up. A "page" is the model's answer for it: the lines it read
under investigations and advice.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from cdi_adapter import result
from cdi_adapter.config import Settings
from cdi_adapter.extract import preparation as P
from cdi_adapter.extract.facts import facts_from_payload
from cdi_adapter.extract.prompt import PREPARATION_RULES, build_extraction_prompt
from cdi_adapter.validate import rules
from cdi_adapter.validate.service import gate

ROOT = Path(__file__).resolve().parents[1]


def _line(text: str, ev: str = "b1") -> dict[str, Any]:
    return {"text": text, "evidence": [ev], "confidence": 0.9}


def _page(tests: list[str], advice: list[str] | None = None, **more: Any) -> dict[str, Any]:
    lines = [_line(a, f"a{i}") for i, a in enumerate(advice or [], 1)]
    return {"confidence": 0.9, "medications": [],
            "investigations_advised": [_line(t, f"b{i}") for i, t in enumerate(tests, 1)],
            "advice": lines, **more}  # fmt: skip


def _read(payload: dict[str, Any]) -> dict[str, list[Any]]:
    facts = facts_from_payload("prescription", payload)
    return {kind: [f for f in facts if f.fact_type == kind]
            for kind in ("preparation", "lab_order", "advice")}  # fmt: skip


def _notes(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [P.describe(f.local_text, f.value_text) for f in _read(payload)["preparation"]]


def _findings(fact: Any, cited: str | None = None) -> list[str]:
    row = {"fact_type": "preparation", "local_text": fact.local_text,
           "value_text": fact.value_text}  # fmt: skip
    found = rules.check_preparation(row, [{"extracted_text": cited or fact.local_text}])
    assert all(f.severity == "blocker" for f in found)
    return [f.code for f in found]


# =====================================================================================
# AC1  GIVEN 'HbA1c, FBS - fasting 12 hrs' is written WHEN it is read
#      THEN FBS gets preparation fasting=12 hours; the original words are kept
# =====================================================================================

AC1 = _page(["HbA1c, FBS - fasting 12 hrs"])


def test_ac1_fbs_gets_fasting_twelve_hours() -> None:
    (note,) = _notes(AC1)
    assert (note["type"], note["value"], note["unit"]) == ("fasting", 12, "hours")
    assert note["applies_to"] == ["FBS"]  # the test it is written with - not HbA1c


def test_ac1_the_original_words_are_kept() -> None:
    (fact,) = _read(AC1)["preparation"]
    assert fact.local_text == "fasting 12 hrs"  # as written: not "12 hours", not "fasting: 12"
    assert (fact.value_num, fact.value_unit_ucum) == (12, "h")  # the structured value, beside
    assert fact.evidence == ["b1"]  # the line it was read from


def test_ac1_the_tests_are_still_the_two_tests_without_the_note() -> None:
    assert [f.local_text for f in _read(AC1)["lab_order"]] == ["HbA1c", "FBS"]


def test_ac1_it_is_the_same_when_the_reader_gave_the_two_tests_separately() -> None:
    (note,) = _notes(_page(["HbA1c", "FBS - fasting 12 hrs"]))
    assert (note["value"], note["applies_to"]) == (12, ["FBS"])


def test_ac1_the_result_links_the_note_to_fbs_only(monkeypatch) -> None:
    out = _result(monkeypatch, AC1)
    (note,) = out["preparation"]
    assert (note["text"], note["type"], note["amount"], note["unit"]) == (
        "fasting 12 hrs", "fasting", 12, "hours")  # fmt: skip
    assert note["applies_to"] == ["FBS"] and note["value"] == "fasting 12 hrs"
    refs = {t["as_written"]: t["preparation_refs"] for t in out["lab_tests"]}
    assert refs == {"HbA1c": [], "FBS": [0]}


# =====================================================================================
# AC2  GIVEN 'All tests in the morning, empty stomach' is written WHEN it is read
#      THEN one note is created that applies to all listed tests
# =====================================================================================

AC2 = _page(["CBC", "LFT", "TSH"], ["All tests in the morning, empty stomach"])


def test_ac2_one_note_that_applies_to_all() -> None:
    (note,) = _notes(AC2)
    assert note["applies_to"] == "all"
    assert note["text"] == "All tests in the morning, empty stomach"  # the words, whole


def test_ac2_it_is_not_left_in_the_advice_as_well() -> None:
    assert _read(AC2)["advice"] == [] and len(_read(AC2)["lab_order"]) == 3


def test_ac2_every_listed_test_points_at_the_one_note(monkeypatch) -> None:
    out = _result(monkeypatch, AC2)
    assert len(out["preparation"]) == 1
    assert [t["preparation_refs"] for t in out["lab_tests"]] == [[0], [0], [0]]


@pytest.mark.parametrize("words", ["All investigations fasting 10 hrs",
                                   "For all the above - overnight fasting",
                                   "All blood tests empty stomach"])  # fmt: skip
def test_ac2_other_ways_of_saying_all(words: str) -> None:
    (note,) = _notes(_page(["CBC", "LFT"], [words]))
    assert note["applies_to"] == "all"


# =====================================================================================
# AC3  GIVEN a note could belong to two tests WHEN it is read
#      THEN applies_to is 'unclear' and the item goes to review
# =====================================================================================

AC3 = _page(["FBS", "Lipid profile"], ["fasting 12 hrs"])


def test_ac3_a_note_on_its_own_with_two_tests_is_unclear() -> None:
    (note,) = _notes(AC3)
    assert note["applies_to"] == "unclear" and (note["type"], note["value"]) == ("fasting", 12)


def test_ac3_a_note_written_against_two_tests_at_once_is_unclear() -> None:
    (note,) = _notes(_page(["FBS/PPBS - fasting 12 hrs"]))
    assert note["applies_to"] == "unclear"


def test_ac3_it_goes_to_review_whatever_the_confidence() -> None:
    (fact,) = _read(AC3)["preparation"]
    assert _findings(fact) == ["preparation-applies-unclear"]
    found = rules.check_preparation({"fact_type": "preparation", "local_text": fact.local_text,
                                     "value_text": fact.value_text},
                                    [{"extracted_text": fact.local_text}])  # fmt: skip
    assert gate("preparation", 0.99, found, False, Settings()) == "in_review"


def test_ac3_an_unclear_note_is_linked_to_no_test(monkeypatch) -> None:
    out = _result(monkeypatch, AC3)
    assert out["preparation"][0]["applies_to"] == "unclear"
    assert [t["preparation_refs"] for t in out["lab_tests"]] == [[], []]


def test_ac3_with_one_test_ordered_the_note_can_only_be_that_tests() -> None:
    (note,) = _notes(_page(["Lipid profile"], ["Come fasting 10 hrs"]))
    assert note["applies_to"] == ["Lipid profile"]


def test_ac3_a_clear_note_is_not_held_by_this_rule() -> None:
    (fact,) = _read(AC1)["preparation"]
    assert _findings(fact) == []
    assert gate("preparation", 0.99, [], False, Settings()) == "auto_accepted"


# =====================================================================================
# AC4  GIVEN no preparation is written WHEN it is read
#      THEN the preparation list is empty and the system adds nothing
# =====================================================================================


@pytest.mark.parametrize("payload", [
    _page(["CBC", "LFT"]),
    _page(["FBS", "PPBS", "Lipid profile"], ["Rest for 3 days", "Review after 1 week"]),
    _page([], ["Drink plenty of fluids"]),
    {"confidence": 0.9, "medications": []},
])  # fmt: skip
def test_ac4_nothing_written_is_nothing_listed(payload: dict[str, Any]) -> None:
    assert _read(payload)["preparation"] == []
    assert "_preparation" not in P.with_preparation(payload)


def test_ac4_the_result_has_an_empty_list_and_no_links(monkeypatch) -> None:
    out = _result(monkeypatch, _page(["FBS", "Lipid profile"]))
    assert out["preparation"] == []
    assert [t["preparation_refs"] for t in out["lab_tests"]] == [[], []]


def test_ac4_the_tests_and_advice_are_untouched_when_there_is_no_note() -> None:
    payload = _page(["CBC", "LFT"], ["Rest for 3 days"])
    assert P.with_preparation(payload) is payload


# =====================================================================================
# AC5  GIVEN 'fasting 12 hrs' is read as '1Z hrs' WHEN it is checked
#      THEN the value goes to review (number check)
# =====================================================================================

AC5 = _page(["FBS - fasting 1Z hrs"])


def test_ac5_a_number_that_is_not_a_number_is_not_given_a_value() -> None:
    (note,) = _notes(AC5)
    assert (note["type"], note["value"]) == ("fasting", None)  # never "12" by guess, never "1"
    assert note["problems"] == ["number-unclear"]
    assert _read(AC5)["preparation"][0].local_text == "fasting 1Z hrs"  # kept as read


def test_ac5_it_goes_to_review() -> None:
    (fact,) = _read(AC5)["preparation"]
    assert _findings(fact) == ["preparation-number-unclear"]
    found = [rules.Finding("blocker", "preparation-number-unclear", "x")]
    assert gate("preparation", 0.99, found, False, Settings()) == "in_review"


@pytest.mark.parametrize("read", ["fasting l2 hrs", "fasting I2 hrs", "fasting 1? hrs",
                                  "fasting O8 hrs"])  # fmt: skip
def test_ac5_other_misread_digits_are_caught_or_not_taken_for_a_number(read: str) -> None:
    prep = P.parse(read)
    assert prep is None or prep.value is None  # never a number it did not clearly read


def test_ac5_a_number_that_differs_from_the_picture_is_held() -> None:
    """NFR1. The reader wrote 12; the line it cites says 10."""
    (fact,) = _read(AC1)["preparation"]
    assert _findings(fact, cited="FBS - fasting 10 hrs") == ["preparation-number-not-in-picture"]


def test_ac5_a_number_that_cannot_be_is_held() -> None:
    (fact,) = _read(_page(["FBS - fasting 72 hrs"]))["preparation"]
    assert _findings(fact) == ["preparation-number-implausible"]


# =====================================================================================
# AC6  NEW v3: GIVEN 'HbA1c' is ordered with no preparation written WHEN it is read
#      THEN no fasting is added by the system (red-team case)
# =====================================================================================

USUALLY_FASTING = ["HbA1c", "FBS", "Fasting blood sugar", "Lipid profile", "Fasting lipid profile",
                   "OGTT", "Iron studies", "Thyroid profile", "LFT"]  # fmt: skip


@pytest.mark.parametrize("test", USUALLY_FASTING)
def test_ac6_no_fasting_is_added_for_a_test_that_usually_needs_it(test: str) -> None:
    payload = _page([test])
    assert _read(payload)["preparation"] == []
    assert [f.local_text for f in _read(payload)["lab_order"]] == [test]  # and it is still a test


def test_ac6_the_word_fasting_in_a_tests_name_is_not_a_note() -> None:
    read = _read(_page(["Fasting blood sugar", "Fasting lipid profile"]))
    assert read["preparation"] == [] and len(read["lab_order"]) == 2


def test_ac6_nothing_in_the_rules_knows_what_a_test_usually_needs() -> None:
    """There is no table from a test to a preparation anywhere: a note comes only from words."""
    bank = P.bank()
    assert set(bank) == {"about", "version", "review_status", "types", "phrases",
                         "applies_to_all", "not_preparation", "plausible", "examples"}  # fmt: skip
    import io
    import tokenize

    source = Path(P.__file__).read_text(encoding="utf-8")
    names = {tok.string.lower() for tok in tokenize.generate_tokens(io.StringIO(source).readline)
             if tok.type == tokenize.NAME}  # the code's own words: no comments, no examples
    for word in ("hba1c", "lipid", "glucose", "usual", "standard", "typical"):
        assert not any(word in name for name in names)


def test_ac6_the_model_is_told_the_same() -> None:
    assert "never add a usual fasting time" in PREPARATION_RULES
    assert "ONLY when it is written" in PREPARATION_RULES
    prompt, _ = build_extraction_prompt("prescription", [{"id": "x", "text": "HbA1c"}])
    assert PREPARATION_RULES in prompt


def test_ac6_a_value_the_model_adds_without_words_for_it_is_not_taken() -> None:
    """The model answers with a preparation the page does not word: text "HbA1c" and a value
    of 8 hours. The value is never read from the model - only from the words - so nothing."""
    payload = _page(["HbA1c"], investigation_preparation=[
        {"text": "HbA1c", "type": "fasting", "value": 8, "unit": "hours",
         "applies_to": ["HbA1c"]}])  # fmt: skip
    assert _read(payload)["preparation"] == []


# =====================================================================================
# AC7  NEW v3: GIVEN an unrelated sentence mentions 'fasting' in another context
#      WHEN it is read THEN it is not turned into a preparation item for a test
# =====================================================================================


@pytest.mark.parametrize("sentence", [
    "Patient was fasting during Ramadan",
    "Fasting blood sugar 140 mg/dl",
    "H/o intermittent fasting",
    "Has been fasting since yesterday",
    "Fasting glucose was high last month",
    "Take tablets on empty stomach",
    "To be taken in the morning before food",
    "Avoid fasting, eat small frequent meals",
])  # fmt: skip
def test_ac7_fasting_in_another_context_is_not_a_preparation(sentence: str) -> None:
    payload = _page(["HbA1c", "CBC"], [sentence])
    read = _read(payload)
    assert read["preparation"] == []
    assert [f.local_text for f in read["advice"]] == [sentence]  # it stays what it was


def test_ac7_a_medicine_line_with_empty_stomach_is_a_medicine_not_a_preparation() -> None:
    read = facts_from_payload("prescription",
                              _page(["TSH", "CBC"], ["Tab. Thyronorm 50 on empty stomach"]))
    assert [f.fact_type for f in read if f.fact_type in ("preparation", "medication")] == [
        "medication"]  # fmt: skip
    assert P.parse("Tab. Thyronorm 50 on empty stomach") is None


def test_ac7_a_medicines_own_direction_is_never_looked_at() -> None:
    payload = _page(["TSH"], medications=[
        {"drug_name": "Tab. Thyronorm 50", "instructions": "empty stomach, morning"}])  # fmt: skip
    assert _read(payload)["preparation"] == []


def test_ac7_a_real_note_beside_such_a_sentence_is_still_read() -> None:
    payload = _page(["Lipid profile"], ["Patient was fasting during Ramadan",
                                        "Come after overnight fasting"])  # fmt: skip
    (note,) = _notes(payload)
    assert (note["text"], note["applies_to"]) == ("Come after overnight fasting",
                                                  ["Lipid profile"])  # fmt: skip


# =====================================================================================
# the requirements behind the criteria
# =====================================================================================


def test_fr6_the_types_are_a_fixed_list() -> None:
    types = P.bank()["types"]
    assert types == ["fasting", "timing", "diet", "hold_medicine", "collection",
                     "bring_documents", "other"]  # fmt: skip
    schema = json.loads((ROOT / "schemas" / "prescription.v3.json").read_text(encoding="utf-8"))
    item = schema["properties"]["investigation_preparation"]["items"]["properties"]
    assert item["type"]["enum"] == [*types, None]
    assert set(item) >= {"text", "type", "value", "unit", "applies_to", "evidence"}


@pytest.mark.parametrize("example", P.bank()["examples"], ids=lambda e: e["text"])
def test_fr7_every_phrase_of_the_phrase_bank_reads_as_it_says(example: dict[str, Any]) -> None:
    prep = P.parse(example["text"])
    if example["type"] is None:
        assert prep is None
        return
    assert prep is not None and prep.type == example["type"]
    assert prep.value == example.get("value") and prep.unit == example.get("unit")
    assert list(prep.problems) == ([example["problem"]] if example.get("problem") else [])
    if "range" in example:
        assert [prep.low, prep.high] == example["range"]


def test_fr7_the_phrase_bank_has_fifteen_or_more_fixtures_and_says_it_is_not_approved() -> None:
    bank = P.bank()
    assert len([e for e in bank["examples"] if e["type"]]) >= 15
    assert len([e for e in bank["examples"] if e["type"] is None]) >= 5  # what is NOT one
    assert bank["review_status"].startswith("unreviewed")


def test_fr1_each_type_can_be_read() -> None:
    seen = {P.parse(e["text"]).type for e in P.bank()["examples"] if e["type"]}  # type: ignore[union-attr]
    assert seen == {"fasting", "timing", "diet", "hold_medicine", "collection",
                    "bring_documents"}  # fmt: skip


def test_a_written_range_is_kept_as_a_range_not_made_one_number(monkeypatch) -> None:
    out = _result(monkeypatch, _page(["FBS - fasting 10-12 hrs"]))
    (note,) = out["preparation"]
    assert note["amount"] is None and note["range"] == [10, 12] and note["unit"] == "hours"


def test_the_database_takes_the_new_kind_of_fact() -> None:
    migration = (ROOT / "db" / "alembic" / "versions" / "0038_preparation.py").read_text(
        encoding="utf-8")  # fmt: skip
    assert 'down_revision = "0037"' in migration and "'preparation'" in migration


def test_the_reviewer_shows_the_words_the_type_the_value_and_what_it_applies_to() -> None:
    from cdi_adapter.webapp.reviewer_page import SCRIPT

    assert "['preparation', 'Lab preparation']" in SCRIPT
    assert "t === 'preparation' ? COLS.prep" in SCRIPT
    assert "unclear - which test?" in SCRIPT and "all tests" in SCRIPT


@pytest.mark.xfail(strict=True, reason=(
    "EX-S4 FR7: the phrase bank was written by the engineer. No clinician has supplied the "
    "real phrases used on the clinic's prescriptions or approved the types, and only English "
    "is covered (the story's open question)."))  # fmt: skip
def test_fr7_the_phrase_bank_is_approved_by_a_clinician() -> None:
    assert P.bank()["review_status"].startswith("approved")


# ------------------------------------------------------------------ helper


def _result(monkeypatch, payload: dict[str, Any]) -> dict[str, Any]:
    """The result's ``lab_tests`` and ``preparation`` for a page, built as ``result.build``
    builds them, with the facts given a status and a proof."""
    from cdi_adapter.terminology.service import bind_fact

    proof = {"page": 1, "bbox": [10, 10, 300, 30], "observation_ids": ["u1"]}
    monkeypatch.setattr(result, "_evidence", lambda conn, fact_id: proof)
    facts = []
    for i, d in enumerate(facts_from_payload("prescription", payload)):
        row = {"id": i, "fact_type": d.fact_type, "local_text": d.local_text,
               "value_text": d.value_text, "value_num": d.value_num,
               "review_state": "auto_accepted", "confidence_overall": 0.97}  # fmt: skip
        bound = bind_fact(row)
        facts.append({**row, "code": bound["code"], "code_system": bound["code_system"],
                      "code_display": bound["code_display"]})  # fmt: skip
    tests = result._ordered_tests(None, facts)  # type: ignore[arg-type]
    notes = result._preparation(None, facts)  # type: ignore[arg-type]
    result._link_preparation(tests, notes)
    return {"lab_tests": tests, "preparation": notes}
