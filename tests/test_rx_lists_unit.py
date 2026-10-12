"""The entries of a written list come out one item each (owner, 2026-10-10).

A handwritten prescription on a printed "vaccination advised" sheet came out with two vaccines
as four lines, "Bladder Training" as "17 Bladder Training", a medicine read as "Ing ..." and
every tablet's dose as "1x1" with "- 30d" as its frequency. ``PAYLOAD`` is what the model wrote
for that page; the page itself, and the patient, are not in the repository.

Nothing here knows those strings: the rules are about the shape of a line.
"""

from __future__ import annotations

from typing import Any

import pytest

from cdi_adapter.extract.facts import facts_from_payload, tidy_lists, tidy_medication


def _line(text: str, ev: str) -> dict[str, Any]:
    return {"text": text, "evidence": [ev], "confidence": 0.9}


def _med(name: str, ev: str, strength: str, frequency: str) -> dict[str, Any]:
    return {"drug_name": name, "evidence": [ev], "confidence": 0.9, "dose": "1x1", "route": "PO",
            "duration": "", "strength": strength, "frequency": frequency,
            "instructions": ""}  # fmt: skip


PAYLOAD: dict[str, Any] = {
    "confidence": 0.9,
    "advice": [
        _line("Post op check", "b19"), _line("June 2023", "b20"), _line("RFA", "b21"),
        _line("17 Bladder Training", "b22"), _line("17 Kegel exercise", "b23"),
        _line("6 Lab - Physiotherapy", "b24"), _line("Stop U Days", "b35"),
    ],
    "medications": [
        _med("Tab Sodrecept", "b25", "(5mg)", "- 30d"),
        _med("Tab Mira", "b26", "(25)", "- 30d"),
        _med("Tab Peg SR", "b27", "(75)", "- 30d Night"),
        _med("Ing Resunex PFS", "b28", "1mg", "- 30d + 10"),
    ],
    "investigations_advised": [
        _line("Inj. Influenza Inactivated Vaccine", "b15"),
        _line("0.5 ml IM Deltoid every year", "b16"),
        _line("Inj. Pneumococcal Vaccine", "b17"),
        _line("0.5ml IM Deltoid every 5 years", "b18"),
    ],
}  # fmt: skip


def _facts(kind: str) -> list[Any]:
    return [f for f in facts_from_payload("prescription", PAYLOAD) if f.fact_type == kind]


# ------------------------------------------------------------- a line that carries on


def test_an_amount_line_is_joined_to_the_entry_above_it() -> None:
    tests = _facts("lab_order")
    assert [f.local_text for f in tests] == [
        "Inj. Influenza Inactivated Vaccine 0.5 ml IM Deltoid every year",
        "Inj. Pneumococcal Vaccine 0.5ml IM Deltoid every 5 years",
    ]
    assert [f.evidence for f in tests] == [["b15", "b16"], ["b17", "b18"]]  # both lines kept


def test_a_date_on_its_own_is_joined_to_the_entry_above_it() -> None:
    advice = [f.local_text for f in _facts("advice")]
    assert "Post op check June 2023" in advice and "June 2023" not in advice


@pytest.mark.parametrize("line", ["Review after 5 days", "CBC", "2 glasses of water at night",
                                  "10 minutes of walking", "3 months later HbA1c",
                                  "Rest"])  # fmt: skip
def test_an_entry_of_its_own_is_never_joined(line: str) -> None:
    payload = {"advice": [_line("Drink fluids", "b1"), _line(line, "b2")]}
    assert tidy_lists(payload) is payload


def test_the_first_entry_is_never_joined_to_anything() -> None:
    payload = {"advice": [_line("0.5 ml at night", "b1"), _line("Rest", "b2")]}
    assert tidy_lists(payload) is payload


def test_a_joined_entry_takes_the_lower_confidence() -> None:
    payload = {"advice": [{"text": "Vaccine", "confidence": 0.9, "evidence": ["b1"]},
                          {"text": "0.5 ml every year", "confidence": 0.4,
                           "evidence": ["b2"]}]}  # fmt: skip
    (item,) = tidy_lists(payload)["advice"]
    assert item["confidence"] == 0.4 and item["evidence"] == ["b1", "b2"]


# ------------------------------------------------------------- a list mark read as digits


def test_a_number_repeated_in_front_of_entries_is_the_lists_mark() -> None:
    advice = [f.local_text for f in _facts("advice")]
    assert "Bladder Training" in advice and "Kegel exercise" in advice
    assert not any(a.startswith("17 ") for a in advice)


def test_a_number_seen_once_is_left_alone() -> None:
    """"6 Lab - Physiotherapy": nothing in the text says the 6 is not written. It stays, and the
    line stays held for the reviewer."""
    assert "6 Lab - Physiotherapy" in [f.local_text for f in _facts("advice")]


def test_a_numbered_list_keeps_its_numbers() -> None:
    payload = {"advice": [_line("1 Rest", "b1"), _line("2 Fluids", "b2"), _line("3 Walk", "b3")]}
    assert tidy_lists(payload) is payload


def test_amounts_that_happen_to_repeat_inside_a_line_are_untouched() -> None:
    payload = {"advice": [_line("Walk 30 minutes", "b1"), _line("Sleep 30 minutes more", "b2")]}
    assert tidy_lists(payload) is payload


def test_every_advice_line_stays_for_the_reviewer_and_none_is_added() -> None:
    advice = [f.local_text for f in _facts("advice")]
    assert advice == ["Post op check June 2023", "RFA", "Bladder Training", "Kegel exercise",
                      "6 Lab - Physiotherapy", "Stop U Days"]  # fmt: skip
    assert [f.evidence for f in _facts("advice")][2:4] == [["b22"], ["b23"]]


# ------------------------------------------------------------- the medicine lines


def test_inj_spelt_ing_is_the_form_not_the_name() -> None:
    inj = _facts("medication")[3]
    assert inj.local_text == "Ing Resunex PFS"  # as read, for the reviewer
    assert inj.medication.drug_text == "Resunex" and inj.medication.form == "injection"


def test_a_name_that_only_looks_like_a_misread_form_is_kept() -> None:
    (fact,) = facts_from_payload("prescription", {"medications": [{"drug_name": "Ing"}]})
    assert fact.medication.drug_text == "Ing"


def test_an_injection_is_not_given_by_mouth() -> None:
    meds = [f.medication for f in _facts("medication")]
    assert meds[3].route != "PO"
    assert [m.route for m in meds[:3]] == ["PO", "PO", "PO"]  # a tablet's route is left alone


def test_one_by_one_is_a_dose_and_a_frequency() -> None:
    for m in (f.medication for f in _facts("medication")):
        assert (m.dose_num, m.dose_unit_ucum) == (1, None)
        assert (m.frequency_code, m.frequency_per_day) == ("1x1", 1)


@pytest.mark.parametrize(("dose", "per_day"), [("1x2", 2), ("1 x 3", 3), ("2x1", 1)])
def test_n_by_m(dose: str, per_day: int) -> None:
    item = {"drug_name": "Tab X", "dose": dose, "frequency": ""}
    (fact,) = facts_from_payload("prescription", {"medications": [item]})
    assert fact.medication.frequency_per_day == per_day
    assert fact.medication.dose_num == float(dose[0])


def test_days_written_after_an_arrow_are_the_duration() -> None:
    meds = [f.medication for f in _facts("medication")]
    assert [m.duration_days for m in meds] == [30, 30, 30, 30]
    assert meds[2].instructions == "1x1 Night x 30d"  # "Night" says when, and is kept
    assert meds[3].instructions == "1x1 x 30d + 10"  # what follows the days is kept as written


def test_a_real_frequency_is_not_mistaken_for_days() -> None:
    item = tidy_medication({"drug_name": "Tab X", "dose": "1 tab", "frequency": "BD",
                            "duration": "5 days"})  # fmt: skip
    assert (item["frequency"], item["duration"]) == ("BD", "5 days")
