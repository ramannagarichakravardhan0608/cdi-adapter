"""SW-S6 on real PostgreSQL: the real ingest code on the filesystem store (AC1), and a diagnosis
saved with no SNOMED CT / ICD-10 licence (AC3). Golden documents: ARCHITECTURE §8.2."""

from __future__ import annotations

import hashlib
import uuid

import pytest
from sqlalchemy import text

from cdi_adapter import repo, storage, swapdrill
from cdi_adapter.config import get_settings
from cdi_adapter.db import session_scope
from cdi_adapter.ingest.service import ingest_bytes
from cdi_adapter.terminology import canonical, seed
from cdi_adapter.terminology.service import bind_document
from tests.test_extract_integration import RX_LINES, _doc_with_ocr, _write

pytestmark = pytest.mark.integration


@pytest.fixture
def fs_store(db_env, tmp_path, monkeypatch):
    monkeypatch.setenv("CDI_OBJECT_STORE", "fs")
    monkeypatch.setenv("CDI_FILE_STORE_ROOT", str(tmp_path / "scans"))
    get_settings.cache_clear()
    storage.get_store.cache_clear()
    yield tmp_path / "scans"
    get_settings.cache_clear()
    storage.get_store.cache_clear()


def test_ac1_a_scan_saved_and_read_back_by_the_unchanged_ingest_code(fs_store):
    golden = swapdrill.golden_files()["prescription_00"]
    raw = golden + f"\n% {uuid.uuid4()}".encode()  # a document of this run only
    got = ingest_bytes(raw, "golden-rx.pdf")
    store = storage.get_store()
    assert isinstance(store, storage.FileStore)
    with session_scope() as c:
        doc = repo.get_document(c, got.document_id)
        pages = repo.get_pages(c, got.document_id)
    assert store.get_bytes(doc["storage_key"]) == raw  # the scan, read back as saved
    assert doc["sha256"] == hashlib.sha256(raw).hexdigest()
    assert pages and all(store.get_bytes(p["storage_key"]).startswith(b"\x89PNG") for p in pages)
    on_disk = {k for k, _ in store.listing()}
    assert doc["storage_key"] in on_disk and {p["storage_key"] for p in pages} <= on_disk
    assert (fs_store / doc["storage_key"]).is_file()


def test_ac3_a_diagnosis_is_saved_as_its_text_plus_the_canonical_term(db_env, monkeypatch):
    monkeypatch.setenv("CDI_LICENSED_CODE_SYSTEMS", "")  # no SNOMED CT, no ICD-10 licence
    get_settings.cache_clear()
    try:
        doc, blocks = _doc_with_ocr(RX_LINES)
        _write(doc, blocks)
        bind_document(doc)
        with session_scope() as c:
            facts = repo.get_facts_for_document(c, doc)
            meds = c.execute(
                text(
                    "SELECT m.drug_text, m.rxlike_system, m.rxlike_code "
                    "FROM medication_detail m JOIN clinical_fact f ON "
                    "f.id = m.fact_id WHERE :d = ANY(f.source_doc_ids)"
                ),
                {"d": doc},
            ).all()
    finally:
        get_settings.cache_clear()
    by = {f["local_text"]: f for f in facts}
    dm = by["Type 2 diabetes mellitus"]
    assert (dm["code_system"], dm["code"], dm["code_display"]) == (
        canonical.INTERNAL_SYSTEM,
        "condition:type-2-diabetes-mellitus",
        "Type 2 diabetes mellitus",
    )
    assert by["Hypertension"]["code_display"] == "Essential hypertension"  # the canonical term
    assert by["Hypertension"]["local_text"] == "Hypertension"  # the page's own words, kept
    external = [f["local_text"] for f in facts if f["code_system"] in (seed.SNOMED, seed.ICD10)]
    assert external == []
    assert meds and all(s == canonical.INTERNAL_SYSTEM for _, s, _ in meds)
    assert by["body weight"]["code_system"] == seed.LOINC  # LOINC needs no licence
