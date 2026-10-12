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
pins; ``schemas/contract/result.v1.json`` validates what we emit. It lives apart from the
model schemas in ``schemas/``: those are what the model is *asked to produce* and must
grammar-lock (E2-S3); this one describes what we *hand over*, and uses JSON Schema features
(``format``, mixed types) a decoding grammar does not need to support.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, text

SCHEMA_VERSION = "result.v1"
SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schemas" / "contract" / "result.v1.json"

# the S6 gate's governed states: only these may be "accepted" (ARCHITECTURE §5 S6)
GOVERNED = ("auto_accepted", "clinician_confirmed", "corrected")
ACCEPTED, NEEDS_CHECK = "accepted", "needs_check"

# contract fields whose extractor or table is not built yet (OUT-S1, EX-S1/S2/S4/S5): reported as
# absent rather than guessed. Keeping the list here means the screen and the file agree on it.
ABSENT: tuple[str, ...] = (
    "engines",
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
        "document": {
            "source": source_of(doc["source_channel"], doc["filename"], _arrivals(conn, doc["id"]))
        },  # fmt: skip
        "patient": _patient(conn, doc, facts),
        "doctor": _doctor(conn, facts, doc["id"]),
        "visit": _visit(conn, facts),
        "lab_tests": _lab_tests(conn, facts),
        "lab_master_version": _lab_master_version(conn, doc["id"]),
        "diagnoses": _diagnoses(conn, facts),
        "preparation": _preparation(conn, facts),
        "preparation_phrases_version": _preparation_version(facts),
        "absent": list(ABSENT),
    }
    _link_preparation(out["lab_tests"], out["preparation"])
    out["context_rules_version"] = _link_context(conn, out["lab_tests"], facts)
    out["review"] = _review(out)
    out["status"] = _status(doc, out)
    return out


# ------------------------------------------------------------------ where the result came from


def drive_of(source_id: str | None) -> str | None:
    """``local:/data/inbox`` -> ``local``: the kind of drive a watched folder is on."""
    return (source_id or "").split(":", 1)[0] or None


def _arrival(row: dict[str, Any]) -> dict[str, Any]:
    source_id = row.get("source_id") or ""
    return {"channel": "listener", "drive": drive_of(source_id),
            "folder": row.get("folder") or source_id.split(":", 1)[-1] or None,
            "file_name": row.get("original_name") or row.get("name")}  # fmt: skip


def source_of(channel: str, filename: str | None,
              arrivals: list[dict[str, Any]]) -> dict[str, Any]:  # fmt: skip
    """Where a result came from (LS-S8). **The same four keys whatever the channel** - channel,
    drive, folder, file name - so a result that arrived through the watched folder has exactly
    the shape of one uploaded on the screen; an upload simply has no drive and no folder.

    ``arrivals`` are the watched-folder files that became this document, oldest first. A
    document is stored once however often its bytes arrive, so the same file dropped twice, or
    dropped after being uploaded, is one result: the first arrival is its source, and the
    later ones are listed under ``also_received`` - present only when there are any."""
    source: dict[str, Any] = {"channel": channel, "drive": None, "folder": None,
                              "file_name": filename}  # fmt: skip
    rest = [_arrival(a) for a in arrivals]
    if channel == "listener" and rest:
        source.update(rest.pop(0))
    if rest:
        source["also_received"] = rest
    return source


def _arrivals(conn: Connection, document_id: Any) -> list[dict[str, Any]]:
    rows = conn.execute(
        text("SELECT source_id, folder, original_name, name FROM listener_file "
             "WHERE document_id = :d ORDER BY first_seen_at, id"), {"d": str(document_id)},
    ).mappings().all()  # fmt: skip
    return [dict(r) for r in rows]


def _like(typed: str) -> str:
    """What the person typed as a pattern that finds it anywhere in a name - with the three
    characters that mean something to LIKE taken literally."""
    escaped = typed.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def find_by_file_name(conn: Connection, typed: str, limit: int = 20) -> list[dict[str, Any]]:
    """The results of the files whose name contains ``typed`` (LS-S8 AC4), newest first.

    A file is found by the name it was dropped or uploaded under - and by the name it was
    kept under, if a second file of the same name made the listener add a number. A dropped
    file that never became a result (it failed, or is in quarantine) is found too, with what
    became of it, so "where is my file?" always has an answer."""
    pattern = _like(typed.strip())
    documents = conn.execute(
        text("SELECT d.id, d.filename, d.source_channel, d.status, d.created_at, d.page_count, "
             "  lf.original_name, lf.name, lf.source_id, lf.folder, lf.state "
             "FROM source_document d "
             "LEFT JOIN listener_file lf ON lf.document_id = d.id "
             "WHERE d.filename ILIKE :q ESCAPE '\\' OR lf.original_name ILIKE :q ESCAPE '\\' "
             "   OR lf.name ILIKE :q ESCAPE '\\' "
             "ORDER BY d.created_at DESC, d.id, lf.first_seen_at LIMIT :n"),
        {"q": pattern, "n": limit},
    ).mappings().all()  # fmt: skip
    found: dict[str, dict[str, Any]] = {}
    for r in documents:
        hit = found.setdefault(str(r["id"]), {
            "document_id": str(r["id"]), "file_name": r["filename"],
            "result_url": f"/result/{r['id']}", "channel": r["source_channel"],
            "drive": None, "folder": None, "status": r["status"],
            "received_at": _iso(r["created_at"]), "page_count": r["page_count"] or 0,
            "dropped_as": []})  # fmt: skip
        if r["source_id"]:
            hit["dropped_as"].append(r["original_name"] or r["name"])
            if hit["channel"] == "listener" and hit["drive"] is None:
                arrival = _arrival(dict(r))
                hit.update(drive=arrival["drive"], folder=arrival["folder"])
    no_result = conn.execute(
        text("SELECT original_name, name, source_id, folder, state, kind, first_seen_at "
             "FROM listener_file WHERE document_id IS NULL AND state <> 'seen' "
             "AND (original_name ILIKE :q ESCAPE '\\' OR name ILIKE :q ESCAPE '\\') "
             "ORDER BY first_seen_at DESC, id LIMIT :n"),
        {"q": pattern, "n": limit},
    ).mappings().all()  # fmt: skip
    return [*found.values(),
            *({"document_id": None, "file_name": r["original_name"] or r["name"],
               "result_url": None, **{k: v for k, v in _arrival(dict(r)).items()
                                      if k != "file_name"},
               "status": f"no result: {r['state']}" + (f" ({r['kind']})" if r["kind"] else ""),
               "received_at": _iso(r["first_seen_at"]), "page_count": 0, "dropped_as": []}
              for r in no_result)]  # fmt: skip


def _iso(when: Any) -> str | None:
    return when.isoformat() if isinstance(when, datetime) else (str(when) if when else None)


def _facts(conn: Connection, document_id: UUID | str) -> list[dict[str, Any]]:
    from cdi_adapter import repo

    return repo.get_facts_for_document(conn, document_id)


# EX-S1: the result's key for each patient detail read from this page
_DETAIL_KEYS = {"name": "name", "sex": "sex", "age": "age_text", "date of birth": "dob",
                "phone": "phone", "address": "address"}  # fmt: skip
_DETAIL_IDS = {"hospital number": "mrn", "abha number": "abha"}


def _patient_details(conn: Connection, facts: list[dict[str, Any]]) -> dict[str, Any]:
    """EX-S1: what THIS page says about the patient - each value as written, with its proof (the
    part of the picture) and its status. A detail the page does not give is not in the result:
    empty, and nothing to review."""
    out: dict[str, Any] = {}
    ids = []
    for f in _by_type(facts, "patient_detail"):
        label, raw = str(f["local_text"]), f.get("value_text")
        if not raw or f.get("review_state") == "rejected":
            continue
        if label in _DETAIL_KEYS:
            out[_DETAIL_KEYS[label]] = _fact_value(conn, f, raw)
        elif label in _DETAIL_IDS:
            ids.append({"type": _DETAIL_IDS[label], **_fact_value(conn, f, raw)})
    if ids:
        out["ids_read"] = ids
    return out


def _patient(conn: Connection, doc: dict[str, Any],
             facts: list[dict[str, Any]] | None = None) -> dict[str, Any]:  # fmt: skip
    """Identity as the documents gave it. The MPI row is the system's own record, so its fields
    carry the identity confidence rather than a per-fact one. A detail read from this page
    (EX-S1) takes the place of the record's: it has a proof and a status of its own."""
    read = _patient_details(conn, facts or [])
    known = _patient_record(conn, doc)
    return {**known, **read}


def _patient_record(conn: Connection, doc: dict[str, Any]) -> dict[str, Any]:
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


# EX-S2: the result's key for each doctor detail read from the page
_DOCTOR_KEYS = {"name": "name", "registration number": "registration_no",
                "qualification": "qualification", "department": "department"}  # fmt: skip
_CLINIC_KEYS = {"clinic name": "name", "clinic address": "address", "clinic phone": "phone"}
UNKNOWN_DOCTOR = {"method": "unknown", "confidence": None, "practitioner_id": None}


def _doctor(conn: Connection, facts: list[dict[str, Any]],
            document_id: Any = None) -> dict[str, Any]:  # fmt: skip
    """EX-S2: what the page says about the doctor - each value as written, with its proof and
    status - whether it is stamped and signed, and how the doctor was identified. ``link`` is
    always there: "unknown" is an answer, and the rest of the result does not depend on it."""
    out: dict[str, Any] = {}
    clinic: dict[str, Any] = {}
    for f in _by_type(facts, "doctor_detail"):
        label, raw = str(f["local_text"]), f.get("value_text")
        if not raw or f.get("review_state") == "rejected":
            continue
        if label in _DOCTOR_KEYS:
            out[_DOCTOR_KEYS[label]] = _fact_value(conn, f, raw)
        elif label in _CLINIC_KEYS:
            clinic[_CLINIC_KEYS[label]] = _fact_value(conn, f, raw)
    if "name" not in out:  # documents read before EX-S2: the name some carried as a fact
        for f in _by_type(facts, "referral", "document"):
            text_value = _text_of(f)
            if text_value and "name" not in out:
                out["name"] = _fact_value(conn, f, text_value)
    if clinic:
        out["clinic"] = clinic
    link = _doctor_link(conn, document_id)
    out["stamp_present"] = link.pop("stamp_present", None)
    out["signature_present"] = link.pop("signature_present", None)
    if stamp_text := link.pop("stamp_text", None):
        out["stamp_text"] = stamp_text
    out["link"] = link
    return out


def _doctor_link(conn: Connection, document_id: Any) -> dict[str, Any]:
    if document_id is None or conn is None:
        return dict(UNKNOWN_DOCTOR)
    from cdi_adapter.recognition import practitioner

    row = practitioner.link_of(conn, document_id)
    if row is None:
        return dict(UNKNOWN_DOCTOR)
    link: dict[str, Any] = {
        "method": row["method"],
        "confidence": float(row["confidence"]) if row["confidence"] is not None else None,
        "practitioner_id": str(row["practitioner_id"]) if row["practitioner_id"] else None,
        "stamp_present": row["stamp_present"], "signature_present": row["signature_present"],
        "stamp_text": row["stamp_text"],
    }  # fmt: skip
    if row["practitioner_id"]:
        link["practitioner_name"] = row["practitioner_name"]
    if row["candidates"]:
        link["candidates"] = row["candidates"]
    if row["why"]:
        link["why"] = row["why"]
    return link


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
    # EX-S5: the follow-up, when one is written - as written, with its own proof and status
    from cdi_adapter.extract.facts import advice_kind

    follow = [_fact_value(conn, f, _text_of(f)) for f in _by_type(facts, "advice")
              if advice_kind(f.get("local_text")) == "follow_up"
              and f.get("review_state") != "rejected"]  # fmt: skip
    if follow:
        out["follow_up"] = follow
    return out


def reasons_of(conn: Connection, facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """EX-S5: the reasons written on the prescription - its diagnoses, complaints and findings
    - each with the text it came from and its proof. One that was rejected is not a reason."""
    from cdi_adapter.extract.context import REASON_TYPES

    out = []
    for f in _by_type(facts, *REASON_TYPES):
        if f.get("review_state") == "rejected":
            continue
        ev = _evidence(conn, f["id"]) or {}
        written = _text_of(f)
        proven = bool(ev.get("bbox") or ev.get("observation_ids"))
        out.append({"type": REASON_TYPES[f["fact_type"]], "text": written, "quote": written,
                    "page": ev.get("page"), "blocks": ev.get("observation_ids") or [],
                    "evidence": ev if proven else None})  # fmt: skip
    # FR5: a link rests on a quote of the page; a reason with no proof gives no link
    return [r for r in out if r["evidence"]]


def _link_context(conn: Connection, tests: list[dict[str, Any]],
                  facts: list[dict[str, Any]]) -> str | None:  # fmt: skip
    """EX-S5: ``lab_tests[].context`` - for each ordered test, the reasons written on the same
    prescription, with how the page connects them (same line / same page / other page: never
    "ordered because") - and ``check: does not fit`` when the test goes with none of them. The
    test itself is never changed. Returns the version of the rules used, or None when there is
    no ordered test to link."""
    from cdi_adapter.extract import context

    ordered = [t for t in tests if "match" in t]  # not the lab RESULTS written on the page
    if not ordered:
        return None
    asked = [{"as_written": t["as_written"],
              "page": ((t.get("value") or {}).get("evidence") or {}).get("page"),
              "blocks": ((t.get("value") or {}).get("evidence") or {}).get("observation_ids")}
             for t in ordered]  # fmt: skip
    for test, link in zip(ordered, context.link_context(asked, reasons_of(conn, facts)),
                          strict=True):  # fmt: skip
        test["context"] = link["context"]
        if link.get("check"):
            test["check"] = link["check"]
    return context.version()


def _lab_master_version(conn: Connection, document_id: Any) -> str | None:
    """EX-S3: which version of the lab-test master named this document's tests - the one its
    terminology run used, so a result read under an older list still says so."""
    if conn is None:
        return None
    return conn.execute(
        text("SELECT model_version FROM pipeline_run WHERE document_id = :d "
             "AND stage = 'terminology' AND model_version IS NOT NULL "
             "ORDER BY started_at DESC LIMIT 1"), {"d": str(document_id)},
    ).scalar_one_or_none()  # fmt: skip


def _ordered_tests(conn: Connection, facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """EX-S3: every test or panel the prescription orders - as written (always), its standard
    name and code when the lab-test master has one, whether it is a test, a panel or a study,
    its specimen when known, and how the name was arrived at. ``match`` is ``not_matched`` for a
    text the list does not know: listed all the same, with the candidates a reviewer can pick
    from. A panel is ONE item: it is never expanded into its tests."""
    from cdi_adapter.terminology import lab_master

    out = []
    for f in _by_type(facts, "lab_order"):
        if f.get("review_state") == "rejected":
            continue
        written = _text_of(f)
        seen = lab_master.describe(str(written), f.get("code"))
        row: dict[str, Any] = {
            "as_written": written, "kind": seen["kind"], "specimen": seen["specimen"],
            "urgency": seen["urgency"], "standard": seen["standard"], "match": seen["match"],
            "matched_by": seen["how"], "value": _fact_value(conn, f, written),
        }  # fmt: skip
        if seen.get("candidates"):
            row["candidates"] = seen["candidates"]
        out.append(row)
    return out


def _lab_tests(conn: Connection, facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The investigations this prescription orders (EX-S3), then any lab *result* written on the
    same page, reported with its value."""
    out = _ordered_tests(conn, facts)
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


# UP-S3 AC1: what the patient has to do before a test. No extractor produces "preparation" as
# its own kind of fact, so it is recognised among the advice the prescription already gives:
# a piece of advice is a preparation instruction when it says one of these things. A short list
# on purpose - "take after food" is how to take a medicine, not how to prepare for a test, so
# the bare "before food" / "after food" are not here.
PREPARATION: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("fasting", ("fasting", "fast for", "overnight fast", "hours fast", "hrs fast",
                 "empty stomach", "nil by mouth", "nil orally", "npo")),
    ("after_food", ("post prandial", "post-prandial", "postprandial", "pp sample",
                    "2 hours after", "2 hrs after", "two hours after")),
    ("timing", ("early morning", "first morning", "morning sample", "24 hour", "24-hour",
                "24 hr")),
    ("collection", ("midstream", "mid-stream", "mid stream", "clean catch")),
)  # fmt: skip


def preparation_kind(written: Any) -> str | None:
    """Which kind of preparation instruction this text is, or ``None`` if it is not one."""
    low = " ".join(str(written or "").lower().split())
    for kind, phrases in PREPARATION:
        if any(p in low for p in phrases):
            return kind
    return None


def _preparation_version(facts: list[dict[str, Any]]) -> str | None:
    from cdi_adapter.extract import preparation

    return preparation.version() if _by_type(facts, "preparation") else None


def _preparation(conn: Connection, facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The preparation instructions on this prescription (EX-S4): each one a value object like
    any other (status, confidence, evidence) with its **original words** (``text``), and beside
    them its type, its value and unit when a number is written, and what it applies to -
    ``"all"``, a list of the tests as written, or ``"unclear"``. Nothing is here that was not
    written: no preparation written, no item.

    A document read before EX-S4 has no preparation facts; its advice lines that say a
    preparation are still listed, as they were (UP-S3), with no test to apply to."""
    from cdi_adapter.extract import preparation

    out = []
    for f in _by_type(facts, "preparation"):
        if f.get("review_state") == "rejected":
            continue
        written = _text_of(f)
        seen = preparation.describe(str(written), f.get("value_text"))
        item = {**_fact_value(conn, f, written), "text": written, "type": seen["type"],
                "amount": seen["value"], "unit": seen["unit"],
                "applies_to": seen["applies_to"],
                "instruction": preparation_kind(written) or seen["type"]}  # fmt: skip
        if "range" in seen:
            item["range"] = seen["range"]
        if seen["problems"]:
            item["problems"] = seen["problems"]
        out.append(item)
    for f in _by_type(facts, "advice", "care_plan"):
        written = _text_of(f)
        kind = preparation_kind(written)
        if kind:
            out.append({**_fact_value(conn, f, written), "text": written, "instruction": kind})
    return out


def _link_preparation(tests: list[dict[str, Any]], notes: list[dict[str, Any]]) -> None:
    """``lab_tests[].preparation_refs``: the positions, in ``preparation``, of the notes that
    apply to each ordered test - by what the note says ("all"), or by the test it was written
    with. A note whose test is unclear is linked to none: it waits for a person."""
    for test in tests:
        if "match" not in test:  # a lab RESULT on the page: nothing to prepare for
            continue
        name = " ".join(str(test.get("as_written") or "").lower().split())
        refs = []
        for i, note in enumerate(notes):
            applies = note.get("applies_to")
            listed = {" ".join(str(a).lower().split()) for a in applies} if isinstance(
                applies, list) else set()  # fmt: skip
            if applies == "all" or name in listed:
                refs.append(i)
        test["preparation_refs"] = refs


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


JOB_SCHEMA_VERSION = "result-job.v1"
_NOT_READ = ("quality_hold", "error", "skipped", "mismatch")  # job statuses with nothing to show
_WORST = ("complete", "needs_check", "held_for_rescan")


def build_job(conn: Connection, job_id: str) -> dict[str, Any]:
    """**One result for one upload** (UP-S1 AC2): every page sent together, as a single document.

    An upload of several pictures is one prescription photographed page by page. The pipeline
    reads each page as its own document, so this gathers them back: the patient, doctor and visit
    from the first page that gives them; the lab tests and diagnoses of every page, each tagged
    with the page it was read from; one count of what needs a person; and the worst status of
    any page - an upload with one unreadable page is not "complete".

    Nothing is re-read or re-judged here. ``documents`` holds each page's own result.v1 document
    unchanged, and the merged sections are those same value objects, so a value can never appear
    here with a status its own page did not give it.
    """
    row = conn.execute(
        text("SELECT id, state, docs, created_at FROM web_job WHERE id = :j"), {"j": job_id}
    ).mappings().first()  # fmt: skip
    if row is None:
        raise LookupError(f"no upload {job_id}")
    pages: list[dict[str, Any]] = []
    documents: list[dict[str, Any]] = []
    seen: set[str] = set()
    for n, d in enumerate(row["docs"] or [], start=1):
        page: dict[str, Any] = {"page": n, "file_name": d.get("filename"),
                                "document_id": d.get("document_id"), "status": None,
                                "reason": None}  # fmt: skip
        doc_id = d.get("document_id")
        if d.get("status") in _NOT_READ or not doc_id:
            page["status"] = "held_for_rescan" if d.get("status") != "skipped" else "not_processed"
            page["reason"] = (
                (d.get("quality") or {}).get("message")
                or d.get("error")
                or (
                    "not recognised as a clinical document"
                    if d.get("status") == "skipped"
                    else None
                )
            )
        elif doc_id in seen:  # the same picture sent twice in one upload: read once
            page["status"] = "duplicate"
        else:
            seen.add(doc_id)
            one = build(conn, doc_id)
            page["status"] = one["status"]
            documents.append({**one, "page": n})
        pages.append(page)

    source: dict[str, str | None] = {}  # which page's document a single section was taken from

    def first(section: str) -> dict[str, Any]:
        found = next((d for d in documents if d.get(section)), None)
        source[section] = found["document_id"] if found else None
        return found[section] if found else {}

    def every(section: str) -> list[dict[str, Any]]:
        # each item says which page of the upload it was read from, and which document that is
        # (the result screen needs the document to show the picture behind a value, UP-S3 AC4)
        return [{**item, "page": d["page"], "document_id": d["document_id"]}
                for d in documents for item in d.get(section, [])]  # fmt: skip

    patient_ids = {i["value"] for d in documents for i in d.get("patient", {}).get("ids", [])
                   if i.get("type") == "mpi_id"}  # fmt: skip
    out: dict[str, Any] = {
        "schema_version": JOB_SCHEMA_VERSION,
        "job_id": row["id"],
        "received_at": _iso(row["created_at"]),
        "source": "upload_screen",
        "page_count": len(pages),
        "pages": pages,
        "patient": first("patient"),
        "patients_differ": len(patient_ids) > 1,  # pages of two people sent as one upload
        "doctor": first("doctor"),
        "visit": first("visit"),
        "lab_tests": every("lab_tests"),
        "diagnoses": every("diagnoses"),
        "preparation": every("preparation"),
        "absent": list(ABSENT),
    }
    out["review"] = _review(out)
    out["from_document"] = source
    worst = [d["status"] for d in documents] + [p["status"] for p in pages
                                                if p["status"] == "held_for_rescan"]  # fmt: skip
    out["status"] = max(worst, key=_WORST.index) if worst else "held_for_rescan"
    out["documents"] = documents  # each page's own result.v1, unchanged (added after the count)
    return out


def job_as_json(conn: Connection, job_id: str) -> str:
    """The upload's one result as stable bytes (sorted keys), like :func:`as_json`."""
    return json.dumps(build_job(conn, job_id), sort_keys=True, ensure_ascii=False, indent=2)


def as_json(conn: Connection, document_id: UUID | str) -> str:
    """The file's bytes: sorted keys and a stable separator, so two builds of one document are
    byte-identical (OUT-S2 AC2)."""
    return json.dumps(build(conn, document_id), indent=2, sort_keys=True, default=str) + "\n"


# ------------------------------------------------------------------ the result as a file (OUT-S2)

FILE_SUFFIX = ".result.v1.json"


def file_name(document_id: UUID | str) -> str:
    """``<document id>.result.v1.json``: one file per prescription, named by its document, with
    the format's version in the name."""
    return f"{document_id}{FILE_SUFFIX}"


def write_file(conn: Connection, document_id: UUID | str, *, settings: Any = None,
               store: Any = None) -> dict[str, Any]:  # fmt: skip
    """Write the result of one prescription as a JSON file (OUT-S2), and say where it went.

    * **Built from the database**, by the same function that serves the API - so the file
      always matches the rows, and is the same for an upload and a dropped file.
    * **Validated first.** A result that does not fit ``result.v1.json`` raises and nothing is
      written; the file that was there before, if any, is left as it was.
    * **Written all at once.** In a folder: a temporary file beside it, then a rename, so a
      reader never sees half a file. In the document store: one ``put``, which is all or
      nothing.
    * **Writing it again gives the identical file** - same bytes, same name - so it is safe to
      write after every change (a reviewer's correction, an approval).

    Where: the document store under ``CDI_RESULT_OUTPUT_PREFIX`` and, when one is named, the
    folder ``CDI_RESULT_OUTPUT_DIR``. With neither set nothing is written."""
    from cdi_adapter.config import get_settings

    s = settings or get_settings()
    document = build(conn, document_id)
    validate(document)
    raw = (json.dumps(document, indent=2, sort_keys=True, default=str) + "\n").encode("utf-8")
    name = file_name(document["document_id"])
    out: dict[str, Any] = {"document_id": document["document_id"], "bytes": len(raw),
                           "sha256": hashlib.sha256(raw).hexdigest(), "file": None,
                           "key": None}  # fmt: skip
    if s.result_output_dir:
        folder = Path(s.result_output_dir)
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / name
        temp = folder / f".{name}.{os.getpid()}.tmp"
        try:
            temp.write_bytes(raw)
            os.replace(temp, target)
        finally:
            temp.unlink(missing_ok=True)
        out["file"] = str(target)
    if s.result_output_prefix:
        if store is None:
            from cdi_adapter.storage import get_store

            store = get_store()
        key = f"{s.result_output_prefix.strip('/')}/{name}"
        store.put_bytes(key, raw, "application/json")
        out["key"] = key
    return out


def refresh_files(document_ids: Any) -> list[dict[str, Any]]:
    """Write the result files of these documents again, each in its own short transaction.
    For after something changed them (the pipeline finished, a reviewer corrected a value, an
    item was approved). **Never raises**: the file follows the database, and a file that could
    not be written must not undo or fail what was just saved - it is logged and left for the
    next change, or for ``GET /api/documents/<id>/result.json``, which is always current."""
    from cdi_adapter.db import session_scope
    from cdi_adapter.logging import get_logger

    written = []
    for document_id in dict.fromkeys(str(d) for d in document_ids if d):
        try:
            with session_scope() as conn:
                written.append(write_file(conn, document_id))
        except Exception as exc:  # noqa: BLE001 - see above
            get_logger(__name__).warning("result_file_not_written", document_id=document_id,
                                         error=f"{type(exc).__name__}: {exc}")  # fmt: skip
    return written


def validate(document: dict[str, Any]) -> None:
    """Check a built result against ``schemas/contract/result.v1.json``. Raises
    ``ValidationError``."""
    from jsonschema import Draft202012Validator

    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(document)
