"""E5-S1: medication continuity across encounters on the real pipeline (PostgreSQL).

A medication reordered or changed at a later encounter supersedes the earlier order in the active
list (``v_medication_list``); the earlier order is never deleted and stays in the history
(``v_medication_history``), linked in ``medication_supersession``."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from cdi_adapter import repo
from cdi_adapter.canon.repo import current_fact
from cdi_adapter.db import session_scope
from cdi_adapter.terminology.service import bind_document
from cdi_adapter.validate.service import validate_document
from cdi_adapter.webapp.review import ReviewDecision, decide
from tests.test_extract_integration import _doc_with_ocr, _write

pytestmark = pytest.mark.integration


def _name() -> str:
    return "Lng" + uuid.uuid4().hex[:8].translate(str.maketrans("0123456789", "ghijklmnop"))


def _visit(name: str, day: str, meds: list[str]) -> tuple[str, str]:
    """One prescription of patient ``name`` on ``day`` (dd/mm/yyyy), through S4 -> S6."""
    lines = ["CareFlow Polyclinic", f"Name: {name} Das   Age/Sex: 48 Y / F", f"Date: {day}",
             "Diagnosis: Hypertension", "Rx", *meds]  # fmt: skip
    doc, blocks = _doc_with_ocr(lines)
    out = _write(doc, blocks)
    bind_document(doc)
    validate_document(doc)
    return doc, str(out["patient_id"])


def _active(patient: str) -> list[tuple[str, str]]:
    with session_scope() as c:
        return sorted((r.medication, r.doc) for r in c.execute(
            text("SELECT v.medication, f.source_doc_ids[1]::text AS doc FROM v_medication_list v "
                 "JOIN clinical_fact f ON f.id = v.fact_id WHERE v.patient_id = :p"),
            {"p": patient}))  # fmt: skip


def _history(patient: str) -> list[dict]:
    with session_scope() as c:
        return [dict(r) for r in c.execute(
            text("SELECT h.*, f.source_doc_ids[1]::text AS doc FROM v_medication_history h "
                 "JOIN clinical_fact f ON f.id = h.fact_id WHERE h.patient_id = :p "
                 "ORDER BY h.encounter_date, h.medication"), {"p": patient},
        ).mappings()]  # fmt: skip


def _amlodipine(rows: list[dict], doc: str) -> dict:
    return next(r for r in rows if r["doc"] == doc and "mlodipine" in r["medication"])


def test_a_later_reorder_supersedes_the_earlier_order_which_is_kept_in_history(db_env):
    name = _name()
    v1, p1 = _visit(name, "01/08/2026", ["Tab Amlodipine 5 mg OD x 30 days",
                                          "Tab Atorvastatin 10 mg 0-0-1"])  # fmt: skip
    v2, p2 = _visit(name, "01/09/2026", ["Tab Amlodipine 10 mg OD x 30 days"])
    assert p1 == p2
    active = _active(p1)
    # the active list: the latest Amlodipine (visit 2) and the Atorvastatin nobody reordered
    assert [d for m, d in active if "mlodipine" in m] == [v2]
    assert [d for m, d in active if "torvastatin" in m] == [v1]
    history = _history(p1)
    old, new = _amlodipine(history, v1), _amlodipine(history, v2)
    assert old["order_status"] == "superseded" and new["order_status"] == "active"
    assert old["superseded_by_fact_id"] == new["fact_id"]
    assert new["supersedes_fact_id"] == old["fact_id"]
    assert old["superseded_change"] == "strength_changed"
    with session_scope() as c:  # nothing deleted, both orders still current facts
        states = c.execute(text("SELECT is_current FROM clinical_fact WHERE id IN (:a, :b)"),
                           {"a": old["fact_id"], "b": new["fact_id"]}).scalars().all()  # fmt: skip
        audit = c.execute(text("SELECT count(*) FROM audit_log WHERE entity_id = :o "
                               "AND detail ? 'medication_superseded_by'"),
                          {"o": str(old["fact_id"])}).scalar_one()  # fmt: skip
    assert states == [True, True] and audit == 1


def test_an_older_document_uploaded_later_never_supersedes_the_newer_order(db_env):
    name = _name()
    newer, p = _visit(name, "01/09/2026", ["Tab Amlodipine 10 mg OD x 30 days"])
    older, _ = _visit(name, "01/08/2026", ["Tab Amlodipine 5 mg OD x 30 days"])
    assert [d for m, d in _active(p) if "mlodipine" in m] == [newer]
    assert _amlodipine(_history(p), older)["order_status"] == "superseded"


def test_orders_of_the_same_date_and_different_medicines_stay_active(db_env):
    name = _name()
    a, p = _visit(name, "01/08/2026", ["Tab Amlodipine 5 mg OD x 30 days"])
    b, _ = _visit(name, "01/08/2026", ["Tab Amlodipine 5 mg OD x 30 days"])
    assert sorted(d for m, d in _active(p) if "mlodipine" in m) == sorted([a, b])


def test_a_held_order_supersedes_nothing_until_a_reviewer_decides_it(db_env):
    name = _name()
    v1, p = _visit(name, "01/08/2026", ["Tab Metformin 500 mg 1-0-1 x 30 days"])
    v2, _ = _visit(name, "01/09/2026", ["Tab Metformin 5000 mg 1-1-1"])  # over the ceiling: held
    assert [d for m, d in _active(p) if "etformin" in m and d == v1] == [v1]  # still active
    with session_scope() as c:
        held = next(f for f in repo.get_facts_for_document(c, v2)
                    if f["fact_type"] == "medication")  # fmt: skip
        assert held["review_state"] == "in_review"
        out = decide(c, held["id"], ReviewDecision(action="correct",
                                                   corrections={"strength_num": 1000}),
                     "dr-e5s1")  # fmt: skip
    corrected = out["result_fact_id"]
    active = [(m, d) for m, d in _active(p) if "etformin" in m]
    assert [d for _, d in active] == [v2]  # the decided order now supersedes visit 1's
    old = next(r for r in _history(p) if r["doc"] == v1)
    assert str(old["superseded_by_fact_id"]) == corrected
    with session_scope() as c:  # a reviewer's correction chain is untouched by the reorder link
        v1_fact = repo.get_facts_for_document(c, v1)[0]["id"]
        assert str(current_fact(c, v1_fact)["id"]) == str(v1_fact)


def test_reconciliation_is_idempotent(db_env):
    name = _name()
    _visit(name, "01/08/2026", ["Tab Amlodipine 5 mg OD x 30 days"])
    v2, p = _visit(name, "01/09/2026", ["Tab Amlodipine 10 mg OD x 30 days"])
    validate_document(v2)  # the gate again: the links are re-derived, not doubled
    with session_scope() as c:
        n = c.execute(text("SELECT count(*) FROM medication_supersession WHERE patient_id = :p"),
                      {"p": p}).scalar_one()  # fmt: skip
    assert n == 1
