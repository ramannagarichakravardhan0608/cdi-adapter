"""Medicines stay out of advice, and a medicine's name stays a name (owner, 2026-10-10).

A handwritten OPD prescription whose Rx section mixes home care and drugs came out with every
medicine listed twice - once under Medications and once under Advice & follow-up - and with
names such as "Sonpraz 1" and "Audeflu (#5) 1". ``PAYLOAD`` is what the model wrote for that
page (the patient's details left out); the page itself is not in the repository.
"""

from __future__ import annotations

from typing import Any

import pytest

from cdi_adapter.extract.facts import (
    advice_kind,
    facts_from_payload,
    split_advice,
    tidy_medication,
)
from cdi_adapter.extract.prompt import MEDICATION_RULES, build_extraction_prompt
from cdi_adapter.webapp.reviewer_page import SCRIPT


def _line(text: str, ev: str) -> dict[str, Any]:
    return {"text": text, "evidence": [ev], "confidence": 0.9}


def _med(name: str, ev: str, **fields: str) -> dict[str, Any]:
    return {"drug_name": name, "evidence": [ev], "confidence": 0.9, "dose": "", "frequency": "",
            "duration": "", "route": "", "strength": "", "instructions": "", **fields}  # fmt: skip


PAYLOAD: dict[str, Any] = {
    "confidence": 0.9,
    "advice": [
        _line("Gargle & warm water & betadine steam inhalation", "b16"),
        _line("Rest for 3-5 days", "b16"),
        _line("Tab. sas cefixime", "b26"),
        _line("Cap. Sonpraz 1 cap BPR x 5 days", "b27"),
        _line("Tab. Montina fx 1 tab at bedtime x 5 days", "b28"),
        _line("Cap. Audeflu (#5) 1 cap 5 days (after meals)", "b30"),
        _line("Rel. Zincoilt 1 tab after lunch x 1st day", "b32"),
    ],
    "medications": [
        _med("Tab. PCM - 650", "b25", strength="650"),
        _med("Tab. sas cefixime", "b26"),
        _med("Cap. Sonpraz 1 cap", "b27", dose="1 cap", frequency="BPR", duration="5 days"),
        _med("Tab. Montina fx 1 tab", "b28", dose="1 tab", frequency="at bedtime",
             duration="5 days"),  # fmt: skip
        _med("Cap. Audeflu (#5) 1 cap", "b30", dose="1 cap", frequency="5 days",
             duration="(after meals)"),  # fmt: skip
        _med("Rel. Zincoilt 1 tab", "b32", dose="1 tab", frequency="after lunch",
             duration="1st day"),  # fmt: skip
    ],
    "investigations_advised": [_line("Blood for fever profile", "b23")],
    "vitals": [{"name": "Pulse", "value": "110/70 mmhg", "evidence": ["b21"]}],
}
MEDICINES = ("PCM", "Sas Cefixime", "Sonpraz", "Montina Fx", "Audeflu (#5)", "Zincoilt")


def _facts(kind: str) -> list[Any]:
    return [f for f in facts_from_payload("prescription", PAYLOAD) if f.fact_type == kind]


# ------------------------------------------------------------- medicines are not advice


def test_no_medicine_is_left_under_advice() -> None:
    advice = [f.local_text for f in _facts("advice")]
    assert advice == ["Gargle & warm water & betadine steam inhalation", "Rest for 3-5 days"]


def test_every_medicine_is_listed_once_under_medications() -> None:
    names = [f.medication.drug_text for f in _facts("medication")]
    assert len(names) == len(MEDICINES)
    for want, got in zip(MEDICINES, names, strict=True):
        assert got.startswith(want)


def test_a_medicine_written_only_under_advice_is_moved_not_lost() -> None:
    payload = {"advice": [_line("Tab. Dolo 650 1 tab sos", "b9"), _line("Drink fluids", "b10")],
               "medications": []}  # fmt: skip
    facts = facts_from_payload("prescription", payload)
    med = next(f for f in facts if f.fact_type == "medication")
    assert med.medication.drug_text.startswith("Dolo") and med.medication.prn
    assert med.confidence_extract <= 0.5  # low: a reviewer looks at it
    assert [f.local_text for f in facts if f.fact_type == "advice"] == ["Drink fluids"]


@pytest.mark.parametrize("line", ["Rest for 3-5 days", "Gargle with warm water",
                                  "Betadine steam inhalation", "Drink enough fluids",
                                  "Take ORS as instructed", "Capsicum and spicy food to be avoided",
                                  "Injection site to be kept dry"])  # fmt: skip
def test_home_care_is_never_taken_for_a_medicine(line: str) -> None:
    payload = {"advice": [_line(line, "b1")], "medications": [_med("Tab. PCM - 650", "b2")]}
    assert split_advice(payload) is payload


def test_advice_that_shares_a_line_with_a_medicine_but_not_its_name_is_kept() -> None:
    payload = {"advice": [_line("Plenty of fluids", "b2")],
               "medications": [_med("Tab. PCM - 650", "b2")]}  # fmt: skip
    assert split_advice(payload) is payload


# ------------------------------------------------------------- the name is only the name


@pytest.mark.parametrize(("written", "name", "dose"), [
    ("Cap. Sonpraz 1 cap", "Cap. Sonpraz", "1 cap"),
    ("Tab. Montina fx 1 tab at bedtime", "Tab. Montina fx", "1 tab"),
    ("= Tab. Zincovit 1 tab after lunch x 15 days", "Tab. Zincovit", "1 tab"),
    ("Syp. Ascoril 2 tsp TDS", "Syp. Ascoril", "2 tsp"),
    ("Tab. Glycomet GP 1", "Tab. Glycomet GP 1", ""),  # "1" is part of this brand's name
    ("Inj. Human Mixtard 30/70 PEN 3 ML", "Inj. Human Mixtard 30/70 PEN 3 ML", ""),
])  # fmt: skip
def test_the_name_ends_where_the_dose_begins(written: str, name: str, dose: str) -> None:
    item = tidy_medication({"drug_name": written})
    assert item["drug_name"] == name and str(item.get("dose") or "") == dose


def test_dose_and_days_are_not_part_of_the_name() -> None:
    for f in _facts("medication"):
        assert not f.medication.drug_text.endswith(" 1")
        assert "days" not in f.medication.drug_text.lower()
        assert " tab" not in f.local_text.lower()[4:] and " cap" not in f.local_text.lower()[4:]


def test_each_medicine_keeps_its_own_dose_and_duration() -> None:
    meds = {f.medication.drug_text: f.medication for f in _facts("medication")}
    montina = meds["Montina Fx"]
    assert (montina.dose_num, montina.dose_unit_ucum, montina.duration_days) == (1, "tab", 5)
    assert montina.frequency_code == "at bedtime"
    assert montina.instructions == "1 tab at bedtime x 5 days"
    audeflu = meds["Audeflu (#5)"]
    assert audeflu.duration_days == 5 and audeflu.frequency_code is None  # "5 days" is no frequency
    assert audeflu.instructions == "1 cap after meals x 5 days"
    assert meds["Sonpraz"].instructions == "1 cap BPR x 5 days"
    assert meds["Zincoilt"].instructions == "1 tab after lunch x 1st day"


def test_nothing_is_filled_in_that_was_not_written() -> None:
    cefixime = next(f.medication for f in _facts("medication")
                    if f.medication.drug_text == "Sas Cefixime")  # fmt: skip
    assert (cefixime.dose_num, cefixime.frequency_code, cefixime.duration_days,
            cefixime.instructions) == (None, None, None, None)  # fmt: skip
    zinc = next(f.medication for f in _facts("medication") if f.medication.drug_text == "Zincoilt")
    assert zinc.duration_days is None  # "1st day" is not a number of days: left for the reviewer


def test_a_misread_form_word_is_not_part_of_the_name_but_stays_as_written() -> None:
    zinc = next(f for f in _facts("medication") if f.medication.drug_text == "Zincoilt")
    assert zinc.local_text == "Rel. Zincoilt"  # as written, for the reviewer
    vit = facts_from_payload("prescription", {"medications": [_med("Vit. D3 60000 IU", "b1")]})
    assert vit[0].medication.drug_text.startswith("Vit")


def test_the_evidence_of_every_medicine_is_kept() -> None:
    assert [f.evidence for f in _facts("medication")] == [[b] for b in
                                                          ("b25", "b26", "b27", "b28", "b30",
                                                           "b32")]  # fmt: skip


# ------------------------------------------------------------- the four categories


def test_tests_are_their_own_category() -> None:
    assert [f.local_text for f in _facts("lab_order")] == ["Blood for fever profile"]


@pytest.mark.parametrize(("line", "kind"), [
    ("Rest for 3-5 days", "advice"), ("Drink enough fluids", "advice"),
    ("Gargle with warm water", "advice"), ("Review after 5 days", "follow_up"),
    ("R/w with reports", "follow_up"), ("Follow up on 17/08/2026", "follow_up"),
    ("Next visit after 2 weeks", "follow_up"), ("Re-check BP in 1 week", "follow_up"),
])  # fmt: skip
def test_follow_up_is_told_apart_from_home_care(line: str, kind: str) -> None:
    assert advice_kind(line) == kind


def test_the_reviewer_page_has_the_four_sections() -> None:
    for section in ("['medication', 'Medications']", "['advice', 'Advice & home care']",
                    "['follow_up', 'Follow-up instructions']",
                    "['lab_order', 'Investigations & tests']"):  # fmt: skip
        assert section in SCRIPT
    assert "Advice & follow-up" not in SCRIPT
    # a medicine is drawn under Medications whatever category it carries
    assert "e.element_type === 'medication' ? 'medication' : e.category || e.element_type" in SCRIPT


def test_accept_reject_edit_and_details_are_still_on_the_page() -> None:
    for call in ("/api/facts/${fid}/review", "action: 'correct'", "function editFact(",
                 "/api/reviewer/items/${id}/approve"):  # fmt: skip
        assert call in SCRIPT


def test_a_blood_pressure_written_under_pulse_is_a_blood_pressure() -> None:
    bp = _facts("vital_sign")
    assert [(f.local_text, f.value_text) for f in bp] == [("blood pressure", "110/70")]


def test_a_height_in_kilograms_is_not_a_height() -> None:
    """The empty "Height:" line came out as "77 kg", the weight written on the line below."""
    payload = {"vitals": [{"name": "Height", "value": "77 kg"},
                          {"name": "Weight", "value": "77 kg"}]}  # fmt: skip
    facts = facts_from_payload("prescription", payload)
    assert [(f.local_text, f.value_num) for f in facts] == [("body weight", 77.0)]


# ------------------------------------------------------------- the prompt says so too


def test_the_prompt_tells_the_model_both_rules() -> None:
    assert "drug_name holds the name only" in MEDICATION_RULES
    assert "never repeat it under advice" in MEDICATION_RULES
    prompt, _ = build_extraction_prompt("prescription", [{"id": "x", "text": "Tab. PCM"}])
    assert MEDICATION_RULES in prompt
