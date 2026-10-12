"""EX-S6 - Every value carries its proof and a status: the acceptance criteria.

The three criteria are rules of the DATABASE; they are proven against a real PostgreSQL in
``tests/test_ex_s6_acceptance_integration.py`` (and, for the readings, in
``tests/test_evidence_schema_integration.py``). What is checked here needs no database: that
the rules exist in the migrations exactly as the criteria word them, that no later migration
takes them away, and that every NEW kind of value - patient, doctor, ordered test, preparation,
context - goes through them like any other.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from cdi_adapter import result
from cdi_adapter.config import Settings
from cdi_adapter.extract import context
from cdi_adapter.validate import rules
from cdi_adapter.validate.service import gate

ROOT = Path(__file__).resolve().parents[1]
VERSIONS = ROOT / "db" / "alembic" / "versions"
NEW_KINDS = ("patient_detail", "doctor_detail", "lab_order", "preparation")
PROOF = {"page": 1, "bbox": [10, 20, 300, 44], "observation_ids": ["u7"]}


def _migration(prefix: str) -> str:
    (path,) = VERSIONS.glob(f"{prefix}_*.py")
    return path.read_text(encoding="utf-8")


def _all_migrations() -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8") for p in sorted(VERSIONS.glob("0*.py"))}


def _upgrade(source: str) -> str:
    """The part of a migration that is applied going forward."""
    return source.split("def downgrade", 1)[0]


# =====================================================================================
# AC1  GIVEN a direct database edit of a reading is tried WHEN it is run
#      THEN the database refuses it
# =====================================================================================


def test_ac1_a_reading_cannot_be_updated_or_deleted() -> None:
    evidence = _migration("0013")
    assert "CREATE TRIGGER ocr_observation_append_only BEFORE UPDATE OR DELETE ON " \
           "ocr_observation" in evidence.replace('"\n        "', "")  # fmt: skip
    assert "CREATE FUNCTION ocr_observation_append_only() RETURNS trigger" in evidence
    assert "RAISE EXCEPTION" in evidence.split("ocr_observation_append_only()", 1)[1][:1500]


def test_ac1_nor_emptied_in_one_go() -> None:
    evidence = _migration("0013")
    assert "BEFORE TRUNCATE ON {table}" in evidence
    tables = re.search(r"for table in \(([^)]*)\)", evidence)
    assert tables and "ocr_observation" in tables.group(1)


def test_ac1_the_same_holds_for_what_was_decided_from_a_reading() -> None:
    evidence = _migration("0013")
    for trigger in ("interpretation_candidate_append_only", "verified_fact_immutable"):
        assert f"CREATE TRIGGER {trigger} BEFORE UPDATE OR DELETE" in evidence.replace(
            '"\n        "', "")  # fmt: skip


def test_ac1_no_later_migration_takes_the_rule_away() -> None:
    """DO NOT relax any trigger: nothing after 0013 drops or disables one of them."""
    guards = ("ocr_observation_append_only", "interpretation_candidate_append_only",
              "verified_fact_immutable", "clinical_fact_evidence_before_auto_accept",
              "provenance_recheck_fact")  # fmt: skip
    for name, source in _all_migrations().items():
        forward = _upgrade(source)
        assert "DISABLE TRIGGER" not in forward.upper(), name
        for guard in guards:
            assert f"DROP TRIGGER IF EXISTS {guard}" not in forward, (name, guard)
            assert f"DROP TRIGGER {guard}" not in forward, (name, guard)
            assert f"DROP FUNCTION IF EXISTS {guard}" not in forward, (name, guard)


def test_ac1_the_migrations_of_this_epic_touch_no_trigger() -> None:
    for prefix in ("0036", "0037", "0038"):
        forward = _upgrade(_migration(prefix))
        assert "TRIGGER" not in forward.upper() and "FUNCTION" not in forward.upper(), prefix


# =====================================================================================
# AC2  GIVEN a value has no proof WHEN acceptance is attempted THEN the database refuses it
# =====================================================================================


def test_ac2_the_database_refuses_acceptance_without_proof() -> None:
    rule = _migration("0012")
    assert "CREATE CONSTRAINT TRIGGER clinical_fact_evidence_before_auto_accept" in rule
    assert "AFTER INSERT OR UPDATE OF review_state ON clinical_fact" in rule
    assert "NOT EXISTS (SELECT 1 FROM fact_provenance p WHERE p.fact_id = NEW.id)" in rule
    assert "cannot be auto_accepted" in rule and "ERRCODE = 'check_violation'" in rule


def test_ac2_the_rule_is_on_the_table_every_kind_of_value_is_saved_in() -> None:
    """The new kinds are rows of ``clinical_fact`` - the table the rule guards - not a table of
    their own that the rule would not see."""
    for prefix, kind in (("0036", "patient_detail"), ("0037", "doctor_detail"),
                         ("0038", "preparation")):  # fmt: skip
        forward = _upgrade(_migration(prefix))
        assert "clinical_fact_fact_type_check" in forward and f"'{kind}'" in forward
        assert not re.search(r"CREATE TABLE \w*(patient_detail|preparation|doctor_detail)",
                             forward)  # fmt: skip
    assert "'lab_order'" in _migration("0011")


def test_ac2_taking_the_proof_away_afterwards_is_checked_too() -> None:
    integrity = _migration("0024")
    assert "CREATE TRIGGER provenance_recheck_fact BEFORE DELETE OR UPDATE OF fact_id" in integrity
    assert "cannot truncate evidence of auto_accepted facts" in integrity


@pytest.mark.parametrize("kind", NEW_KINDS)
def test_ac2_a_new_kind_of_value_with_no_proof_is_never_accepted_by_the_gate(kind: str) -> None:
    found = rules.check_evidence([])
    assert [(f.severity, f.code) for f in found] == [("blocker", "evidence-missing")]
    assert gate(kind, 0.999, found, False, Settings()) == "in_review"
    empty_row = [{"ocr_block_ids": [], "extracted_text": None}]
    assert rules.check_evidence(empty_row)[0].severity == "blocker"


@pytest.mark.parametrize("kind", NEW_KINDS)
def test_ac2_with_proof_it_may_be(kind: str) -> None:
    proven = [{"ocr_block_ids": ["u7"], "extracted_text": "as written"}]
    assert rules.check_evidence(proven) == []
    assert gate(kind, 0.999, [], False, Settings()) == "auto_accepted"


@pytest.mark.parametrize("state", ["auto_accepted", "clinician_confirmed", "corrected"])
def test_ac2_the_result_never_calls_a_value_accepted_without_proof(state: str) -> None:
    for evidence in (None, {}, {"page": 1, "bbox": None, "observation_ids": []}):
        out = result.value("98765 43210", review_state=state, confidence=0.99,
                           evidence=evidence)  # fmt: skip
        assert out["status"] == result.NEEDS_CHECK and out["reason"] == "no evidence recorded"
    assert result.value("98765 43210", review_state=state, confidence=0.99,
                        evidence=PROOF)["status"] == result.ACCEPTED  # fmt: skip


def _row(kind: str, written: str, **more: Any) -> dict[str, Any]:
    return {"id": f"{kind}:{written}", "fact_type": kind, "local_text": written,
            "review_state": "auto_accepted", "confidence_overall": 0.99, "code": None,
            **more}  # fmt: skip


def test_ac2_every_new_value_of_the_result_carries_its_proof_and_status(monkeypatch) -> None:
    """Patient, doctor, ordered test, preparation: with proof ``accepted``; the same rows with
    the proof taken away, ``needs_check`` - every one of them."""
    facts = [_row("patient_detail", "phone", value_text="98765 43210"),
             _row("doctor_detail", "registration number", value_text="71112 (WBMC)"),
             _row("lab_order", "HbA1c", code="4548-4"),
             _row("preparation", "fasting 12 hrs", value_text="tests: HbA1c",
                  value_num=12)]  # fmt: skip

    def built() -> list[dict[str, Any]]:
        return [result._patient_details(None, facts)["phone"],  # type: ignore[arg-type]
                result._doctor(None, facts)["registration_no"],  # type: ignore[arg-type]
                result._ordered_tests(None, facts)[0]["value"],  # type: ignore[arg-type]
                result._preparation(None, facts)[0]]  # type: ignore[arg-type]

    monkeypatch.setattr(result, "_evidence", lambda conn, fact_id: PROOF)
    assert [v["status"] for v in built()] == ["accepted"] * 4
    assert all(v["evidence"] == PROOF for v in built())
    monkeypatch.setattr(result, "_evidence", lambda conn, fact_id: None)
    assert [v["status"] for v in built()] == ["needs_check"] * 4
    assert all(v["reason"] == "no evidence recorded" for v in built())


def test_ac2_a_context_link_needs_proof_too() -> None:
    """A reason with no quote and no region behind it gives no link at all (EX-S5 FR5)."""
    test = {"as_written": "HbA1c", "page": 1, "blocks": ["u9"]}
    unproven = {"type": "diagnosis", "text": "T2DM", "quote": None, "page": 1, "blocks": []}
    proven = {**unproven, "quote": "T2DM", "blocks": ["u3"], "evidence": PROOF}
    assert context.link_context([test], [unproven])[0]["context"] == []
    (link,) = context.link_context([test], [proven])[0]["context"]
    assert link["evidence"] == PROOF and link["quote"] == "T2DM"


# ------------------------------------------------------------- FR6: page-level proof


def test_fr6_a_value_proven_only_by_the_whole_page_is_never_auto_accepted() -> None:
    kinds = {"p1": "page", "p2": "page", "l1": "line"}
    whole_page = [{"ocr_block_ids": ["p1", "p2"], "extracted_text": "Tab X"}]
    found = rules.check_page_level(whole_page, kinds)
    assert [(f.severity, f.code) for f in found] == [("blocker", "page-level-evidence")]
    for kind in (*NEW_KINDS, "medication", "condition"):
        assert gate(kind, 0.999, found, False, Settings()) == "in_review"


def test_fr6_one_line_level_region_is_enough() -> None:
    kinds = {"p1": "page", "l1": "line"}
    assert rules.check_page_level([{"ocr_block_ids": ["p1", "l1"]}], kinds) == []
    assert rules.check_page_level([{"ocr_block_ids": ["l1"]}], kinds) == []
    assert rules.check_page_level([], kinds) == []  # no proof at all: check_evidence's finding


def test_fr6_the_rule_runs_for_every_fact_of_a_document() -> None:
    service = (ROOT / "src" / "cdi_adapter" / "validate" / "service.py").read_text(
        encoding="utf-8")  # fmt: skip
    assert "rules.check_page_level(provenance, block_types)" in service
    assert "SELECT id, block_type FROM ocr_block WHERE document_id = :d" in service


def test_fr5_a_proof_box_is_the_ocr_lines_own_never_the_models() -> None:
    """The model cites line ids; the box is taken from those lines (their union)."""
    service = (ROOT / "src" / "cdi_adapter" / "extract" / "service.py").read_text(
        encoding="utf-8")  # fmt: skip
    assert "bbox_union=_bbox_union(same_page)" in service
    assert 'min(b["bbox"][0] for b in blocks)' in service
    schema = (ROOT / "schemas" / "common.defs.json").read_text(encoding="utf-8")
    assert '"bbox"' not in schema  # the model is never asked for coordinates


# =====================================================================================
# AC3  GIVEN a person corrects a value WHEN it is saved
#      THEN a new value exists and the original reading is unchanged
# =====================================================================================


def _review_source() -> str:
    return (ROOT / "src" / "cdi_adapter" / "webapp" / "review.py").read_text(encoding="utf-8")


def test_ac3_a_correction_is_a_new_value_that_points_at_the_one_it_replaces() -> None:
    source = _review_source()
    assert 'vals = {**new, "review_state": "corrected", "supersedes": fact["id"], ' \
           '"is_current": True}' in source  # fmt: skip
    assert 'text(f"INSERT INTO clinical_fact ({\', \'.join(cols)}) "' in source


def test_ac3_the_original_value_is_only_marked_replaced_never_rewritten() -> None:
    """The one UPDATE a correction makes to the original row sets that it is no longer the
    current one. Not its text, not its value, not its code."""
    source = _review_source()
    updates = re.findall(r'UPDATE clinical_fact SET ([^"]*)"', source.split(
        "def _close_tasks", 1)[0].split("new_id = conn.execute", 1)[1])  # fmt: skip
    assert updates == ["is_current = false, review_state = 'corrected', "]
    for column in ("local_text", "value_text", "value_num", "code ="):
        assert column not in updates[0]


def test_ac3_the_new_value_keeps_the_proof_of_the_one_it_replaces() -> None:
    source = _review_source()
    assert "for p in repo.get_provenance(conn, fact[\"id\"]):" in source
    assert "repo.insert_fact_provenance(\n            conn, fact_id=new_id" in source


def test_ac3_the_reading_itself_is_out_of_a_corrections_reach() -> None:
    """A correction never writes to the readings at all; and the database would refuse it."""
    source = _review_source()
    for write in ("UPDATE ocr_", "DELETE FROM ocr_", "INSERT INTO ocr_", "TRUNCATE"):
        assert write not in source
    assert "UPDATE OR DELETE ON ocr_observation" in _migration("0013").replace(
        '"\n        "', "")  # fmt: skip


def test_ac3_the_value_of_every_new_kind_can_be_corrected_this_way() -> None:
    from cdi_adapter.webapp import review

    assert {"local_text", "value_text", "value_num", "code"} <= review.CORRECTABLE
    assert {"fact_type", "value_text", "local_text"} <= set(review._COPY_COLS)


# ------------------------------------------------------------- what is not proven here


@pytest.mark.xfail(strict=True, reason=(
    "EX-S6 AC2: the database rule asks that a fact_provenance ROW exists. A row that cites no "
    "OCR line and no region still satisfies it; only the application's gate (check_evidence) "
    "catches that. Tightening the trigger needs a PostgreSQL to test against - none was "
    "available - and was left alone rather than changed blind."))  # fmt: skip
def test_ac2_the_database_also_refuses_an_empty_proof_row() -> None:
    rule = "\n".join(_all_migrations().values())
    assert "cardinality(p.ocr_block_ids) > 0" in rule or "bbox_union IS NOT NULL" in rule


@pytest.mark.xfail(strict=True, reason=(
    "EX-S6 NFR3: 'at least 99% of automatically accepted critical values are correct' is not "
    "measured. It needs an answer-key set and is ENT-S3's work."))  # fmt: skip
def test_nfr3_precision_of_auto_accepted_values_is_measured() -> None:
    assert (ROOT / "docs" / "evidence" / "EX-S6" / "precision.md").exists()
