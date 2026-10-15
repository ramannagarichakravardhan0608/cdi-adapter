"""EX-S1 - Read the patient details: the acceptance criteria, one group each.

Every patient here is made up. The "page" is a list of OCR lines shaped like a clinic's
letterhead prescription; the "model's answer" is the ``patient`` block of a prescription.v3
payload. No real prescription or patient is in this file.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from cdi_adapter import result
from cdi_adapter.config import Settings
from cdi_adapter.extract import patient as P
from cdi_adapter.extract import service as X
from cdi_adapter.extract.facts import facts_from_payload
from cdi_adapter.extract.prompt import PATIENT_RULES, build_extraction_prompt
from cdi_adapter.validate import rules
from cdi_adapter.validate.service import gate

ROOT = Path(__file__).resolve().parents[1]
TODAY = date(2026, 10, 12)

PAGE = [
    "City Care Clinic",                                   # b1
    "Dr. Meera Nair  MBBS, MD",                           # b2
    "Clinic phone: 033 4000 1234",                        # b3
    "Name: Ravi Kumar      Age: 42 Y      Sex: Male",     # b4
    "Mobile: 98765 43210     UHID: CC-10293",             # b5
    "Rx",                                                 # b6
    "Tab. Paracetamol 650 1-0-1 x 3 days",                # b7
]  # fmt: skip
BLOCKS = [{"id": f"u{i}", "text": t, "page_id": "p1", "ocr_conf": 0.95,
           "bbox": [10, 20 * i, 500, 20 * i + 18]} for i, t in enumerate(PAGE, 1)]  # fmt: skip


def _payload(**patient: Any) -> dict[str, Any]:
    return {"confidence": 0.9, "medications": [],
            "patient": {"evidence": ["b4", "b5"], **patient}}  # fmt: skip


def _details(payload: dict[str, Any]) -> dict[str, Any]:
    return {f.local_text: f for f in facts_from_payload("prescription", payload)
            if f.fact_type == "patient_detail"}  # fmt: skip


def _findings(label: str, value: str, cited: str, others: dict[str, str] | None = None,
              on: date = TODAY) -> list[str]:  # fmt: skip
    fact = {"fact_type": "patient_detail", "local_text": label, "value_text": value}
    found = rules.check_patient_detail(fact, [{"extracted_text": cited}], others, on)
    assert all(f.severity == "blocker" for f in found)  # a failure always goes to a person
    return [f.code for f in found]


# =====================================================================================
# AC1  GIVEN a prescription with name, age, sex and phone WHEN it is read
#      THEN all four are saved with proof and status
# =====================================================================================

FOUR = _payload(name="Ravi Kumar", age_text="42 Y", sex="Male", phone="98765 43210")


def test_ac1_each_of_the_four_is_one_fact_with_the_value_as_written() -> None:
    details = _details(FOUR)
    assert {k: f.value_text for k, f in details.items()} == {
        "name": "Ravi Kumar", "age": "42 Y", "sex": "Male", "phone": "98765 43210"}  # fmt: skip
    assert all(f.value_kind == "text" for f in details.values())


@pytest.mark.parametrize(("label", "value", "line"), [
    ("name", "Ravi Kumar", 4), ("age", "42 Y", 4), ("sex", "Male", 4),
    ("phone", "98765 43210", 5),
])  # fmt: skip
def test_ac1_the_proof_is_the_line_the_value_is_written_on(label: str, value: str,
                                                           line: int) -> None:  # fmt: skip
    cited = [BLOCKS[3], BLOCKS[4]]  # what the model cited for the whole patient block
    assert [b["id"] for b in P.proof_blocks(label, value, BLOCKS, cited)] == [f"u{line}"]


def test_ac1_the_four_pass_the_format_rules() -> None:
    others = {"age": "42 Y"}
    four = (("name", "Ravi Kumar", PAGE[3]), ("age", "42 Y", PAGE[3]), ("sex", "Male", PAGE[3]),
            ("phone", "98765 43210", PAGE[4]))  # fmt: skip
    for label, value, line in four:
        assert _findings(label, value, line, others) == []


class _Conn:
    """Enough of a connection for ``write_extraction``: the document has no earlier patient."""

    def execute(self, *a: Any, **k: Any) -> Any:
        return self

    def scalar_one_or_none(self) -> None:
        return None

    def mappings(self) -> Any:  # OUT-S1 reads the document's facts back: none here
        return self

    def all(self) -> list[Any]:
        return []


@pytest.fixture
def saved(monkeypatch) -> dict[str, Any]:
    """``write_extraction`` with the database calls recorded instead of made."""
    rec: dict[str, Any] = {"facts": [], "proof": [], "resolved": 0, "patient": None}

    def fact(conn: Any, **f: Any) -> str:
        rec["facts"].append(f)
        return f"fact-{len(rec['facts'])}"

    def resolve(conn: Any, cand: Any) -> tuple[str, str]:
        rec["resolved"] += 1
        return "00000000-0000-0000-0000-00000000aaaa", "minted"

    monkeypatch.setattr(X.repo, "purge_document_facts", lambda *a, **k: 0)
    monkeypatch.setattr(X.repo, "insert_extraction", lambda *a, **k: "ext-1")
    monkeypatch.setattr(X.repo, "insert_encounter", lambda *a, **k: "enc-1")
    monkeypatch.setattr(X.repo, "insert_clinical_fact", fact)
    monkeypatch.setattr(X.repo, "insert_fact_provenance",
                        lambda conn, **p: rec["proof"].append(p))  # fmt: skip
    monkeypatch.setattr(X.repo, "set_document_patient",
                        lambda conn, doc, pid: rec.update(patient=str(pid)))  # fmt: skip
    monkeypatch.setattr(X.mpi, "resolve_identity", resolve)
    monkeypatch.setattr(X.mpi, "add_alias", lambda *a, **k: None)
    monkeypatch.setattr(X.practitioner, "link_document", lambda *a, **k: None)  # EX-S2's own
    return rec


def _write(payload: dict[str, Any], **more: Any) -> dict[str, Any]:
    block_map = {f"b{i}": f"u{i}" for i in range(1, len(PAGE) + 1)}
    return X.write_extraction(_Conn(), document_id="00000000-0000-0000-0000-000000000001",  # type: ignore[arg-type]
                              doc_type="prescription", payload=payload, block_id_map=block_map,
                              blocks=BLOCKS, run_ids=[], model_stack={}, **more)  # fmt: skip


def test_ac1_all_four_are_saved_with_proof_and_a_status(saved) -> None:
    _write(FOUR)
    rows = {f["local_text"]: (f, p) for f, p in zip(saved["facts"], saved["proof"], strict=True)
            if f["fact_type"] == "patient_detail"}  # fmt: skip
    assert set(rows) == {"name", "age", "sex", "phone"}
    for fact, proof in rows.values():
        assert fact["review_state"] == "pending"  # a status from the start; the gate sets it
        assert proof["ocr_block_ids"] and proof["bbox_union"]  # the part of the picture
    assert rows["phone"][1]["ocr_block_ids"] == ["u5"]
    assert rows["phone"][1]["extracted_text"] == PAGE[4]
    assert rows["name"][1]["ocr_block_ids"] == ["u4"]


def test_ac1_the_result_carries_each_value_with_its_status_and_evidence(monkeypatch) -> None:
    proof = {"page": 1, "bbox": [10, 100, 500, 118], "observation_ids": ["u5"]}
    monkeypatch.setattr(result, "_evidence", lambda conn, fact_id: proof)
    facts = [
        {"id": 1, "fact_type": "patient_detail", "local_text": "phone",
         "value_text": "98765 43210", "review_state": "auto_accepted",
         "confidence_overall": 0.97},
        {"id": 2, "fact_type": "patient_detail", "local_text": "age", "value_text": "42 Y",
         "review_state": "in_review", "confidence_overall": 0.7},
        {"id": 3, "fact_type": "patient_detail", "local_text": "hospital number",
         "value_text": "CC-10293", "review_state": "in_review", "confidence_overall": 0.7},
    ]  # fmt: skip
    out = result._patient_details(None, facts)  # type: ignore[arg-type]
    assert out["phone"] == {"value": "98765 43210", "status": result.ACCEPTED,
                            "confidence": 0.97, "evidence": proof}  # fmt: skip
    assert out["age_text"]["status"] == result.NEEDS_CHECK
    assert out["ids_read"][0]["type"] == "mrn" and out["ids_read"][0]["value"] == "CC-10293"
    assert "patient.phone" not in result.ABSENT and "patient.address" not in result.ABSENT


# =====================================================================================
# AC2  GIVEN a phone number where one digit is unclear WHEN it is read
#      THEN the value goes to review
# =====================================================================================


@pytest.mark.parametrize("read", ["98765 4321", "98765 4?210", "98765 4x210", "9876543210 1"])
def test_ac2_a_number_with_a_digit_missing_or_marked_unclear_is_held(read: str) -> None:
    assert "phone-format" in _findings("phone", read, PAGE[4])


def test_ac2_a_number_that_differs_from_the_picture_by_one_digit_is_held() -> None:
    """The model wrote ten clean digits, but the line it cites has another digit: the two
    readings of the unclear digit disagree."""
    assert _findings("phone", "98765 48210", PAGE[4]) == ["number-not-in-picture"]


def test_ac2_a_number_that_is_on_no_line_has_no_proof() -> None:
    assert P.proof_blocks("phone", "98765 48210", BLOCKS, [BLOCKS[4]]) == []
    assert rules.check_evidence([{"ocr_block_ids": [], "extracted_text": None}])[0].severity == (
        "blocker")  # fmt: skip


def test_ac2_held_means_in_review_whatever_the_confidence(saved) -> None:
    found = rules.check_patient_detail(
        {"fact_type": "patient_detail", "local_text": "phone", "value_text": "98765 4?210"},
        [{"extracted_text": PAGE[4]}])  # fmt: skip
    assert gate("patient_detail", 0.99, found, False, Settings()) == "in_review"


def test_ac2_a_clear_number_is_not_held_by_these_rules() -> None:
    for written in ("98765 43210", "9876543210", "+91 98765 43210", "09876543210"):
        assert _findings("phone", written, "Mobile: " + written) == []
    assert gate("patient_detail", 0.99, [], False, Settings()) == "auto_accepted"


def test_ac2_two_numbers_are_each_checked() -> None:
    line = "Contact: 9007498749 / 9830585954"
    assert _findings("phone", "9007498749 / 9830585954", line) == []
    assert _findings("phone", "9007498749 / 983058595", line) == ["phone-format"]


# =====================================================================================
# AC3  GIVEN no address on the page WHEN it is read
#      THEN the address is empty and no review is raised
# =====================================================================================


@pytest.mark.parametrize("address", [None, "", "   "])
def test_ac3_no_address_is_no_fact(address: Any) -> None:
    details = _details(_payload(name="Ravi Kumar", address=address))
    assert "address" not in details and set(details) == {"name"}


def test_ac3_nothing_is_raised_for_a_detail_that_is_not_there() -> None:
    assert P.check("address", None, PAGE[3]) == [] and P.check("phone", "", PAGE[4]) == []
    assert result._patient_details(None, []) == {}  # type: ignore[arg-type]


def test_ac3_a_patient_block_with_nothing_in_it_gives_nothing(saved) -> None:
    _write(_payload())
    assert [f for f in saved["facts"] if f["fact_type"] == "patient_detail"] == []


def test_ac3_an_address_that_is_written_is_kept_with_its_lines() -> None:
    page = [*BLOCKS, {"id": "u8", "text": "Address: 12 Lake View Road,", "page_id": "p1",
                      "ocr_conf": 0.9, "bbox": [10, 200, 500, 218]},
            {"id": "u9", "text": "Salt Lake, Kolkata 700091", "page_id": "p1", "ocr_conf": 0.9,
             "bbox": [10, 220, 500, 238]}]  # fmt: skip
    written = "12 Lake View Road, Salt Lake, Kolkata 700091"
    assert [b["id"] for b in P.proof_blocks("address", written, page)] == ["u8", "u9"]
    assert _findings("address", written, "Address: 12 Lake View Road, | Salt Lake, Kolkata") == []


# =====================================================================================
# AC4  GIVEN an existing patient reference is entered on the screen WHEN the result is saved
#      THEN the reference is kept and the patient is not guessed
# =====================================================================================

ENTERED = "00000000-0000-0000-0000-00000000beef"


def test_ac4_the_entered_patient_is_kept_and_no_matching_is_done(saved) -> None:
    out = _write(FOUR, patient_id=ENTERED)
    assert saved["resolved"] == 0  # the registry was not searched for a look-alike
    assert out["patient_id"] == ENTERED and out["identity"] == "given"
    assert saved["patient"] == ENTERED
    assert {f["patient_id"] for f in saved["facts"]} == {ENTERED}


def test_ac4_without_a_reference_the_existing_lookup_decides_not_this_story(saved) -> None:
    _write(FOUR)
    assert saved["resolved"] == 1  # the registry lookup that was already there, once


def test_ac4_a_page_that_names_someone_else_is_refused_not_filed(saved) -> None:
    with pytest.raises(X.IdentityMismatch):
        _write(FOUR, patient_id=ENTERED, expected_patient={"name": "Sunita Devi"})


def test_ac4_reading_the_details_never_touches_the_patient_record() -> None:
    source = Path(P.__file__).read_text(encoding="utf-8")
    assert "cdi_adapter.mpi" not in source and "resolve_identity" not in source
    assert "patient_identity" not in source and "session_scope" not in source


# =====================================================================================
# AC5  NEW v3: GIVEN age 42 and a date of birth in 1990 are both read
#      WHEN they are cross-checked THEN the mismatch is flagged for review
# =====================================================================================

BOTH = {"age": "42 Y", "date of birth": "15/06/1990"}


def test_ac5_age_42_and_a_birth_in_1990_do_not_agree() -> None:
    line = "Age: 42 Y   DOB: 15/06/1990"
    assert _findings("age", "42 Y", line, BOTH) == ["age-dob-mismatch"]
    assert _findings("date of birth", "15/06/1990", line, BOTH) == ["age-dob-mismatch"]


def test_ac5_the_flag_sends_both_values_to_review() -> None:
    fact = {"fact_type": "patient_detail", "local_text": "age", "value_text": "42 Y"}
    found = rules.check_patient_detail(fact, [{"extracted_text": "Age: 42 Y"}], BOTH, TODAY)
    assert gate("patient_detail", 0.99, found, False, Settings()) == "in_review"
    assert "36 years" in found[0].message  # what the date of birth says


def test_ac5_an_age_that_agrees_with_the_date_of_birth_is_not_flagged() -> None:
    agree = {"age": "36 Y", "date of birth": "15/06/1990"}
    assert _findings("age", "36 Y", "Age: 36 Y  DOB: 15/06/1990", agree) == []
    before_birthday = {"age": "35", "date of birth": "25/12/1990"}  # birthday not yet reached
    assert _findings("age", "35", "Age 35 DOB 25/12/1990", before_birthday) == []


def test_ac5_the_age_is_checked_on_the_day_of_the_visit() -> None:
    """A prescription from 2032 read today: 42 in 2032 is right for a birth in 1990."""
    assert _findings("age", "42 Y", "Age: 42 Y", BOTH, on=date(2032, 8, 1)) == []


def test_ac5_one_of_the_two_alone_is_not_a_mismatch() -> None:
    assert _findings("age", "42 Y", "Age: 42 Y", {"age": "42 Y"}) == []
    assert _findings("date of birth", "15/06/1990", "DOB: 15/06/1990",
                     {"date of birth": "15/06/1990"}) == []  # fmt: skip


# =====================================================================================
# the functional and non-functional requirements behind the criteria
# =====================================================================================


def test_fr1_every_detail_of_the_story_is_read() -> None:
    details = _details(_payload(name="Ravi Kumar", age_text="42 Y", sex="Male",
                                dob="15/06/1984", phone="98765 43210", address="12 Lake Road",
                                mrn="CC-10293", abha="91-1234-5678-9012"))  # fmt: skip
    assert list(details) == ["name", "age", "sex", "date of birth", "phone", "address",
                             "hospital number", "abha number"]  # fmt: skip


def test_fr2_a_value_is_kept_exactly_as_written() -> None:
    details = _details(_payload(name="ravi  KUMAR", phone="98765-43210", sex="M"))
    assert details["name"].value_text == "ravi KUMAR"  # only the spacing is tidied
    assert details["phone"].value_text == "98765-43210" and details["sex"].value_text == "M"


def test_fr6_an_abha_number_is_fourteen_digits() -> None:
    assert _findings("abha number", "91-1234-5678-9012", "ABHA: 91-1234-5678-9012") == []
    assert _findings("abha number", "91-1234-5678-901", "ABHA: 91-1234-5678-901") == [
        "abha-format"]  # fmt: skip
    assert _findings("abha number", "91-1234-5678-9012", "ABHA: 91-1234-5678-9013") == [
        "number-not-in-picture"]  # fmt: skip


@pytest.mark.parametrize("age", ["420 Y", "abc", "130"])
def test_fr6_an_age_that_cannot_be_is_held(age: str) -> None:
    assert "age-implausible" in _findings("age", age, f"Age: {age}")


def test_fr6_a_date_of_birth_that_cannot_be_is_held() -> None:
    assert "dob-implausible" in _findings("date of birth", "15/06/2031", "DOB 15/06/2031")
    assert "dob-unreadable" in _findings("date of birth", "15/??/1990", "DOB 15/??/1990")


def test_fr7_a_sex_the_page_does_not_state_is_held_never_taken_from_the_name() -> None:
    """The model answered "Male" for a page that gives no sex: the line it cites has none."""
    assert _findings("sex", "Male", "Name: Ravi Kumar   Age: 42 Y") == ["not-in-picture"]
    assert P.proof_blocks("sex", "Male", [{"id": "u1", "text": "Name: Ravi Kumar"}]) == []
    assert _findings("sex", "Male", "Ravi Kumar (42 Y / M)") == []  # written as "M": the same


def test_fr7_an_age_the_page_does_not_state_is_held() -> None:
    assert _findings("age", "42 Y", "Name: Ravi Kumar   Sex: Male") == ["not-in-picture"]


def test_fr7_the_prompt_forbids_it() -> None:
    assert "never work out sex or age from a name" in PATIENT_RULES
    assert "digit for digit" in PATIENT_RULES
    prompt, _ = build_extraction_prompt("prescription", [{"id": "x", "text": "Name: R"}])
    assert PATIENT_RULES in prompt


def test_tr1_the_schema_has_the_new_fields_all_optional() -> None:
    schema = json.loads((ROOT / "schemas" / "common.defs.json").read_text(encoding="utf-8"))
    patient = schema["$defs"]["patient"]
    for field in ("name", "age_text", "sex", "dob", "phone", "address", "mrn", "abha"):
        assert field in patient["properties"]
    assert "required" not in patient and "evidence" in patient["properties"]


def test_tr_the_database_takes_the_new_kind_of_fact() -> None:
    migration = (ROOT / "db" / "alembic" / "versions" / "0036_patient_detail.py").read_text(
        encoding="utf-8")  # fmt: skip
    assert 'down_revision = "0035"' in migration and "'patient_detail'" in migration


def test_nfr2_personal_details_are_never_logged() -> None:
    source = Path(P.__file__).read_text(encoding="utf-8")
    assert "log." not in source and "print(" not in source and "structlog" not in source


def test_the_reviewer_shows_the_details_with_the_reason_a_value_is_held() -> None:
    from cdi_adapter.webapp.reviewer_page import SCRIPT

    assert "['patient_detail', 'Patient details']" in SCRIPT
    assert "['patient_detail', 'doctor_detail'].includes(t) ? COLS.detail" in SCRIPT
    assert "function whyHeld(e)" in SCRIPT
    # Edit changes the value, not the detail's name
    assert "['patient_detail', 'doctor_detail'].includes(e.element_type) ? [['value_text'" in SCRIPT


def test_only_a_prescription_gets_patient_detail_facts() -> None:
    lab = {"confidence": 0.9, "tests": [], "patient": {"name": "Ravi Kumar", "phone": "98765"}}
    assert [f for f in facts_from_payload("lab_report", lab)
            if f.fact_type == "patient_detail"] == []  # fmt: skip


# =====================================================================================
# the story's open question: an Aadhaar number must not be stored without a legal basis
# =====================================================================================

AADHAAR = "2345 6789 0124"  # made up; twelve digits with a valid check digit
NOT_ONE = "2345 6789 0123"  # the same with a wrong check digit: an ordinary number


def test_aadhaar_a_number_of_that_form_is_recognised_and_an_ordinary_one_is_not() -> None:
    assert P.is_aadhaar(AADHAAR) and P.is_aadhaar("234567890124")
    assert not P.is_aadhaar(NOT_ONE)
    assert not P.is_aadhaar("1234 5678 9012"), "an Aadhaar number never starts with 0 or 1"
    assert not P.is_aadhaar("91-1234-5678-9012"), "an ABHA number has fourteen digits"
    assert not P.is_aadhaar("98765 43210") and not P.is_aadhaar("") and not P.is_aadhaar(None)


@pytest.mark.parametrize("field", ["mrn", "abha"])
def test_aadhaar_given_as_an_id_is_not_saved_as_a_value(field: str) -> None:
    payload = P.withhold_aadhaar(_payload(name="Ravi Kumar", **{field: AADHAAR}), store=False)
    assert payload["patient"][field] is None
    assert payload["_withheld"] == [{"field": f"patient.{field}", "kind": "aadhaar",
                                     "why": "an Aadhaar number is not stored without a legal "
                                            "basis"}]  # fmt: skip
    details = _details(payload)
    assert "hospital number" not in details and "abha number" not in details
    assert details["name"].value_text == "Ravi Kumar", "the rest of the patient is untouched"


def test_aadhaar_the_note_never_holds_the_number_itself() -> None:
    payload = P.withhold_aadhaar(_payload(name="Ravi Kumar", mrn=f"Aadhaar: {AADHAAR}"),
                                 store=False)  # fmt: skip
    assert payload["patient"]["mrn"] is None, "a label and the number: the detail is not there"
    said = json.dumps(payload)
    assert "0124" not in said and "234567890124" not in said.replace(" ", "")


def test_aadhaar_inside_an_address_is_taken_out_and_the_address_kept() -> None:
    address = f"12 Lake Road, Kolkata 700029, Aadhaar {AADHAAR}"
    payload = P.withhold_aadhaar(_payload(name="Ravi Kumar", address=address), store=False)
    kept = payload["patient"]["address"]
    assert kept == f"12 Lake Road, Kolkata 700029, Aadhaar {P.AADHAAR_WITHHELD}"
    assert payload["_withheld"][0]["field"] == "patient.address"


def test_aadhaar_the_models_own_words_are_cleaned_too() -> None:
    payload = _payload(name="Ravi Kumar", mrn=AADHAAR)
    payload["_raw_answer"] = '{"patient": {"name": "Ravi Kumar", "mrn": "234567890124"}}'
    cleaned = P.withhold_aadhaar(payload, store=False)["_raw_answer"]
    assert "234567890124" not in cleaned and P.AADHAAR_WITHHELD in cleaned
    assert "Ravi Kumar" in cleaned


def test_aadhaar_an_ordinary_number_and_a_phone_with_91_are_left_alone() -> None:
    payload = _payload(name="Ravi Kumar", mrn=NOT_ONE, phone="91 98765 43216",
                       abha="91-1234-5678-9012", age_text="42 Y")  # fmt: skip
    assert P.is_aadhaar("919876543216"), "this phone, with 91, happens to pass the check digit"
    out = P.withhold_aadhaar(json.loads(json.dumps(payload)), store=False)
    assert out == payload and "_withheld" not in out


def test_aadhaar_nothing_of_it_reaches_the_database(saved, monkeypatch) -> None:
    written: dict[str, Any] = {}
    monkeypatch.setattr(X.repo, "insert_extraction",
                        lambda conn, **k: written.update(k) or "ext-1")  # fmt: skip
    payload = _payload(name="Ravi Kumar", mrn=AADHAAR, phone="98765 43210")
    payload["_raw_answer"] = f'{{"mrn": "{AADHAAR}"}}'
    _write(payload)
    everything = json.dumps([written["payload"], saved["facts"], saved["proof"]], default=str)
    assert "0124" not in everything, "not as a value, not in the saved answer, not in a proof"
    labels = [f["local_text"] for f in saved["facts"] if f["fact_type"] == "patient_detail"]
    assert "hospital number" not in labels and "name" in labels and "phone" in labels
    assert written["payload"]["_withheld"][0]["kind"] == "aadhaar"


def test_aadhaar_it_is_stored_only_when_the_owner_says_there_is_a_legal_basis() -> None:
    assert Settings.model_fields["store_aadhaar"].default is False
    payload = _payload(name="Ravi Kumar", mrn=AADHAAR)
    assert P.withhold_aadhaar(json.loads(json.dumps(payload)), store=True) == payload
    assert "store_aadhaar" in Path(X.__file__).with_name("patient.py").read_text("utf-8")


def test_aadhaar_the_log_line_names_the_field_never_the_number(saved, monkeypatch) -> None:
    logged: list[dict[str, Any]] = []
    monkeypatch.setattr(X.log, "warning", lambda event, **k: logged.append({"event": event, **k}))
    _write(_payload(name="Ravi Kumar", mrn=AADHAAR))
    line = next(entry for entry in logged if entry["event"] == "aadhaar_not_stored")
    assert line["fields"] == ["patient.mrn"] and "0124" not in json.dumps(line)


@pytest.mark.xfail(strict=True, reason="The PICTURE and its OCR lines still show an Aadhaar "
                   "number that is on the page: only the extracted value and the saved answer "
                   "are cleaned. Blanking it in the stored picture is a separate decision "
                   "(counsel, Open Items 10).")
def test_aadhaar_is_also_removed_from_the_stored_picture_and_its_ocr_lines() -> None:
    assert "withhold_aadhaar" in (Path(X.__file__).parents[1] / "ocr" / "service.py").read_text(
        encoding="utf-8")  # fmt: skip


def test_fr4_the_result_never_shows_a_birth_date_worked_out_from_an_age() -> None:
    """An identity made from an age keeps 1 January of the estimated year, for matching. The
    result asks for a date of birth only where a document or the registry holds that date."""
    import inspect

    sql = " ".join(inspect.getsource(result._patient_record).split())
    assert "CASE WHEN EXISTS (SELECT 1 FROM patient_identity_alias a" in sql
    assert "a.birth_date = i.birth_date" in sql and "patient_registry r" in sql
    assert "THEN i.birth_date END AS birth_date" in sql

