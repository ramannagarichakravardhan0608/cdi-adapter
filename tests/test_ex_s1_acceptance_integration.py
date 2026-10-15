"""EX-S1 on real PostgreSQL: a made-up prescription goes through the real writer, the real
checks and the real result builder, and the five criteria are read back from the database.

The model's answer is given here (no GPU): what is proven is everything AFTER the model -
what is saved, with what proof and status, and what the result says. Needs the test database
(``db_env``); skipped where there is none. With ``CDI_EVIDENCE_DIR`` set, the result of the
first prescription is also written there as ``extracted.json`` (the story's "one extracted
JSON from a synthetic prescription").
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text

from cdi_adapter import repo, result
from cdi_adapter.db import session_scope
from cdi_adapter.extract.prompt import build_extraction_prompt
from cdi_adapter.extract.service import annotate, write_extraction
from cdi_adapter.terminology.service import bind_document
from cdi_adapter.validate.service import validate_document
from tests.test_extract_integration import _doc_with_ocr

pytestmark = pytest.mark.integration

AADHAAR = "2345 6789 0124"  # made up; the form of an Aadhaar number


def _page(person: str, **over: str) -> list[str]:
    d = {"age": "42 Y", "sex": "M", "phone": "98765 43210", "extra": "", **over}
    lines = [
        "City Test Clinic  (made-up page: not a real patient)",
        f"Name: {person}   Age/Sex: {d['age']} / {d['sex']}",
        f"Mobile: {d['phone']}",
        "Date: 12/10/2026",
        "Diagnosis: Type 2 diabetes mellitus",
        "Adv: HbA1c",
    ]
    return lines + ([d["extra"]] if d["extra"] else [])


def _person() -> str:
    """A made-up name no other test shares (letters only: a digit in a name is itself a flag)."""
    tail = uuid.uuid4().hex[:8].translate(str.maketrans("0123456789", "ghijklmnop"))
    return f"Ravi Test{tail}"


def _run(lines: list[str], patient: dict[str, Any], **kw: Any) -> tuple[str, dict[str, Any]]:
    """Write the answer for this page, bind, validate, and build the result."""
    doc, blocks = _doc_with_ocr(lines)
    _, bmap = build_extraction_prompt("prescription", blocks)
    refs = list(bmap)  # b1, b2, ... in page order
    payload: dict[str, Any] = {
        "confidence": 0.97,
        "medications": [],
        "patient": {**patient, "evidence": refs[1:3], "confidence": 0.97},
        "diagnoses": [
            {"text": "Type 2 diabetes mellitus", "evidence": [refs[4]], "confidence": 0.97}
        ],
        "investigations_advised": [{"text": "HbA1c", "evidence": [refs[5]], "confidence": 0.97}],
    }
    payload = annotate(payload, "prescription", blocks)
    with session_scope() as c:
        write_extraction(
            c,
            document_id=doc,
            doc_type="prescription",
            payload=payload,
            block_id_map=bmap,
            blocks=blocks,
            run_ids=[],
            model_stack={"extractor": "given answer (test)"},
            **kw,
        )
    bind_document(doc)
    validate_document(doc)
    with session_scope() as c:
        return doc, result.build(c, doc)


def _details(doc: str) -> dict[str, dict[str, Any]]:
    with session_scope() as c:
        rows = (
            c.execute(
                text(
                    "SELECT detail, value, review_state, evidence_id, page_no, "
                    "cardinality(ocr_block_ids) AS lines, evidence_text FROM v_rx_patient_detail "
                    "WHERE document_id = :d"
                ),
                {"d": doc},
            )
            .mappings()
            .all()
        )
    return {r["detail"]: dict(r) for r in rows}


def test_ac1_name_age_sex_and_phone_are_saved_with_proof_and_a_status(db_env):
    person = _person()
    doc, built = _run(
        _page(person), {"name": person, "age_text": "42 Y", "sex": "M", "phone": "98765 43210"}
    )
    saved = _details(doc)
    assert {"name", "age", "sex", "phone"} <= set(saved)
    for label in ("name", "age", "sex", "phone"):
        row = saved[label]
        assert row["evidence_id"] is not None and row["page_no"] == 1 and row["lines"] >= 1, label
        assert row["review_state"] in ("auto_accepted", "in_review", "pending"), label
    assert (
        saved["phone"]["value"] == "98765 43210"
        and "98765 43210" in saved["phone"]["evidence_text"]
    )
    patient = built["patient"]
    for key in ("name", "age_text", "sex", "phone"):
        assert patient[key]["status"] in ("accepted", "needs_check")
        assert patient[key]["evidence"]["page"] == 1 and patient[key]["evidence"]["observation_ids"]
    assert result.problems(built) == [], "and the result fits the fixed format"
    out = os.environ.get("CDI_EVIDENCE_DIR")
    if out:
        Path(out).mkdir(parents=True, exist_ok=True)
        (Path(out) / "extracted.json").write_text(
            json.dumps(built, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
        )


def test_ac2_a_phone_with_an_unclear_digit_goes_to_review(db_env):
    person = _person()
    doc, built = _run(
        _page(person, phone="98765 4321?"),
        {"name": person, "age_text": "42 Y", "sex": "M", "phone": "98765 4321?"},
    )
    assert _details(doc)["phone"]["review_state"] == "in_review"
    phone = built["patient"]["phone"]
    assert phone["status"] == "needs_check" and "not a ten-digit phone number" in phone["reason"]
    assert built["status"] == "needs_check"


def test_ac2_a_phone_that_differs_from_the_page_by_one_digit_goes_to_review(db_env):
    person = _person()
    doc, built = _run(_page(person), {"name": person, "phone": "98765 43219"})
    phone = built["patient"]["phone"]
    # that number is on no line of the page, so it has no proof at all - and says so
    assert phone["status"] == "needs_check"
    assert phone["reason"] == "no OCR block or extracted text supports it"
    assert _details(doc)["phone"]["review_state"] == "in_review"


def test_ac3_no_address_is_empty_and_raises_no_review(db_env):
    person = _person()
    doc, built = _run(
        _page(person),
        {"name": person, "age_text": "42 Y", "sex": "M", "phone": "98765 43210", "address": None},
    )
    assert "address" not in _details(doc) and "address" not in built["patient"]
    with session_scope() as c:
        about_address = c.execute(
            text(
                "SELECT count(*) FROM clinical_fact WHERE :d = ANY(source_doc_ids) "
                "AND fact_type = 'patient_detail' AND local_text = 'address'"
            ),
            {"d": doc},
        ).scalar_one()
    assert about_address == 0, "nothing was saved for it, so nothing can be sent to review"
    assert not [i for i in built["review"]["items"] if "address" in str(i).lower()]


def test_ac4_an_entered_patient_is_kept_and_nobody_is_guessed(db_env):
    person = _person()
    with session_scope() as c:
        entered = str(
            c.execute(
                text("INSERT INTO patient_identity (name) VALUES (:n) RETURNING id"), {"n": person}
            ).scalar_one()
        )
        before = c.execute(text("SELECT count(*) FROM patient_identity")).scalar_one()
    doc, _ = _run(_page(person), {"name": person, "phone": "98765 43210"}, patient_id=entered)
    with session_scope() as c:
        assert str(repo.get_document(c, doc)["patient_id"]) == entered
        assert c.execute(text("SELECT count(*) FROM patient_identity")).scalar_one() == before
        owners = (
            c.execute(
                text(
                    "SELECT DISTINCT patient_id FROM clinical_fact WHERE :d = ANY(source_doc_ids)"
                ),
                {"d": doc},
            )
            .scalars()
            .all()
        )
    assert [str(o) for o in owners] == [entered], "every value is filed under the entered patient"


def test_ac5_age_42_and_a_birth_in_1990_are_flagged_for_review(db_env):
    person = _person()
    doc, built = _run(
        _page(person, extra="DOB: 15/06/1990"),
        {"name": person, "age_text": "42 Y", "dob": "15/06/1990"},
    )
    saved = _details(doc)
    assert saved["age"]["review_state"] == saved["date of birth"]["review_state"] == "in_review"
    for key in ("age_text", "dob"):
        assert built["patient"][key]["status"] == "needs_check"
        assert "does not agree with the date of birth" in built["patient"][key]["reason"]


def test_ac5_an_age_that_agrees_is_not_flagged(db_env):
    person = _person()
    _, built = _run(
        _page(person, extra="DOB: 15/06/1984"),
        {"name": person, "age_text": "42 Y", "dob": "15/06/1984"},
    )
    for key in ("age_text", "dob"):
        assert "does not agree" not in str(built["patient"][key].get("reason"))


def test_an_aadhaar_number_is_not_in_the_database_anywhere_we_write(db_env):
    person = _person()
    doc, built = _run(
        _page(person, extra=f"Aadhaar: {AADHAAR}"),
        {"name": person, "phone": "98765 43210", "mrn": AADHAAR},
    )
    assert "hospital number" not in _details(doc)
    with session_scope() as c:
        payload = c.execute(
            text("SELECT payload FROM extraction WHERE document_id = :d"), {"d": doc}
        ).scalar_one()
        values = (
            c.execute(
                text(
                    "SELECT coalesce(value_text, '') || ' ' || local_text "
                    "FROM clinical_fact WHERE :d = ANY(source_doc_ids)"
                ),
                {"d": doc},
            )
            .scalars()
            .all()
        )
    assert payload["_withheld"][0] == {
        "field": "patient.mrn",
        "kind": "aadhaar",
        "why": "an Aadhaar number is not stored without a legal basis",
    }
    assert "0124" not in json.dumps(payload) and not any("0124" in v for v in values)
    assert "0124" not in json.dumps(built, default=str)


def test_fr4_an_age_alone_never_becomes_a_date_of_birth_in_the_result(db_env):
    """The page gives an age and no date of birth. The identity keeps 1 January of the
    estimated year for matching - and the result must not show it as a date of birth."""
    person = _person()
    doc, built = _run(_page(person), {"name": person, "age_text": "42 Y", "sex": "M"})
    assert "dob" not in built["patient"], "nobody wrote a date of birth"
    assert built["patient"]["age_text"]["value"] == "42 Y"
    with session_scope() as c:
        kept = c.execute(
            text(
                "SELECT i.birth_date FROM patient_identity i JOIN source_document d "
                "ON d.patient_id = i.id WHERE d.id = :d"
            ),
            {"d": doc},
        ).scalar_one()
    assert kept is not None and (kept.month, kept.day) == (1, 1), "the estimate, for matching only"


def test_fr4_a_date_of_birth_that_is_written_is_shown(db_env):
    person = _person()
    _, built = _run(
        _page(person, extra="DOB: 15/06/1984"),
        {"name": person, "age_text": "42 Y", "dob": "15/06/1984"},
    )
    assert built["patient"]["dob"]["value"] == "15/06/1984"
    assert built["patient"]["dob"]["evidence"]["observation_ids"], "as written, with its proof"
