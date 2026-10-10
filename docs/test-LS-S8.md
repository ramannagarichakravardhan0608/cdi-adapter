# Test document — LS-S8

**Epic:** LS — File listener (watched folder)
**Story ID:** LS-S8
**Title:** Give the same database rows and JSON as an upload, and say where each result came from
**Checked on:** 2026-10-09, against the local working tree (branch `dev_chakravardhan`, with uncommitted changes) and by hand on the running vast.ai instance
**Test files:** `tests/test_ls_s8_acceptance_unit.py` (34 tests), `tests/test_result_file_unit.py` (16 tests, the JSON file writer)
**History:** before 2026-10-09 a dropped file went through the same pipeline as an upload and was stored with channel `listener`, but nothing linked the dropped file to its document, the result's source said only the channel and a file name, there was no search, and no JSON file was written. All of that was built that day.
**Run:** `python -m pytest tests/test_ls_s8_acceptance_unit.py tests/test_result_file_unit.py -v` → **50 passed, 0 expected-fail**
**Run with the related suites:** the other listener suites, the result, upload, job, review and sync suites all pass

## Result

| AC | What it asks | Result |
|---|---|---|
| AC1 | A file is read from the folder, the result is saved → the rows and the JSON have the same shape as an uploaded prescription | **PASS**, in unit tests and on the running instance |
| AC2 | The result is opened, its source is read → channel `listener`, the drive, the folder and the original file name | **PASS**, in unit tests and on the running instance |
| AC3 | The same file is uploaded and dropped → one result | **PASS** in unit tests. On the instance the reuse of an existing document was seen once (earlier the same day); the "also received" listing was not looked at there |
| AC4 | A person searches for a file name → the result of that file is found | **PASS**, in unit tests and on the running instance (the search function; the search box was not clicked through in a browser) |

**All four ACs pass.** The parts that need a database were checked by hand on the running instance, not by an automated test.

## How the tests work

- **Unit tests.** The watched folder is a real temporary folder with the real listener loop. The pipeline is a stand-in that keeps one document per set of bytes, as the real ingest does. The function that builds a result's source, the contract's JSON schema, the file writer and the search's own logic are all the real code; the search runs against a stand-in for the database that applies the search pattern the way PostgreSQL would.
- **What unit tests cannot reach:** `result.build` reads PostgreSQL, so the rows and the JSON of a real result are not built in any unit test. There is **no automated database test** for this story.
- **By hand, on the running instance (2026-10-09).** Three new synthetic prescriptions (made-up patients) were dropped into a temporary watched folder on this PC and read by the listener through the instance's real pipeline; the stored rows, the result, the JSON file and the search were then read back on the instance. The output of those checks is quoted below.

## Checked by hand on the running instance

| What was checked | What was seen |
|---|---|
| The dropped file's row | `state: succeeded`, linked to its document, `original_name` and `folder` filled in |
| Channel on the stored document | `listener` |
| The result's source | `{"channel": "listener", "drive": "local", "folder": "<the watched folder>", "file_name": "<the name it was dropped under>"}` |
| The result validates against `result.v1.json` | Yes |
| Compared with an uploaded result | Same top-level keys; same source keys; the upload's source has `drive` and `folder` empty |
| Rows made for the dropped file | 1 page, 1 review item — as for an upload |
| The JSON file | `results/<document id>.result.v1.json` in the document store, 2,684 bytes, validates |
| File against the API | Byte-identical |
| File written again | Identical bytes |
| Search for part of the file name | Found, with channel, drive, status and a link to the result |

## Test cases

### AC1 — the same shape as an uploaded prescription

| Test case | Steps | Expected | Result |
|---|---|---|---|
| `test_ac1_the_source_block_has_the_same_keys_for_an_upload_and_a_dropped_file` | Build the source for an upload and for a dropped file | The same four keys; the upload has no drive and no folder | PASS |
| `test_ac1_both_validate_against_the_one_contract_schema` | Validate a result of each kind against `result.v1.json` | Both valid; same top-level keys | PASS |
| `test_ac1_the_result_is_built_by_one_function_whatever_the_channel` | Read the builder's code | One function; the only place the channel matters is whose arrival is the source | PASS |
| `test_ac1_the_rows_come_through_the_same_pipeline_as_an_upload` | Read the listener's code and the runner | The listener writes no clinical rows; the runner changes only the channel recorded | PASS |
| `test_ac1_a_json_file_is_written_for_each_result` | Read the runner | The file is written when a document is done, with no difference by channel | PASS |

The JSON file itself (`tests/test_result_file_unit.py`):

| Test case | Expected | Result |
|---|---|---|
| `test_a_json_file_is_written_for_the_result` | A file named `<document id>.result.v1.json` with the result in it | PASS |
| `test_it_is_also_put_in_the_document_store` | Stored under `results/` as JSON | PASS |
| `test_the_name_carries_the_document_and_the_format_version` | Name holds the document id and `v1` | PASS |
| `test_the_folder_and_the_store_prefix_are_settings` | `CDI_RESULT_OUTPUT_DIR` and `CDI_RESULT_OUTPUT_PREFIX` are honoured | PASS |
| `test_with_neither_set_nothing_is_written` | Nothing written | PASS |
| `test_an_upload_and_a_dropped_file_give_files_of_the_same_shape` | Same keys at every level | PASS |
| `test_a_result_that_does_not_fit_the_format_is_not_written` | Refused; no file, nothing in the store | PASS |
| `test_a_bad_rebuild_leaves_the_earlier_file_as_it_was` | The earlier file is untouched | PASS |
| `test_a_reader_never_sees_half_a_file` | Written to a temporary file, then renamed; no litter | PASS |
| `test_writing_it_again_gives_the_identical_file` | Same bytes | PASS |
| `test_the_file_is_what_the_api_serves` | Same bytes as the API | PASS |
| `test_a_changed_row_changes_the_file` | A changed value shows in the rewritten file | PASS |
| `test_the_pipeline_writes_it_when_a_document_is_done` | Written only for a finished document | PASS |
| `test_the_runner_writes_it_for_both_channels_and_survives_a_failure` | Written for an upload and a dropped file; a failed write does not fail the document | PASS |
| `test_it_is_written_again_after_a_correction_and_after_an_approval` | Rewritten after the change is saved | PASS |
| `test_a_file_that_cannot_be_written_never_raises_from_a_refresh` | Logged, not raised | PASS |

### AC2 — the source: listener, drive, folder, original file name

| Test case | Steps | Expected | Result |
|---|---|---|---|
| `test_ac2_a_dropped_files_result_says_listener_drive_folder_and_file_name` | Drop a file; build its source | Channel `listener`, drive `local`, the watched folder, the file's name | PASS |
| `test_ac2_the_dropped_file_is_linked_to_the_document_it_became` | Read the file's row | It holds the document's id and the job number | PASS |
| `test_ac2_the_original_name_is_kept_when_the_file_is_renamed_on_the_way` | Two files called `rx.pdf` | Kept as `rx.pdf` and `rx.1.pdf`; both results say `rx.pdf` | PASS |
| `test_ac2_every_kind_of_drive_says_which_drive_and_which_folder` (×4) | Local, OneDrive, SharePoint, Google Drive | The drive's name, and the folder: a path, or for Google Drive the folder's id | PASS |
| `test_ac2_a_drive_class_of_ones_own_with_no_label_still_gives_a_folder` | A custom drive with no folder label | Drive and folder taken from its source id | PASS |
| `test_ac2_the_schema_has_a_place_for_each_of_the_four` | Read the contract schema | Channel, drive, folder, file name | PASS |
| `test_ac2_the_runner_records_the_channel_on_the_stored_document` | A listener job; a finished job | Channel `listener`; the document id is passed back to the listener | PASS |
| `test_ac2_the_result_file_is_written_again_once_the_file_is_linked` | Drop a file; drop the same bytes again | The reader is told after each link, so the file is refreshed | PASS |
| `test_ac2_a_refresh_that_fails_does_not_fail_the_file` | The refresh raises | The file is still read and filed | PASS |

### AC3 — uploaded and dropped: one result

| Test case | Steps | Expected | Result |
|---|---|---|---|
| `test_ac3_the_same_file_dropped_twice_is_one_document` | Drop the same bytes under two names | One read; both rows point at one document | PASS |
| `test_ac3_uploaded_first_then_dropped_is_the_uploads_one_document` | The screen stores the document first; then drop the file | The row points at the upload's document; still one document | PASS |
| `test_ac3_that_one_result_says_it_was_uploaded_and_also_dropped` | Build that result's source | Channel `upload`; the dropped arrival listed under `also_received` | PASS |
| `test_ac3_dropped_twice_the_first_is_the_source_and_the_second_is_listed` | Two arrivals of one document | The first is the source; the second is listed | PASS |
| `test_ac3_one_document_per_set_of_bytes_is_the_ingest_rule` | Read the ingest code | A document is looked up by its hash before a new one is made | PASS |

### AC4 — found by file name

| Test case | Steps | Expected | Result |
|---|---|---|---|
| `test_ac4_a_dropped_file_is_found_by_its_name` | Search the full name | Found, with a link to its result, channel, drive and folder | PASS |
| `test_ac4_part_of_the_name_in_any_letter_case_is_enough` | Search `ANJALI` | Found | PASS |
| `test_ac4_an_uploaded_file_is_found_the_same_way` | Search an uploaded file's name | Found; channel `upload` | PASS |
| `test_ac4_a_file_is_found_by_the_name_it_was_dropped_under_and_the_one_it_was_kept_under` | Search `rx.pdf` and `rx.1.pdf` | Both find it | PASS |
| `test_ac4_a_dropped_file_with_no_result_is_found_too_and_says_what_became_of_it` | Search a file that was refused | Found, with "no result: refused (rescan)" and no link | PASS |
| `test_ac4_a_file_still_waiting_in_the_folder_is_not_listed_as_a_result` | Search a file not yet read | Not listed | PASS |
| `test_ac4_the_characters_that_mean_something_to_the_search_are_taken_literally` | Search `100%` and `rx_anjali` | `%` and `_` mean themselves | PASS |
| `test_ac4_the_search_route_wants_two_characters_and_is_for_staff_who_may_see_results` | One character; read the access rule | Refused in plain words; same access as opening a result | PASS |
| `test_ac4_the_upload_screen_has_the_search_box` | Read the Generate EMR page | A "Find a result by file name" box that calls the search | PASS |

### Non-functional requirements

| Test case | Expected | Result |
|---|---|---|
| `test_the_original_file_is_kept_unchanged_as_evidence` | The file in `success` is byte-for-byte what was dropped; the pipeline keeps the original bytes | PASS |
| `test_people_are_told_not_to_put_patient_names_in_file_names` | The guide says so | PASS |
| `test_the_table_links_a_file_to_its_document` | Migration 0033 adds `document_id`, `original_name` and `folder` | PASS |

## Missing tests

| What | Why it is missing |
|---|---|
| An automated database test for the rows, the result and the search | Not written; these were checked by hand on the instance. If the code changes, nothing re-checks them |
| NFR1: the same file through the screen and through the folder, results compared value for value | Not done. On one database the second arrival reuses the first document, so a true comparison needs two clean runs |
| AC3 on the instance: a result showing `also_received` | The reuse of a document was seen; the listing itself was not read back |
| The search box clicked through in a browser | Only the search function was run on the instance; the page was checked for what it contains |
| The result file after a reviewer's correction and after an approval, on the instance | Tested in unit tests only |
| OneDrive / SharePoint / Google Drive sources on real drives | Stand-ins only |

## Issues found

1. **No automated test on a real database.** The story's database side rests on one manual check on 2026-10-09.
2. **Found and fixed during the manual check: the GPU worker was never restarted by a code sync** (`infra/colab/sync.sh`). It runs the last steps of every job, so it had been running the morning's code all day. Any change to those steps made earlier that day was not live on the instance until this was fixed.
3. **Found and fixed during the manual check: the result file was written too early.** The pipeline wrote it before the listener had linked the dropped file to its document, so the file's drive and folder were empty. The listener now refreshes the file once the link is made. Until that refresh, for a moment, the file lags behind the API.
4. **The refresh after linking is done by the listener's own process.** If the listener is stopped between the pipeline finishing and the link being made, the file keeps the empty drive and folder until the next change to that result.
5. **Google Drive's folder is an id, not a path.** The listener only knows the folder's id there.
6. **File names are visible everywhere** — folders, notes, search results, the result's source. The guide tells people not to put a patient's name in a file name; the system does not enforce it.
7. **The search is for staff who may open results.** A front-desk account is told it cannot open results.
8. **Search results are capped at 20** of each kind, newest first.
9. **Four synthetic test documents were left on the instance** ("Test Patient Ls Eight", "Out Two", "Out Three", "Out Four"), each with a review item.
10. **Not pushed.** Migration 0033 has run on the instance only.
