"""S7 longitudinal reconciliation: medication continuity across encounters (backlog E5-S1).

A medication reordered or changed at a later encounter supersedes the earlier order.
``reconcile_patient_medications(conn, patient_id)`` derives the patient's supersession links from
scratch (idempotent, so it runs after every S6 gate pass and every reviewer decision):

* only **governed**, **current** medication orders take part (``auto_accepted`` /
  ``clinician_confirmed`` / ``corrected``): a held fact never removes a verified order;
* the **same medication** is the same bound code, else the same normalized medicine name (the
  catalogue's name key: the name without form words and strength);
* orders are placed on their **encounter date**; an order without one is left alone. The orders of
  a later date supersede those of the latest earlier date - so an older document uploaded
  afterwards never supersedes a newer order - and orders of one date never supersede each other;
* **nothing is deleted** from ``clinical_fact``: the earlier order stays, in
  ``v_medication_history``, linked in ``medication_supersession``; ``v_medication_list`` (the
  active list) shows the latest order only. Each new link is written to ``audit_log``.

``clinical_fact.supersedes`` is untouched: it remains a reviewer's correction.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, text

from cdi_adapter import repo
from cdi_adapter.catalogue.normalize import split_name

GOVERNED = ("auto_accepted", "clinician_confirmed", "corrected")
_CHANGES = (("strength_num", "strength_changed"), ("dose_num", "dose_changed"),
            ("frequency_code", "frequency_changed"),
            ("duration_days", "duration_changed"))  # fmt: skip


def match_key(order: dict[str, Any]) -> str | None:
    """What makes two orders the same medication: the bound code, else the medicine's name key."""
    if order.get("code_system") and order.get("code"):
        return f"code:{order['code_system']}|{order['code']}"
    name, _ = split_name(order.get("drug_text") or order.get("local_text"))
    return f"name:{name}" if name else None


def change_kind(new: dict[str, Any], old: dict[str, Any]) -> str:
    changed = [kind for col, kind in _CHANGES if _differs(new.get(col), old.get(col))]
    if not changed:
        return "reordered"
    return changed[0] if len(changed) == 1 else "changed"


def _differs(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return False  # not written on one of them: not a change we can assert
    if isinstance(a, int | float) or isinstance(b, int | float):
        try:
            return abs(float(a) - float(b)) > 1e-9
        except (TypeError, ValueError):
            return str(a) != str(b)
    return str(a).strip().lower() != str(b).strip().lower()


def _orders(conn: Connection, patient_id: UUID | str) -> list[dict[str, Any]]:
    rows = conn.execute(
        text("SELECT f.id, f.code_system, f.code, f.local_text, f.source_doc_ids, f.created_at, "
             "  e.period_start AS encounter_date, m.drug_text, m.strength_num, m.dose_num, "
             "  m.frequency_code, m.duration_days "
             "FROM clinical_fact f "
             "JOIN encounter e ON e.id = f.encounter_id "
             "LEFT JOIN medication_detail m ON m.fact_id = f.id "
             "WHERE f.patient_id = :p AND f.fact_type = 'medication' AND f.is_current "
             "  AND f.review_state IN ('auto_accepted', 'clinician_confirmed', 'corrected') "
             "  AND e.period_start IS NOT NULL"),
        {"p": str(patient_id)},
    ).mappings()  # fmt: skip
    return [dict(r) for r in rows]


def desired_links(orders: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    """(newer fact, superseded fact) -> link, for every medication: the orders of each encounter
    date supersede those of the latest earlier date."""
    by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for o in orders:
        key = match_key(o)
        if key:
            by_key[key].append(o)
    links: dict[tuple[str, str], dict[str, Any]] = {}
    for key, group in by_key.items():
        dates = sorted({o["encounter_date"] for o in group})
        for earlier, later in zip(dates, dates[1:], strict=False):
            for new in (o for o in group if o["encounter_date"] == later):
                for old in (o for o in group if o["encounter_date"] == earlier):
                    links[(str(new["id"]), str(old["id"]))] = {
                        "match_key": key, "change_kind": change_kind(new, old),
                        "encounter_date": later, "superseded_date": earlier,
                    }  # fmt: skip
    return links


def reconcile_patient_medications(conn: Connection, patient_id: UUID | str | None,
                                  *, actor: str = "system") -> dict[str, int]:  # fmt: skip
    """Bring the patient's supersession links in line with their governed orders."""
    if patient_id is None:
        return {"added": 0, "removed": 0}
    want = desired_links(_orders(conn, patient_id))
    have = {(str(r.fact_id), str(r.supersedes_fact_id)): r.id for r in conn.execute(
        text("SELECT id, fact_id, supersedes_fact_id FROM medication_supersession "
             "WHERE patient_id = :p"), {"p": str(patient_id)},
    )}  # fmt: skip
    stale = [have[k] for k in have if k not in want]  # an order corrected, rejected or re-dated
    if stale:
        conn.execute(text("DELETE FROM medication_supersession WHERE id = ANY(CAST(:i AS uuid[]))"),
                     {"i": [str(i) for i in stale]})  # fmt: skip
    added = 0
    for (new, old), link in want.items():
        if (new, old) in have:
            continue
        conn.execute(
            text("INSERT INTO medication_supersession (patient_id, fact_id, supersedes_fact_id, "
                 "  match_key, change_kind, encounter_date, superseded_date) "
                 "VALUES (:p, :n, :o, :k, :c, :d, :sd)"),
            {"p": str(patient_id), "n": new, "o": old, "k": link["match_key"],
             "c": link["change_kind"], "d": link["encounter_date"],
             "sd": link["superseded_date"]},
        )  # fmt: skip
        repo.write_audit(conn, actor=actor, action="update", entity="clinical_fact", entity_id=old,
                         detail={"medication_superseded_by": new, **{k: str(v) for k, v in
                                 link.items()}})  # fmt: skip
        added += 1
    return {"added": added, "removed": len(stale)}
