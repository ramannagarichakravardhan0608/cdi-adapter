# Test document — UP-S3

**Story ID:** UP-S3
**Title:** Show the result on the screen and let the person download the JSON
**Last updated:** 2026-10-08 (after AC1's preparation card, AC4 and AC5 were built)
**Checked on:** 2026-10-08, against the local working tree (branch `dev_chakravardhan`, with uncommitted changes)
**Test file:** `tests/test_up_s3_acceptance_unit.py`
**History:** the first check changed no application code and found AC4, AC5 and AC1's preparation card unbuilt (25 passed, 9 expected-fail). All three were built the same day and this document updated.
**Run:** `python -m pytest tests/test_up_s3_acceptance_unit.py -v` → **67 passed**
Run together with the neighbouring suites (UP-S1 and UP-S2 acceptance, result, auth, upload page, jobs, queueing, ingress): 423 passed.

## Result

| AC | What it asks | Result |
|---|---|---|
| AC1 | Patient, doctor, lab tests and preparation cards, with a status tag on each value | **PASS** (was partial; preparation card built 2026-10-08) |
| AC2 | A value that needs a check carries a "needs a check" tag and the reason | **PASS** |
| AC3 | "Download JSON" gives a file equal to the stored JSON for that document | **PASS** (see the note: no JSON file is stored) |
| AC4 | Clicking a value opens the evidence with the picture region highlighted | **PASS** (was FAIL; built 2026-10-08) |
| AC5 | "Report a wrong result" saves a report with the document id into the evaluation backlog | **PASS** (was FAIL; built 2026-10-08) — not yet run on a real database |

**5 of 5 pass** at the level tested here (unit tests, plus a browser check of the screen with stand-in data). See "Missing tests and issues" for what that level does not cover.

## How the tests work

The result screen's script is checked as text, the result builder is called for real, and the routes are called directly with the database replaced by a stand-in. In addition, on 2026-10-08 the screen was opened in a browser with a stand-in result and a test page picture, and clicked through: the cards, a clickable value, the highlight's position, and sending a report.

## Test cases

### AC1 — patient, doctor, lab tests and preparation cards, each value tagged

| Test case | Steps | Expected | Result |
|---|---|---|---|
| `test_ac1_a_finished_page_links_to_its_result` | Read the upload screen and the route list | A "see result" link once the page is read; `/result/<document>` exists | PASS |
| `test_ac1_the_patient_doctor_and_lab_test_cards_are_drawn` | Read the result screen | Patient, Doctor, Lab tests, Preparation and Status are drawn, in that order | PASS |
| `test_ac1_there_is_only_one_way_to_draw_a_value_and_it_prints_the_status_tag` | Read the value-drawing helper | One helper draws every value, always with its tag | PASS |
| `test_ac1_each_patient_doctor_and_test_value_goes_through_the_tag` | Read each card | Name, sex, date of birth, doctor's name and registration, each test and its result go through the helper | PASS |
| `test_ac1_every_value_the_builder_makes_has_a_status` | Build values of each kind | Each has `accepted` or `needs_check` | PASS |
| `test_ac1_there_is_a_preparation_card_of_its_own` | Look for a Preparation card | It exists; it is no longer a list under Lab tests | PASS |
| `test_ac1_every_preparation_instruction_is_drawn_with_its_status_tag` | Read the Preparation card | Each instruction goes through the tag helper; "No preparation instruction…" when there is none | PASS |
| `test_ac1_the_result_carries_preparation_items` | Build preparation from four facts (two instructions, one diet advice, one medicine) | Two items, "fasting" and "timing"; each a value with status and evidence | PASS |
| `test_ac1_what_counts_as_a_preparation_instruction` (14 cases) | Classify phrases | Fasting, empty stomach, post-prandial, early morning, 24-hour, midstream → preparation; diet, exercise, "take after food", "review after 2 weeks" → not | PASS |
| `test_ac1_a_held_instruction_shows_as_needing_a_check_and_is_counted` | Build a held instruction | `needs_check` with a reason; counted under "needs a person" | PASS |
| `test_ac1_which_test_an_instruction_belongs_to_is_still_not_worked_out` | Read the "absent" list | `lab_tests[].preparation_refs` is still listed as not extracted | PASS |

**How the preparation card works.** No extractor produces "preparation" as its own kind of fact, so it is recognised among the advice the prescription already gives: a piece of advice (or care plan) is a preparation instruction when it mentions fasting / empty stomach, a post-prandial sample, the timing of a sample (early morning, 24-hour) or how to collect it (midstream). Each instruction is shown with its status tag and its kind.
**Browser check:** the card showed "Do all tests fasting — accepted 0.97 — fasting".

### AC2 — a value that needs a check shows the tag and the reason

| Test case | Steps | Expected | Result |
|---|---|---|---|
| `test_ac2_a_value_the_gate_did_not_accept_needs_a_check_and_says_why` | Build a held value | `needs_check`, reason "not governed by the gate" | PASS |
| `test_ac2_a_governed_value_without_proof_needs_a_check_and_says_why` | Build an accepted value with no evidence | `needs_check`, reason "no evidence recorded" | PASS |
| `test_ac2_a_needs_check_value_can_never_be_built_without_a_reason` | Build every combination of state and evidence | Each needs-check value has a reason | PASS |
| `test_ac2_an_accepted_value_carries_no_reason` | Build an accepted value | No reason | PASS |
| `test_ac2_the_screen_shows_the_tag_in_warning_colour_and_the_reason_beside_it` | Read the value-drawing helper | Warning-coloured tag, with the reason beside it | PASS |
| `test_ac2_the_tag_reads_needs_check` | Read how the tag text is made | The tag reads "needs check" | PASS |
| `test_ac2_held_values_are_also_listed_once_with_their_reasons` | Build the review summary; read the Status card | The held value and its reason are listed under "Needs a person" | PASS |

**Browser check:** a held test showed "HbA1c — needs check — 0.41 — not governed by the gate".
**Wording:** the AC quotes "needs a check"; the tag reads "needs check". The reasons are technical ("not governed by the gate", "no evidence recorded").

### AC3 — the downloaded file equals the document's JSON

| Test case | Steps | Expected | Result |
|---|---|---|---|
| `test_ac3_the_button_is_there_and_downloads_the_result_document` | Read the screen and the route list | A "Download JSON" button that goes to the download route | PASS |
| `test_ac3_the_downloaded_file_is_byte_for_byte_the_documents_json` | Call the result route and the download route for one document | Identical bytes; both asked for that document only | PASS |
| `test_ac3_the_download_is_a_named_json_file` | Read the download's headers | `application/json`, saved as `result-<document id>.json` | PASS |
| `test_ac3_the_screen_and_the_file_come_from_the_same_place` | Read both routes | The download returns the screen's own document | PASS |
| `test_ac3_the_same_document_gives_the_same_bytes_every_time` | Download twice | Identical | PASS |
| `test_ac3_the_json_is_written_in_a_fixed_order` | Read the JSON writer | Keys are sorted | PASS |
| `test_ac3_a_document_that_does_not_exist_downloads_nothing` | Download an unknown document | 404 | PASS |
| `test_ac3_the_json_is_built_when_asked_for_not_kept_as_a_file` | Read the builder | It writes nothing to storage | PASS (records the note below) |

**Note on "the stored JSON":** no JSON file is stored. The document is built from the database each time it is asked for, in a fixed order, so the download equals what the screen shows and equals any later download while the values are unchanged. If a reviewer corrects a value, the next download will differ.

### AC4 — clicking a value highlights where it came from

| Test case | Steps | Expected | Result |
|---|---|---|---|
| `test_ac4_every_value_carries_where_on_the_page_it_came_from` | Build a value with evidence | It carries the page number and the box | PASS |
| `test_ac4_the_page_picture_can_be_fetched` | Read the route list | A route serves the page picture | PASS |
| `test_ac4_a_value_on_the_result_screen_can_be_clicked` | Read the value-drawing helper and the click handler | A placed value is a button carrying its document, page and box; a click opens the evidence | PASS |
| `test_ac4_a_value_with_no_place_on_the_page_stays_plain_text` | Read the helper | A value with no page or box is not made clickable | PASS |
| `test_ac4_the_status_tag_is_still_printed_beside_a_clickable_value` | Read the helper | The tag is drawn after the button | PASS |
| `test_ac4_the_evidence_view_shows_the_page_picture` | Read the evidence view | It loads `/api/documents/<doc>/pages/<n>` and says which page | PASS |
| `test_ac4_the_region_the_value_came_from_is_highlighted` | Read the highlight code | The box is scaled to the drawn picture and laid over it once the picture has loaded | PASS |
| `test_ac4_a_picture_that_cannot_be_loaded_is_said_so_and_the_view_can_be_closed` | Read the error and close handling | A plain message if the picture is missing; a Close button | PASS |
| `test_ac4_each_value_knows_which_document_its_picture_is_in` | Read the tagging code | Every value is told its document, per item for a multi-page upload | PASS |
| `test_ac4_an_uploads_result_says_which_document_each_item_came_from` | Build an upload's result from two pages | Each test and instruction names its own document and page | PASS |

**How it works.** A value that has a place on the page is drawn as a link-style button. Pressing it opens a panel above the cards with that page's picture and a red outline round the region the value was read from. Values with no recorded place (the patient's identity, a date worked out from the visit) stay plain text.
**Browser check:** with a 1000 × 1400 test picture and a box at (200, 600)–(700, 660), the outline landed at left 106, top 317, 264 × 32 on the drawn picture; the expected position was 106, 318, 265 × 32 (within 1 pixel). The patient's name, which has no box, was not clickable. Close hid the panel.

### AC5 — "Report a wrong result"

| Test case | Steps | Expected | Result |
|---|---|---|---|
| `test_ac5_there_is_a_report_a_wrong_result_button` | Read the screen | The button, a panel and one text box for the note | PASS |
| `test_ac5_there_is_a_route_that_takes_the_report_for_a_document` | Read the route list and the send code | `POST /api/documents/<id>/result/report` | PASS |
| `test_ac5_the_report_is_saved_with_the_document_id` | Send a report | One row saved: the document id, who reported, the note; a report number comes back | PASS |
| `test_ac5_the_report_is_kept_in_the_append_only_audit_log` | Read where it is written | The audit log, under `result_report` | PASS |
| `test_ac5_a_report_about_a_document_that_does_not_exist_is_refused` | Report on an unknown document | 404; nothing saved | PASS |
| `test_ac5_a_report_that_says_nothing_is_refused` (4 cases) | Send blank or one- or two-letter notes | Refused; nothing saved | PASS |
| `test_ac5_a_report_cannot_be_endless` | Send a 1001-character note | Refused; the box stops at 1000 | PASS |
| `test_ac5_the_note_is_tidied_and_an_empty_value_is_not_kept` | Send a note with stray spaces and line breaks | Saved as one clean line | PASS |
| `test_ac5_reporting_changes_nothing_in_the_result` | Read the screen and the route | No value becomes editable; the route writes one audit row only | PASS |
| `test_ac5_the_screen_says_it_was_saved_and_where_to_find_it` | Read the send code | "Saved as report #…" with a link to the backlog | PASS |
| `test_ac5_reports_can_be_listed_as_an_evaluation_backlog` | Call the backlog route on a stand-in database | The report is listed with its number, time, reporter, document, file name and note; newest first | PASS |
| `test_ac5_the_backlog_has_a_page_and_a_link_in_the_header` | Read the routes, the page and the header | `/evaluation` exists, lists reports, each links to its result; "Evaluation" is in the header | PASS |
| `test_ac5_only_clinical_staff_may_report_or_read_the_backlog` | Read the access policy | Reviewer and admin | PASS |
| `test_ac5_a_silly_backlog_size_is_refused` (2 cases) | Ask for 0 or 501 rows | Refused | PASS |

**How it works.** "Report a wrong result" opens a small panel: the person types what is wrong and sends it. It is saved with the document id, who reported it and when, in the append-only audit log (no new table), and listed at `/evaluation` — the evaluation backlog — newest first, each row linking back to the result. For a multi-page upload the person picks which page is wrong. Reporting changes nothing in the result; corrections are still made in the review console.
**Browser check:** an empty note was refused on the screen with no request sent; a real note was posted to `/api/documents/d1/result/report` and the screen answered "Saved as report #101. It is in the evaluation backlog."

## Missing tests and issues

1. **Not run on a real database.** AC5's save and the backlog listing, and AC3's "identical bytes", were run against stand-ins. The backlog query joins the audit log to documents and has never met PostgreSQL.
2. **The browser check used stand-in data**, not a real processed prescription: a made-up result and a test picture. Nothing was clicked through on the live site.
3. **Preparation is recognised by a short phrase list**, in English, among advice the extractor already produced. An instruction worded differently, or one the extractor did not capture as advice, will not appear. It has not been tried on real prescriptions.
4. **Which test an instruction applies to is not worked out** (`lab_tests[].preparation_refs` is still absent).
5. **A report cannot be closed.** The backlog lists every report ever made; there is no "done" state, no assignment and no link to a fix.
6. **AC3: "stored JSON" is not literally true** — the JSON is built on request, not stored.
7. **AC2's wording:** tag "needs check" vs the AC's "needs a check"; reasons are technical.
8. **Who may open the result or report:** reviewer and admin only. A front-desk (intake) user is refused, although the story is about the person who uploaded.
9. **The highlight is not re-drawn if the window is resized** while the evidence is open; pressing the value again re-draws it.
10. **Not deployed.** The test instance was unreachable on 2026-10-08.

## Summary

- **Passed:** AC1, AC2, AC3, AC4 and AC5 — 67 of 67 acceptance tests.
- **Failed:** none now. On the first check AC4 and AC5 failed and AC1 was partial; all three pass since they were built.
- **Missing:** a run on a real database, a click-through with a real prescription on the live site, a way to close a report, preparation tied to its test, and decisions on "stored JSON", the tag wording and front-desk access.
