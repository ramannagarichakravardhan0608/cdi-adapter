"""The Reviewer shows the prescribing doctor (owner, 2026-10-10: it showed nothing).

The model's answer has the doctor's name (``prescriber``), but only facts reached the page.
The letterheads below are shaped like real ones; the names are made up.
"""

from __future__ import annotations

from cdi_adapter.extract.doctor import doctor_of
from cdi_adapter.webapp.reviewer_page import SCRIPT

LETTERHEAD = ["City Sugar Clinics", "RAVI KUMAR (40 Y / MALE)", "DR. ANITA SEN ROY",
              "MD.PHYSICIAN, DIP IN FAMILY MEDICINE", "CCEBDM(DIABETES)", "General Physician",
              "Reg No. Regd No 12345 ( WBMC)", "Vital Signs", "Rx", "Tab. PCM - 650"]  # fmt: skip


def test_the_name_the_model_read_is_shown_with_the_letterhead_beside_it() -> None:
    doctor = doctor_of({"prescriber": "DR. ANITA SEN ROY"}, LETTERHEAD)
    assert doctor == {"name": "DR. ANITA SEN ROY", "source": "extraction",
                      "qualification": "MD.PHYSICIAN, DIP IN FAMILY MEDICINE",
                      "registration": "Reg No. Regd No 12345 ( WBMC)"}  # fmt: skip


def test_without_a_name_from_the_model_the_dr_line_of_the_page_is_used() -> None:
    lines = ["Dr. Arun Mehta", "MBBS (PAT) MD(PAT) Internal Medicine", "Consultant Physician"]
    doctor = doctor_of({"_absent": {"prescriber": "not on the page"}}, lines)
    assert (doctor["name"], doctor["source"]) == ("Dr. Arun Mehta", "letterhead")
    assert doctor["qualification"] == "MBBS (PAT) MD(PAT) Internal Medicine"
    assert doctor["registration"] is None  # not on the page: not filled in


def test_a_page_with_no_doctor_gives_none() -> None:
    assert doctor_of({}, ["Tab X 1 tab", "Rest", "Drink water"]) == {
        "name": None, "qualification": None, "registration": None, "source": None}  # fmt: skip
    assert doctor_of(None, [])["name"] is None


def test_a_patient_line_or_a_drug_is_never_taken_for_the_doctor() -> None:
    lines = ["Mr. Dravid Rao (68/M)", "Drink fluids", "Tab. Drotin 40", "Dry cough since 3 days"]
    assert doctor_of({}, lines)["name"] is None


def test_the_second_doctors_block_is_not_mixed_into_the_first() -> None:
    lines = ["Dr. A Sen", "Dr. B Roy", "MBBS, MD", "Reg No 999"]
    doctor = doctor_of({"prescriber": "Dr. A Sen"}, lines)
    assert (doctor["qualification"], doctor["registration"]) == (None, None)


def test_only_the_lines_beside_the_name_are_read() -> None:
    lines = ["Dr. A Sen", *["line"] * 8, "MD", "Reg No 4"]
    doctor = doctor_of({"prescriber": "Dr. A Sen"}, lines)
    assert (doctor["qualification"], doctor["registration"]) == (None, None)


def test_a_prescriber_given_as_an_object_is_read() -> None:
    assert doctor_of({"prescriber": {"name": "Dr. A Sen"}}, [])["name"] == "Dr. A Sen"


def test_the_page_draws_the_doctor_and_says_when_none_was_read() -> None:
    assert "function doctorLine(doc)" in SCRIPT and "${doctorLine(d.doctor)}" in SCRIPT
    assert "Doctor: not read from this page" in SCRIPT
    assert "esc(doc.name)" in SCRIPT  # escaped like every other value on the page
