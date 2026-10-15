"""OUT-S1 on real PostgreSQL: the three criteria through ``write_extraction`` itself, read back
with plain SQL.

Needs the test database (``db_env``) with migration 0039; skipped where there is none.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import text

from cdi_adapter.db import get_engine, session_scope
from cdi_adapter.extract import service as X
from cdi_adapter.persist import normalized
from tests.test_evidence_schema_integration import _doc_with_observations

pytestmark = pytest.mark.integration

PAGE = ["Dr. Anita Sen Roy  MBBS, MD   Reg No 71112 (WBMC)", "Name: Rahul Sharma   Age: 45 Y / M",
        "Mobile: 98765 43210", "Diagnosis: Type 2 diabetes mellitus", "Adv: HbA1c, Lipid profile",
        "Lipid profile - fasting 12 hrs"]  # fmt: skip
PAYLOAD: dict[str, Any] = {
    "patient": {"name": "Rahul Sharma", "age": "45 Y", "sex": "male", "phone": "98765 43210"},
    "prescriber": "Dr. Anita Sen Roy",
    "prescriber_details": {"registration_number": "71112 (WBMC)", "qualification": "MBBS, MD"},
    "diagnoses": ["Type 2 diabetes mellitus"],
    # the note is on the test's own line: that is what ties it to the test (EX-S4) - what the
    # model merely says a note applies to is not taken on trust
    "investigations_advised": ["HbA1c", "Lipid profile - fasting 12 hrs"],
}
COUNTS = ("SELECT (SELECT count(*) FROM v_rx_patient_detail WHERE document_id = :d), "
          "(SELECT count(*) FROM v_rx_doctor_detail WHERE document_id = :d), "
          "(SELECT count(*) FROM rx_lab_test WHERE document_id = :d), "
          "(SELECT count(*) FROM rx_preparation WHERE document_id = :d), "
          "(SELECT count(*) FROM rx_test_context WHERE document_id = :d)")  # fmt: skip


def _save(doc: str, run: str, blocks: list[dict[str, Any]], conn: Any = None) -> dict[str, Any]:
    payload = X.annotate(dict(PAYLOAD), "prescription", blocks)
    args: dict[str, Any] = {
        "document_id": doc, "doc_type": "prescription", "payload": payload, "blocks": blocks,
        "block_id_map": {f"b{i}": str(b["id"]) for i, b in enumerate(blocks, 1)},
        "run_ids": [run], "model_stack": {"extractor": "test", **X.versions("prescription")},
    }  # fmt: skip
    if conn is not None:
        return X.write_extraction(conn, **args)
    with session_scope() as c:
        return X.write_extraction(c, **args)


def _all(sql: str, **params: Any) -> list[tuple]:
    with get_engine().connect() as c:
        return [tuple(r) for r in c.execute(text(sql), params).all()]


def _shape(doc: str) -> dict[str, list[tuple]]:
    """The rows without their ids and times - what must be the same after a second save."""
    return {
        "patient": _all("SELECT detail, value FROM v_rx_patient_detail WHERE document_id = :d "
                        "ORDER BY 1, 2", d=doc),
        "doctor": _all("SELECT detail, value FROM v_rx_doctor_detail WHERE document_id = :d "
                       "ORDER BY 1, 2", d=doc),
        "tests": _all("SELECT as_written, standard_name, code, kind, match FROM rx_lab_test "
                      "WHERE document_id = :d ORDER BY 1", d=doc),
        "preparation": _all("SELECT text, type, amount, unit, applies_to, "
                            "cardinality(test_fact_ids) FROM rx_preparation "
                            "WHERE document_id = :d ORDER BY 1", d=doc),
        "context": _all("SELECT test_as_written, reason_type, reason_text, relation "
                        "FROM v_rx_test_context WHERE document_id = :d ORDER BY 1, 2, 3", d=doc),
    }  # fmt: skip


def test_ac1_patient_doctor_test_preparation_and_context_rows_exist(db_env):
    doc, run, blocks = _doc_with_observations(PAGE)
    _save(doc, run, blocks)
    patient, doctor, tests, preps, links = _all(COUNTS, d=doc)[0]
    assert patient >= 3 and doctor >= 1 and tests == 2 and preps == 1 and links >= 2


def test_ac1_every_row_links_to_its_evidence(db_env):
    doc, run, blocks = _doc_with_observations(PAGE)
    _save(doc, run, blocks)
    for view in ("v_rx_patient_detail", "v_rx_doctor_detail", "v_rx_lab_test", "v_rx_preparation",
                 "v_rx_test_context"):  # fmt: skip
        rows = _all(f"SELECT evidence_id, page_no, cardinality(ocr_block_ids) FROM {view} "
                    "WHERE document_id = :d", d=doc)  # fmt: skip
        assert rows and all(e is not None and p == 1 and n >= 1 for e, p, n in rows), view
    # the preparation points at the test it is written for
    assert _all("SELECT t.as_written FROM rx_preparation r JOIN rx_lab_test t "
                "ON t.fact_id = ANY(r.test_fact_ids) WHERE r.document_id = :d", d=doc) == [
        ("Lipid profile",)]  # fmt: skip


def test_ac1_the_versions_are_saved_with_every_value(db_env):
    doc, run, blocks = _doc_with_observations(PAGE)
    _save(doc, run, blocks)
    stacks = _all("SELECT model_stack FROM v_rx_evidence WHERE document_id = :d", d=doc)
    assert stacks and all(s[0].get("prompt_version") and s[0].get("lab_master") for s in stacks)


def test_ac2_processed_twice_the_rows_are_the_same(db_env):
    doc, run, blocks = _doc_with_observations(PAGE)
    _save(doc, run, blocks)
    first, counts = _shape(doc), _all(COUNTS, d=doc)
    _save(doc, run, blocks)
    assert _shape(doc) == first and _all(COUNTS, d=doc) == counts
    with session_scope() as c:  # and refreshing alone changes nothing either
        normalized.refresh(c, doc)
    assert _shape(doc) == first


def test_ac2_no_row_outlives_its_fact(db_env):
    doc, run, blocks = _doc_with_observations(PAGE)
    _save(doc, run, blocks)
    with session_scope() as c:
        c.execute(text("DELETE FROM clinical_fact WHERE :d = ANY(source_doc_ids)"), {"d": doc})
    assert _all(COUNTS, d=doc) == [(0, 0, 0, 0, 0)]


def test_ac3_a_failure_half_way_stores_nothing_of_the_document(db_env, monkeypatch):
    doc, run, blocks = _doc_with_observations(PAGE)

    def breaks(conn: Any, document_id: Any) -> None:
        conn.execute(text("INSERT INTO rx_lab_test (fact_id, document_id, as_written, match, "
                          "master_version) SELECT id, :d, 'x', 'matched', 'v' FROM clinical_fact "
                          "WHERE :d = ANY(source_doc_ids) LIMIT 1"),
                     {"d": str(document_id)})  # fmt: skip
        raise RuntimeError("the database went away")

    monkeypatch.setattr(X.normalized, "refresh", breaks)
    with pytest.raises(RuntimeError), session_scope() as c:
        _save(doc, run, blocks, conn=c)
    assert _all(COUNTS, d=doc) == [(0, 0, 0, 0, 0)]
    assert _all("SELECT (SELECT count(*) FROM clinical_fact WHERE :d = ANY(source_doc_ids)), "
                "(SELECT count(*) FROM extraction WHERE document_id = :d), "
                "(SELECT count(*) FROM encounter WHERE source_document_id = :d)", d=doc) == [
        (0, 0, 0)]  # fmt: skip


def test_ac3_a_failed_second_save_leaves_the_first_one_whole(db_env, monkeypatch):
    doc, run, blocks = _doc_with_observations(PAGE)
    _save(doc, run, blocks)
    first = _shape(doc)

    def breaks(conn: Any, document_id: Any) -> None:
        raise RuntimeError("the database went away")

    monkeypatch.setattr(X.normalized, "refresh", breaks)
    with pytest.raises(RuntimeError), session_scope() as c:
        _save(doc, run, blocks, conn=c)
    assert _shape(doc) == first
