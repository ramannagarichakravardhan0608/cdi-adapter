"""UP-S3 acceptance: show the result on the screen and let the person download the JSON.

One group of tests per acceptance criterion, written on 2026-10-08 to record what the code does
today. **No application code was changed for this file.**

How they run: the result screen's script is checked as text, the result builder
(``cdi_adapter/result.py``) is called for real, and the two result routes are called directly
with the database replaced by a stand-in. Nothing here opens a browser, and the paths that need
PostgreSQL are covered by ``tests/test_result_integration.py`` (not run on this box).

The first check (the same day) found AC4, AC5 and AC1's preparation card unbuilt; their tests
were strict expected failures. All three were then built and every test here now has to pass.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from cdi_adapter import result
from cdi_adapter.webapp import app as app_module
from cdi_adapter.webapp.page import SCRIPT as UPLOAD
from cdi_adapter.webapp.result_page import BODY, SCRIPT

ROOT = Path(__file__).resolve().parents[1]
DOC = UUID("00000000-0000-0000-0000-0000000000a1")
EVIDENCE = {"page": 1, "bbox": [120, 340, 560, 380], "observation_ids": ["obs-1"]}


def _accepted(raw: Any) -> dict[str, Any]:
    return result.value(raw, review_state="auto_accepted", confidence=0.97, evidence=EVIDENCE)


def _held(raw: Any) -> dict[str, Any]:
    return result.value(raw, review_state="held", confidence=0.41, evidence=EVIDENCE)


def _routes() -> set[str]:
    return set(app_module.app.openapi()["paths"])


def _card(name: str) -> str:
    """The source of one card-drawing function on the result screen."""
    start = SCRIPT.index(f"function {name}(d){{")
    return SCRIPT[start : SCRIPT.index("\nfunction ", start + 1)] if "\nfunction " in SCRIPT[
        start + 1 :] else SCRIPT[start:]  # fmt: skip


# ------------------------------------------------------------------ AC1
# GIVEN a job is finished WHEN the person opens the result
# THEN patient, doctor, lab tests and preparation cards are shown with a status tag on each value


def test_ac1_a_finished_page_links_to_its_result() -> None:
    assert "d.steps_done.includes('validate')" in UPLOAD  # only once it has been read
    assert 'href="/result/${d.document_id}"' in UPLOAD
    assert "/result/{document_id}" in _routes()


def test_ac1_the_patient_doctor_and_lab_test_cards_are_drawn() -> None:
    assert "card('Patient'," in SCRIPT and "card('Doctor'," in SCRIPT
    assert "card('Lab tests'," in SCRIPT
    drawn = SCRIPT[SCRIPT.index("async function load(){") :]
    assert "patientCard(d) + doctorCard(d) + testsCard(d)" in drawn
    assert "preparationCard(d) + statusCard(d)" in drawn


def test_ac1_there_is_only_one_way_to_draw_a_value_and_it_prints_the_status_tag() -> None:
    """Every value on the screen goes through ``val()``, which prints the value and its tag
    together: there is no path that prints a bare value."""
    val = SCRIPT[SCRIPT.index("function val(v){") : SCRIPT.index("const row =")]
    assert "class=\"pill ${ok ? '' : 'warn'}\"" in val and "v.status.replace('_', ' ')" in val
    assert (
        'const row = (label, v) => `<tr><th style="width:38%">${esc(label)}</th><td>${val(v)}'
        in SCRIPT
    )
    for card in ("patientCard", "doctorCard", "testsCard"):
        body = _card(card)
        assert "row(" in body or "val(" in body, card


def test_ac1_each_patient_doctor_and_test_value_goes_through_the_tag() -> None:
    assert "row('Name', p.name)" in SCRIPT and "row('Sex', p.sex)" in SCRIPT
    assert "row('Date of birth', p.dob)" in SCRIPT
    assert (
        "row('Name', doc.name)" in SCRIPT
        and "row('Registration no.', doc.registration_no)" in SCRIPT
    )
    assert "<td>${val(t.value)}</td>" in SCRIPT  # each lab test
    assert "result: ${val(t.result)}" in SCRIPT  # and its result, when there is one


def test_ac1_every_value_the_builder_makes_has_a_status() -> None:
    for v in (_accepted("HbA1c"), _held("HbA1c"),
              result.value("x", review_state=None, confidence=None)):  # fmt: skip
        assert v["status"] in ("accepted", "needs_check") and "confidence" in v


def _fact(fact_type: str, text: str, **more: Any) -> dict[str, Any]:
    return {"id": f"fact-{text}", "fact_type": fact_type, "local_text": text, "value_text": None,
            "value_num": None, "review_state": "auto_accepted", "confidence_overall": 0.93,
            "is_current": True, **more}  # fmt: skip


@pytest.fixture
def no_database(monkeypatch) -> None:
    """Evidence lookups answered without a database: every fact was read at one place."""
    monkeypatch.setattr(result, "_evidence", lambda conn, fact_id: dict(EVIDENCE))


def test_ac1_there_is_a_preparation_card_of_its_own() -> None:
    assert "card('Preparation'," in SCRIPT
    assert "Preparation</h3>" not in _card("testsCard")  # no longer a list under the lab tests


def test_ac1_every_preparation_instruction_is_drawn_with_its_status_tag() -> None:
    body = _card("preparationCard")
    assert "const items = d.preparation || [];" in body and "${val(p)}" in body
    assert "No preparation instruction" in body  # said plainly when there is none


def test_ac1_the_result_carries_preparation_items(no_database) -> None:
    facts = [_fact("advice", "Do all tests fasting"), _fact("advice", "Low salt diet"),
             _fact("care_plan", "Urine: first morning sample, midstream"),
             _fact("medication", "Tab Metformin 500 mg, empty stomach")]  # fmt: skip
    items = result._preparation(None, facts)  # type: ignore[arg-type]
    assert [(i["text"], i["instruction"]) for i in items] == [
        ("Do all tests fasting", "fasting"),
        ("Urine: first morning sample, midstream", "timing")]  # fmt: skip
    for item in items:  # each one is a value like any other: a status, and where it was read
        assert item["status"] == "accepted" and item["value"] == item["text"]
        assert item["evidence"]["bbox"] == EVIDENCE["bbox"]
    assert "preparation" not in result.ABSENT


@pytest.mark.parametrize(("written", "kind"), [
    ("Do all tests Fasting", "fasting"), ("8 hours fast before sample", "fasting"),
    ("on empty stomach", "fasting"), ("PPBS - 2 hours after breakfast", "after_food"),
    ("Post prandial sample", "after_food"), ("early morning urine", "timing"),
    ("24 hour urine protein", "timing"), ("midstream clean catch", "collection"),
    ("Low salt diet", None), ("Regular exercise, weight reduction", None),
    ("Take after food", None), ("Review after 2 weeks", None), ("", None), (None, None),
])  # fmt: skip
def test_ac1_what_counts_as_a_preparation_instruction(written: Any, kind: str | None) -> None:
    assert result.preparation_kind(written) == kind


def test_ac1_a_held_instruction_shows_as_needing_a_check_and_is_counted(no_database) -> None:
    held = result._preparation(None, [_fact("advice", "Fasting sample", review_state="held")])  # type: ignore[arg-type]
    assert held[0]["status"] == "needs_check" and held[0]["reason"]
    assert result._review({"preparation": held})["needs_check_count"] == 1


def test_ac1_which_test_an_instruction_belongs_to_is_still_not_worked_out() -> None:
    assert "lab_tests[].preparation_refs" in result.ABSENT
    assert "Not extracted yet" in SCRIPT and "d.absent" in SCRIPT


# ------------------------------------------------------------------ AC2
# GIVEN a value needs a check WHEN it is shown
# THEN it carries a 'needs a check' tag and the reason


def test_ac2_a_value_the_gate_did_not_accept_needs_a_check_and_says_why() -> None:
    v = _held("Tab Metformin 500 mg")
    assert v["status"] == "needs_check" and v["reason"] == "not governed by the gate"


def test_ac2_a_governed_value_without_proof_needs_a_check_and_says_why() -> None:
    v = result.value("HbA1c", review_state="auto_accepted", confidence=0.99, evidence=None)
    assert v["status"] == "needs_check" and v["reason"] == "no evidence recorded"


def test_ac2_a_needs_check_value_can_never_be_built_without_a_reason() -> None:
    for state in (None, "held", "pending", "rejected"):
        for evidence in (None, EVIDENCE):
            v = result.value("x", review_state=state, confidence=0.5, evidence=evidence)
            assert v["status"] == "needs_check" and v["reason"]


def test_ac2_an_accepted_value_carries_no_reason() -> None:
    assert "reason" not in _accepted("HbA1c")


def test_ac2_the_screen_shows_the_tag_in_warning_colour_and_the_reason_beside_it() -> None:
    val = SCRIPT[SCRIPT.index("function val(v){") : SCRIPT.index("const row =")]
    assert "const ok = v.status === 'accepted';" in val
    assert "(!ok && v.reason) ? ` <span class=muted" in val and "${esc(v.reason)}" in val
    assert "${why}" in val  # the reason is part of what is drawn


def test_ac2_the_tag_reads_needs_check() -> None:
    """The AC quotes 'needs a check'; the tag is the status with its underscore removed, which
    reads "needs check". Same meaning, one word short."""
    assert "needs_check".replace("_", " ") == "needs check"
    assert "v.status.replace('_', ' ')" in SCRIPT


def test_ac2_held_values_are_also_listed_once_with_their_reasons() -> None:
    doc = {"lab_tests": [{"value": _held("HbA1c")}, {"value": _accepted("TSH")}]}
    review = result._review(doc)
    assert review == {
        "needs_check_count": 1,
        "items": [{"value": "HbA1c", "reason": "not governed by the gate"}],
    }
    assert "Needs a person: ${esc(r.needs_check_count ?? 0)}" in SCRIPT
    assert "held.map(i => `<li>${esc(i.value)} <span class=muted>${esc(i.reason || '')}" in SCRIPT


# ------------------------------------------------------------------ AC3
# GIVEN the person clicks 'Download JSON' WHEN the file downloads
# THEN it equals the stored JSON for that document


@pytest.fixture
def stored(monkeypatch) -> dict[str, Any]:
    """The result routes with the database replaced: what ``result.as_json`` returns stands for
    what the database holds, and every call is counted."""
    state: dict[str, Any] = {"calls": [], "body": json.dumps(
        {"schema_version": "result.v1", "document_id": str(DOC), "status": "complete",
         "lab_tests": [{"as_written": "HbA1c", "value": _accepted("HbA1c")}]},
        sort_keys=True, indent=2)}  # fmt: skip

    @contextmanager
    def scope() -> Any:
        yield "conn"

    def as_json(conn: Any, document_id: Any) -> str:
        state["calls"].append(str(document_id))
        if state.get("missing"):
            raise LookupError(f"no document {document_id}")
        return str(state["body"])

    monkeypatch.setattr(app_module, "session_scope", scope)
    monkeypatch.setattr(result, "as_json", as_json)
    return state


def test_ac3_the_button_is_there_and_downloads_the_result_document() -> None:
    assert "id=dl" in BODY and "Download JSON" in BODY
    assert "$('#dl').onclick = () => { location.href = api + '/download'; };" in SCRIPT
    assert "/api/documents/{document_id}/result.json/download" in _routes()


def test_ac3_the_downloaded_file_is_byte_for_byte_the_documents_json(stored) -> None:
    shown = app_module.document_result(DOC)
    saved = app_module.download_result(DOC)
    assert saved.body == shown.body == stored["body"].encode()
    assert stored["calls"] == [str(DOC), str(DOC)]  # both asked for that document, no other


def test_ac3_the_download_is_a_named_json_file(stored) -> None:
    saved = app_module.download_result(DOC)
    assert saved.media_type == "application/json"
    assert saved.headers["content-disposition"] == f'attachment; filename="result-{DOC}.json"'
    assert json.loads(saved.body)["document_id"] == str(DOC)


def test_ac3_the_screen_and_the_file_come_from_the_same_place() -> None:
    """The screen reads ``result.json`` and the button saves ``result.json/download``, which
    returns the first one's bytes: they cannot disagree."""
    assert "const r = await fetch(api);" in SCRIPT
    assert "/api/documents/${docId}/result.json" in SCRIPT
    import inspect

    assert "out = document_result(document_id)" in inspect.getsource(app_module.download_result)


def test_ac3_the_same_document_gives_the_same_bytes_every_time(stored) -> None:
    assert app_module.download_result(DOC).body == app_module.download_result(DOC).body


def test_ac3_the_json_is_written_in_a_fixed_order() -> None:
    """Why two downloads match: keys sorted, nothing from the clock. (That a real document
    builds identical bytes twice is in tests/test_result_integration.py.)"""
    import inspect

    assert "sort_keys=True" in inspect.getsource(result.as_json)


def test_ac3_a_document_that_does_not_exist_downloads_nothing(stored) -> None:
    from fastapi import HTTPException

    stored["missing"] = True
    with pytest.raises(HTTPException) as refused:
        app_module.download_result(DOC)
    assert refused.value.status_code == 404


def test_ac3_the_json_is_built_when_asked_for_not_kept_as_a_file() -> None:
    """ISSUE, recorded as a fact: the AC says "the stored JSON". No JSON file is stored - the
    document is built from the database each time it is asked for, in a fixed order, so the
    download equals what the screen shows and what any later download gives while the
    underlying values are unchanged. If a reviewer corrects a value, the next download differs."""
    import inspect

    source = inspect.getsource(result)
    assert "put_bytes" not in source and "INSERT INTO" not in source


# ------------------------------------------------------------------ AC4
# GIVEN a value is clicked WHEN the evidence opens
# THEN the picture region it came from is highlighted


def _val() -> str:
    return SCRIPT[SCRIPT.index("function val(v){") : SCRIPT.index("const row =")]


def test_ac4_every_value_carries_where_on_the_page_it_came_from() -> None:
    v = _accepted("HbA1c")
    assert v["evidence"] == {"page": 1, "bbox": [120, 340, 560, 380], "observation_ids": ["obs-1"]}


def test_ac4_the_page_picture_can_be_fetched() -> None:
    assert "/api/documents/{document_id}/pages/{page_no}" in _routes()


def test_ac4_a_value_on_the_result_screen_can_be_clicked() -> None:
    val = _val()
    assert "<button type=button class=ev" in val and "data-evidence=" in val
    assert "{doc: v._doc, page: ev.page, bbox: ev.bbox, value: String(v.value)}" in val
    assert "const b = e.target.closest('[data-evidence]');" in SCRIPT
    assert "if (b) showEvidence(JSON.parse(b.dataset.evidence));" in SCRIPT


def test_ac4_a_value_with_no_place_on_the_page_stays_plain_text() -> None:
    """Identity and worked-out dates have no box: they are not made to look clickable."""
    val = _val()
    assert (
        "const placed = v._doc && ev.page && Array.isArray(ev.bbox) && ev.bbox.length === 4;" in val
    )
    assert ": esc(v.value);" in val


def test_ac4_the_status_tag_is_still_printed_beside_a_clickable_value() -> None:
    assert "return `${shown} <span class=\"pill ${ok ? '' : 'warn'}\">" in _val()


def test_ac4_the_evidence_view_shows_the_page_picture() -> None:
    assert "<img id=evimg" in BODY and "id=evidence" in BODY
    show = SCRIPT[
        SCRIPT.index("function showEvidence(ev){") : SCRIPT.index("$('#cards').addEventListener")
    ]
    assert "img.src = `/api/documents/${ev.doc}/pages/${ev.page}`;" in show
    assert "read on page ${ev.page}" in show


def test_ac4_the_region_the_value_came_from_is_highlighted() -> None:
    """The box is in the picture's own pixels; it is scaled to the size the picture is drawn at
    and laid over it once the picture has loaded."""
    assert "<div id=evbox" in BODY and "border:3px solid var(--bad)" in BODY
    show = SCRIPT[
        SCRIPT.index("function showEvidence(ev){") : SCRIPT.index("$('#cards').addEventListener")
    ]
    assert (
        "const sx = img.clientWidth / img.naturalWidth, sy = img.clientHeight / img.naturalHeight;"
        in show
    )
    assert "const [x0, y0, x1, y1] = ev.bbox;" in show
    assert "left: x0 * sx + 'px', top: y0 * sy + 'px', width: (x1 - x0) * sx + 'px'" in show
    assert show.index("img.onload = () => {") < show.index("img.src =")  # ready before it loads


def test_ac4_a_picture_that_cannot_be_loaded_is_said_so_and_the_view_can_be_closed() -> None:
    assert "img.onerror = () => { img.hidden = true; $('#everr').hidden = false; };" in SCRIPT
    assert "id=everr" in BODY and "id=evclose" in BODY
    assert "$('#evclose').onclick" in SCRIPT


def test_ac4_each_value_knows_which_document_its_picture_is_in() -> None:
    assert (
        "function tagDoc(node, doc){" in SCRIPT and "if (isValue(node)) node._doc = doc;" in SCRIPT
    )
    assert "tagDoc(d, d.document_id);" in SCRIPT  # one page: its own document
    assert "tagDoc(item, item.document_id)" in SCRIPT  # an upload: each item's own page


def test_ac4_an_uploads_result_says_which_document_each_item_came_from(monkeypatch) -> None:
    def page(doc_id: str) -> dict[str, Any]:
        return {"schema_version": "result.v1", "document_id": doc_id, "status": "complete",
                "page_count": 1, "patient": {}, "doctor": {"name": _accepted("Dr Rao")},
                "visit": {}, "lab_tests": [{"as_written": doc_id, "value": _accepted(doc_id)}],
                "diagnoses": [], "preparation": [{**_accepted("Fasting"), "text": "Fasting",
                                                  "instruction": "fasting"}],
                "review": {"needs_check_count": 0, "items": []}}  # fmt: skip

    class _Rows:
        def mappings(self) -> Any:
            return self

        def first(self) -> Any:
            return {"id": "a" * 32, "state": "review", "created_at": None, "docs": [
                {"filename": "p1.jpg", "status": "done", "document_id": "d1"},
                {"filename": "p2.jpg", "status": "done", "document_id": "d2"}]}  # fmt: skip

    monkeypatch.setattr(result, "build", lambda conn, doc_id: page(doc_id))
    from types import SimpleNamespace

    out = result.build_job(SimpleNamespace(execute=lambda *a, **k: _Rows()), "a" * 32)  # type: ignore[arg-type]
    assert [(t["document_id"], t["page"]) for t in out["lab_tests"]] == [("d1", 1), ("d2", 2)]
    assert [p["document_id"] for p in out["preparation"]] == ["d1", "d2"]
    assert out["from_document"]["doctor"] == "d1" and out["from_document"]["patient"] is None


# ------------------------------------------------------------------ AC5
# GIVEN a person presses 'Report a wrong result' WHEN they submit
# THEN the report is saved with the document id and appears in the evaluation backlog


class _User:
    username = "dr.rao"


@pytest.fixture
def audit(monkeypatch) -> list[dict[str, Any]]:
    """The report route with the database replaced: what would be written to the audit log is
    kept in a list, and the document is taken to exist unless its id is all ``f``."""
    from fastapi import HTTPException

    written: list[dict[str, Any]] = []

    @contextmanager
    def scope() -> Any:
        yield "conn"

    def doc(conn: Any, document_id: Any) -> dict[str, Any]:
        if str(document_id).startswith("ffffffff"):
            raise HTTPException(404, "document not found")
        return {"id": document_id}

    def write(conn: Any, **row: Any) -> int:
        written.append(row)
        return 100 + len(written)

    monkeypatch.setattr(app_module, "session_scope", scope)
    monkeypatch.setattr(app_module, "_doc", doc)
    monkeypatch.setattr(app_module.repo, "write_audit", write)
    monkeypatch.setattr(app_module.auth, "actor", lambda request: _User())
    return written


def _report(note: str, document: UUID = DOC, value: str | None = None) -> dict[str, Any]:
    body = app_module.WrongResult(note=note, value=value)
    return app_module.report_wrong_result(document, body, request=None)  # type: ignore[arg-type]


def test_ac5_there_is_a_report_a_wrong_result_button() -> None:
    assert "<button id=wrong type=button>Report a wrong result</button>" in BODY
    assert "id=reportpanel" in BODY and "<textarea id=reportnote" in BODY
    assert "$('#wrong').onclick" in SCRIPT


def test_ac5_there_is_a_route_that_takes_the_report_for_a_document() -> None:
    paths = app_module.app.openapi()["paths"]
    assert "post" in paths["/api/documents/{document_id}/result/report"]
    assert "fetch(`/api/documents/${doc}/result/report`, {method: 'POST'," in SCRIPT


def test_ac5_the_report_is_saved_with_the_document_id(audit) -> None:
    out = _report("The test is HbA1c, not Hb")
    assert out == {"report_id": 101, "document_id": str(DOC)}
    (row,) = audit
    assert row["entity"] == "result_report" and row["entity_id"] == DOC
    assert row["action"] == "create" and row["actor"] == "dr.rao"  # who reported it
    assert row["detail"]["note"] == "The test is HbA1c, not Hb"
    assert row["detail"]["schema_version"] == result.SCHEMA_VERSION


def test_ac5_the_report_is_kept_in_the_append_only_audit_log() -> None:
    """Where it is kept: ``audit_log`` (no new table), whose rows cannot be changed or removed.
    That the row really lands and is listed back is for the PostgreSQL run."""
    import inspect

    source = inspect.getsource(app_module.report_wrong_result)
    assert "repo.write_audit(" in source and "entity=RESULT_REPORT" in source
    assert app_module.RESULT_REPORT == "result_report"


def test_ac5_a_report_about_a_document_that_does_not_exist_is_refused(audit) -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as refused:
        _report("wrong", document=UUID("ffffffff-0000-0000-0000-000000000000"))
    assert refused.value.status_code == 404 and audit == []


@pytest.mark.parametrize("note", ["  ", "x", "ab", " \n a \n "])
def test_ac5_a_report_that_says_nothing_is_refused(audit, note: str) -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as refused:
        _report(note)
    assert refused.value.status_code == 422 and audit == []


def test_ac5_a_report_cannot_be_endless() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        app_module.WrongResult(note="x" * 1001)
    assert "maxlength=1000" in BODY


def test_ac5_the_note_is_tidied_and_an_empty_value_is_not_kept(audit) -> None:
    _report("  the dose   is wrong \n  it says 50 mg ", value="   ")
    assert audit[0]["detail"] == {"note": "the dose is wrong it says 50 mg", "value": None,
                                  "schema_version": result.SCHEMA_VERSION}  # fmt: skip


def test_ac5_reporting_changes_nothing_in_the_result() -> None:
    """No value on the page becomes editable, and the route writes one audit row and nothing
    else: corrections stay in the review console."""
    import inspect

    assert "<input" not in BODY and BODY.count("<textarea") == 1
    source = inspect.getsource(app_module.report_wrong_result)
    assert "UPDATE" not in source and "INSERT" not in source
    assert "It does not change the result" in BODY


def test_ac5_the_screen_says_it_was_saved_and_where_to_find_it() -> None:
    assert "Saved as report #${esc(saved.report_id)}" in SCRIPT
    assert 'href="/evaluation"' in SCRIPT and 'href="/evaluation"' in BODY


def test_ac5_reports_can_be_listed_as_an_evaluation_backlog(monkeypatch) -> None:
    from datetime import UTC, datetime
    from types import SimpleNamespace

    rows = [{"id": 102, "at": datetime(2026, 10, 8, 9, 30, tzinfo=UTC), "actor": "dr.rao",
             "document_id": str(DOC), "note": "The test is HbA1c, not Hb", "value": None,
             "filename": "rx-001.png"}]  # fmt: skip
    asked: list[str] = []

    def execute(statement: Any, params: Any = None) -> Any:
        sql = " ".join(str(statement).split())
        asked.append(sql)
        if sql.startswith("SELECT count(*)"):
            return SimpleNamespace(scalar_one=lambda: 1)
        return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: rows))

    @contextmanager
    def scope() -> Any:
        yield SimpleNamespace(execute=execute)

    monkeypatch.setattr(app_module, "session_scope", scope)
    out = app_module.evaluation_backlog()
    assert out == {"count": 1, "reports": [{
        "report_id": 102, "reported_at": "2026-10-08T09:30:00+00:00", "reported_by": "dr.rao",
        "document_id": str(DOC), "file_name": "rx-001.png",
        "note": "The test is HbA1c, not Hb", "value": None}]}  # fmt: skip
    listing = asked[1]
    assert "FROM audit_log a" in listing and "WHERE a.entity = :e" in listing
    assert "ORDER BY a.at DESC" in listing  # newest first


def test_ac5_the_backlog_has_a_page_and_a_link_in_the_header() -> None:
    from cdi_adapter.webapp.result_page import BACKLOG_BODY, BACKLOG_SCRIPT, evaluation_page
    from cdi_adapter.webapp.theme import header

    assert {"/evaluation", "/api/evaluation/backlog"} <= _routes()
    assert (
        "Evaluation backlog" in BACKLOG_BODY
        and "fetch('/api/evaluation/backlog')" in BACKLOG_SCRIPT
    )
    assert (
        'href="/result/${esc(x.document_id)}"' in BACKLOG_SCRIPT
    )  # each report leads to its result
    assert 'href="/evaluation"' in header() and "Evaluation backlog" in evaluation_page()


def test_ac5_only_clinical_staff_may_report_or_read_the_backlog() -> None:
    from cdi_adapter.webapp import auth

    for key in (("POST", "/api/documents/{document_id}/result/report"),
                ("GET", "/api/evaluation/backlog"), ("GET", "/evaluation")):  # fmt: skip
        assert auth.POLICY[key] == {"reviewer", "admin"}, key


@pytest.mark.parametrize("limit", [0, 501])
def test_ac5_a_silly_backlog_size_is_refused(limit: int) -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as refused:
        app_module.evaluation_backlog(limit=limit)
    assert refused.value.status_code == 422
