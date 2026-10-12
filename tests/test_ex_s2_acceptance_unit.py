"""EX-S2 - Read the doctor details: the acceptance criteria, one group each.

Every doctor, clinic and number here is made up. The "page" is a list of OCR lines shaped like
a letterhead; the "doctor list" is a few rows shaped like the ``practitioner`` table.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

from cdi_adapter import result
from cdi_adapter.extract import doctor as D
from cdi_adapter.extract import service as X
from cdi_adapter.extract.facts import facts_from_payload
from cdi_adapter.extract.prompt import PRESCRIBER_RULES, build_extraction_prompt
from cdi_adapter.recognition import practitioner as P
from cdi_adapter.validate import rules

ROOT = Path(__file__).resolve().parents[1]

MASTER = [
    {"id": "d-1", "name": "Dr. Anita Sen Roy", "registration_no": "71112", "council": "WBMC",
     "department": "General Medicine", "active": True},
    {"id": "d-2", "name": "Dr. Arun Mehta", "registration_no": "55683", "council": "MCI",
     "department": "Endocrinology", "active": True},
    {"id": "d-3", "name": "Dr. Rajesh Kumar", "registration_no": "40021", "council": "DMC",
     "department": "Cardiology", "active": True},
    {"id": "d-4", "name": "Dr Rajesh Kumar", "registration_no": "40577", "council": "DMC",
     "department": "Orthopaedics", "active": True},
]  # fmt: skip

PAGE = [
    "City Care Clinic",                                   # b1
    "12 Lake View Road, Salt Lake, Kolkata 700091",       # b2
    "Phone: 033 4000 1234",                               # b3
    "DR. ANITA SEN ROY",                                  # b4
    "MD (Medicine), DNB",                                 # b5
    "General Medicine",                                   # b6
    "Reg No. Regd No 71112 (WBMC)",                       # b7
    "Name: Ravi Kumar   Age: 42 Y   Mobile: 98765 43210", # b8
    "Rx",                                                 # b9
    "Tab. Paracetamol 650 1-0-1 x 3 days",                # b10
]  # fmt: skip
BLOCKS = [{"id": f"u{i}", "text": t, "page_id": "p1", "ocr_conf": 0.95,
           "bbox": [10, 20 * i, 500, 20 * i + 18]} for i, t in enumerate(PAGE, 1)]  # fmt: skip


def _link(lines: list[str], name: str | None, master: list[dict[str, Any]] = MASTER) -> P.Link:
    return P.match(master, P.find_reg_numbers(lines), name)


# =====================================================================================
# AC1  GIVEN a printed registration number that is in the doctor list WHEN it is read
#      THEN the doctor is linked by registration number
# =====================================================================================


def test_ac1_the_number_on_the_page_links_the_doctor() -> None:
    link = _link(PAGE, "DR. ANITA SEN ROY")
    assert (link.method, link.practitioner_id) == (P.REGISTRATION, "d-1")
    assert link.confidence == 1.0 and link.registration_read == "71112"
    assert link.candidates == []


@pytest.mark.parametrize("line", ["Reg No: 71112", "Regd. No. 71112 (WBMC)",
                                  "Registration Number - 71112", "Regn No 71112",
                                  "WBMC Reg. No. 71112"])  # fmt: skip
def test_ac1_the_number_is_found_however_the_label_is_printed(line: str) -> None:
    (reg,) = P.find_reg_numbers(["Dr. Anita Sen Roy", line])
    assert reg.number == "71112" and reg.line == 1


def test_ac1_it_links_even_when_no_name_was_read() -> None:
    assert _link(PAGE, None).practitioner_id == "d-1"


def test_ac1_the_number_must_be_the_same_digit_for_digit() -> None:
    """NFR2. One digit off is another number: not in the list, so not linked by it."""
    off = _link(["Dr. Someone Else", "Reg No: 71113"], None)
    assert (off.method, off.practitioner_id) == (P.UNKNOWN, None)
    assert P.find_reg_numbers(["Reg No: 07111 2"])[0].number == "07111"  # nothing is joined up


def test_ac1_a_phone_or_a_bill_number_is_never_taken_for_a_registration() -> None:
    lines = ["Phone: 033 4000 1234", "Bill No: 71112", "Date: 12/10/2026", "M: 9876543210"]
    assert P.find_reg_numbers(lines) == []
    assert _link(lines, None).method == P.UNKNOWN


def test_ac1_a_council_on_the_page_must_agree_with_the_list() -> None:
    assert _link(["Reg No 71112 (DMC)"], None).method == P.UNKNOWN  # the list says WBMC
    assert _link(["Reg No 71112"], None).practitioner_id == "d-1"  # no council printed: digits


def test_ac1_a_number_that_is_another_doctors_by_name_is_not_trusted() -> None:
    """A wrong link is worse than none: the number is doctor d-1's, the page names someone
    else. One misread digit does this - so no link, and the doctor is shown to choose."""
    link = _link(["Dr. Vikram Shah", "Reg No: 71112"], "Dr. Vikram Shah")
    assert link.method == P.UNKNOWN and link.practitioner_id is None
    assert [c["practitioner_id"] for c in link.candidates] == ["d-1"]


def test_ac1_the_link_is_saved_with_how_it_was_made(saved) -> None:
    _write({"confidence": 0.9, "medications": [], "prescriber": "DR. ANITA SEN ROY"})
    assert saved["link"]["m"] == "registration_number" and saved["link"]["p"] == "d-1"
    assert saved["link"]["c"] == 1.0 and saved["link"]["r"] == "71112"


# =====================================================================================
# AC2  GIVEN a name that matches two doctors WHEN it is read
#      THEN it is NOT linked and the candidates are shown
# =====================================================================================


def test_ac2_two_doctors_of_one_name_are_not_linked() -> None:
    link = _link(["Dr. Rajesh Kumar", "MBBS, MD"], "Dr. Rajesh Kumar")
    assert (link.method, link.practitioner_id, link.confidence) == (P.UNKNOWN, None, None)
    assert [c["practitioner_id"] for c in link.candidates] == ["d-3", "d-4"]
    assert {c["registration_no"] for c in link.candidates} == {"40021", "40577"}
    assert "2 doctors" in link.why and "not linked" in link.why


def test_ac2_the_candidates_reach_the_result_and_the_reviewer(monkeypatch) -> None:
    row = {"method": "unknown", "confidence": None, "practitioner_id": None,
           "practitioner_name": None, "why": "2 doctors are named Dr. Rajesh Kumar: not linked",
           "candidates": [{"practitioner_id": "d-3", "name": "Dr. Rajesh Kumar",
                           "registration_no": "40021"},
                          {"practitioner_id": "d-4", "name": "Dr Rajesh Kumar",
                           "registration_no": "40577"}],
           "stamp_present": None, "signature_present": None, "stamp_text": None}  # fmt: skip
    monkeypatch.setattr(P, "link_of", lambda conn, document_id: row)
    link = result._doctor(object(), [], "doc-1")["link"]  # type: ignore[arg-type]
    assert link["method"] == "unknown" and link["practitioner_id"] is None
    assert [c["name"] for c in link["candidates"]] == ["Dr. Rajesh Kumar", "Dr Rajesh Kumar"]
    from cdi_adapter.webapp.reviewer_page import SCRIPT

    assert "doctor unknown" in SCRIPT and "could be: " in SCRIPT


def test_ac2_the_number_decides_between_them_when_it_is_printed() -> None:
    link = _link(["Dr. Rajesh Kumar", "Reg No: 40577 (DMC)"], "Dr. Rajesh Kumar")
    assert (link.method, link.practitioner_id) == (P.REGISTRATION, "d-4")


def test_ac2_one_doctor_of_that_full_name_is_a_safe_match() -> None:
    link = _link(["Dr. Arun Mehta", "MBBS"], "Dr. Arun Mehta")
    assert (link.method, link.practitioner_id, link.confidence) == (P.NAME, "d-2", 0.9)


@pytest.mark.parametrize("name", ["Dr. Mehta", "Dr. Arun", "Dr. Arun K Mehta", "Dr. Arum Mehta"])
def test_ac2_a_part_of_a_name_or_a_name_that_is_alike_never_links(name: str) -> None:
    link = _link([name], name)
    assert link.method == P.UNKNOWN and link.practitioner_id is None


def test_ac2_a_name_with_another_doctors_number_is_not_linked_by_the_name() -> None:
    """The page says Arun Mehta but prints a number that is not his: something is misread."""
    link = _link(["Dr. Arun Mehta", "Reg No: 99999"], "Dr. Arun Mehta")
    assert link.method == P.UNKNOWN and [c["practitioner_id"] for c in link.candidates] == ["d-2"]


def test_ac2_a_doctor_marked_inactive_is_not_linked() -> None:
    gone = [{**MASTER[1], "active": False}]
    assert _link(["Dr. Arun Mehta", "Reg No: 55683"], "Dr. Arun Mehta", gone).method == P.UNKNOWN


# =====================================================================================
# AC3  GIVEN no doctor information WHEN it is read
#      THEN the doctor is 'unknown' and the rest of the result is still saved
# =====================================================================================

NO_DOCTOR = ["Name: Ravi Kumar   Age: 42 Y", "Rx", "Tab. Paracetamol 650 1-0-1 x 3 days"]


def test_ac3_no_doctor_information_is_unknown_not_an_error() -> None:
    link = _link(NO_DOCTOR, None)
    assert (link.method, link.practitioner_id, link.candidates) == (P.UNKNOWN, None, [])
    assert link.why == "no doctor information on the page"
    assert D.doctor_details({"medications": []}, NO_DOCTOR) == {}


def test_ac3_an_empty_doctor_list_makes_every_doctor_unknown() -> None:
    link = _link(PAGE, "DR. ANITA SEN ROY", master=[])
    assert link.method == P.UNKNOWN and link.why == "not in the doctor list"


def test_ac3_the_rest_of_the_result_is_still_saved(saved) -> None:
    payload = {"confidence": 0.9, "patient": {"name": "Ravi Kumar", "evidence": ["b1"]},
               "medications": [{"drug_name": "Tab. Paracetamol 650", "evidence": ["b3"]}]}
    blocks = [{"id": f"u{i}", "text": t, "page_id": "p1", "ocr_conf": 0.9, "bbox": [0, i, 9, i + 1]}
              for i, t in enumerate(NO_DOCTOR, 1)]  # fmt: skip
    out = _write(payload, blocks=blocks)
    assert saved["link"]["m"] == "unknown" and saved["link"]["p"] is None
    kinds = [f["fact_type"] for f in saved["facts"]]
    assert "medication" in kinds and "patient_detail" in kinds and "doctor_detail" not in kinds
    assert out["facts"] == len(saved["facts"]) == 2


def test_ac3_the_result_always_has_a_link_and_it_says_unknown() -> None:
    doctor = result._doctor(None, [], None)  # type: ignore[arg-type]
    assert doctor["link"] == {"method": "unknown", "confidence": None, "practitioner_id": None}
    assert doctor["stamp_present"] is None and "name" not in doctor


# =====================================================================================
# AC4  GIVEN a stamp is on the page WHEN it is read THEN stamp_present is yes
# =====================================================================================

STAMPED = {"confidence": 0.9, "medications": [], "prescriber": "DR. ANITA SEN ROY",
           "prescriber_details": {"stamp_present": True, "signature_present": True,
                                  "stamp_text": "Dr Anita Sen Roy Regd 71112"}}  # fmt: skip


def test_ac4_a_stamp_the_reading_reports_is_saved_as_yes(saved) -> None:
    _write(STAMPED)
    assert saved["link"]["s"] is True and saved["link"]["g"] is True
    assert saved["link"]["t"] == "Dr Anita Sen Roy Regd 71112"


def test_ac4_the_result_says_stamp_present_yes(monkeypatch) -> None:
    row = {"method": "registration_number", "confidence": 1.0, "practitioner_id": "d-1",
           "practitioner_name": "Dr. Anita Sen Roy", "why": "registration number 71112",
           "candidates": [], "stamp_present": True, "signature_present": False,
           "stamp_text": "Dr Anita Sen Roy Regd 71112"}  # fmt: skip
    monkeypatch.setattr(P, "link_of", lambda conn, document_id: row)
    doctor = result._doctor(object(), [], "doc-1")  # type: ignore[arg-type]
    assert doctor["stamp_present"] is True and doctor["signature_present"] is False
    assert doctor["stamp_text"] == "Dr Anita Sen Roy Regd 71112"
    assert doctor["link"]["method"] == "registration_number"
    assert "stamp_present" not in doctor["link"]  # the stamp is the page's, not the link's


@pytest.mark.parametrize(("said", "want"), [(True, True), ("yes", True), ("Yes", True),
                                            (False, False), ("no", False), (None, None),
                                            ("", None), ("maybe", None)])  # fmt: skip
def test_ac4_yes_is_yes_no_is_no_and_silence_is_not_a_no(said: Any, want: Any) -> None:
    assert D.stamp_of({"prescriber_details": {"stamp_present": said}})["stamp_present"] is want


def test_ac4_the_stamps_text_is_kept_apart_from_the_letterhead() -> None:
    """FR6. What is inside the stamp is its own field: it is not the doctor's name, number or
    qualification as the letterhead gives them."""
    details = D.doctor_details(STAMPED, PAGE)
    assert "Regd 71112" not in " ".join(details.values())
    assert details["registration number"] == "71112 (WBMC)"  # the letterhead's own line


def _stamped_picture() -> bytes:
    """A drawn page with a round blue stamp at the bottom right. Not a real prescription."""
    import cv2
    import numpy as np

    img = np.full((1400, 1000, 3), 255, np.uint8)
    cv2.putText(img, "CITY CARE CLINIC", (60, 80), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (20, 20, 20), 2)
    cv2.ellipse(img, (760, 1180), (110, 110), 0, 0, 360, (170, 60, 30), 4, cv2.LINE_AA)
    for i, word in enumerate(("DR OTHER", "REG 99999")):
        cv2.putText(img, word, (685, 1170 + 34 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                    (170, 60, 30), 2)  # fmt: skip
    ok, buf = cv2.imencode(".png", img)
    assert ok
    return bytes(buf)


STAMP_LINES = [{"id": "s1", "text": "DR OTHER", "page_id": "p1", "ocr_conf": 0.9,
                "bbox": [685, 1150, 840, 1180]},
               {"id": "s2", "text": "Reg No 99999", "page_id": "p1", "ocr_conf": 0.9,
                "bbox": [685, 1185, 850, 1215]}]  # fmt: skip


def test_ac4_a_stamp_in_the_picture_is_yes_even_when_the_model_said_nothing(saved) -> None:
    """The stamp is found in the picture itself; the model's answer does not mention one."""
    answer = {"confidence": 0.9, "medications": [], "prescriber": "DR. ANITA SEN ROY"}
    payload = D.with_stamp(answer, _stamped_picture(), BLOCKS)
    assert payload["_stamp"]["present"] is True and payload["_stamp"]["shape"] == "round"
    assert D.stamp_of(payload)["stamp_present"] is True
    _write(payload)
    assert saved["link"]["s"] is True


def test_ac4_a_stamp_in_the_picture_overrules_a_model_that_said_no() -> None:
    payload = D.with_stamp({"prescriber_details": {"stamp_present": False}},
                           _stamped_picture(), BLOCKS)  # fmt: skip
    assert D.stamp_of(payload)["stamp_present"] is True


def test_ac4_no_stamp_in_the_picture_leaves_the_models_answer_standing() -> None:
    """Nothing found in the picture is not a "no": a black or outline-less stamp is not looked
    for there, so what the model said - yes, no or nothing - is kept."""
    import cv2
    import numpy as np

    ok, buf = cv2.imencode(".png", np.full((700, 500, 3), 255, np.uint8))
    assert ok
    plain = bytes(buf)
    for said, want in ((True, True), (False, False), (None, None)):
        payload = D.with_stamp({"prescriber_details": {"stamp_present": said}}, plain, BLOCKS)
        assert "_stamp" not in payload and D.stamp_of(payload)["stamp_present"] is want


def test_ac4_the_stamps_own_text_is_read_as_its_own_region() -> None:
    """FR6. The lines that sit inside the stamp are the stamp's text - and they are NOT read as
    the letterhead: a registration number inside the stamp does not become the doctor's."""
    blocks = [*BLOCKS, *STAMP_LINES]
    payload = D.with_stamp({"confidence": 0.9, "medications": []}, _stamped_picture(), blocks)
    assert payload["_stamp"]["text"] == "DR OTHER Reg No 99999"
    assert D.stamp_of(payload)["stamp_text"] == "DR OTHER Reg No 99999"
    details = D.with_doctor(payload, [str(b["text"]) for b in blocks])["_doctor"]
    assert details["registration number"] == "71112 (WBMC)"  # the letterhead's, not the stamp's
    assert details["name"] == "DR. ANITA SEN ROY"


def test_ac4_extraction_looks_for_the_stamp_in_the_page_picture() -> None:
    source = Path(X.__file__).read_text(encoding="utf-8")
    assert "ctx.image = image" in source  # the picture the model was sent
    assert "payload = with_stamp(payload, ctx.image, ctx.blocks)" in source


@pytest.mark.xfail(strict=True, reason=(
    "EX-S2 AC4: the stamp finder is proven on drawn pages only (tests/test_stamp_unit.py). No "
    "recall or false-alarm rate has been measured on real stamped prescriptions - that needs "
    "a labelled set, and its result recorded under docs/evidence/EX-S2/. Stamps in black ink "
    "and stamps with no outline are not found in the picture at all."))  # fmt: skip
def test_ac4_the_stamp_finder_is_measured_on_real_stamped_pages() -> None:
    assert (ROOT / "docs" / "evidence" / "EX-S2" / "stamps.md").exists()


# =====================================================================================
# the requirements behind the criteria
# =====================================================================================

FULL = {"confidence": 0.9, "medications": [], "prescriber_details": {
    "name": "DR. ANITA SEN ROY", "registration_no": "71112 (WBMC)",
    "qualification": "MD (Medicine), DNB", "department": "General Medicine",
    "clinic": {"name": "City Care Clinic", "address": "12 Lake View Road, Salt Lake, Kolkata",
               "phone": "033 4000 1234"}, "evidence": ["b4"]}}  # fmt: skip


def test_fr1_every_detail_of_the_story_is_read() -> None:
    assert list(D.doctor_details(FULL, PAGE)) == [
        "name", "registration number", "qualification", "department", "clinic name",
        "clinic address", "clinic phone"]  # fmt: skip


def test_fr1_the_letterhead_gives_what_the_model_left_out() -> None:
    details = D.doctor_details({"medications": [], "prescriber": "DR. ANITA SEN ROY"}, PAGE)
    assert details == {"name": "DR. ANITA SEN ROY", "registration number": "71112 (WBMC)",
                       "qualification": "MD (Medicine), DNB"}  # fmt: skip


def test_fr1_each_detail_is_a_fact_with_the_line_it_is_on() -> None:
    payload = D.with_doctor(dict(FULL), PAGE)
    facts = {f.local_text: f for f in facts_from_payload("prescription", payload)
             if f.fact_type == "doctor_detail"}  # fmt: skip
    assert len(facts) == 7 and facts["clinic phone"].value_text == "033 4000 1234"
    from cdi_adapter.extract.patient import proof_blocks

    where = {label: [b["id"] for b in proof_blocks(label, f.value_text, BLOCKS)]
             for label, f in facts.items()}  # fmt: skip
    assert where == {"name": ["u4"], "registration number": ["u7"], "qualification": ["u5"],
                     "department": ["u6"], "clinic name": ["u1"], "clinic address": ["u2"],
                     "clinic phone": ["u3"]}  # fmt: skip


def test_nfr2_a_registration_number_that_differs_from_the_picture_is_held() -> None:
    fact = {"fact_type": "doctor_detail", "local_text": "registration number",
            "value_text": "71113 (WBMC)"}  # fmt: skip
    found = rules.check_patient_detail(fact, [{"extracted_text": PAGE[6]}])
    assert [f.code for f in found] == ["not-in-picture"] and found[0].severity == "blocker"
    fact["value_text"] = "71112 (WBMC)"
    assert rules.check_patient_detail(fact, [{"extracted_text": PAGE[6]}]) == []


def test_fr3_how_the_doctor_was_identified_is_part_of_the_answer() -> None:
    assert _link(PAGE, "DR. ANITA SEN ROY").as_json() == {
        "method": "registration_number", "practitioner_id": "d-1", "confidence": 1.0,
        "candidates": [], "why": "registration number 71112"}  # fmt: skip


def test_fr4_handwriting_is_never_the_basis_for_naming_a_doctor() -> None:
    source = Path(P.__file__).read_text(encoding="utf-8")
    for word in ("embedding", "style", "image", "pixels", "cv2"):
        assert word not in source.split('"""', 2)[2]  # the code, not the module's description
    assert "Never name a doctor from the handwriting" in PRESCRIBER_RULES


def test_the_prompt_and_the_schema_ask_for_the_new_fields() -> None:
    prompt, _ = build_extraction_prompt("prescription", [{"id": "x", "text": "Dr. A"}])
    assert PRESCRIBER_RULES in prompt and "stamp_present is true only" in PRESCRIBER_RULES
    schema = json.loads((ROOT / "schemas" / "prescription.v3.json").read_text(encoding="utf-8"))
    details = schema["properties"]["prescriber_details"]["properties"]
    for name in ("name", "registration_no", "qualification", "department", "clinic",
                 "stamp_present", "signature_present", "stamp_text", "evidence"):  # fmt: skip
        assert name in details
    assert set(details["clinic"]["properties"]) == {"name", "address", "phone"}
    assert "prescriber_details" not in schema["required"]  # all of it optional
    assert schema["properties"]["prescriber"] == {"type": ["string", "null"]}  # as it was


def test_the_database_has_the_doctor_list_and_the_link() -> None:
    migration = (ROOT / "db" / "alembic" / "versions" / "0037_practitioner_link.py").read_text(
        encoding="utf-8")  # fmt: skip
    assert 'down_revision = "0036"' in migration
    for needed in ("CREATE TABLE practitioner", "CREATE TABLE document_practitioner",
                   "'registration_number', 'name', 'unknown'", "'doctor_detail'",
                   "method <> 'unknown' OR practitioner_id IS NULL"):  # fmt: skip
        assert needed in migration


def test_the_doctor_list_file_is_read_strictly(tmp_path) -> None:
    script = ROOT / "scripts" / "load_doctor_master.py"
    spec = importlib.util.spec_from_file_location("load_doctor_master", script)
    assert spec and spec.loader
    loader = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loader)
    listed = tmp_path / "doctors.csv"
    listed.write_text("name,registration_no,council,department\n"
                      "Dr. Anita Sen Roy,71112,wbmc,General Medicine\n"
                      "Dr. No Number,,,\n"
                      "Dr. Words Only,pending,,\n"
                      "Dr. Twice Listed,71112,WBMC,\n", encoding="utf-8")  # fmt: skip
    rows, problems = loader.read_rows(listed)
    assert [(r["name"], r["registration_no"], r["council"]) for r in rows] == [
        ("Dr. Anita Sen Roy", "71112", "WBMC")]  # fmt: skip
    assert len(problems) == 3 and all("skipped" in p for p in problems)


# ------------------------------------------------------------------ helpers


class _Conn:
    """Enough of a connection for ``write_extraction`` and ``link_document``: the doctor list
    is ``MASTER``, and the link that would be written is kept."""

    def __init__(self, rec: dict[str, Any]) -> None:
        self.rec = rec

    def execute(self, statement: Any, params: Any = None) -> Any:
        sql = str(statement)
        if "INSERT INTO document_practitioner" in sql:
            self.rec["link"] = params
        self._rows = MASTER if "FROM practitioner" in sql else []
        return self

    def mappings(self) -> Any:
        return self

    def all(self) -> list[dict[str, Any]]:
        return list(self._rows)

    def scalar_one_or_none(self) -> None:
        return None


@pytest.fixture
def saved(monkeypatch) -> dict[str, Any]:
    rec: dict[str, Any] = {"facts": [], "link": None}

    def fact(conn: Any, **f: Any) -> str:
        rec["facts"].append(f)
        return f"fact-{len(rec['facts'])}"

    monkeypatch.setattr(X.repo, "purge_document_facts", lambda *a, **k: 0)
    monkeypatch.setattr(X.repo, "insert_extraction", lambda *a, **k: "ext-1")
    monkeypatch.setattr(X.repo, "insert_encounter", lambda *a, **k: "enc-1")
    monkeypatch.setattr(X.repo, "insert_clinical_fact", fact)
    monkeypatch.setattr(X.repo, "insert_fact_provenance", lambda *a, **k: None)
    monkeypatch.setattr(X.repo, "set_document_patient", lambda *a, **k: None)
    minted = ("00000000-0000-0000-0000-00000000aaaa", "minted")
    monkeypatch.setattr(X.mpi, "resolve_identity", lambda conn, cand: minted)
    monkeypatch.setattr(X.mpi, "add_alias", lambda *a, **k: None)
    _write.rec = rec  # type: ignore[attr-defined]
    return rec


def _write(payload: dict[str, Any], blocks: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    blocks = blocks or BLOCKS
    payload = X.annotate(dict(payload), "prescription", blocks)
    block_map = {f"b{i}": b["id"] for i, b in enumerate(blocks, 1)}
    return X.write_extraction(_Conn(_write.rec), document_id="doc-1",  # type: ignore[attr-defined,arg-type]
                              doc_type="prescription", payload=payload, block_id_map=block_map,
                              blocks=blocks, run_ids=[], model_stack={})  # fmt: skip
