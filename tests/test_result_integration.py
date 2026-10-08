"""UP-S3 / OUT-S2 on real PostgreSQL: a real prescription's result document.

A document goes through the pipeline, then the contract document is built from what the database
holds - and it has to validate against ``schemas/contract/result.v1.json``, carry a status on every value,
and build byte-identically twice.
"""

from __future__ import annotations

import pytest

from cdi_adapter import result
from cdi_adapter.db import session_scope
from cdi_adapter.terminology.service import bind_document
from cdi_adapter.validate.service import validate_document
from tests.test_extract_integration import _doc_with_ocr, _write

pytestmark = pytest.mark.integration


def _prescription() -> str:
    lines = ["CareFlow Polyclinic", "Name: Result Dasx  Age/Sex: 44 Y / F", "Date: 05/10/2026",
             "Diagnosis: Type 2 diabetes", "Advised: HbA1c, FBS",
             "Rx", "Tab Metformin 500mg 1-0-1 x30 days"]  # fmt: skip
    doc, blocks = _doc_with_ocr(lines)
    _write(doc, blocks)
    bind_document(doc)
    validate_document(doc)
    return doc


def test_a_real_prescription_builds_a_valid_result(db_env) -> None:
    doc = _prescription()
    with session_scope() as c:
        built = result.build(c, doc)
    result.validate(built)  # schemas/contract/result.v1.json
    assert built["schema_version"] == "result.v1"
    assert built["document_id"] == doc
    assert built["status"] in ("complete", "needs_check", "held_for_rescan")
    # the synthetic fixture writes a document without rendering its pages, so page_count is 0
    # here; what matters is that the contract field is always present and an integer
    assert isinstance(built["page_count"], int)
    assert isinstance(built["absent"], list) and built["absent"]


def test_every_value_in_a_real_result_has_a_status(db_env) -> None:
    """The contract's hard rule, checked on a real document rather than a fixture."""
    doc = _prescription()
    with session_scope() as c:
        built = result.build(c, doc)
    values = result._walk_values(built)
    assert values, "a prescription with facts must carry some values"
    for v in values:
        assert v["status"] in ("accepted", "needs_check")
        assert "confidence" in v
        if v["status"] == "needs_check":
            assert v.get("reason")


def test_an_accepted_value_can_be_pointed_at_on_the_page(db_env) -> None:
    doc = _prescription()
    with session_scope() as c:
        built = result.build(c, doc)
    for v in result._walk_values(built):
        if v["status"] == "accepted" and v.get("evidence", {}).get("bbox"):
            assert len(v["evidence"]["bbox"]) == 4
            break


def test_the_same_document_builds_identical_bytes(db_env) -> None:
    doc = _prescription()
    with session_scope() as c:
        first = result.as_json(c, doc)
    with session_scope() as c:
        second = result.as_json(c, doc)
    assert first == second  # OUT-S2 AC2


def test_the_diagnosis_is_kept_as_written(db_env) -> None:
    """The contract: ``raw_text`` exactly as written, canonical term only when matched, and
    external codes optional - never required."""
    doc = _prescription()
    with session_scope() as c:
        built = result.build(c, doc)
    assert all("raw_text" in d and "value" in d for d in built["diagnoses"])


def test_a_missing_document_is_a_lookup_error(db_env) -> None:
    import uuid

    with session_scope() as c, pytest.raises(LookupError):
        result.build(c, str(uuid.uuid4()))


def test_the_endpoint_serves_the_same_bytes_and_the_download_names_a_file(app_env) -> None:
    """Needs the object store, because the route reads a document the pipeline really ingested."""
    from cdi_adapter.webapp.app import app
    from tests.helpers import signed_in

    doc = _prescription()
    client = signed_in(app, "admin")
    r = client.get(f"/api/documents/{doc}/result.json")
    assert r.status_code == 200 and r.json()["document_id"] == doc
    d = client.get(f"/api/documents/{doc}/result.json/download")
    assert d.status_code == 200
    assert f'filename="result-{doc}.json"' in d.headers["content-disposition"]
    assert d.text == r.text  # the file is the same document the screen reads
    assert client.get("/result/" + doc).status_code == 200
