# Test document — UP-S1

**Story:** UP-S1 — Choose, scan or take a picture of the pages and send them
**Checked on:** 2026-10-08, against the local working tree (branch `dev_chakravardhan`, with uncommitted changes)
**Test file:** `tests/test_up_s1_acceptance_unit.py`
**History:** the first check changed no application code and found AC3 and AC6 unbuilt. Both were then built the same day (`webapp/app.py`, `webapp/page.py`) and this document updated.
**Run:** `python -m pytest tests/test_up_s1_acceptance_unit.py -v` → **50 passed** (first run, before AC3 and AC6 were built: 19 passed, 4 expected-fail)

## Result

| AC | What it asks | Result |
|---|---|---|
| AC1 | Two taps of "Take picture" on a phone → two previews in order, removable and reorderable | **PASS** (code and script checks; not run on a real phone) |
| AC2 | Two pages + Send → one job, and the person sees one result | **PASS** (unit level; not run against a real database) |
| AC3 | A `.docx` is refused with "Please add a photo, PDF or scan" | **PASS** (was FAIL; built 2026-10-08) |
| AC4 | Send pressed twice quickly → only one job | **PASS** |
| AC5 | A 4000 px photo: original kept as is, no repeated lossy re-compression | **PASS** |
| AC6 | Letters in the ABHA field → refused with a plain message | **PASS** (was FAIL; built 2026-10-08) |

**6 of 6 pass** at the level tested here (see "Missing tests and issues" for what that level does not cover).

## Test cases

### AC1 — two pictures, two previews, in order, removable and reorderable

| Test case | Steps | Expected | Result |
|---|---|---|---|
| `test_ac1_take_photo_is_offered_and_a_phone_uses_its_own_camera` | Read the screen's markup and script | A "Take photo" button; a camera input (`capture=environment`) that feeds `addPhoto` | PASS |
| `test_ac1_each_photo_is_added_after_the_ones_already_there` | Read how a photo joins the list | Appended (`chosen.concat`), chooser cleared so a second tap fires | PASS |
| `test_ac1_previews_are_drawn_in_the_order_they_will_be_sent` | Read the preview strip and the send loop | "Page 1, Page 2…" drawn from the same list, in the order it is sent | PASS |
| `test_ac1_a_page_can_be_removed_and_moved_either_way` | Read the remove and move handlers | Remove drops that page; move swaps neighbours; no move off either end | PASS |

Also checked by hand in a browser on 2026-10-07 with a stand-in camera: two captures gave two previews in order, and remove and reorder worked. **Not checked:** a real phone, a real camera.

### AC2 — one job for two pages, one result

| Test case | Steps | Expected | Result |
|---|---|---|---|
| `test_ac2_two_pages_go_in_one_request_and_make_one_job` | Call the upload route with two files | One store call, one job, files in the order sent | PASS |
| `test_ac2_the_screen_makes_one_request_for_all_the_pages` | Count upload requests in the script | Exactly one `POST /api/jobs` | PASS |
| `test_ac2_the_person_is_given_one_result_for_the_upload` | Read the progress script and the route list | One link `/result/job/<id>` for several pages; no per-row link; the routes exist | PASS |

The combined result itself is covered by 10 tests in `tests/test_result_unit.py` (pages merged, worst status wins, held page named, duplicates read once).
**Limits:** the combined result was written on 2026-10-08 and has only been run with a stand-in database. The pages are still read separately and gathered afterwards; a line continuing from page 1 to page 2 is read as two lines.

### AC3 — a .docx is refused with "Please add a photo, PDF or scan"

| Test case | Steps | Expected | Result |
|---|---|---|---|
| `test_ac3_the_file_chooser_already_hints_at_the_allowed_types` | Read the file input | `accept=".pdf,.png,.jpg,.jpeg,.tif,.tiff"` | PASS |
| `test_ac3_the_screen_refuses_a_docx_with_the_plain_message` | Look for the message in the screen's script | "Please add a photo, PDF or scan" | PASS |
| `test_ac3_the_server_refuses_a_docx_and_creates_no_job` | Send a `.docx` to the upload route | Refused (415) with the message; no job | PASS |
| `test_ac3_anything_that_is_not_a_photo_pdf_or_scan_is_refused` (4 cases) | Send `.txt`, `.xlsx`, `.heic`, a file with no extension | Each refused with the message; no job | PASS |
| `test_ac3_a_photo_a_pdf_and_a_scan_are_all_taken` (5 cases) | Send PDF, JPG, PNG, TIFF, and a PNG with no extension | Each accepted | PASS |
| `test_ac3_one_wrong_file_among_good_ones_refuses_the_upload_and_makes_no_job` | Send a PNG and a `.docx` together | Whole upload refused; no job | PASS |
| `test_ac3_the_screen_refuses_it_when_added_and_keeps_the_pages_already_there` | Read `addFiles` | The wrong file is named and left out; the rest are added | PASS |
| `test_ac3_the_screen_and_the_server_say_the_same_words` | Compare the two messages | Identical | PASS |
| `test_ac3_a_retaken_page_obeys_the_same_rule` | Read the retake route | It makes the same check | PASS |

**How it works:** on the screen, a file that is not a PDF, PNG, JPEG or TIFF is not added; a line "not added — Please add a photo, PDF or scan (letter.docx)" appears and the pages already chosen stay. The server makes the same check before any job exists, judging by the file's first bytes and then its extension, and answers 415 with the same words.
**Checked in a browser (2026-10-08):** a chosen `.docx` and a dropped `.txt` were refused by name while a PNG and a PDF beside them were kept; the message cleared on the next good file.
**Note:** HEIC phone pictures are refused too - the pipeline cannot read them.

### AC4 — Send twice quickly, only one job

| Test case | Steps | Expected | Result |
|---|---|---|---|
| `test_ac4_the_button_is_switched_off_before_the_request_leaves` | Read the send handler | The button is disabled before the request is made | PASS |
| `test_ac4_the_same_upload_sent_twice_is_one_job` | Call the upload route twice with the same two files | Same job id both times; one job | PASS |
| `test_ac4_a_different_upload_is_still_its_own_job` | Send two different files | Two jobs | PASS |
| `test_ac4_the_real_key_is_made_from_the_bytes_the_names_and_who_sent_them` | Build the key for the same and for changed uploads | Same upload → same key; other user or other bytes → other key | PASS |

Already covered on PostgreSQL by `tests/test_queueing_integration.py` (`test_the_identical_upload_sent_twice_is_one_job`, `test_two_copies_arriving_at_once_still_make_one_job`). Those were not re-run today: no local database.

### AC5 — 4000 px photo: original kept, no repeated lossy compression

| Test case | Steps | Expected | Result |
|---|---|---|---|
| `test_ac5_the_screen_sends_the_file_it_was_given_untouched` | Read the script | Files go into the request as they are; the only canvas is the live-camera snapshot, encoded once | PASS |
| `test_ac5_the_server_is_handed_the_exact_bytes` | Send a 4000 × 3000 JPEG to the upload route | The bytes stored are identical to the bytes sent | PASS |
| `test_ac5_the_working_copy_is_full_size_and_lossless` | Render that JPEG to the page copy | A PNG, still 4000 × 3000, pixel-for-pixel the decoded JPEG | PASS |
| `test_ac5_the_reduced_copy_is_a_png_made_from_the_working_copy` | Make the thumbnail | A PNG at the thumbnail size, made once | PASS |
| `test_ac5_the_original_is_stored_under_its_own_key_and_never_rewritten` | Read the ingest code | The original is written to its own key | PASS |

**Notes:** the "reduced copy" is only a 256 px thumbnail for the screen; readers use the full-size page. The in-page live camera asks the browser for about 1920 × 1080, so a 4000 px picture comes from the phone's own camera app or a chosen file, which is passed through untouched. That the stored original reads back identical on MinIO is in `tests/test_ingest_integration.py` (not re-run today).

### AC6 — letters in the ABHA field are refused with a plain message

| Test case | Steps | Expected | Result |
|---|---|---|---|
| `test_ac6_the_upload_has_an_abha_box_and_sends_what_is_typed` | Read the screen | An ABHA box whose trimmed value is sent | PASS |
| `test_ac6_a_well_formed_abha_number_is_accepted` | Send with `91-1234-5678-9012` | Accepted and passed on | PASS |
| `test_ac6_letters_in_the_abha_box_are_refused_with_a_plain_message` | Send with `ABCD-EFGH-IJKL` | Refused (422) with a message naming ABHA; no job | PASS |
| `test_ac6_the_screen_checks_the_abha_box_before_sending` | Read the send handler | The check runs before the request and stops it | PASS |
| `test_ac6_anything_but_fourteen_digits_is_refused` (6 cases) | Letters, one letter, too few, too many, 5 digits, non-Latin digits | Each refused with the message; no job | PASS |
| `test_ac6_fourteen_digits_are_accepted_however_they_are_spaced` (4 cases) | Hyphens, no separators, spaces, surrounding spaces | Each accepted | PASS |
| `test_ac6_an_empty_abha_box_is_fine` (3 cases) | Nothing, empty, spaces only | Accepted with no ABHA | PASS |
| `test_ac6_the_message_is_plain_and_says_what_to_do` | Read the message and where it is shown | Says "14 digits" and "leave the box empty"; shown beside the box | PASS |

**The message:** "The ABHA number must be 14 digits, like 91-1234-5678-9012. Correct it, or leave the box empty."
**How it works:** pressing Generate EMR with a bad ABHA shows the message beside the box, puts the cursor there and sends nothing; it disappears as soon as the number is corrected. The server makes the same check and answers 422.
**Checked in a browser (2026-10-08):** letters and a 10-digit number each stopped the send (0 requests); the corrected number went through; an empty box went through with no ABHA.
**Not checked:** whether the 14 digits are a real ABHA number (there is no checksum or registry lookup).

## Missing tests and issues

1. **Nothing was run on a real phone or with a real camera.** AC1 rests on code checks and a desktop browser with a stand-in camera.
2. **No database today.** AC2's combined result and AC4's "one job" were run with stand-ins; the PostgreSQL/MinIO integration tests that cover AC4 and AC5 exist but were not re-run.
3. **AC2's one result is new and uncommitted**, and gathers per-page results rather than reading the pages as one document.
4. **AC3 judges a file by its first bytes, then its extension.** A Word file renamed to `.pdf` passes this check and fails later at ingest with a technical error, as before.
5. **AC6 checks the shape only** (14 digits), not that the ABHA number exists.
6. **Not deployed.** The test instance was unreachable on 2026-10-08, so none of this has been tried on the live site.
7. **Not an AC, but related:** the upload accepts at most 10 files, and the message for more is "upload 1 to 10 files".
