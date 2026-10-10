"""The Reviewer page shows one entry per patient (owner, 2026-10-09).

A review item is one document, and stays one: each prescription is approved into its own FHIR
record. What changed is how the lists are drawn - one patient with two prescriptions waiting is
one entry in the workbasket and the worklist, with both prescriptions under it.

The page is vanilla JavaScript in a Python string, so these tests check what the script
contains and that the server side is untouched. The behaviour itself - grouping, claiming both,
the tabs, moving on to the next prescription after an approval - was checked in a browser
against made-up data (``.tools/preview/make_reviewer_group_preview.py``), not here.
"""

from __future__ import annotations

from pathlib import Path

from cdi_adapter.webapp.reviewer_page import SCRIPT, reviewer_page

ROOT = Path(__file__).resolve().parents[1]


def test_the_lists_are_grouped_by_patient() -> None:
    assert "function byPatient(items)" in SCRIPT
    assert "i.patient_id || 'item:' + i.id" in SCRIPT  # an item with no patient stands alone
    for drawn in ("byPatient(await (await fetch('/api/reviewer/workbasket')).json())",
                  "byPatient(await (await fetch('/api/reviewer/worklist')).json())"):  # fmt: skip
        assert drawn in SCRIPT


def test_a_patient_with_one_document_is_drawn_as_before() -> None:
    assert "g.length === 1 ? row(g[0], btn)" in SCRIPT


def test_a_patient_with_several_documents_lists_each_of_them() -> None:
    assert "data-patient-entry" in SCRIPT and "documents</span>" in SCRIPT
    assert "g.map(i => `<li><small>${docLine(i)}</small></li>`)" in SCRIPT


def test_claiming_the_entry_claims_every_document_of_the_patient() -> None:
    assert "async function claimGroup(list)" in SCRIPT
    assert "claimGroup('${ids(g)}')" in SCRIPT
    assert "post(`/api/reviewer/items/${id}/claim`, {})" in SCRIPT  # the same call as before


def test_opening_the_entry_shows_the_documents_as_tabs() -> None:
    assert "function openGroup(list)" in SCRIPT and "openGroup('${ids(g)}')" in SCRIPT
    assert "role=tablist" in SCRIPT and "group.length > 1 && group.includes(id)" in SCRIPT
    assert "${groupTabs(id)}<div class=rv-split>" in SCRIPT


def test_after_a_decision_the_next_document_of_the_patient_is_offered() -> None:
    assert "function leaveGroup(id)" in SCRIPT and "Next for this patient" in SCRIPT
    assert SCRIPT.count("leaveGroup(id)") >= 3  # defined, after an approval, after a rejection


def test_each_document_is_still_approved_on_its_own() -> None:
    """One FHIR record per prescription: there is no "approve all"."""
    assert "post(`/api/reviewer/items/${id}/approve`, {})" in SCRIPT
    assert "approveGroup" not in SCRIPT and "approveAll" not in SCRIPT


def test_the_server_side_is_untouched() -> None:
    """No new route, no change to what the two list routes return."""
    api = (ROOT / "src" / "cdi_adapter" / "webapp" / "reviewer.py").read_text(encoding="utf-8")
    assert 'return canon.list_items(c, status="workbasket")' in api
    assert 'return canon.list_items(c, status="claimed", assignee=me)' in api
    assert "patients" not in api
    repo = (ROOT / "src" / "cdi_adapter" / "canon" / "repo.py").read_text(encoding="utf-8")
    assert "one per validated document" in repo and "SELECT i.*" in repo  # patient_id is in the row


def test_the_page_still_renders_and_says_what_an_entry_is() -> None:
    html = reviewer_page()
    assert "A patient with several prescriptions is one entry" in html
    assert "Workbasket" in html and "My worklist" in html and "Approved" in html
