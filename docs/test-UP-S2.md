# Test document — UP-S2

**Story ID:** UP-S2
**Title:** Tell the person at once if a picture is not good enough, and let them retake just that page
**Last updated:** 2026-10-08 (after AC5 was built)
**Checked on:** 2026-10-08, against the local working tree (branch `dev_chakravardhan`, with uncommitted changes)
**Test file:** `tests/test_up_s2_acceptance_unit.py`
**History:** the first check changed no application code and found AC5 unbuilt. It was built the same day (`webapp/quality_report.py`, a hook in `webapp/jobs.py`, a route in `webapp/app.py`, `scripts/quality_report.py`) and this document updated.
**Run:** `python -m pytest tests/test_up_s2_acceptance_unit.py -v` → **38 passed** (first run, before AC5 was built: 22 passed, 3 expected-fail)
Run together with the story's existing suites and their neighbours (upload quality, recognition, auth, jobs, result, queueing): 317 passed.

## Result

| AC | What it asks | Result |
|---|---|---|
| AC1 | A blurry page → within 5 s the screen says "too blurry, please retake" for that page | **PASS** — the check is on by default since 2026-10-08 (IM-S1 AC2) |
| AC2 | Retake that page → only that page is re-checked; the others stay as they were | **PASS** |
| AC3 | All pages good → no warning, and the result appears | **PASS** |
| AC4 | A held page → no values are saved from it | **PASS** |
| AC5 | Weekly report shows the retake rate and the top reasons | **PASS** (was FAIL; built 2026-10-08) — not yet run on a real database |

**5 of 5 pass** at the level tested here. The picture check is on by default since 2026-10-08; with `CDI_QUALITY_CHECK=false` nothing is held and AC5 has nothing to count.

## How the tests work

The real picture check and the real job runner are used, with the check switched on, on pictures built in the test (a rendered prescription and a blurred copy). The six pipeline stages are recorders, so the tests prove *which pages reach which stage*. A retake is made the way the retake route makes it (a fresh page in the same place, the job reopened); the database half of that route is not run here.

## Test cases

### AC1 — a blurry page is called out, for that page, within 5 s

| Test case | Steps | Expected | Result |
|---|---|---|---|
| `test_ac1_a_blurry_page_is_held_and_named_blurred` | Send a sharp page and a blurred page in one job | Page 2 is held with reason `blurred`; page 1 finishes with no warning | PASS |
| `test_ac1_the_person_is_told_it_is_too_blurry_and_to_take_it_again` | Read what the held page says | The message contains "too blurry" and "take it again"; the row shows it with a "Retake this page" button | PASS |
| `test_ac1_the_message_is_on_that_pages_row` | Read the progress script | The note and the retake button are drawn on the held page's own row | PASS |
| `test_ac1_the_check_itself_takes_well_under_five_seconds` | Time the check on a blurred 1920 × 1080 frame | Verdict `blurred` in under 5 s | PASS |
| `test_ac1_a_held_job_comes_back_within_five_seconds_of_being_sent` | Time a whole job of one blurred page | Held in under 5 s | PASS |
| `test_ac1_the_screen_asks_again_every_second_and_a_half` | Read the polling line | The screen re-asks every 1.5 s | PASS |
| `test_ac1_the_check_comes_before_any_reading` | Read the runner | The check runs before ingest | PASS |
| `test_ac1_the_check_runs_by_default` | Send a blurred page with default settings | Held as blurred | PASS |
| `test_ac1_it_can_be_switched_off_and_then_nothing_is_held` | Send it with `CDI_QUALITY_CHECK=false` | Not held; nothing said | PASS |

**Issues for AC1**
- **On by default since 2026-10-08.** It used to be off; the limits behind it are still starting values, so it may hold pictures it misjudges.
- **Wording.** The AC quotes "too blurry, please retake". The screen says "This photo is too blurry to read. Hold the phone still, let it focus, and take it again." with a "Retake this page" button. Same meaning, different words; the wording is still marked draft.
- **The 5 s is measured without queueing.** The check takes a fraction of a second; on a busy server, time waiting for a worker comes on top and is not measured.

### AC2 — retake re-checks only that page

| Test case | Steps | Expected | Result |
|---|---|---|---|
| `test_ac2_only_the_retaken_page_is_read_again` | Job of a good page and a blurred page; retake page 2 with a good picture | Only the new page 2 runs (all six stages); page 1's record is byte-for-byte unchanged; job ends in review | PASS |
| `test_ac2_a_retake_that_is_still_blurry_is_held_again_and_only_it` | Retake page 2 with another blurred picture | Held again; no stage runs for any page | PASS |
| `test_ac2_the_runner_starts_over_only_pages_that_are_not_finished` | Ask the runner what to redo for [done, held, fresh] | Only the fresh page | PASS |
| `test_ac2_the_retake_rewrites_that_one_page_and_nothing_else` | Read the retake function | It rewrites one file slot and one document, by that page's index | PASS |
| `test_ac2_the_screen_sends_the_retake_for_that_page_only` | Read the retake handler | It posts to `/api/jobs/<job>/pages/<n>/replace` | PASS |
| `test_ac2_a_page_that_is_not_in_the_job_cannot_be_retaken` | Retake page 4 of a 1-page job; retake with an empty file | Both refused | PASS |

**Limit:** the SQL inside the retake route (and its refusal while a job is still running) needs PostgreSQL and was not run today.

### AC3 — good pages: no warning, result appears

| Test case | Steps | Expected | Result |
|---|---|---|---|
| `test_ac3_good_pages_are_not_held_and_carry_no_warning` | Send two sharp pages with the check on | Both finish all six stages; no verdict, no error | PASS |
| `test_ac3_the_api_carries_no_verdict_for_a_good_page` | Read the job's API form for a good page | No quality entry | PASS |
| `test_ac3_the_warning_is_drawn_only_for_a_held_page_and_the_result_is_offered` | Read the progress script | The note is drawn only when held; a result link is offered when the page is read | PASS |

### AC4 — nothing is saved from a held page

| Test case | Steps | Expected | Result |
|---|---|---|---|
| `test_ac4_a_held_page_reaches_no_stage_at_all` | Good page + blurred page | The blurred page reaches no stage (not ingested, not read, not extracted); the job still finishes | PASS |
| `test_ac4_a_held_page_has_no_document_and_no_facts` | Look at the held page's record | No document id, no steps, 0 facts | PASS |
| `test_ac4_a_job_of_only_held_pages_saves_nothing` | Two blurred pages | No stage is called at all | PASS |
| `test_ac4_the_upload_result_shows_the_held_page_as_held_with_no_values` | Build the upload's one result for a held page | Listed as held with its reason; no tests, no diagnoses | PASS |

**Why it holds:** the page is stopped before ingest, so no document row, no OCR and no facts can exist for it.

### AC5 — weekly report with retake rate and top reasons

| Test case | Steps | Expected | Result |
|---|---|---|---|
| `test_ac5_what_a_report_would_be_built_from_is_recorded_today` | Hold a tiny picture; read what is kept | Reason `too_small` and the measurements are kept; a retake writes an audit row | PASS |
| `test_ac5_there_is_a_report_that_gives_the_retake_rate` | Search the code for a retake rate | Something computes it | PASS |
| `test_ac5_the_report_counts_the_top_reasons_pages_were_held` | Search for a count of top reasons | Something computes it | PASS |
| `test_ac5_the_report_can_be_read_through_a_route_or_a_command` | Look for a report route or script | One exists | PASS |
| `test_ac5_many_pages_held_for_too_small_show_as_the_top_reason_and_a_rate` | 180 pages, 20 retakes, held: 30 too small, 9 blurred, 3 too dark | 200 pictures sent, 42 held, retake rate 21 %, "too small" first with its share | PASS |
| `test_ac5_a_week_with_nothing_held_and_a_week_with_nothing_sent` | No holds; then no uploads at all | Rate 0 with no reasons; then no rate at all | PASS |
| `test_ac5_equal_reasons_are_listed_in_a_fixed_order` | Two reasons with the same count | Listed alphabetically | PASS |
| `test_ac5_the_report_reads_as_plain_text` | Print the report | "retake rate 21.0 %", "Top reasons", most common first | PASS |
| `test_ac5_the_report_counts_the_last_seven_days_from_the_audit_log` | Run the report on a stand-in database | Three queries (pages, holds by reason, retakes), all over the same 7 days | PASS |
| `test_ac5_each_hold_is_written_down_when_it_happens` | Run a job with a good and a blurred page on a stored-job runner | One audit row: page 2, reason blurred, nothing else | PASS |
| `test_ac5_a_report_row_that_cannot_be_written_never_costs_the_hold` | Make the database unavailable while a page is held | The page is still held and the person still told | PASS |
| `test_ac5_the_report_carries_counts_only` | Read what is recorded and returned | Page number and reason only; no file name, patient or measurement | PASS |
| `test_ac5_the_report_has_a_route_and_a_command_and_a_week_is_the_default` | Read the route list, the access policy and the script | `GET /api/reports/quality` (reviewer and admin), 7 days by default, `scripts/quality_report.py` | PASS |
| `test_ac5_a_silly_number_of_days_is_refused` (3 cases) | Ask for 0, -1 and 367 days | Refused | PASS |

**How it works.** A held page is never ingested and a retake overwrites its place in the job, so nothing remembered a hold. Now the runner writes one row to the append-only audit log each time it holds a page (page number and reason). The report counts those rows, the retake rows that were already written, and the pages in `web_job`, over the last 7 days.

**How to read it**

```
python scripts/quality_report.py              # last 7 days, as text
python scripts/quality_report.py --days 30 --json
GET /api/reports/quality?days=7               # the same numbers; reviewer and admin
```

```
Picture quality, last 7 day(s)   2026-10-01 to 2026-10-08

  uploads            90
  pictures sent      200
  held for a retake  42
  retake rate        21.0 %
  retaken            20

  Top reasons
    too small             30    71.4 %
    blurred                9    21.4 %
    too dark               3     7.1 %
```
(Example figures from the test, not real data.)

**What the numbers mean.** *Pictures sent* is every page of every upload plus every retake. *Retake rate* is the share of pictures sent that were held with "please take it again". *Retaken* is how many held pages the person did replace.

## Missing tests and issues

1. **AC5 has not run against a real database.** The arithmetic and the hook are unit-tested; the three SQL queries were only run against a stand-in. `tests/test_quality_report_integration.py` proves them on PostgreSQL but was not run (no local database).
2. **Holds are counted from the day this is deployed.** A page held earlier left no audit row and is not in the report.
3. **There is no screen for the report.** It is read through the command or the API; nothing on the website shows it, and nothing sends it weekly by itself.
4. **The picture check is on by default with untuned limits** (`CDI_QUALITY_CHECK`; starting values, not tuned on real photos). With it switched off nothing is held and the report's retake rate is 0.
5. **AC1's wording differs from the AC's quoted text**, and the plain-language wording has no owner's approval yet.
6. **The 5 s was measured on this PC without queue wait**, not on the server under load.
7. **The retake route's SQL and its "job still running" refusal** need PostgreSQL and are untested here.
8. **Nothing was run on a real phone**, and the screen was checked as text, not by clicking through it.
9. **Not deployed.** The test instance was unreachable on 2026-10-08.

## Summary

- **Passed:** AC1, AC2, AC3, AC4 and AC5 — 38 of 38 acceptance tests.
- **Failed:** none now. AC5 failed on the first check (no report existed) and passes since it was built.
- **Changed since the first check:** the picture check is now on by default, so AC1 no longer depends on a switch.
- **Missing:** a run on a real database (AC2's retake SQL, AC5's report queries), a run on a real phone, a screen or weekly delivery for the report, tuned limits, approved wording, and deployment.

