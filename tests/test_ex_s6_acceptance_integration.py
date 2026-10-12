"""EX-S6 on real PostgreSQL: the three criteria, with plain SQL and no application code between
the test and the database - and with the NEW kinds of value (patient, doctor, ordered test,
preparation), as the build brief asks.

Needs the test database (``db_env``); skipped where there is none.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from cdi_adapter.db import get_engine, session_scope
from tests.test_evidence_schema_integration import _doc_with_observations, _one_observation

pytestmark = pytest.mark.integration

KINDS = (("patient_detail", "phone", "98765 43210"),
         ("doctor_detail", "registration number", "71112 (WBMC)"),
         ("lab_order", "HbA1c", None),
         ("preparation", "fasting 12 hrs", "tests: HbA1c"))  # fmt: skip


def _patient(c) -> str:
    return str(c.execute(text("INSERT INTO patient_identity (name) VALUES ('EX-S6 Test') "
                              "RETURNING id")).scalar_one())  # fmt: skip


def _fact(c, patient: str, kind: str, written: str, value: str | None,
          state: str = "pending") -> str:  # fmt: skip
    return str(c.execute(
        text("INSERT INTO clinical_fact (patient_id, fact_type, local_text, value_kind, "
             "value_text, review_state) VALUES (:p, :k, :t, 'text', :v, :s) RETURNING id"),
        {"p": patient, "k": kind, "t": written, "v": value, "s": state},
    ).scalar_one())  # fmt: skip


def _one(sql: str, **params) -> tuple:
    with get_engine().connect() as c:
        return tuple(c.execute(text(sql), params).one())


# ------------------------------------------------------------- AC1


def test_ac1_a_direct_edit_of_a_reading_is_refused(db_env):
    doc, _, _ = _doc_with_observations(["Mobile: 98765 43210"])
    obs = _one_observation(doc)
    with pytest.raises(IntegrityError), get_engine().begin() as c:
        c.execute(text("UPDATE ocr_observation SET raw_text = 'Mobile: 98765 48210' "
                       "WHERE id = :o"), {"o": obs})  # fmt: skip
    assert _one("SELECT raw_text FROM ocr_observation WHERE id = :o", o=obs) == (
        "Mobile: 98765 43210",)  # fmt: skip


def test_ac1_a_direct_delete_of_a_reading_is_refused(db_env):
    doc, _, _ = _doc_with_observations(["Reg No 71112 (WBMC)"])
    with pytest.raises(IntegrityError), get_engine().begin() as c:
        c.execute(text("DELETE FROM ocr_observation WHERE document_id = :d"), {"d": doc})
    assert _one("SELECT count(*) FROM ocr_observation WHERE document_id = :d", d=doc)[0] >= 1


# ------------------------------------------------------------- AC2


@pytest.mark.parametrize(("kind", "written", "value"), KINDS)
def test_ac2_a_new_kind_of_value_with_no_proof_cannot_be_accepted(db_env, kind, written, value):
    with session_scope() as c:
        fact = _fact(c, _patient(c), kind, written, value)
    with pytest.raises(IntegrityError, match="cannot be auto_accepted"):
        with get_engine().begin() as c:
            c.execute(text("UPDATE clinical_fact SET review_state = 'auto_accepted' "
                           "WHERE id = :f"), {"f": fact})  # fmt: skip
    assert _one("SELECT review_state FROM clinical_fact WHERE id = :f", f=fact) == ("pending",)


@pytest.mark.parametrize(("kind", "written", "value"), KINDS)
def test_ac2_nor_inserted_already_accepted(db_env, kind, written, value):
    with pytest.raises(IntegrityError, match="cannot be auto_accepted"):
        with get_engine().begin() as c:
            _fact(c, _patient(c), kind, written, value, state="auto_accepted")


@pytest.mark.parametrize(("kind", "written", "value"), KINDS)
def test_ac2_with_its_proof_it_can(db_env, kind, written, value):
    with get_engine().begin() as c:
        fact = _fact(c, _patient(c), kind, written, value)
        proof = text("INSERT INTO fact_provenance (fact_id, extracted_text, bbox_union) "
                     "VALUES (:f, :t, ARRAY[10, 20, 300, 44])")  # fmt: skip
        c.execute(proof, {"f": fact, "t": written})
        c.execute(text("UPDATE clinical_fact SET review_state = 'auto_accepted' WHERE id = :f"),
                  {"f": fact})  # fmt: skip
    assert _one("SELECT review_state FROM clinical_fact WHERE id = :f", f=fact) == (
        "auto_accepted",)  # fmt: skip


def test_ac2_taking_the_proof_of_an_accepted_value_away_is_refused(db_env):
    with get_engine().begin() as c:
        fact = _fact(c, _patient(c), "preparation", "fasting 12 hrs", "all")
        c.execute(text("INSERT INTO fact_provenance (fact_id, extracted_text) "
                       "VALUES (:f, 'fasting 12 hrs')"), {"f": fact})  # fmt: skip
        c.execute(text("UPDATE clinical_fact SET review_state = 'auto_accepted' WHERE id = :f"),
                  {"f": fact})  # fmt: skip
    with pytest.raises(IntegrityError, match="cannot be auto_accepted"):
        with get_engine().begin() as c:
            c.execute(text("DELETE FROM fact_provenance WHERE fact_id = :f"), {"f": fact})


# ------------------------------------------------------------- AC3


def test_ac3_a_correction_is_a_new_value_and_the_original_is_unchanged(db_env):
    from cdi_adapter.webapp import review

    with get_engine().begin() as c:
        original = _fact(c, _patient(c), "patient_detail", "phone", "98765 48210",
                         state="in_review")  # fmt: skip
        c.execute(text("INSERT INTO fact_provenance (fact_id, extracted_text, bbox_union) "
                       "VALUES (:f, 'Mobile: 98765 43210', ARRAY[10, 20, 300, 44])"),
                  {"f": original})  # fmt: skip
    with session_scope() as c:
        fact = dict(c.execute(text("SELECT * FROM clinical_fact WHERE id = :f"),
                              {"f": original}).mappings().one())  # fmt: skip
        new_id = review.apply_fact_correction(c, fact, {"value_text": "98765 43210"},
                                              "reviewer-1")  # fmt: skip
    assert str(new_id) != original
    assert _one("SELECT value_text, review_state, is_current, supersedes::text "
                "FROM clinical_fact WHERE id = :f", f=str(new_id)) == (
        "98765 43210", "corrected", True, original)  # fmt: skip
    # the original: its value as it was read, only marked as replaced
    assert _one("SELECT value_text, local_text, is_current FROM clinical_fact WHERE id = :f",
                f=original) == ("98765 48210", "phone", False)  # fmt: skip
    # and the new value carries the same proof
    assert _one("SELECT extracted_text FROM fact_provenance WHERE fact_id = :f",
                f=str(new_id)) == ("Mobile: 98765 43210",)  # fmt: skip
