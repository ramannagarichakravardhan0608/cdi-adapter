"""Clinical integrity regressions against PostgreSQL, with real locking semantics."""

import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from cdi_adapter import repo
from cdi_adapter.canon import promote
from cdi_adapter.canon.repo import create_rv_item_for_document
from cdi_adapter.db import get_engine, session_scope
from cdi_adapter.extract.service import IdentityMismatch, _record_failure
from cdi_adapter.fhir import canonical
from cdi_adapter.mpi import service as mpi
from cdi_adapter.webapp.review import ReviewDecision, ReviewError, decide
from tests.test_extract_integration import _doc_with_ocr, _write, repo_facts
from tests.test_review_integration import _held_doc
from tests.test_reviewer_integration import _item_with_held_facts
from tests.test_validate_integration import _rx

pytestmark = pytest.mark.integration


def test_second_patient_in_unselected_batch_is_refused(db_env):
    doc, blocks = _doc_with_ocr(_rx())
    pid = _write(doc, blocks)["patient_id"]
    wrong, other = _doc_with_ocr([s.replace("Name: ", "Name: Totally Different ") for s in _rx()])
    with pytest.raises(IdentityMismatch):
        _write(wrong, other, patient_id=pid)
    assert repo_facts(wrong) == []
    assert repo_facts(doc)


def test_failed_mismatch_preserves_preexisting_facts_and_does_not_log_identity(db_env, capsys):
    doc, blocks = _doc_with_ocr(_rx())
    _write(doc, blocks)
    before = {f["id"] for f in repo_facts(doc)}
    found = mpi.Candidate(name="Confidential Name", abha="11111111111111")
    with session_scope() as c:
        run = repo.start_run(c, document_id=doc, stage="extract")
    _record_failure(
        SimpleNamespace(document_id=doc, run_id=run), IdentityMismatch(doc, {}, found), "test"
    )
    assert {f["id"] for f in repo_facts(doc)} == before
    output = capsys.readouterr().out
    assert "Confidential Name" not in output and "11111111111111" not in output


def test_reupload_preserves_clinician_correction(db_env):
    doc, blocks = _doc_with_ocr(_rx())
    _write(doc, blocks)
    fact = next(f for f in repo_facts(doc) if f["fact_type"] == "condition")
    with session_scope() as c:
        decided = decide(
            c,
            fact["id"],
            ReviewDecision(action="correct", corrections={"clinical_status": "resolved"}),
            "reviewer",
        )
    before = {f["id"] for f in repo_facts(doc)}
    out = _write(doc, blocks)
    assert out["preserved"] and {f["id"] for f in repo_facts(doc)} == before
    assert (
        next(f for f in repo_facts(doc) if str(f["id"]) == str(decided["result_fact_id"]))[
            "clinical_status"
        ]
        == "resolved"
    )


def test_concurrent_corrections_have_only_one_current_successor(db_env):
    doc = _held_doc()
    fact = next(f for f in repo_facts(doc) if f["review_state"] == "in_review")
    body = ReviewDecision(action="correct", corrections={"local_text": "Synthetic corrected"})
    entered = threading.Event()
    first = get_engine().connect()
    tx = first.begin()
    decide(first, fact["id"], body, "one")

    def second():
        entered.set()
        with session_scope() as c:
            with pytest.raises(ReviewError) as err:
                decide(c, fact["id"], body, "two")
            return err.value.status

    try:
        with ThreadPoolExecutor(1) as pool:
            future = pool.submit(second)
            assert entered.wait(5)
            assert not future.done()
            tx.commit()
            assert future.result(timeout=10) == 409
    finally:
        if tx.is_active:
            tx.rollback()
        first.close()
    with session_scope() as c:
        assert (
            c.execute(
                text("SELECT count(*) FROM clinical_fact WHERE supersedes = :f AND is_current"),
                {"f": fact["id"]},
            ).scalar_one()
            == 1
        )


def test_approval_refuses_unresolved_facts(db_env):
    _, item = _item_with_held_facts(["Tab Metformin 5000 mg 1-1-1"])
    with session_scope() as c:
        promote.claim(c, item, "reviewer")
    with session_scope() as c, pytest.raises(promote.WorkflowError, match="held fact"):
        promote.promote_review_item(c, item, "reviewer")
    with session_scope() as c:
        assert (
            c.execute(text("SELECT status FROM rv_item WHERE id = :i"), {"i": item}).scalar_one()
            == "claimed"
        )
        assert (
            c.execute(
                text("SELECT count(*) FROM cn_fhir_bundle WHERE rv_item_id = :i"), {"i": item}
            ).scalar_one()
            == 0
        )


def test_canonical_export_preserves_prn_and_resolved_condition(seeded_scans, db_env):
    doc, blocks = _doc_with_ocr(_rx())
    _write(doc, blocks)
    with session_scope() as c:
        for f in repo.get_facts_for_document(c, doc):
            corrections = (
                {"clinical_status": "resolved"}
                if f["fact_type"] == "condition"
                else {"prn": True}
                if f["fact_type"] == "medication"
                else None
            )
            decide(
                c,
                f["id"],
                ReviewDecision(
                    action="correct" if corrections else "accept", corrections=corrections
                ),
                "reviewer",
            )
        item = create_rv_item_for_document(c, doc)
        promote.claim(c, item, "reviewer")
        promote.promote_review_item(c, item, "reviewer")
        bundle = canonical.build_bundle(c, item)
        assert (
            create_rv_item_for_document(c, doc) == item
        )  # re-upload cannot duplicate canonical data
    entries = [e["resource"] for e in bundle["entry"]]
    meds = [r for r in entries if r["resourceType"] == "MedicationRequest"]
    conditions = [r for r in entries if r["resourceType"] == "Condition"]
    assert meds and all(m["dosageInstruction"][0]["asNeededBoolean"] for m in meds)
    assert conditions and all(
        c["clinicalStatus"]["coding"][0]["code"] == "resolved" for c in conditions
    )


def test_parallel_login_failures_trigger_lockout(db_env, monkeypatch):
    from cdi_adapter.config import get_settings
    from cdi_adapter.webapp.auth import verify_login
    from tests.helpers import add_web_user

    monkeypatch.setenv("CDI_LOGIN_MAX_FAILURES", "2")
    get_settings.cache_clear()
    username = add_web_user("reviewer")
    entered = threading.Event()
    first = get_engine().connect()
    tx = first.begin()
    assert verify_login(first, username, "wrong")[1] == "wrong password"

    def second():
        entered.set()
        with session_scope() as c:
            return verify_login(c, username, "wrong")[1]

    try:
        with ThreadPoolExecutor(1) as pool:
            result = pool.submit(second)
            assert entered.wait(5)
            assert not result.done()
            tx.commit()
            assert result.result(timeout=10) == "locked"
    finally:
        if tx.is_active:
            tx.rollback()
        first.close()
        get_settings.cache_clear()


def test_conflicting_new_document_cannot_demote_human_decision(db_env):
    from cdi_adapter.terminology.service import bind_document
    from cdi_adapter.validate.service import validate_document

    lines = [
        "Name: Human Protected  Age/Sex: 60 Y / M",
        "Date: 01/09/2026",
        "LABORATORY REPORT",
        "HbA1c 7.8 % 4.0-5.6",
    ]
    doc, blocks = _doc_with_ocr(lines, doc_type="lab_report")
    pid = _write(doc, blocks, doc_type="lab_report")["patient_id"]
    bind_document(doc)
    fact = repo_facts(doc)[0]
    with session_scope() as c:
        decide(c, fact["id"], ReviewDecision(action="accept"), "reviewer")
    other, blocks2 = _doc_with_ocr([s.replace("7.8", "9.1") for s in lines], doc_type="lab_report")
    _write(other, blocks2, doc_type="lab_report", patient_id=pid)
    bind_document(other)
    validate_document(other)
    assert repo_facts(doc)[0]["review_state"] == "clinician_confirmed"
    assert repo_facts(other)[0]["review_state"] == "in_review"


def test_fuzzy_identity_does_not_merge_conflicting_abha_or_full_dob(db_env):
    from datetime import date

    with session_scope() as c:
        first, _ = mpi.resolve_identity(
            c,
            mpi.Candidate(
                name="Synthetic Similar",
                dob=date(1980, 5, 1),
                birth_year=1980,
                abha="12345678901234",
            ),
        )
        second, how = mpi.resolve_identity(
            c,
            mpi.Candidate(
                name="Synthetic Similar",
                dob=date(1980, 5, 1),
                birth_year=1980,
                abha="23456789012345",
            ),
        )
        third, _ = mpi.resolve_identity(
            c, mpi.Candidate(name="Synthetic Similar", dob=date(1980, 5, 2), birth_year=1980)
        )
    assert len({first, second, third}) == 3 and how == "minted"
