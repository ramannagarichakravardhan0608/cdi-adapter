"""Tests ordered inside an advice line, and a medicine line's slots (owner, 2026-10-10).

A handwritten diabetes prescription ended "To review after 2 wks with HbA1c / FBS / PPBS /
S. Lipase, TSH / FT4": the line stayed one piece of advice and no test was listed. Its medicines
had "(25)" as a strength nobody parsed, "SC" as a frequency and "BBF" as a route. ``PAYLOAD`` is
shaped like what the model wrote for that page; the page and the patient are not in the
repository, and no rule here knows these strings.
"""

from __future__ import annotations

from typing import Any

import pytest

from cdi_adapter.extract import facts as F
from cdi_adapter.extract.facts import facts_from_payload


def _line(text: str, ev: str = "b1") -> dict[str, Any]:
    return {"text": text, "evidence": [ev], "confidence": 0.9}


def _med(name: str, **fields: str) -> dict[str, Any]:
    return {"drug_name": name, "evidence": ["b2"], "confidence": 0.9, "dose": "", "route": "",
            "duration": "", "strength": "", "frequency": "", "instructions": "",
            **fields}  # fmt: skip


REVIEW = "To review after 2wks of HbA1c/FBS/PPBS/S LIPASE [TSML/PTA]"


def _orders(*advice: str, have: list[str] | None = None) -> list[tuple[str, float]]:
    payload = {"advice": [_line(a, f"b{i}") for i, a in enumerate(advice, 1)],
               "investigations_advised": [_line(h) for h in have or []]}  # fmt: skip
    facts = facts_from_payload("prescription", payload)
    return [(f.local_text, f.confidence_extract) for f in facts if f.fact_type == "lab_order"]


# ------------------------------------------------------------- tests written in advice


def test_a_list_of_tests_in_an_advice_line_is_listed_as_investigations() -> None:
    orders = dict(_orders(REVIEW))
    assert list(orders)[:3] == ["HbA1c", "FBS", "PPBS"]
    assert orders["HbA1c"] == orders["FBS"] == orders["PPBS"] == 0.9


def test_a_part_of_the_list_the_lookup_does_not_know_is_kept_for_the_reviewer() -> None:
    """"TSML" is "TSH" misread and "PTA" is not a test the list knows. Both were written in
    the list of tests, so both are shown - at a low confidence, never dropped and never
    corrected to a guess. ("S LIPASE" is in the lab-test master since EX-S3.)"""
    orders = dict(_orders(REVIEW))
    assert {"S LIPASE", "TSML", "PTA"} <= set(orders)
    assert orders["TSML"] == orders["PTA"] == 0.5 and orders["S LIPASE"] == 0.9


def test_the_advice_line_itself_stays() -> None:
    facts = facts_from_payload("prescription", {"advice": [_line(REVIEW)]})
    assert [f.local_text for f in facts if f.fact_type == "advice"] == [REVIEW]


def test_the_tests_carry_the_evidence_of_the_line_they_were_read_from() -> None:
    facts = facts_from_payload("prescription", {"advice": [_line(REVIEW, "b33")]})
    assert {tuple(f.evidence) for f in facts if f.fact_type == "lab_order"} == {("b33",)}


def test_a_test_already_under_investigations_is_not_added_twice() -> None:
    orders = [t for t, _ in _orders("Repeat CBC, LFT after 1 week", have=["CBC"])]
    assert orders.count("CBC") == 1 and "LFT" in orders


@pytest.mark.parametrize("line", ["Repeat HbA1c after 3 months", "Check TSH in 6 weeks",
                                  "Get CBC done"])  # fmt: skip
def test_one_test_counts_when_the_line_says_it_is_a_test(line: str) -> None:
    assert len(_orders(line)) == 1


@pytest.mark.parametrize("line", [
    "Take calcium daily", "Vitamin D rich diet", "Rest for 3-5 days",
    "Drink fluids / ORS / soup", "Avoid sugar, salt and oil", "Review after 5 days",
    "To continue other meds as per Cardiologist's advice", "Walk 30 minutes, morning and evening",
    "Calcium and Vitamin D supplements", "Take iron, calcium and vitamin D",
])  # fmt: skip
def test_ordinary_advice_orders_nothing(line: str) -> None:
    assert _orders(line) == []
    payload = {"advice": [_line(line)]}
    assert F.tests_in_advice(payload) is payload


def test_only_the_list_is_read_not_the_words_before_it() -> None:
    orders = [t for t, _ in _orders("To review after 2 weeks with FBS / PPBS")]
    assert orders == ["FBS", "PPBS"]


# ------------------------------------------------------------- the medicine's slots


def _medication(**fields: str) -> Any:
    (fact,) = facts_from_payload("prescription", {"medications": [_med("T Xyz", **fields)]})
    return fact.medication


def test_a_strength_in_brackets_is_a_strength() -> None:
    assert _medication(strength="(25)").strength_num == 25
    m = _medication(strength="(5mg)")
    assert (m.strength_num, m.strength_unit) == (5, "mg")


def test_a_strength_of_nothing_is_no_strength() -> None:
    assert _medication(strength="00").strength_num is None


def test_a_route_in_the_frequency_slot_is_the_route() -> None:
    m = _medication(frequency="SC")
    assert (m.route, m.frequency_code) == ("SC", None)


@pytest.mark.parametrize("said", ["BBF", "before lunch", "(1hr. before breakfast)", "in"])
def test_what_is_not_a_route_is_kept_as_an_instruction(said: str) -> None:
    m = _medication(route=said, frequency="OD")
    assert m.route is None and said.strip("()") in (m.instructions or "")
    assert m.frequency_per_day == 1


def test_a_real_route_is_left_alone() -> None:
    assert _medication(route="IM").route == "IM"
    assert _medication(route="/ nostril").route == "nostril"


def test_the_days_pattern_given_as_the_dose_is_the_frequency() -> None:
    m = _medication(dose="1-0-1", frequency="X 10 days")
    assert (m.dose_num, m.dose_unit_ucum) == (None, None)  # it was "1" with the unit "-0-1"
    assert (m.frequency_code, m.frequency_per_day, m.duration_days) == ("1-0-1", 2, 10)


def test_what_stood_in_the_frequency_is_kept_when_it_is_not_days() -> None:
    m = _medication(dose="9-0-9", frequency="X Day 1")
    assert m.frequency_code == "9-0-9" and "X Day 1" in (m.instructions or "")


def test_a_dose_written_in_front_of_the_frequency_is_the_dose() -> None:
    m = _medication(frequency="1 tab OD")
    assert (m.dose_num, m.dose_unit_ucum, m.frequency_code) == (1, "tab", "OD")


def test_a_dose_and_a_frequency_already_in_place_are_untouched() -> None:
    m = _medication(dose="1 tab", frequency="BD", duration="5 days")
    assert (m.dose_num, m.frequency_code, m.frequency_per_day, m.duration_days) == (1, "BD", 2, 5)
