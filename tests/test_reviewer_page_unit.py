"""The Reviewer groups an item's facts into clinical sections (owner, 2026-09-26: "the extracted
data should be grouped properly"): the sections, their order and their columns are fixed here."""

from __future__ import annotations

import re

from cdi_adapter.webapp.reviewer_page import reviewer_page


def test_facts_are_grouped_into_clinical_sections_in_reading_order() -> None:
    html = reviewer_page()
    keys = re.findall(r"\['([a-z_]+)', '[^']+'\]", html.split("const SECTIONS = ")[1].split(";")[0])
    # EX-S1 / EX-S2: who the page is about, and who wrote it, come first
    assert keys[:9] == ["patient_detail", "doctor_detail", "condition", "symptom", "finding",
                        "vital_sign", "lab_result", "diagnostic_report",
                        "medication"]  # fmt: skip
    assert keys[-3:] == ["advice", "follow_up", "document"]  # advice last, then "Other"
    medication_columns = ("Strength", "Dose", "Frequency", "Duration", "Route", "Instructions")
    for column in (f"'{c}'" for c in medication_columns):
        assert column in html  # a medication's own columns
    assert "k = known.has(c) ? c : 'document'" in html  # never dropped
    assert "cdn" not in html.lower()


def test_the_uploaded_document_is_shown_beside_its_facts() -> None:
    html = reviewer_page()
    assert "class=rv-split" in html and "class=rv-doc" in html  # facts left, document right
    assert "/api/documents/${encodeURIComponent(viewer.id)}/pages/${viewer.page}" in html
    assert "/api/documents/${esc(viewer.id)}/original" in html  # the file exactly as uploaded
    assert "position:sticky" in html  # stays in view while the facts scroll


def test_medications_show_the_catalogue_identification_beside_the_written_name() -> None:
    html = reviewer_page()
    assert "'Catalogue (Tata 1mg)'" in html and "catalogueText(e.catalogue)" in html
    assert "'UNKNOWN_MEDICATION'" in html  # never a guessed product


def test_medications_show_the_agents_suggestion_which_the_reviewer_applies() -> None:
    """Backlog E12-S4: advisory only. The suggestion pre-fills the Edit form; saving it is the
    reviewer's correction (never applied automatically)."""
    html = reviewer_page()
    assert "'Agent (diagnosis check)'" in html and "agentText(e.agent)" in html
    assert "no matching diagnosis" in html  # the fit check against the prescription's diagnoses
    assert "Use suggestion" in html and "function useAgent" in html
    assert "editFact(fid, item)" in html  # opens the normal Edit form, pre-filled
    assert "/api/reviewer/items/${id}/agent" in html and "Run agent again" in html


def test_a_medicine_has_one_button_and_the_catalogue_choice_is_inside_edit() -> None:
    """Owner, 2026-10-10: Edit and Replace were two buttons; choosing the medicine from the
    catalogue is now part of the Edit form."""
    html = reviewer_page()
    assert ">Replace</button>" not in html  # no second button on the row
    assert "Choose the medicine from the catalogue…</button>" in html  # inside the Edit form
    assert "const pick = e.element_type === 'medication' ?" in html  # only for a medicine
    assert "/api/reviewer/facts/${fid}/replace`" in html  # the same save as before
    assert "if (rp) rp.style.display = 'none'" in html  # closing Edit closes the catalogue too


def test_the_row_has_no_details_link() -> None:
    """Owner, 2026-10-10: "details" only opened the old Review page (no longer in the menu) and
    showed nothing about the line it sat on."""
    html = reviewer_page()
    assert ">details</a>" not in html and 'href="/review"' not in html
