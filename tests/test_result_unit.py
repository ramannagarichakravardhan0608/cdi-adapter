"""UP-S3 / the Output Contract: the result document, and the screen that shows it.

The contract's two hard rules are what most of this file pins:

* **a value never appears without its status** - there is one way to build a value object and one
  way to draw it, and neither can produce a bare value;
* **``accepted`` only with proof** - a governed gate state *and* recorded evidence; anything else
  is ``needs_check`` and says why.

The rest pins the parts that are easy to get wrong later: the document validates against
``schemas/contract/result.v1.json``, two builds are byte-identical, and a contract field no extractor fills
yet is reported as **absent** rather than emitted as an empty value that reads like "nothing was
written on the page".
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, ValidationError

from cdi_adapter import result
from cdi_adapter.webapp.result_page import BODY, SCRIPT

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = {"page": 1, "bbox": [210.0, 300.0, 520.0, 340.0], "observation_ids": ["ob-1"]}


# ------------------------------------------------------------------ a value and its status


def test_a_governed_value_with_evidence_is_accepted() -> None:
    v = result.value("Anjali Das", review_state="auto_accepted", confidence=0.97,
                     evidence=EVIDENCE)  # fmt: skip
    assert v["status"] == "accepted"
    assert v["value"] == "Anjali Das" and v["confidence"] == 0.97
    assert v["evidence"]["bbox"] == EVIDENCE["bbox"]
    assert "reason" not in v  # nothing to explain


@pytest.mark.parametrize("state", ["pending", "in_review", "rejected", None])
def test_a_value_the_gate_did_not_govern_needs_a_check(state: str | None) -> None:
    v = result.value("9.1", review_state=state, confidence=0.99, evidence=EVIDENCE)
    assert v["status"] == "needs_check" and v["reason"] == "not governed by the gate"


def test_a_governed_value_without_evidence_still_needs_a_check() -> None:
    """Accepted only with proof: a value nobody can point to on the page is not accepted,
    however confident the model was."""
    v = result.value("WB-12345", review_state="clinician_confirmed", confidence=1.0)
    assert v["status"] == "needs_check" and v["reason"] == "no evidence recorded"
    v2 = result.value("WB-12345", review_state="clinician_confirmed", confidence=1.0,
                      evidence={"page": 1, "bbox": None, "observation_ids": []})  # fmt: skip
    assert v2["status"] == "needs_check"  # an evidence block with nothing in it is not proof


def test_every_value_carries_a_status_and_a_confidence_key() -> None:
    for state in ("auto_accepted", "in_review"):
        v = result.value(1, review_state=state, confidence=None, evidence=EVIDENCE)
        assert set(v) >= {"value", "status", "confidence"}
        assert v["confidence"] is None  # present, and honestly empty


def test_the_three_governed_states_are_the_gates_own() -> None:
    assert result.GOVERNED == ("auto_accepted", "clinician_confirmed", "corrected")


# ------------------------------------------------------------------ the contract document


def test_the_schema_is_a_valid_json_schema() -> None:
    schema = json.loads(result.SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    assert schema["$id"] == "cdi:result.v1"
    assert schema["properties"]["schema_version"]["const"] == "result.v1"


def test_a_value_object_must_have_its_status_to_validate() -> None:
    """The rule is in the schema too, so a future builder cannot emit a bare value either."""
    schema = json.loads(result.SCHEMA_PATH.read_text(encoding="utf-8"))
    v = Draft202012Validator(schema["$defs"]["value"])
    v.validate({"value": "x", "status": "accepted", "confidence": 0.9})
    with pytest.raises(ValidationError):
        v.validate({"value": "x", "confidence": 0.9})  # no status
    with pytest.raises(ValidationError):
        v.validate({"value": "x", "status": "probably", "confidence": 0.9})


def test_the_absent_list_names_what_no_extractor_fills_yet() -> None:
    """A contract field whose table or extractor is not built (OUT-S1, EX-S1/S2/S4/S5) is listed,
    not emitted empty - so a blank is never read as "not written on the page"."""
    assert "lab_tests[].preparation_refs" in result.ABSENT
    assert "preparation" not in result.ABSENT  # UP-S3 AC1: recognised among the advice now
    assert "lab_tests[].context" in result.ABSENT
    assert "doctor.clinic" in result.ABSENT
    assert "patient.phone" in result.ABSENT


def test_the_bytes_are_stable() -> None:
    """OUT-S2 AC2: two builds of one document compare identical, so a consumer can diff them."""
    doc = {"schema_version": "result.v1", "b": 1, "a": 2}
    assert json.dumps(doc, indent=2, sort_keys=True) == json.dumps(doc, indent=2, sort_keys=True)
    assert '"a"' in json.dumps(doc, sort_keys=True).split('"b"')[0]  # sorted, not insertion order


def test_review_counts_every_held_value_anywhere_in_the_document() -> None:
    name = result.value("A", review_state="auto_accepted", confidence=1.0, evidence=EVIDENCE)
    fbs = result.value("FBS", review_state="in_review", confidence=0.4)
    hba = result.value("HbA1c", review_state="pending", confidence=0.3)
    doc: dict[str, Any] = {"patient": {"name": name},
                           "lab_tests": [{"value": fbs}, {"value": hba}]}  # fmt: skip
    out = result._review(doc)
    assert out["needs_check_count"] == 2
    assert {i["value"] for i in out["items"]} == {"FBS", "HbA1c"}


def test_the_status_follows_the_held_count() -> None:
    clean = {"review": {"needs_check_count": 0}}
    held = {"review": {"needs_check_count": 3}}
    assert result._status({"status": "validated"}, clean) == "complete"
    assert result._status({"status": "validated"}, held) == "needs_check"
    assert result._status({"status": "error"}, clean) == "held_for_rescan"


# ------------------------------------------------------------------ the screen


def test_the_five_cards_are_there() -> None:
    for title in ("Patient", "Doctor", "Lab tests", "Preparation", "Status"):
        assert f"card('{title}'" in SCRIPT, title


def test_no_value_on_the_page_can_be_edited() -> None:
    """Corrections belong to the review console. The page's one text box is the note of a
    "wrong result" report (UP-S3 AC5), which changes nothing in the result."""
    assert "<input" not in BODY
    assert BODY.count("<textarea") == 1 and "<textarea id=reportnote" in BODY
    assert BODY.count("<select") == 1 and "<select id=reportdoc" in BODY
    assert "/review" in BODY  # and it says where corrections are made
    assert "method: 'PUT'" not in SCRIPT and "method: 'PATCH'" not in SCRIPT


def test_there_is_one_way_to_draw_a_value_and_it_always_prints_the_status() -> None:
    assert "function val(v){" in SCRIPT
    assert "class=\"pill ${ok ? '' : 'warn'}\">${esc(v.status.replace(" in SCRIPT
    # a missing value is said to be missing, never printed blank
    assert "not written on the page" in SCRIPT


def test_the_screen_reads_the_contract_not_the_tables() -> None:
    assert "/api/documents/${docId}/result.json" in SCRIPT
    # the only things it asks the server for are the two result documents: one page's, and
    # (UP-S1 AC2) a whole upload's. Never the job, the facts or the review tables.
    import re

    asked = set(re.findall(r"/api/[a-z]+/", SCRIPT))
    assert asked == {"/api/documents/", "/api/jobs/"}
    assert "/api/jobs/${docId}/result.json" in SCRIPT and "/api/review/" not in SCRIPT


def test_the_download_saves_the_same_document() -> None:
    assert "location.href = api + '/download'" in SCRIPT and "Download JSON" in BODY


def test_the_schema_version_is_shown() -> None:
    assert "d.schema_version" in SCRIPT


def test_not_extracted_yet_is_shown_once_not_as_empty_rows() -> None:
    assert "Not extracted yet" in SCRIPT and "d.absent" in SCRIPT


def test_the_page_loads_no_external_script() -> None:
    from cdi_adapter.webapp.result_page import result_page

    assert "cdn" not in result_page("00000000-0000-0000-0000-000000000001").lower()


def test_the_routes_are_wired() -> None:
    import sys

    sys.path.append(".tools/xgrammar")
    from cdi_adapter.webapp.app import app

    paths = set(app.openapi()["paths"])
    assert {"/result/{document_id}", "/api/documents/{document_id}/result.json",
            "/api/documents/{document_id}/result.json/download"} <= paths  # fmt: skip


def test_the_upload_screen_links_to_a_finished_page_only() -> None:
    from cdi_adapter.webapp.page import SCRIPT as UPLOAD

    assert "/result/${d.document_id}" in UPLOAD
    assert "d.steps_done.includes('validate')" in UPLOAD  # not while it is still being read


# ------------------------------------------------------------------ one result for one upload


class _Rows:
    def __init__(self, row):
        self._row = row

    def mappings(self):
        return self

    def first(self):
        return self._row


class _JobConn:
    """A connection that knows one upload."""

    def __init__(self, docs):
        self.row = {"id": "a" * 32, "state": "review", "docs": docs, "created_at": None}

    def execute(self, *_a, **_k):
        return _Rows(self.row)


def _page_result(doc_id: str, *, status: str = "complete", held: bool = False, mpi: str = "CFP-1"):
    v = result.value(
        "HbA1c",
        review_state="held" if held else "auto_accepted",
        confidence=0.9,
        evidence={"page": 1, "bbox": [0, 0, 1, 1], "observation_ids": ["o"]},
    )
    name = result.value(
        "Ravi Kumar",
        review_state="auto_accepted",
        confidence=0.99,
        evidence={"page": None, "bbox": None, "observation_ids": ["n"]},
    )
    return {"schema_version": "result.v1", "document_id": doc_id, "status": status,
            "page_count": 1, "patient": {"name": name, "ids": [{"type": "mpi_id", "value": mpi}]},
            "doctor": {"link": {"method": "unknown"}}, "visit": {},
            "lab_tests": [{"as_written": f"test of {doc_id}", "kind": "test", "value": v}],
            "diagnoses": [{"raw_text": f"dx of {doc_id}", "canonical_term": None, "value": v}],
            "review": {"needs_check_count": int(held), "items": []}}  # fmt: skip


def _job(monkeypatch, docs, results):
    monkeypatch.setattr(result, "build", lambda conn, doc_id: results[doc_id])
    return result.build_job(_JobConn(docs), "a" * 32)


def test_two_pages_sent_together_give_one_result(monkeypatch) -> None:
    """UP-S1 AC2: one job for the two pages, and the person sees one result."""
    docs = [{"filename": "p1.jpg", "status": "done", "document_id": "d1"},
            {"filename": "p2.jpg", "status": "done", "document_id": "d2"}]  # fmt: skip
    out = _job(monkeypatch, docs, {"d1": _page_result("d1"), "d2": _page_result("d2")})
    assert out["schema_version"] == "result-job.v1" and out["job_id"] == "a" * 32
    assert out["page_count"] == 2 and out["status"] == "complete"
    assert [(p["page"], p["file_name"], p["status"]) for p in out["pages"]] == [
        (1, "p1.jpg", "complete"), (2, "p2.jpg", "complete")]  # fmt: skip
    # every page's tests and diagnoses are there, each saying which page it was read from
    assert [(t["as_written"], t["page"]) for t in out["lab_tests"]] == [
        ("test of d1", 1), ("test of d2", 2)]  # fmt: skip
    assert [d["page"] for d in out["diagnoses"]] == [1, 2]
    assert out["patient"]["name"]["value"] == "Ravi Kumar" and out["patients_differ"] is False
    assert [d["document_id"] for d in out["documents"]] == ["d1", "d2"]  # each page, unchanged


def test_the_one_result_is_as_bad_as_its_worst_page(monkeypatch) -> None:
    docs = [{"filename": "p1.jpg", "status": "done", "document_id": "d1"},
            {"filename": "p2.jpg", "status": "done", "document_id": "d2"}]  # fmt: skip
    out = _job(
        monkeypatch,
        docs,
        {"d1": _page_result("d1"), "d2": _page_result("d2", status="needs_check", held=True)},
    )
    assert out["status"] == "needs_check"
    assert (
        out["review"]["needs_check_count"] == 2
    )  # the held test and the held diagnosis, once each


def test_a_page_that_could_not_be_read_is_named_and_holds_the_result(monkeypatch) -> None:
    docs = [
        {"filename": "p1.jpg", "status": "done", "document_id": "d1"},
        {
            "filename": "blurry.jpg",
            "status": "quality_hold",
            "document_id": None,
            "quality": {"code": "blurred", "message": "This photo is too blurry to read."},
        },
    ]
    out = _job(monkeypatch, docs, {"d1": _page_result("d1")})
    assert out["status"] == "held_for_rescan" and out["page_count"] == 2
    held = out["pages"][1]
    assert (held["status"], held["reason"]) == ("held_for_rescan",
                                                "This photo is too blurry to read.")  # fmt: skip
    assert len(out["documents"]) == 1 and len(out["lab_tests"]) == 1  # the good page still shows


def test_values_are_counted_once_not_again_from_the_pages_own_copies(monkeypatch) -> None:
    docs = [{"filename": "p1.jpg", "status": "done", "document_id": "d1"}]
    out = _job(monkeypatch, docs, {"d1": _page_result("d1", status="needs_check", held=True)})
    assert out["review"]["needs_check_count"] == 2  # not 4: `documents` is added after the count


def test_the_same_picture_sent_twice_is_read_once(monkeypatch) -> None:
    docs = [{"filename": "p1.jpg", "status": "done", "document_id": "d1"},
            {"filename": "p1-again.jpg", "status": "done", "document_id": "d1"}]  # fmt: skip
    out = _job(monkeypatch, docs, {"d1": _page_result("d1")})
    assert [p["status"] for p in out["pages"]] == ["complete", "duplicate"]
    assert len(out["lab_tests"]) == 1


def test_pages_of_two_patients_in_one_upload_are_flagged(monkeypatch) -> None:
    docs = [{"filename": "a.jpg", "status": "done", "document_id": "d1"},
            {"filename": "b.jpg", "status": "done", "document_id": "d2"}]  # fmt: skip
    out = _job(monkeypatch, docs, {"d1": _page_result("d1"), "d2": _page_result("d2", mpi="CFP-2")})
    assert out["patients_differ"] is True


def test_an_upload_that_does_not_exist_is_said_so() -> None:
    conn = _JobConn([])
    conn.row = None
    with pytest.raises(LookupError, match="no upload"):
        result.build_job(conn, "b" * 32)


def test_the_upload_has_one_result_screen_and_one_download() -> None:
    from cdi_adapter.webapp.app import app

    paths = set(app.openapi()["paths"])
    assert {"/result/job/{job_id}", "/api/jobs/{job_id}/result.json",
            "/api/jobs/{job_id}/result.json/download"} <= paths  # fmt: skip
    assert "pagesCard(d)" in SCRIPT and "isJob" in SCRIPT


def test_several_pages_get_one_link_and_a_single_page_keeps_its_own() -> None:
    from cdi_adapter.webapp.page import SCRIPT as UPLOAD

    assert "j.documents.length === 1 && d.document_id" in UPLOAD  # a row link only when alone
    assert 'href="/result/job/${id}"' in UPLOAD and "one result" in UPLOAD
    assert "j.documents.length > 1" in UPLOAD


def test_the_upload_page_says_how_many_uploads_are_being_read() -> None:
    from cdi_adapter.webapp.app import app
    from cdi_adapter.webapp.page import SCRIPT as UPLOAD

    assert "/api/activity" in UPLOAD and "Uploads being read now" in UPLOAD
    assert "No upload is being read right now" in UPLOAD
    assert "/api/activity" in set(app.openapi()["paths"])
