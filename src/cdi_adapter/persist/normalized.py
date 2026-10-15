"""The prescription in clear tables another system can query (backlog OUT-S1).

Every value read from a prescription is a ``clinical_fact`` with its proof in
``fact_provenance`` - that is where it is saved, reviewed and corrected, and it stays the one
place a value lives. What an integrator needs on top of it is the shape:

* ``rx_lab_test``      - one row per ordered test: as written, its standard name and code, test /
                         panel / study, specimen, urgency, how it was matched, the "does not
                         fit" check;
* ``rx_preparation``   - one row per preparation note: the original words, type, amount, unit,
                         and the tests it applies to (all / unclear / these tests);
* ``rx_test_context``  - one row per test-and-reason link: which written reason, and how the
                         page connects it (same line / same page / other page);
* the patient's and the doctor's details are already whole in their facts: the views
  ``v_rx_patient_detail`` and ``v_rx_doctor_detail`` (migration 0039) give them with evidence.

Each row points at the fact it describes (a foreign key), and through the fact at its evidence -
so no row is without proof, and none outlives its fact. The rows are **derived**: ``refresh``
rebuilds a document's rows from its current facts, in the caller's transaction. So

* saving is one all-or-nothing step - the rows are written in the same transaction as the
  facts, and a failure half way leaves nothing of the document (AC3);
* running it twice gives the same rows - they are replaced, never added to (AC2);
* a correction or a rejection made later shows here too, because the same call follows it.

``rows_for`` is pure (facts in, rows out); ``refresh`` is the only database work.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, text

from cdi_adapter.extract import context, preparation
from cdi_adapter.terminology import lab_master

TABLES = ("rx_test_context", "rx_preparation", "rx_lab_test")  # delete order: links first
_FACTS = """
SELECT f.id, f.fact_type, f.local_text, f.value_text, f.value_num, f.value_unit_ucum,
       f.code_system, f.code, f.code_display, f.review_state, f.review_note,
       p.page_id, p.ocr_block_ids, p.extracted_text
FROM clinical_fact f
LEFT JOIN LATERAL (SELECT q.page_id, q.ocr_block_ids, q.extracted_text FROM fact_provenance q
                   WHERE q.fact_id = f.id ORDER BY q.recorded_at, q.id LIMIT 1) p ON true
WHERE CAST(:d AS uuid) = ANY(f.source_doc_ids) AND f.is_current AND f.review_state <> 'rejected'
ORDER BY f.created_at, f.id
"""


def _norm(text_: Any) -> str:
    return " ".join(str(text_ or "").lower().split())


def _does_not_fit(fact: dict[str, Any]) -> bool:
    note = fact.get("review_note")
    if isinstance(note, str):
        try:
            note = json.loads(note)
        except ValueError:
            note = None
    found = (note or {}).get("findings") or [] if isinstance(note, dict) else []
    return any(isinstance(f, dict) and f.get("code") == "context-does-not-fit" for f in found)


def rows_for(document_id: str, facts: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """The three tables' rows for one document, from its current, not-rejected facts (each with
    ``page_id`` / ``ocr_block_ids`` of its evidence). Deterministic: the same facts give the
    same rows, in the same order."""
    doc = str(document_id)
    tests = [f for f in facts if f["fact_type"] == "lab_order"]
    lab_rows = []
    for f in tests:
        seen = lab_master.describe(f.get("local_text"), f.get("code"))
        std = seen["standard"] or {}
        lab_rows.append({
            "fact_id": str(f["id"]), "document_id": doc, "as_written": f.get("local_text"),
            "standard_name": std.get("name"), "code_system": std.get("code_system"),
            "code": std.get("code"), "kind": seen["kind"], "specimen": seen["specimen"],
            "urgency": seen["urgency"], "match": seen["match"],
            "does_not_fit": _does_not_fit(f), "master_version": lab_master.version(),
        })  # fmt: skip
    by_name: dict[str, list[str]] = {}
    for f in tests:
        by_name.setdefault(_norm(f.get("local_text")), []).append(str(f["id"]))
    prep_rows = []
    for f in facts:
        if f["fact_type"] != preparation.FACT_TYPE:
            continue
        seen = preparation.describe(f.get("local_text"), f.get("value_text"))
        applies = seen["applies_to"]
        if applies == preparation.ALL:
            ids = [str(t["id"]) for t in tests]
        elif isinstance(applies, list):
            ids = [i for a in applies for i in by_name.get(_norm(a), [])]
        else:
            ids = []  # unclear: linked to no test until a person says which
        low, high = (seen.get("range") or [None, None])[:2]
        prep_rows.append({
            "fact_id": str(f["id"]), "document_id": doc, "text": f.get("local_text"),
            "type": seen["type"], "amount": seen["value"], "unit": seen["unit"],
            "range_low": low, "range_high": high,
            "applies_to": applies if isinstance(applies, str) else "tests",
            "test_fact_ids": ids, "phrases_version": preparation.version(),
        })  # fmt: skip
    reasons = [{"type": context.REASON_TYPES[f["fact_type"]], "text": f.get("local_text"),
                "quote": f.get("extracted_text") or f.get("local_text"), "id": str(f["id"]),
                "page": f.get("page_id"), "blocks": [str(b) for b in f.get("ocr_block_ids") or []]}
               for f in facts if f["fact_type"] in context.REASON_TYPES
               and (f.get("ocr_block_ids") or f.get("extracted_text"))]  # fmt: skip
    asked = [{"as_written": f.get("local_text"), "page": f.get("page_id"),
              "blocks": [str(b) for b in f.get("ocr_block_ids") or []]} for f in tests]  # fmt: skip
    by_text = {(r["type"], r["text"]): r for r in reversed(reasons)}
    link_rows = []
    paired: set[tuple[str, str]] = set()
    for f, link in zip(tests, context.link_context(asked, reasons), strict=True):
        for c in link["context"]:
            reason = by_text[(c["type"], c["text"])]
            pair = (str(f["id"]), str(reason["id"]))
            if pair in paired:
                continue  # the same reason written twice: one row per test-and-reason pair
            paired.add(pair)
            link_rows.append({
                "document_id": doc, "test_fact_id": str(f["id"]), "reason_fact_id": reason["id"],
                "reason_type": c["type"], "reason_text": c["text"], "relation": c["relation"],
                "fits": c.get("fits"), "rules_version": context.version(),
            })  # fmt: skip
    return {"rx_lab_test": lab_rows, "rx_preparation": prep_rows, "rx_test_context": link_rows}


_INSERT = {
    "rx_lab_test": (
        "INSERT INTO rx_lab_test (fact_id, document_id, as_written, standard_name, code_system, "
        "code, kind, specimen, urgency, match, does_not_fit, master_version) VALUES (:fact_id, "
        ":document_id, :as_written, :standard_name, :code_system, :code, :kind, :specimen, "
        ":urgency, :match, :does_not_fit, :master_version)"),
    "rx_preparation": (
        "INSERT INTO rx_preparation (fact_id, document_id, text, type, amount, unit, range_low, "
        "range_high, applies_to, test_fact_ids, phrases_version) VALUES (:fact_id, :document_id, "
        ":text, :type, :amount, :unit, :range_low, :range_high, :applies_to, "
        "CAST(:test_fact_ids AS uuid[]), :phrases_version)"),
    "rx_test_context": (
        "INSERT INTO rx_test_context (document_id, test_fact_id, reason_fact_id, reason_type, "
        "reason_text, relation, fits, rules_version) VALUES (:document_id, :test_fact_id, "
        ":reason_fact_id, :reason_type, :reason_text, :relation, :fits, :rules_version)"),
}  # fmt: skip


def refresh(conn: Connection, document_id: UUID | str) -> dict[str, int]:
    """Rebuild the document's rows from its current facts, in the caller's transaction: delete
    what was there, insert what the facts say now. Returns how many rows each table has."""
    doc = str(document_id)
    facts = [dict(r) for r in conn.execute(text(_FACTS), {"d": doc}).mappings().all()]
    rows = rows_for(doc, facts)
    for table in TABLES:
        conn.execute(text(f"DELETE FROM {table} WHERE document_id = :d"), {"d": doc})
    for table in reversed(TABLES):
        for row in rows[table]:
            conn.execute(text(_INSERT[table]), row)
    return {table: len(rows[table]) for table in reversed(TABLES)}


def refresh_for_fact(conn: Connection, fact: dict[str, Any]) -> None:
    """After a person's decision on one fact: every document that fact came from."""
    for doc in fact.get("source_doc_ids") or []:
        refresh(conn, doc)
