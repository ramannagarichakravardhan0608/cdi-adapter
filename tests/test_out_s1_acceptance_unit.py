"""OUT-S1 "Save everything to the database" - the three acceptance criteria, without a database.

* AC1  a processed prescription has patient, doctor, test, preparation and context rows, and
       each links to its evidence;
* AC2  the same prescription processed twice gives the same rows (no duplicates);
* AC3  saving that fails half way stores nothing of that document, and the error is logged.

What only PostgreSQL can show (the foreign keys, the cascade, the rollback itself) is in
``test_out_s1_acceptance_integration.py``; here the database is a recording stand-in and the
migration is read as text.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from cdi_adapter.extract import context, preparation
from cdi_adapter.extract import service as X
from cdi_adapter.ml import client as ml
from cdi_adapter.persist import normalized
from cdi_adapter.terminology import lab_master

DOC = "11111111-1111-1111-1111-111111111111"
MIGRATION = (Path(__file__).parents[1] / "db/alembic/versions/0039_prescription_tables.py"
             ).read_text(encoding="utf-8")  # fmt: skip


def _fact(n: int, kind: str, written: str, value: str | None = None, *, page: str = "page-1",
          blocks: tuple[str, ...] | None = None, code: str | None = None,
          note: dict[str, Any] | None = None) -> dict[str, Any]:  # fmt: skip
    return {"id": f"00000000-0000-0000-0000-{n:012d}", "fact_type": kind, "local_text": written,
            "value_text": value, "value_num": None, "value_unit_ucum": None, "code_system": None,
            "code": code, "code_display": None, "review_state": "pending",
            "review_note": json.dumps(note) if note else None, "page_id": page,
            "ocr_block_ids": list(blocks if blocks is not None else (f"block-{n}",)),
            "extracted_text": written}  # fmt: skip


def _prescription() -> list[dict[str, Any]]:
    return [
        _fact(1, "patient_detail", "name", "Rahul Sharma"),
        _fact(2, "patient_detail", "phone", "98765 43210"),
        _fact(3, "doctor_detail", "registration number", "71112 (WBMC)"),
        _fact(4, "condition", "Type 2 diabetes mellitus"),
        _fact(5, "symptom", "fever"),
        _fact(6, "lab_order", "HbA1c"),
        _fact(7, "lab_order", "Lipid profile"),
        _fact(8, "preparation", "fasting 12 hrs", "tests: Lipid profile"),
        _fact(9, "preparation", "come at 8 am", "all"),
        _fact(10, "preparation", "bring old reports", "unclear"),
    ]


class Conn:
    """A stand-in connection: answers the facts query, keeps every statement it is given."""

    def __init__(self, facts: list[dict[str, Any]], fail_on: str | None = None) -> None:
        self.facts, self.fail_on, self.calls = facts, fail_on, []
        self.tables: dict[str, list[dict[str, Any]]] = {t: [] for t in normalized.TABLES}

    def execute(self, statement: Any, params: Any = None) -> Any:
        sql = " ".join(str(statement).split())
        self.calls.append((sql, params))
        if self.fail_on and self.fail_on in sql:
            raise RuntimeError("the database went away")
        self._rows = self.facts if sql.startswith("SELECT f.id") else []
        for table in normalized.TABLES:
            if sql.startswith(f"DELETE FROM {table} "):
                self.tables[table] = [r for r in self.tables[table]
                                      if r["document_id"] != params["d"]]  # fmt: skip
            if sql.startswith(f"INSERT INTO {table} "):
                self.tables[table].append(dict(params))
        return self

    def mappings(self) -> Any:
        return self

    def all(self) -> list[dict[str, Any]]:
        return list(self._rows)


# ------------------------------------------------------------------ AC1: rows, each with proof


def test_ac1_every_ordered_test_has_a_row_named_from_the_lab_list():
    rows = normalized.rows_for(DOC, _prescription())["rx_lab_test"]
    assert [r["as_written"] for r in rows] == ["HbA1c", "Lipid profile"]
    for r in rows:
        assert r["document_id"] == DOC and r["match"] == "matched" and r["standard_name"]
        assert r["kind"] in lab_master.KINDS and r["master_version"] == lab_master.version()
    assert rows[1]["kind"] == "panel"


def test_ac1_a_test_that_is_not_in_the_list_is_still_saved_as_written():
    rows = normalized.rows_for(DOC, [_fact(1, "lab_order", "Zqx profile")])["rx_lab_test"]
    assert len(rows) == 1 and rows[0]["as_written"] == "Zqx profile"
    assert rows[0]["match"] == "not_matched"
    assert rows[0]["standard_name"] is None and rows[0]["code"] is None


def test_ac1_every_preparation_has_a_row_with_its_words_type_value_and_tests():
    rows = normalized.rows_for(DOC, _prescription())["rx_preparation"]
    by = {r["text"]: r for r in rows}
    assert set(by) == {"fasting 12 hrs", "come at 8 am", "bring old reports"}
    fasting = by["fasting 12 hrs"]
    assert (fasting["type"], fasting["amount"], fasting["applies_to"]) == ("fasting", 12, "tests")
    assert fasting["test_fact_ids"] == ["00000000-0000-0000-0000-000000000007"]
    assert by["come at 8 am"]["applies_to"] == "all"
    assert by["come at 8 am"]["test_fact_ids"] == [
        "00000000-0000-0000-0000-000000000006", "00000000-0000-0000-0000-000000000007"]  # fmt: skip
    # not clear which test: linked to none, never guessed
    assert by["bring old reports"]["applies_to"] == "unclear"
    assert by["bring old reports"]["test_fact_ids"] == []
    assert all(r["phrases_version"] == preparation.version() for r in rows)


def test_ac1_every_test_and_reason_link_has_a_row_pointing_at_both_facts():
    facts = _prescription()
    rows = normalized.rows_for(DOC, facts)["rx_test_context"]
    ids = {f["id"] for f in facts}
    assert rows, "a prescription with a diagnosis and tests has context rows"
    for r in rows:
        assert r["test_fact_id"] in ids and r["reason_fact_id"] in ids
        assert r["relation"] in ("same_line", "same_page", "other_page")
        assert r["reason_type"] in ("diagnosis", "complaint", "note")
        assert r["rules_version"] == context.version()
    hba1c = [r for r in rows if r["test_fact_id"].endswith("6")]
    assert ("diagnosis", "Type 2 diabetes mellitus") in {(r["reason_type"], r["reason_text"])
                                                         for r in hba1c}  # fmt: skip


def test_ac1_the_connection_between_a_test_and_a_reason_is_where_they_are_written():
    same = [_fact(1, "condition", "Type 2 diabetes mellitus", blocks=("b-1",)),
            _fact(2, "lab_order", "HbA1c", blocks=("b-1",)),
            _fact(3, "lab_order", "FBS", blocks=("b-9",)),
            _fact(4, "lab_order", "PPBS", page="page-2", blocks=("b-20",))]  # fmt: skip
    rows = normalized.rows_for(DOC, same)["rx_test_context"]
    assert [r["relation"] for r in rows] == ["same_line", "same_page", "other_page"]


def test_ac1_a_reason_with_no_evidence_is_not_linked_to_anything():
    facts = [_fact(1, "condition", "Type 2 diabetes mellitus", blocks=()),
             _fact(2, "lab_order", "HbA1c")]  # fmt: skip
    facts[0]["extracted_text"] = None
    assert normalized.rows_for(DOC, facts)["rx_test_context"] == []


def test_ac1_the_does_not_fit_check_is_saved_on_the_test():
    note = {"findings": [{"code": "context-does-not-fit", "message": "check: does not fit"}]}
    facts = [_fact(1, "lab_order", "HbA1c", note=note), _fact(2, "lab_order", "FBS")]
    rows = normalized.rows_for(DOC, facts)["rx_lab_test"]
    assert [r["does_not_fit"] for r in rows] == [True, False]


def test_ac1_every_row_links_to_a_fact_and_the_fact_carries_the_evidence():
    """A row holds no proof of its own: it points at its fact (a foreign key), and the fact's
    proof is the page, the box and the OCR lines in ``fact_provenance``."""
    for table, column in (("rx_lab_test", "fact_id"), ("rx_preparation", "fact_id"),
                          ("rx_test_context", "test_fact_id"),
                          ("rx_test_context", "reason_fact_id")):  # fmt: skip
        body = MIGRATION.split(f"CREATE TABLE {table} (")[1].split('"""')[0]
        line = next(ln for ln in body.splitlines() if ln.strip().startswith(column + " "))
        assert "REFERENCES clinical_fact (id) ON DELETE CASCADE" in line
        assert "NOT NULL" in line or "PRIMARY KEY" in line
        assert "document_id    uuid NOT NULL REFERENCES source_document (id)" in body or (
            "document_id     uuid NOT NULL REFERENCES source_document (id)" in body)


def test_ac1_patient_and_doctor_rows_come_with_their_evidence():
    for view, kind in (("v_rx_patient_detail", "patient_detail"),
                       ("v_rx_doctor_detail", "doctor_detail")):  # fmt: skip
        body = MIGRATION.split(f"CREATE VIEW {view} AS")[1].split('"""')[0]
        assert f"e.fact_type = '{kind}'" in body
        for proof in ("evidence_id", "page_no", "bbox_union", "ocr_block_ids", "evidence_text"):
            assert proof in body
    evidence = MIGRATION.split("CREATE VIEW v_rx_evidence AS")[1].split('"""')[0]
    assert "fact_provenance" in evidence and "f.is_current" in evidence
    assert "document_practitioner" in MIGRATION.split("CREATE VIEW v_rx_doctor_detail AS")[1]


def test_ac1_the_test_preparation_and_context_views_give_the_evidence_too():
    for view in ("v_rx_lab_test", "v_rx_preparation", "v_rx_test_context"):
        body = MIGRATION.split(f"CREATE VIEW {view} AS")[1].split('"""')[0]
        assert "JOIN v_rx_evidence e" in body and "e.evidence_id" in body and "e.page_no" in body


def test_ac1_the_migration_only_adds_and_can_be_undone():
    up = MIGRATION.split("def upgrade")[1].split("def downgrade")[0]
    assert not re.search(r"\b(ALTER|DROP|UPDATE|DELETE FROM|TRUNCATE)\b", up)
    assert 'down_revision = "0038"' in MIGRATION
    down = MIGRATION.split("def downgrade")[1]
    assert "DROP VIEW IF EXISTS" in down and "DROP TABLE IF EXISTS" in down


def test_ac1_saving_a_prescription_writes_the_rows_in_the_same_call(saved):
    out = _write({"patient": {"name": "Rahul Sharma"}, "investigations_advised": ["HbA1c"]})
    assert out["facts"] >= 1
    assert saved["refreshed"] == ["doc-1"], "the rows are written by write_extraction itself"


def test_ac1_a_rejected_or_replaced_value_has_no_row():
    """The rows follow the facts: only current, not-rejected ones are read."""
    sql = " ".join(normalized._FACTS.split())
    assert "f.is_current" in sql and "f.review_state <> 'rejected'" in sql
    assert "ANY(f.source_doc_ids)" in sql


# ------------------------------------------------------------------ FR5: versions, raw answer


def test_fr5_every_value_is_saved_with_the_versions_that_read_it():
    v = X.versions("prescription")
    assert set(v) == {"prompt_version", "lab_master", "preparation_phrases", "context_rules"}
    assert re.fullmatch(r"[0-9a-f]{12}", v["prompt_version"])
    assert v["lab_master"] == lab_master.version() and all(v.values())


def test_fr5_the_prompt_version_changes_with_the_wording_and_not_with_the_page(monkeypatch):
    from cdi_adapter.extract import prompt

    before = prompt.prompt_version("prescription")
    assert before == prompt.prompt_version("prescription")
    assert before != prompt.prompt_version("lab_report")
    monkeypatch.setitem(prompt.GUIDANCE, "prescription",
                        prompt.GUIDANCE["prescription"] + " One more rule.")
    assert prompt.prompt_version("prescription") != before


def test_fr5_the_raw_model_answer_is_kept_beside_the_answer():
    class Model(ml.MLClient):
        def __init__(self) -> None:  # no gateway: the answer is given
            pass

        def generate(self, *a: Any, **k: Any) -> str:
            return 'Here it is:\n```json\n{"doc_type": "prescription"}\n```'

    out = Model().vlm_json(None, "read it", X.SCHEMA_FOR_DOC_TYPE["prescription"], retries=0)
    assert out.raw.startswith("Here it is:"), "exactly as the model said it"  # type: ignore[attr-defined]
    assert ml.RAW_ANSWER not in out, "the answer itself is unchanged: still valid for its schema"
    saved = ml.keep_raw({"doc_type": "prescription", "_doctor": {}}, out)
    assert saved[ml.RAW_ANSWER] == out.raw  # type: ignore[attr-defined]
    assert ml.RAW_ANSWER not in ml.keep_raw({}, {"no": "raw answer"})


def test_fr5_the_raw_answer_is_never_read_as_a_value():
    from cdi_adapter.extract.facts import facts_from_payload

    payload = {"patient": {"name": "Rahul Sharma"}, "investigations_advised": ["HbA1c"]}
    plain = facts_from_payload("prescription", dict(payload))
    noted = facts_from_payload("prescription", {**payload, ml.RAW_ANSWER: "CBC, Tab X 500 mg"})
    assert [(d.fact_type, d.local_text) for d in noted] == [(d.fact_type, d.local_text)
                                                           for d in plain]  # fmt: skip


# ------------------------------------------------------------------ AC2: twice gives the same


def test_ac2_the_same_facts_give_exactly_the_same_rows():
    assert normalized.rows_for(DOC, _prescription()) == normalized.rows_for(DOC, _prescription())


def test_ac2_saving_twice_leaves_one_set_of_rows_not_two():
    conn = Conn(_prescription())
    first = normalized.refresh(conn, DOC)
    once = {t: list(rows) for t, rows in conn.tables.items()}
    second = normalized.refresh(conn, DOC)
    assert first == second == {"rx_lab_test": 2, "rx_preparation": 3,
                               "rx_test_context": len(once["rx_test_context"])}  # fmt: skip
    assert conn.tables == once, "the second save replaced the rows, it did not add to them"


def test_ac2_the_old_rows_are_removed_before_the_new_ones_are_written():
    conn = Conn(_prescription())
    normalized.refresh(conn, DOC)
    verbs = [sql.split(" ", 3)[:3] for sql, _ in conn.calls if not sql.startswith("SELECT")]
    deletes = [v[2] for v in verbs if v[0] == "DELETE"]
    assert deletes == ["rx_test_context", "rx_preparation", "rx_lab_test"]
    first_insert = next(i for i, v in enumerate(verbs) if v[0] == "INSERT")
    assert all(v[0] == "DELETE" for v in verbs[:first_insert]) and first_insert == 3
    # a test's row exists before a link to it is written
    inserted = [v[2] for v in verbs if v[0] == "INSERT"]
    assert inserted.index("rx_lab_test") < inserted.index("rx_test_context")


def test_ac2_another_documents_rows_are_left_alone():
    conn = Conn(_prescription())
    other = {"document_id": "22222222-2222-2222-2222-222222222222", "fact_id": "x"}
    conn.tables["rx_lab_test"].append(other)
    normalized.refresh(conn, DOC)
    assert other in conn.tables["rx_lab_test"]


def test_ac2_a_reason_written_twice_gives_one_row_per_test_and_reason():
    facts = [_fact(1, "condition", "Type 2 diabetes mellitus"),
             _fact(2, "condition", "Type 2 diabetes mellitus"),
             _fact(3, "lab_order", "HbA1c")]  # fmt: skip
    rows = normalized.rows_for(DOC, facts)["rx_test_context"]
    pairs = [(r["test_fact_id"], r["reason_fact_id"]) for r in rows]
    assert len(pairs) == len(set(pairs)) == 1


def test_ac2_the_database_itself_refuses_a_second_row_for_the_same_thing():
    assert "fact_id        uuid PRIMARY KEY" in MIGRATION  # one row per test
    assert "fact_id         uuid PRIMARY KEY" in MIGRATION  # one row per preparation
    assert "UNIQUE (test_fact_id, reason_fact_id)" in MIGRATION  # one row per pair


def test_ac2_reading_a_document_again_clears_its_earlier_facts_first(saved):
    _write({"patient": {"name": "Rahul Sharma"}, "investigations_advised": ["HbA1c"]})
    assert saved["order"][0] == "purge" and saved["order"][-1] == "refresh"


def test_ac2_a_correction_by_a_person_refreshes_the_rows_of_that_document(monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(normalized, "refresh", lambda conn, doc: seen.append(str(doc)))
    normalized.refresh_for_fact(object(), {"source_doc_ids": [DOC]})  # type: ignore[arg-type]
    normalized.refresh_for_fact(object(), {"source_doc_ids": None})  # type: ignore[arg-type]
    assert seen == [DOC]


# ------------------------------------------------------------------ AC3: all or nothing


class Scope:
    """``session_scope`` as it behaves: commit when the block ends, roll back when it raises."""

    def __init__(self) -> None:
        self.opened = self.committed = self.rolled_back = 0

    def __call__(self) -> Any:
        return self

    def __enter__(self) -> Any:
        self.opened += 1
        return object()

    def __exit__(self, kind: Any, *rest: Any) -> bool:
        if kind is None:
            self.committed += 1
        else:
            self.rolled_back += 1
        return False


def _ctx() -> Any:
    return X._Ctx(document_id=DOC, doc_type="prescription", run_id="run-1", blocks=[], pages=[],
                  block_map={}, ocr_runs=[], cls=None, image=None)  # fmt: skip


class Vlm:
    model, version, role = "stub", "1", "primary"


def test_ac3_a_failure_while_writing_the_rows_fails_the_whole_save():
    conn = Conn(_prescription(), fail_on="INSERT INTO rx_preparation")
    with pytest.raises(RuntimeError, match="went away"):
        normalized.refresh(conn, DOC)  # nothing swallowed: the caller's transaction rolls back


def test_ac3_the_facts_and_the_rows_are_saved_in_one_transaction(monkeypatch):
    scope, steps = Scope(), []
    monkeypatch.setattr(X, "session_scope", scope)
    monkeypatch.setattr(X, "annotate", lambda payload, *a: payload)
    monkeypatch.setattr(X, "with_stamp", lambda payload, *a: payload)
    monkeypatch.setattr(X, "write_extraction", lambda conn, **k: steps.append("write") or {
        "extraction_id": "e", "facts": 3, "partial": False})  # fmt: skip
    for name in ("set_document_status", "finish_run", "write_audit"):
        monkeypatch.setattr(X.repo, name, lambda *a, _n=name, **k: steps.append(_n))
    X._complete(_ctx(), {}, Vlm(), patient_id=None, expected_patient=None, actor="t")  # type: ignore[arg-type]
    assert steps == ["write", "set_document_status", "finish_run", "write_audit"]
    assert (scope.opened, scope.committed, scope.rolled_back) == (1, 1, 0)


def test_ac3_when_saving_fails_half_way_nothing_is_committed(monkeypatch):
    scope = Scope()
    monkeypatch.setattr(X, "session_scope", scope)
    monkeypatch.setattr(X, "annotate", lambda payload, *a: payload)
    monkeypatch.setattr(X, "with_stamp", lambda payload, *a: payload)

    def half_way(conn: Any, **k: Any) -> dict[str, Any]:
        raise RuntimeError("the database went away")

    monkeypatch.setattr(X, "write_extraction", half_way)
    marked = []
    monkeypatch.setattr(X.repo, "set_document_status", lambda *a, **k: marked.append(a[2]))
    with pytest.raises(RuntimeError):
        X._complete(_ctx(), {}, Vlm(), patient_id=None, expected_patient=None, actor="t")  # type: ignore[arg-type]
    assert (scope.committed, scope.rolled_back) == (0, 1)
    assert marked == [], "the document is not marked extracted"


def test_ac3_the_rows_are_written_inside_write_extraction_not_after_it():
    import inspect

    body = inspect.getsource(X.write_extraction)
    assert "normalized.refresh(conn, document_id)" in body
    assert "session_scope" not in body and "commit" not in body, "it never opens its own"


def test_ac3_the_error_is_logged_and_the_document_marked_failed(monkeypatch):
    scope, logged, state = Scope(), [], {}
    monkeypatch.setattr(X, "session_scope", scope)
    monkeypatch.setattr(X.log, "error", lambda event, **k: logged.append((event, k)))
    monkeypatch.setattr(X.repo, "fail_run", lambda conn, run, error: state.update(run=error))
    monkeypatch.setattr(X.repo, "set_document_status", lambda conn, doc, status, error=None:
                        state.update(status=status, error=error))  # fmt: skip
    X._record_failure(_ctx(), RuntimeError("the database went away"), "t")
    assert logged == [("extract_failed", {"document_id": DOC,
                                          "error": "the database went away"})]  # fmt: skip
    assert state["status"] == "error" and state["error"]["stage"] == "extract"
    assert "the database went away" in state["run"]


def test_ac3_the_caller_is_told_too(monkeypatch):
    recorded: list[Exception] = []
    monkeypatch.setattr(X, "_start", lambda *a, **k: _ctx())
    monkeypatch.setattr(X, "_request", lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("the database went away")))  # fmt: skip
    monkeypatch.setattr(X, "_record_failure", lambda ctx, exc, *a: recorded.append(exc))
    with pytest.raises(RuntimeError):
        X.extract_document(DOC, client=Vlm())  # type: ignore[arg-type]
    assert len(recorded) == 1


# ------------------------------------------------------------------ helpers


@pytest.fixture
def saved(monkeypatch) -> dict[str, Any]:
    rec: dict[str, Any] = {"facts": [], "order": [], "refreshed": []}

    def fact(conn: Any, **f: Any) -> str:
        rec["facts"].append(f)
        return f"fact-{len(rec['facts'])}"

    def refresh(conn: Any, document_id: Any) -> dict[str, int]:
        rec["order"].append("refresh")
        rec["refreshed"].append(str(document_id))
        return {}

    monkeypatch.setattr(X.repo, "purge_document_facts",
                        lambda *a, **k: rec["order"].append("purge") or 0)  # fmt: skip
    monkeypatch.setattr(X.repo, "insert_extraction", lambda *a, **k: "ext-1")
    monkeypatch.setattr(X.repo, "insert_encounter", lambda *a, **k: "enc-1")
    monkeypatch.setattr(X.repo, "insert_clinical_fact", fact)
    monkeypatch.setattr(X.repo, "insert_fact_provenance", lambda *a, **k: None)
    monkeypatch.setattr(X.repo, "set_document_patient", lambda *a, **k: None)
    monkeypatch.setattr(X.mpi, "resolve_identity",
                        lambda conn, cand: ("00000000-0000-0000-0000-00000000aaaa", "minted"))
    monkeypatch.setattr(X.mpi, "add_alias", lambda *a, **k: None)
    monkeypatch.setattr(X.normalized, "refresh", refresh)
    return rec


class _Quiet:
    def execute(self, *a: Any, **k: Any) -> Any:
        return self

    def mappings(self) -> Any:
        return self

    def all(self) -> list[Any]:
        return []

    def scalar_one_or_none(self) -> None:
        return None


BLOCKS = [{"id": f"block-{i}", "text": t, "page_id": "page-1", "ocr_conf": 0.95,
           "bbox": [10, 20 * i, 500, 20 * i + 18]}
          for i, t in enumerate(("Name: Rahul Sharma", "Adv: HbA1c"), 1)]  # fmt: skip


def _write(payload: dict[str, Any]) -> dict[str, Any]:
    payload = X.annotate(dict(payload), "prescription", BLOCKS)
    return X.write_extraction(_Quiet(), document_id="doc-1", doc_type="prescription",  # type: ignore[arg-type]
                              payload=payload, block_id_map={"b1": "block-1", "b2": "block-2"},
                              blocks=BLOCKS, run_ids=[], model_stack={})  # fmt: skip
