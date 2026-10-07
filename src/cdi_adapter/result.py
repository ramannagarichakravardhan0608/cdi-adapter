"""The prescription result as one JSON file (backlog UP-S3, the contract of OUT-S2).

The upload screen has to show a person what was read and let them take it away as a file. Both are
fed from **here**, not from the internal tables, so what the screen shows and what the file holds
can never disagree - and a consumer that is not this screen reads exactly the same document.

The shape is the owner's **Output Contract** (the sheet of the same name in the upload-screen
backlog): ``schema_version: "result.v1"``, then ``patient``, ``doctor``, ``visit``,
``lab_tests[]``, ``diagnoses[]``, ``preparation[]``, ``review`` and ``provenance``, where **every
clinical value is an object**::

    {"value": …, "status": "accepted" | "needs_check", "confidence": 0.0-1.0,
     "evidence": {"page": 1, "bbox": [x0, y0, x1, y1], "observation_ids": […]}}

Two rules the contract states and this module keeps:

* **a value never appears without its status** - :func:`value` is the only way one is built, and it
  cannot produce a bare value;
* **``accepted`` only with proof** - a fact the S6 gate did not govern is ``needs_check``, and so
  is a governed fact whose provenance carries no evidence.

**What is deliberately absent.** The contract marks many fields ``NEW``: preparation items,
test context and ``preparation_refs`` (EX-S4/EX-S5), the clinic block, the patient's phone and
address (EX-S1/EX-S2), stamp/signature detection, the engine version block. The tables and
extractors behind them are not built (that is OUT-S1's work), so those keys are **left out**
rather than filled with an invented value or a null that reads like "none found". ``absent`` lists
them, so a reader can tell "not extracted yet" from "not written on the page".

The field names are the contract's own, and the contract says they are provisional until OUT-S2's
open question - which system reads the JSON - is answered. ``schema_version`` is what a consumer
pins; ``schemas/result.v1.json`` validates what we emit.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, text

SCHEMA_VERSION = "result.v1"
SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schemas" / "result.v1.json"

# the S6 gate's governed states: only these may be "accepted" (ARCHITECTURE §5 S6)
GOVERNED = ("auto_accepted", "clinician_confirmed", "corrected")
ACCEPTED, NEEDS_CHECK = "accepted", "needs_check"

# contract fields whose extractor or table is not built yet (OUT-S1, EX-S1/S2/S4/S5): reported as
# absent rather than guessed. Keeping the list here means the screen and the file agree on it.
ABSENT: tuple[str, ...] = (
    "patient.phone", "patient.address", "patient.dob",
    "doctor.clinic", "doctor.qualification", "doctor.stamp_present", "doctor.signature_present",
    "lab_tests[].specimen", "lab_tests[].urgency", "lab_tests[].context",
    "lab_tests[].preparation_refs", "preparation", "engines",
)  # fmt: skip


def value(raw: Any, *, review_state: str | None, confidence: float | None,
          evidence: dict[str, Any] | None = None,
          reason: str | None = None) -> dict[str, Any]:  # fmt: skip
    """One contract value object. The **only** way a value reaches the result, so a value without
    a status cannot be built: ``accepted`` needs a governed state *and* evidence."""
    governed = review_state in GOVERNED
    proven = bool(evidence and (evidence.get("bbox") or evidence.get("observation_ids")))
    out: dict[str, Any] = {
        "value": raw,
        "status": ACCEPTED if governed and proven else NEEDS_CHECK,
        "confidence": round(float(confidence), 3) if confidence is not None else None,
    }
    if evidence:
        out["evidence"] = evidence
    if out["status"] == NEEDS_CHECK:
        out["reason"] = reason or ("not governed by the gate" if not governed
                                   else "no evidence recorded")  # fmt: skip
    return out


def _evidence(conn: Connection, fact_id: Any) -> dict[str, Any] | None:
    """Where the value came from: the page, the box on it, and the saved readings behind it."""
    rows = conn.execute(
        text("SELECT p.bbox_union, p.ocr_block_ids, p.page_id, g.page_no "
             "FROM fact_provenance p LEFT JOIN document_page g ON g.id = p.page_id "
             "WHERE p.fact_id = :f ORDER BY p.recorded_at LIMIT 1"), {"f": fact_id},
    ).mappings().first()  # fmt: skip
    if rows is None:
        return None
    blocks = [str(i) for i in (rows["ocr_block_ids"] or [])]
    return {"page": rows["page_no"], "bbox": rows["bbox_union"], "observation_ids": blocks}


def _fact_value(conn: Connection, f: dict[str, Any], raw: Any) -> dict[str, Any]:
    return value(raw, review_state=f.get("review_state"),
                 confidence=f.get("confidence_overall"),
                 evidence=_evidence(conn, f["id"]))  # fmt: skip


def _text_of(f: dict[str, Any]) -> Any:
    """What was written, preferring the reading over our own normalisation (E4-S7)."""
    for key in ("local_text", "value_text", "code_display"):
        if f.get(key):
            return f[key]
    return f.get("value_num")


def build(conn: Connection, document_id: UUID | str) -> dict[str, Any]:
    """The result document for one prescription. Deterministic: the same database state builds the
    same bytes (no clock, no ordering by anything unstable), so OUT-S2's "identical twice" holds."""
    row = conn.execute(
        text("SELECT id, filename, mime_type, page_count, status, source_channel, created_at, "
             "  patient_id FROM source_document WHERE id = :d"), {"d": str(document_id)},
    ).mappings().first()  # fmt: skip
    if row is None:
        raise LookupError(f"no document {document_id}")
    doc: dict[str, Any] = dict(row)

    facts = [f for f in _facts(conn, document_id) if f["is_current"]]
    out: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "document_id": str(doc["id"]),
        "received_at": _iso(doc["created_at"]),
        "source": "upload_screen" if doc["source_channel"] == "upload" else doc["source_channel"],
        "page_count": doc["page_count"] or 0,
        "document": {"source": {"channel": doc["source_channel"], "file_name": doc["filename"]}},
        "patient": _patient(conn, doc),
        "doctor": _doctor(conn, facts),
        "visit": _visit(conn, facts),
        "lab_tests": _lab_tests(conn, facts),
        "diagnoses": _diagnoses(conn, facts),
        "absent": list(ABSENT),
    }
    out["review"] = _review(out)
    out["status"] = _status(doc, out)
    return out


def _iso(when: Any) -> str | None:
    return when.isoformat() if isinstance(when, datetime) else (str(when) if when else None)


def _facts(conn: Connection, document_id: UUID | str) -> list[dict[str, Any]]:
    from cdi_adapter import repo

    return repo.get_facts_for_document(conn, document_id)


def _patient(conn: Connection, doc: dict[str, Any]) -> dict[str, Any]:
    """Identity as the documents gave it. The MPI row is the system's own record, so its fields
    carry the identity confidence rather than a per-fact one."""
    if not doc["patient_id"]:
        return {}
    p = conn.execute(
        text("SELECT mpi_id, name, sex, birth_date, legacy_mrn, abha_number, identity_confidence "
             "FROM patient_identity WHERE id = :i"), {"i": doc["patient_id"]},
    ).mappings().first()  # fmt: skip
    if p is None:
        return {}
    conf = p["identity_confidence"]
    ev: dict[str, Any] = {"page": None, "bbox": None, "observation_ids": []}
    out: dict[str, Any] = {}
    for key, raw in (("name", p["name"]), ("sex", p["sex"])):
        if raw:
            out[key] = value(raw, review_state="auto_accepted", confidence=conf, evidence=ev,
                             reason="identity is read from the documents, not typed")  # fmt: skip
    if p["birth_date"]:
        out["dob"] = value(str(p["birth_date"]), review_state="auto_accepted", confidence=conf,
                           evidence=ev)  # fmt: skip
    known = (("mpi_id", p["mpi_id"]), ("mrn", p["legacy_mrn"]), ("abha", p["abha_number"]))
    ids = [{"type": t, "value": v} for t, v in known if v]
    if ids:
        out["ids"] = ids
    return out


def _by_type(facts: list[dict[str, Any]], *types: str) -> list[dict[str, Any]]:
    return [f for f in facts if f["fact_type"] in types]


def _doctor(conn: Connection, facts: list[dict[str, Any]]) -> dict[str, Any]:
    """What the letterhead gave. The clinic block, qualification and stamp/signature are contract
    fields nothing extracts yet (see :data:`ABSENT`)."""
    out: dict[str, Any] = {}
    for f in _by_type(facts, "referral", "document"):
        text_value = _text_of(f)
        if text_value and "name" not in out:
            out["name"] = _fact_value(conn, f, text_value)
    out["link"] = {"method": "unknown", "confidence": None, "practitioner_id": None}
    return out


def _visit(conn: Connection, facts: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    enc = conn.execute(
        text("SELECT period_start FROM encounter WHERE id = :e"),
        {"e": next((f["encounter_id"] for f in facts if f.get("encounter_id")), None)},
    ).scalar_one_or_none() if facts else None  # fmt: skip
    if enc:
        out["encounter_date"] = value(str(enc), review_state="auto_accepted", confidence=None,
                                      evidence={"page": None, "bbox": None,
                                                "observation_ids": []})  # fmt: skip
    return out


def _lab_tests(conn: Connection, facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The investigations this prescription orders. ``cn_lab_order`` holds the advised tests
    (E12 lab_order); a lab *result* on the same page is reported with its value."""
    out = []
    for f in _by_type(facts, "lab_result", "procedure", "diagnostic_report"):
        written = _text_of(f)
        row: dict[str, Any] = {
            "as_written": written,
            "kind": "panel" if f["fact_type"] == "diagnostic_report" else "test",
            "value": _fact_value(conn, f, written),
        }
        if f.get("code"):
            row["standard"] = {"name": f.get("code_display"), "code_system": f.get("code_system"),
                               "code": f.get("code")}  # fmt: skip
        if f.get("value_num") is not None:
            row["result"] = _fact_value(conn, f, f["value_num"])
            row["unit"] = f.get("value_unit_ucum")
        out.append(row)
    return out


def _diagnoses(conn: Connection, facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """What was written as a diagnosis, and our own canonical term when one was bound. The
    contract is explicit that this never depends on SNOMED or ICD: external codes are optional."""
    out = []
    for f in _by_type(facts, "condition", "symptom", "finding"):
        row: dict[str, Any] = {"raw_text": _text_of(f),
                               "canonical_term": f.get("code_display"),
                               "value": _fact_value(conn, f, _text_of(f))}  # fmt: skip
        if f.get("code") and f.get("code_system"):
            row["external_codes"] = [{"system": f["code_system"], "code": f["code"]}]
        out.append(row)
    return out


def _walk_values(node: Any) -> list[dict[str, Any]]:
    """Every contract value object anywhere in the document."""
    found: list[dict[str, Any]] = []
    if isinstance(node, dict):
        if "status" in node and "value" in node:
            found.append(node)
        for v in node.values():
            found += _walk_values(v)
    elif isinstance(node, list):
        for v in node:
            found += _walk_values(v)
    return found


def _review(out: dict[str, Any]) -> dict[str, Any]:
    held = [v for v in _walk_values(out) if v["status"] == NEEDS_CHECK]
    return {"needs_check_count": len(held),
            "items": [{"value": v["value"], "reason": v.get("reason")} for v in held]}  # fmt: skip


def _status(doc: dict[str, Any], out: dict[str, Any]) -> str:
    if doc["status"] == "error":
        return "held_for_rescan"
    return "complete" if out["review"]["needs_check_count"] == 0 else "needs_check"


def as_json(conn: Connection, document_id: UUID | str) -> str:
    """The file's bytes: sorted keys and a stable separator, so two builds of one document are
    byte-identical (OUT-S2 AC2)."""
    return json.dumps(build(conn, document_id), indent=2, sort_keys=True, default=str) + "\n"


def validate(document: dict[str, Any]) -> None:
    """Check a built result against ``schemas/result.v1.json``. Raises ``ValidationError``."""
    from jsonschema import Draft202012Validator

    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(document)
