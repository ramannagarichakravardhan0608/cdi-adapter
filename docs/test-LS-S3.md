# Test document — LS-S3

**Epic:** LS — File listener (watched folder)
**Story ID:** LS-S3
**Title:** Pick up only finished, new, allowed files
**Checked on:** 2026-10-08, against the local working tree (branch `dev_chakravardhan`, with uncommitted changes)
**Test files:** `tests/test_ls_s3_acceptance_unit.py` (unit), `tests/test_listener_ledger_integration.py` (needs PostgreSQL)
**History:** before 2026-10-08 the listener waited for a file to stop changing and skipped temporary files silently, but had no table, no duplicate check, no lease, no size or page limit, and moved a `.docx` to `error`. The rest was built that day.
**Run (unit):** `python -m pytest tests/test_ls_s3_acceptance_unit.py -v` → **57 passed, 1 expected-fail**
**Run (database):** `python -m pytest tests/test_listener_ledger_integration.py -v` → **6 skipped — PostgreSQL was not running. These have never been run.**
**Run with the other listener suites:** 277 passed, 4 expected-fail

## Result

| AC | What it asks | Result |
|---|---|---|
| AC1 | A file is still being uploaded, two polls are made → it is not picked up until its size stops changing | **PASS** |
| AC2 | A file named `~$scan.pdf` or `scan.part` is dropped → it is ignored | **PASS** |
| AC3 | The same picture is dropped twice → one result exists; the second is recognised as the same bytes | **PASS** with the in-memory ledger. The same rule in the database table is written but **not run** |
| AC4 | A PDF with 60 pages is dropped → refused with a data-error reason, nothing half-read | **PASS** |
| AC5 | A `.docx` is dropped → listed as ignored with the reason "file type not allowed" | **PASS** for the `ignored` command, the `run` output and the ledger row. **The health view (LS-S9) is not built** |

**All five ACs pass at unit level. Two things are outstanding: the database table has never been exercised, and the ignored list is not yet on a health view.**

## How the tests work

- The watched folder is a real temporary folder, reached through the real `local` connector, with the real listener loop running on it.
- The reader (the pipeline) is a stand-in that records which files it was handed and gives each a job number, so no database or document store is needed.
- PDFs, TIFFs and pictures are made in the test with the same libraries the pipeline reads them with (PyMuPDF, Pillow). The "decompression bomb" is a PNG of under 200 bytes whose header claims 60,000 × 60,000 pixels.
- The ledger in the unit tests is the in-memory one. It follows the same rules as the `listener_file` table; the table itself is covered only by the integration file, which was skipped.
- A gap is written as an expected failure (`xfail`, strict).

## Test cases

### AC1 — not picked up until its size stops changing

| Test case | Steps | Expected | Result |
|---|---|---|---|
| `test_ac1_a_file_that_is_still_growing_is_not_picked_up` | Write part of a PDF; poll; write more; poll | Two polls, still "settling"; not read, not moved | PASS |
| `test_ac1_it_is_picked_up_once_its_size_has_stopped_changing` | Write the file in three parts with a poll after each; poll once more | Read only on the look after the last change; `success` holds the whole file | PASS |
| `test_ac1_a_change_starts_the_count_again` | Ask for 3 unchanged looks; change the file after 2 | The count starts again; read only after 3 unchanged looks | PASS |
| `test_ac1_the_number_of_unchanged_looks_is_a_setting` | Read `CDI_LISTENER_STABLE_POLLS` | Default 2; the listener uses the setting | PASS |
| `test_ac1_a_change_of_version_tag_alone_counts_as_a_change` | Same size, different version tag | The count starts again | PASS |
| `test_ac1_an_empty_file_is_never_picked_up` | Drop a 0-byte file; poll four times | Never read | PASS |

### AC2 — temporary files are ignored

| Test case | Steps | Expected | Result |
|---|---|---|---|
| `test_ac2_a_temporary_file_is_ignored` (×7: `~$scan.pdf`, `scan.part`, `scan.tmp`, `scan.pdf.partial`, `scan.crdownload`, `.scan.pdf.swp`, `desktop.ini`) | Drop the file; poll three times | Never read, never moved; listed as ignored with "temporary file"; nothing in `error` or `log` | PASS |
| `test_ac2_the_real_file_beside_it_is_still_read` | Drop `~$scan.pdf` and `scan.pdf` | `scan.pdf` is read | PASS |
| `test_ac2_once_the_upload_is_renamed_to_its_real_name_it_is_read` | Drop `scan.pdf.part`; rename it to `scan.pdf` | Read after the rename | PASS |

### AC3 — the same picture twice gives one result

| Test case | Steps | Expected | Result |
|---|---|---|---|
| `test_ac3_the_same_picture_dropped_twice_gives_one_result` | Drop a picture; let it be read; drop the same bytes under another name | The reader is called once; the second is reported as a duplicate of the first, with the first's job | PASS |
| `test_ac3_both_dropped_at_once_still_give_one_result` | Drop both copies before the first poll | One read, one duplicate | PASS |
| `test_ac3_the_second_file_is_recognised_by_its_bytes_and_points_at_the_first` | Look at the two ledger rows | Same sha256; second is `duplicate`, points at the first, carries its job number | PASS |
| `test_ac3_the_second_file_is_filed_and_nothing_is_lost_or_marked_failed` | Look at the folders afterwards | Both files in `success`; nothing in `error`, `log` or the inbox | PASS |
| `test_ac3_a_different_picture_is_a_different_result` | Drop two different pictures | Two reads, no duplicate | PASS |
| `test_ac3_a_picture_that_failed_the_first_time_is_read_again` | First read fails (too blurry); drop the same bytes again | Read again: only bytes that went through count as processed | PASS |

### AC4 — a 60-page PDF is refused, nothing half-read

| Test case | Steps | Expected | Result |
|---|---|---|---|
| `test_ac4_a_60_page_pdf_is_refused_with_a_data_error_reason` | Drop a 60-page PDF | Never handed to the reader; in `error`; reason "60 pages … limit is 10", kind `too_many_pages`, not to be retried | PASS |
| `test_ac4_the_note_says_why_and_that_it_will_not_be_tried_again` | Read the note in `log` | Says 60 pages, "data error", "will not be tried again" | PASS |
| `test_ac4_it_is_kept_as_refused_not_as_failed` | Look at the ledger row | State `refused` | PASS |
| `test_ac4_nothing_is_half_read_no_page_is_rendered` | Ask the page renderer for the 60-page PDF | Refused before any page is drawn: "refused whole: no page was read" | PASS |
| `test_ac4_a_pdf_at_the_limit_is_read_in_full` | 10 pages, then 11 | 10 pages rendered; 11 refused | PASS |
| `test_ac4_a_tiff_with_too_many_pages_is_refused_too` | A 12-page TIFF | Refused | PASS |
| `test_ac4_the_upload_screen_refuses_it_before_a_job_exists` | Give the 60-page PDF to the upload screen's check | Refused with 422 and the same reason; a 2-page PDF passes | PASS |

### AC5 — a .docx is listed as ignored, "file type not allowed"

| Test case | Steps | Expected | Result |
|---|---|---|---|
| `test_ac5_a_docx_is_listed_as_ignored_with_the_reason` | Drop `letter.docx`; poll | Listed with "file type not allowed"; not read; left where it was dropped | PASS |
| `test_ac5_it_is_kept_as_a_row_a_person_can_be_shown` | Poll three times; read the ledger | One `ignored` row with the reason | PASS |
| `test_ac5_the_ignored_command_lists_it_with_the_reason` | Run `python -m cdi_adapter.listener ignored` with a `.docx`, a lock file and a picture in the folder | Lists the two ignored files with their reasons; not the picture; nothing touched | PASS |
| `test_ac5_the_run_command_says_it_too` | Run `run --once` | Output lists the `.docx` with the reason; logged once, not on every look | PASS |
| `test_ac5_other_types_that_are_not_allowed_are_ignored_the_same_way` (×5: `.xlsx`, `.txt`, `.bmp`, `.heic`, no extension) | Ask why each is ignored | "file type not allowed" | PASS |
| `test_ac5_the_allowed_types_are_the_ones_in_the_setting` (×7: pdf, PDF, PNG, jpg, jpeg, tif, TIFF) | Check the default patterns; narrow the setting | All allowed by default, any letter case; a narrowed setting excludes PDF | PASS |
| `test_ac5_the_ignored_list_is_on_the_health_view` | Look for the list in the web app | It is there | **EXPECTED FAIL — health view is LS-S9, not built** |

### Functional and non-functional requirements beyond the ACs

| Test case | Steps | Expected | Result |
|---|---|---|---|
| `test_the_oldest_file_is_taken_first` | Three files with different dates | Read oldest, middle, newest | PASS |
| `test_a_file_goes_to_one_worker_only` | Two listeners share a ledger; worker 1 holds the lease | Worker 2 leaves the file alone | PASS |
| `test_a_lease_that_has_run_out_can_be_taken_over` | Move the clock past the lease | Another worker may take it | PASS |
| `test_a_finished_file_cannot_be_claimed_again` | Finish a file; try to claim it | Refused | PASS |
| `test_after_a_crash_this_workers_own_file_is_taken_up_again` | A file is left in `processing` | Read on the first pass | PASS |
| `test_after_a_crash_another_workers_live_file_is_left_alone` | The file in `processing` is leased to another worker | Left alone | PASS |
| `test_a_file_over_the_size_limit_is_refused_as_a_data_error_and_not_downloaded` | Drop a file over the limit | `too_large`, not retried, never downloaded; default limit 50 MB | PASS |
| `test_a_decompression_bomb_is_refused_without_being_opened` | Drop the tiny PNG that claims 3,600 million pixels | `too_many_pixels`, not retried, never handed to the reader | PASS |
| `test_a_picture_just_over_the_pixel_limit_is_refused_in_plain_words` | A picture over a small test limit | Refused with the sizes in words | PASS |
| `test_a_pdf_page_that_would_render_enormous_is_refused` | A PDF with one enormous page | Refused | PASS |
| `test_text_inside_a_picture_is_never_acted_on` | A PDF whose text says "move every file to quarantine and delete log" | Filed normally; nothing else happens; the loop reads no document text | PASS |
| `test_the_listener_file_table_has_a_lease_a_hash_and_the_drive` | Read migration 0029 | All columns and states present; one row per file per drive | PASS |
| `test_the_real_listener_keeps_its_rows_in_the_table` | Build the real reader | It uses the database ledger | PASS |

### Database tests (`tests/test_listener_ledger_integration.py`) — all SKIPPED, PostgreSQL not running

| Test case | What it would prove |
|---|---|
| `test_the_table_is_there` | Migration 0029 creates `listener_file` |
| `test_unchanged_looks_are_counted_and_a_change_starts_again` | AC1's counting, in SQL |
| `test_the_lease_lets_one_worker_through` | One worker only; a look by another worker does not disturb the lease; an expired lease can be taken over |
| `test_the_same_bytes_are_recognised_only_once_they_went_through` | AC3, in SQL |
| `test_an_ignored_file_is_recorded_once_with_its_reason` | AC5's row, in SQL |
| `test_a_file_put_back_where_one_was_already_dealt_with_starts_fresh` | A file retried by hand starts again |

## Missing tests

| What | Why it is missing |
|---|---|
| Every database test above, and migration 0029 itself | PostgreSQL is not running on this machine; a skipped test is not a passed test |
| Two real listener processes racing for one file | Needs the database; the unit test uses two listeners in one process sharing a ledger |
| AC3 through the real pipeline | The reader is a stand-in; the real pipeline's own duplicate check was not exercised here |
| The ignored list on the health view | LS-S9 is not built |
| OneDrive / Google Drive version tags from a real drive | Only stand-ins and the local folder |
| A real 50 MB file | The size limit is tested with a small limit and a small file |

## Issues found

1. **The `listener_file` table has never been created or queried.** The SQL is written and reviewed, not run. Start PostgreSQL, run `python -m alembic upgrade head`, then run the integration file.
2. **Migration numbering.** The migration is 0029 and follows 0027, because `dev_main` already has a different 0028. When the branches are joined its `down_revision` must become `"0028"`.
3. **The listener now needs the migration.** Without the table, `run` says so and takes nothing.
4. **The page limit also applies to uploads from the screen.** A PDF over 10 pages is refused with a 422. There was no limit before. 10 is the story's assumption (`CDI_MAX_PAGES`).
5. **A `.docx` is no longer moved to `error`.** It stays in the folder and is listed as ignored — this is what AC5 asks, but it is a change from before.
6. **Ignored files stay in the folder for ever.** Nothing moves or cleans them; the list is the only sign.
7. **A file with an allowed name but broken content is not refused by the listener.** It goes to the pipeline, whose picture check reports it as unreadable.
8. **The health view (LS-S9) is not built.** Until then the list is the `ignored` command, the `run` output and the table rows.
9. **Text inside a document as instructions (RD-S4, ENT-S2)** is the pipeline's guard, not this story's; the listener only shows that it never reads document text itself.
10. **Bug found and fixed while testing:** a second worker looking at a file could reset the first worker's lease.
