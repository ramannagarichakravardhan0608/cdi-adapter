# Test document — LS-S6

**Epic:** LS — File listener (watched folder)
**Story ID:** LS-S6
**Title:** File each prescription by outcome and keep only the failure reasons in a log folder
**Checked on:** 2026-10-09, against the local working tree (branch `dev_chakravardhan`, with uncommitted changes)
**Test file:** `tests/test_ls_s6_acceptance_unit.py`
**History:** before 2026-10-09 the listener already moved a file to `success` or `error`, wrote a note for a failure and none for a success, and kept both files on a name clash. Built that day: quarantine after the runs are used up, the run number, the fuller note, notes free of anything read from the picture, and every move written to the file's history.
**Run:** `python -m pytest tests/test_ls_s6_acceptance_unit.py -v` → **39 passed, 2 expected-fail**
**Run with the other listener suites:** LS-S1, LS-S3, LS-S4, LS-S5, sign-in, connectors, loop, schema and secret-scan suites all pass

## Result

| AC | What it asks | Result |
|---|---|---|
| AC1 | A file is read successfully, the result is saved → the file is in `success` and no note was written | **PASS** |
| AC2 | A file fails → the file is in `error` and exactly one note in `log` says why | **PASS** |
| AC3 | The pod is stopped after the database save but before the move, the listener restarts → the file is moved to `success` and the result is not saved twice | **PASS with a stand-in for the database.** Not shown on a running pipeline |
| AC4 | A failure note is searched for patient names and values → none are found | **PASS** |
| AC5 | A file with the same name is already in `success`, another is moved there → both are kept, nothing is overwritten | **PASS**, on all four kinds of drive |

**AC1, AC2, AC4 and AC5 pass. AC3 passes as far as a unit test can show it; a real pod has never been stopped between the save and the move.**

## How the tests work

- The watched folder is a real temporary folder, through the real `local` connector, with the real listener loop on it. AC5 is also run against the stand-ins for OneDrive, SharePoint and Google Drive.
- The reader (the pipeline) is a stand-in that answers as the test tells it: saved, held, or crashed.
- For AC3 the "database" is a stand-in that keeps one result per file's bytes — the way the real job store answers a second identical upload with the job it already has. The pod stopping is an error nothing catches, raised right after the save.
- For AC4 the failing reader raises an error whose message quotes a patient's name, age, phone, ABHA number, medicine, doctor and blood pressure; the note is then searched for each of them.
- Files are real one-page PDFs.

## Test cases

### AC1 — read successfully: in `success`, no note

| Test case | Steps | Expected | Result |
|---|---|---|---|
| `test_ac1_a_file_that_was_read_is_in_success_and_no_note_is_written` | Drop a file; let it be read and saved | In `success`; `log` is empty; nothing left in the inbox, `processing`, `error` or `quarantine` | PASS |
| `test_ac1_the_file_is_the_same_file_byte_for_byte` | Compare the file in `success` with what was dropped | Identical | PASS |
| `test_ac1_the_move_comes_after_the_save` | Look at the folders at the moment the result is saved | The file is still in `processing` then, and in `success` afterwards | PASS |
| `test_ac1_many_successes_still_write_nothing_to_log` | Six files, all read | Six in `success`; `log` is empty | PASS |

### AC2 — failed: in `error`, exactly one note saying why

| Test case | Steps | Expected | Result |
|---|---|---|---|
| `test_ac2_a_failed_file_is_in_error_with_exactly_one_note_saying_why` | A file the pipeline holds as too blurry | In `error`; one note named `<file>.<UTC time>.run1.log`; the note gives the reason | PASS |
| `test_ac2_the_note_has_every_line_the_story_asks_for` | A file whose reading crashes | The note has the reason, the kind of error, the batch id, the run number ("1 of 3"), what happens next, and the end of the error trace | PASS |
| `test_ac2_three_failed_files_get_one_note_each` | Three files fail | Three in `error`, three notes, one per file | PASS |
| `test_ac2_a_good_file_beside_a_bad_one_gets_no_note` | One file read, one fails | One note only, for the bad one | PASS |
| `test_ac2_a_data_error_says_it_will_not_be_tried_again` | A `.pdf` that is not a PDF | In `error`; note says "will not be tried again" | PASS |

### AC3 — stopped after the save, before the move

| Test case | Steps | Expected | Result |
|---|---|---|---|
| `test_ac3_a_stop_between_the_save_and_the_move_leaves_the_file_where_it_is` | Save the result, then stop the pod | Result saved once; file still in `processing`; not written up as a failure; the lease still holds it | PASS |
| `test_ac3_after_the_restart_the_file_is_in_success_and_saved_once` | Restart the listener | The file is taken up again and moved to `success`; the database was asked twice and saved once; `log` is empty | PASS |
| `test_ac3_the_restarted_run_points_at_the_result_that_was_already_saved` | Read the file's row | State `succeeded`, with the first save's job number | PASS |
| `test_ac3_another_pod_takes_it_over_once_the_lease_has_run_out` | A different pod restarts instead | It leaves the file alone while the lease holds, and finishes it once the lease is over; still saved once | PASS |
| `test_ac3_the_real_job_store_knows_an_upload_by_its_bytes` | Ask the real job store for the key of the same bytes twice, and of other bytes | Same key for the same bytes; a different key for different bytes | PASS |
| `test_ac3_has_been_shown_on_a_running_pipeline` | Look for a test that stops a real pod | It exists | **EXPECTED FAIL — needs PostgreSQL, the document store and the pipeline** |

### AC4 — no patient names or values in a failure note

| Test case | Steps | Expected | Result |
|---|---|---|---|
| `test_ac4_a_failure_note_holds_no_patient_name_or_value` | Reading crashes with a message quoting name, age, phone, ABHA, medicine, doctor and BP | None of them, and no part of them, is in the note | PASS |
| `test_ac4_it_still_says_what_kind_of_error_it_was_and_where` | Read the same note | It gives the kind of error (`ValueError`) and the place in the code | PASS |
| `test_ac4_a_job_that_failed_in_the_pipeline_is_written_up_the_same_way` | A job fails with an error text that quotes the patient | Only the kind of error is written; the job number is given for someone who may look | PASS |
| `test_ac4_an_error_with_no_name_is_still_not_quoted` | An error text with no recognisable error name | "the pipeline failed"; nothing quoted | PASS |
| `test_ac4_the_trace_gives_places_never_the_message_or_the_code_line` | Build the trace end for an error quoting a patient | File, line and function names and the error's kind; no message, no line of code | PASS |
| `test_ac4_reasons_this_system_writes_itself_are_given_in_full` | Too many pages, too large, rescan, timeout | The reason is written in full: it is a technical fact | PASS |
| `test_ac4_the_file_name_is_the_only_thing_from_outside_in_the_note` | A file called `anjali-das.pdf` fails | The note names the file (recorded as a fact — see Issues, 2) | PASS |

### AC5 — same name already there: both kept

| Test case | Steps | Expected | Result |
|---|---|---|---|
| `test_ac5_a_second_file_of_the_same_name_is_kept_beside_the_first` | Two different files, both called `rx.pdf`, read one after the other | `rx.pdf` and `rx.1.pdf` in `success`; the first is untouched; the report names where the second went | PASS |
| `test_ac5_the_same_in_error_and_each_run_has_its_own_note` | Two files of one name both fail | Both kept in `error`; two notes | PASS |
| `test_ac5_every_drive_keeps_both` (×4: local, OneDrive, SharePoint, Google Drive) | Move a file into `success` where one of that name is | Both kept, each with its own contents | PASS |
| `test_ac5_nothing_is_ever_deleted` | Read the listener's and connectors' code | No delete call anywhere | PASS |
| `test_ac5_every_file_dropped_is_still_somewhere_afterwards` | Drop good, failing, corrupt and not-allowed files | Every one of them is still in some folder | PASS |

### Quarantine, history and folder names

| Test case | Steps | Expected | Result |
|---|---|---|---|
| `test_a_file_that_fails_three_times_goes_to_quarantine` | The same file fails three times (put back in the inbox each time) | Runs 1 and 2 go to `error`; run 3 goes to `quarantine` and is not to be retried | PASS |
| `test_each_failed_run_has_its_own_note_with_its_run_number` | Read the three notes | Named `run1`, `run2`, `run3`; the last says the runs are used up and a person must look | PASS |
| `test_a_data_error_never_goes_to_quarantine_however_often_it_is_dropped` | A not-a-PDF dropped four times | Always `error`, never `quarantine` | PASS |
| `test_a_success_after_two_failures_is_a_success` | Fails twice, then is read | In `success`; two notes, none for the success | PASS |
| `test_the_number_of_runs_is_a_setting` | Read `CDI_LISTENER_MAX_RUNS`; set it to 1 | Default 3; with 1 the first failure goes to `quarantine` | PASS |
| `test_a_quarantined_file_is_recorded_as_such` | Read the states and migration 0031 | `quarantined` is a state; the migration adds it after 0030 | PASS |
| `test_failed_files_are_tried_again_without_a_person` | Look for a retry agent | It exists | **EXPECTED FAIL — LS-S7 is not built** |
| `test_every_move_of_a_file_is_written_to_its_history` | Read a successful file's history | Moved to `processing`, then to `success`, each with the name and time | PASS |
| `test_a_failed_files_history_shows_where_it_went_and_under_what_name` | Two failing files of one name | Each history ends with the folder and the name it was kept under | PASS |
| `test_the_history_can_be_stored_as_it_is` | Turn a history entry into JSON and back | Unchanged | PASS |
| `test_the_four_folder_names_are_settings` | Name the folders `completed`, `failed`, `held`, `notes` | Files and notes go to those folders; no `success` or `log` folder is made | PASS |

## Missing tests

| What | Why it is missing |
|---|---|
| AC3 on a real pipeline: stop the pod after the save, restart, count results | Needs PostgreSQL, the document store and the pipeline running |
| Migration 0031 and the `quarantined` state on a real database | PostgreSQL is not running on this PC |
| The run count read from the real table (`failures`) | Same; only the in-memory ledger is exercised |
| Automatic retry leading to quarantine | The retry agent (LS-S7) is not built; in the tests a file is put back by hand |
| Notes checked against real pipeline error messages | The error messages here are written for the test; real ones have not been collected |
| Quarantine and notes on OneDrive / Google Drive | Local folder only, except AC5 |

## Issues found

1. **Runs happen only by hand.** A failed file is run again when someone puts it back in the inbox. Until the retry agent (LS-S7) exists, "the retries are used up" can only be reached that way.
2. **The file name is in the note.** The note names the file, so a patient's name in a file name appears in it — as it does on the file itself.
3. **The full error message is still in the listener's log and in the job.** They are scrubbed of secrets, not of patient values. Only the notes were in scope for this story.
4. **For an unexpected failure the note says little:** the kind of error and where in the code. That is the price of never quoting the document; the detail is under the job number given in the note.
5. **"Logical name 'completed'".** The story calls the success folder's logical name `completed`. The code's internal name is still `success`; what a person sees is whatever `CDI_LISTENER_SUCCESS` says.
6. **If the pod stops after the move but before the row is updated,** the file is in `success` while its row still says `processing`. Nothing is lost or read twice, but the row is out of date until someone corrects it.
7. **Migration 0031 is new** and follows 0030. It has not been run on any database.
8. **Earlier tries stay in `error`.** A file that fails three times leaves two copies in `error` and the third in `quarantine`, because each try is a separate drop and nothing is ever deleted.
9. **Not deployed, not pushed.**
