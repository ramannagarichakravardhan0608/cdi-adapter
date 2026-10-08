# Test document — LS-S4

**Epic:** LS — File listener (watched folder)
**Story ID:** LS-S4
**Title:** Read each image or PDF with the same OCR steps as the upload screen
**Checked on:** 2026-10-08, against the local working tree (branch `dev_chakravardhan`, with uncommitted changes)
**Test file:** `tests/test_ls_s4_acceptance_unit.py`
**History:** before 2026-10-08 the listener already handed each file to the same job store as the screen, but recorded it as an upload, treated a poor picture as an ordinary failure, passed a broken PDF on to the pipeline, and PDFs were rendered by PyMuPDF. The rest was built that day.
**Run:** `python -m pytest tests/test_ls_s4_acceptance_unit.py -v` → **41 passed, 3 expected-fail** (6 closer checks of AC1, AC2, AC3 and AC4 on the real job runner were added later the same day)
**Regression:** all 70 unit suites were run one by one (the three known to stall or be blocked on this PC were left out). **One test still fails because of this story:** `tests/test_bootstrap_pins_unit.py::test_lock_covers_every_runtime_dependency` (Issues, 1). Three other existing tests failed at first and were updated (Issues, 7).

## Result

| AC | What it asks | Result |
|---|---|---|
| AC1 | A 2-page PDF is dropped → one document with 2 pages is saved and both pages are read | **PARTIAL.** One file goes in as one job and renders to 2 pages, and every page is judged. **The saved document has not been checked** — that needs the database |
| AC2 | A JPEG photo and a PNG scan of different prescriptions → both went through the same steps and are saved the same way | **PASS up to the job store.** "Saved the same way" has not been checked on a database |
| AC3 | A blurred picture → the file goes to `error` with the reason "rescan" and is not retried | **PASS** |
| AC4 | The same prescription uploaded on the screen and dropped in the folder → the extracted values are identical | **PARTIAL.** The folder hands the same bytes to the same function as the screen, and only the recorded source differs. **The two results have never been compared** — that needs the database, the document store and the model |
| AC5 | A file called `scan.pdf` is not a real PDF → `error` as a data error, not retried | **PASS** |

**AC3 and AC5 pass. AC1, AC2 and AC4 pass as far as a unit test can go; what they say about saved documents and extracted values has not been shown on a running pipeline.**

## How the tests work

- **Real:** the watched folder (a temporary folder through the `local` connector), the listener loop, the listener's real reader, the page renderer (pypdfium2) and the picture-quality check.
- **Not real: the pipeline behind the job store.** The tests stop at the door: they record exactly what the listener hands to `jobstore.submit`, and answer for the job. The real quality check decides whether the answer is "held" or "done".
- Nothing here saves a document, runs OCR, calls the model or extracts a value. PostgreSQL, the document store and the model were not running.
- Prescriptions are pictures of typed text made in the test; the blurred one is the same picture heavily blurred. No real patient data.
- A gap is written as an expected failure (`xfail`, strict).

## Test cases

### AC1 — a 2-page PDF: one document, 2 pages, both read

| Test case | Steps | Expected | Result |
|---|---|---|---|
| `test_ac1_a_two_page_pdf_goes_in_as_one_file_one_job` | Drop a 2-page PDF | One job, holding one file (the PDF, bytes untouched); 2 pages rendered; moved to `success` | PASS |
| `test_ac1_both_pages_become_pictures_at_the_dpi_setting` | Render the PDF at the default setting | 2 different pictures, each A4 at 200 dpi | PASS |
| `test_ac1_every_page_of_the_file_is_judged_not_just_the_first` | A PDF whose second page is blurred | The file is held: page 2 was looked at | PASS |
| `test_ac1_one_file_is_one_prescription_whatever_its_page_count` | Drop a 3-page PDF and a 1-page picture | Two jobs of one file each: never split, never merged | PASS |
| `test_ac1_the_saved_document_has_been_checked_on_a_real_database` | Look for a database test of a stored listener document | It exists | **EXPECTED FAIL — needs PostgreSQL and the document store** |

### AC2 — a JPEG and a PNG go through the same steps

| Test case | Steps | Expected | Result |
|---|---|---|---|
| `test_ac2_a_jpeg_and_a_png_are_both_read_through_the_same_door` | Drop a JPEG photo and a PNG scan of different prescriptions | Both succeed; each handed over with its own type; the options are identical; both in `success` | PASS |
| `test_ac2_the_runner_has_one_set_of_steps_whatever_the_file_type` | Read the runner's code | No branch on JPEG or PNG; quality check, save, read, classify and form-fill are all there | PASS |
| `test_ac2_both_kinds_of_picture_become_the_same_kind_of_page` | Render each | Both become one RGB PNG page | PASS |

### AC3 — a blurred picture: `error`, "rescan", not retried

| Test case | Steps | Expected | Result |
|---|---|---|---|
| `test_ac3_the_blurred_picture_really_fails_the_quality_check` | Run the real quality check on the blurred and the sharp picture | Blurred fails as "blurred"; sharp passes | PASS |
| `test_ac3_it_goes_to_error_with_the_reason_rescan_and_is_not_retried` | Drop the blurred picture | In `error`; kind `rescan`; reason starts "rescan: image quality below threshold"; not to be retried | PASS |
| `test_ac3_the_note_says_rescan_why_and_that_it_will_not_be_tried_again` | Read the note in `log` | Says rescan, the particular reason, "data error", "will not be tried again", and the job number | PASS |
| `test_ac3_it_is_kept_as_refused_so_nothing_will_retry_it` | Look at the ledger row; poll three more times | State `refused`; never touched again | PASS |
| `test_ac3_nothing_is_guessed_the_file_never_gets_past_the_quality_check` | Read the runner's code | The quality check comes before saving and before reading | PASS |

### AC4 — screen and folder give identical values

| Test case | Steps | Expected | Result |
|---|---|---|---|
| `test_ac4_the_folder_and_the_screen_hand_the_same_bytes_to_the_same_function` | Drop a picture; look at what was submitted | The bytes as dropped; the only option is who sent it (`listener`); the screen uses the same function | PASS |
| `test_ac4_the_only_thing_the_channel_changes_is_what_is_recorded_as_the_source` | Read the runner's code | The channel is used in one place only: the source recorded when the document is saved | PASS |
| `test_ac4_the_listener_has_no_reading_steps_of_its_own` | Read the listener's code | No OCR, model or extraction call anywhere in it | PASS |
| `test_ac4_the_two_results_have_been_compared_on_a_running_pipeline` | Look for a test that compares the two results | It exists | **EXPECTED FAIL — needs database, document store and model** |

### AC1, AC2, AC4 — closer check on the real job runner

The real job runner (the one the screen's uploads run on) is run to the end of a job, with stand-in steps that record every call; the first step renders the pages for real. Still no database, document store or model: this proves which steps run, on what and in what order — not what the model reads or what is stored.

| Test case | Steps | Expected | Result |
|---|---|---|---|
| `test_ac1_on_the_runner_a_two_page_pdf_is_one_document_with_two_pages` | Run a 2-page PDF as a listener job | One document; 2 pages; saved once, not once per page; job ends in review | PASS |
| `test_ac1_on_the_runner_the_document_is_read_after_it_is_saved_whole` | Look at the steps that document went through | save → read print → classify → fill the form → terminology → rules, each on the whole document | PASS |
| `test_ac2_on_the_runner_a_jpeg_and_a_png_go_through_the_same_steps_in_the_same_order` | Run a JPEG job and a PNG job | The two lists of steps are identical; both recorded with the same channel | PASS |
| `test_ac4_on_the_runner_the_screen_and_the_folder_run_the_same_steps` | Run the same bytes once as an upload and once as a listener job | Step for step identical; same pages; same end state; the only difference is the channel recorded (`upload` / `listener`) | PASS |
| `test_ac4_the_pages_handed_to_the_reading_steps_are_identical_pixel_for_pixel` | Render and normalise the same file twice | The page pictures are identical, byte for byte | PASS |
| `test_ac3_on_the_runner_the_blurred_picture_is_held_before_anything_is_saved_or_read` | Run the blurred picture as a listener job | Held; no step ran at all; the listener files it as "rescan" | PASS |

### AC5 — `scan.pdf` that is not a PDF: data error, not retried

| Test case | Steps | Expected | Result |
|---|---|---|---|
| `test_ac5_a_scan_pdf_that_is_not_a_pdf_goes_to_error_as_a_data_error` (×4: a text file, a PDF header and nothing else, a Word document, half a PDF) | Drop it as `scan.pdf` | In `error`; kind `not_readable`; "not a PDF that can be opened"; not to be retried; never reached the pipeline | PASS |
| `test_ac5_the_note_says_so_and_that_it_will_not_be_tried_again` | Read the note and the ledger row; poll three more times | Says so; state `refused`; never touched again | PASS |
| `test_ac5_opening_it_raises_a_data_error_everywhere_not_only_in_the_listener` | Give it to the page renderer | A data error there too | PASS |
| `test_ac5_a_real_pdf_named_scan_pdf_is_read` | Drop a real PDF called `scan.pdf` | Read | PASS |

### The PDF renderer (pypdfium2) and other requirements

| Test case | Steps | Expected | Result |
|---|---|---|---|
| `test_the_pdf_renderer_is_pypdfium2` | Ask which renderer is in use | pypdfium2 | PASS |
| `test_page_count` | PDFs of 1, 2 and 7 pages | Counted and rendered as 1, 2 and 7 | PASS |
| `test_dpi` (×4: 72, 100, 200, 300) | Render an A4 page at each | Pixel size = points × dpi ÷ 72 (rounded up); the picture records its dpi | PASS |
| `test_one_known_pdf` | A page with a black block in a known place, rendered at twice its dpi | Page size 8.5 × 11 inches; the block is where it should be; everything else is paper | PASS |
| `test_page_sizes_are_read_without_drawing_anything` | Ask for page sizes | Sizes in points | PASS |
| `test_a_pdf_is_read_as_pictures_never_from_its_text_layer` | Read the renderer's code | No text-layer call | PASS |
| `test_rendering_from_several_threads_at_once_is_safe` | Render the same PDF 18 times from 6 threads | All results identical | PASS |
| `test_pymupdf_is_gone` | Search the source, scripts, test helper and project file | Nothing imports or declares PyMuPDF; pypdfium2 is declared | PASS |
| `test_the_pods_locked_requirements_have_been_updated` | Read the pod's locked requirements | pypdfium2 is there, PyMuPDF is not | **EXPECTED FAIL — the lock has not been regenerated** |
| `test_ingest_records_the_renderer_it_used` | Read the ingest code | Records the renderer's name, not "pymupdf" | PASS |
| `test_handwriting_is_read_by_whatever_the_screen_uses_not_a_listener_copy` | Search for TrOCR | None in the code (recorded as a fact) | PASS |

## Missing tests

| What | Why it is missing |
|---|---|
| AC1: a stored listener document with 2 pages, channel `listener` | Needs PostgreSQL and the document store |
| AC2: both pictures "saved the same way" | Same |
| AC4: one file through the screen and through the folder, results side by side | Needs the database, the document store and the model. Note: on one database the second copy is recognised by its bytes and reuses the first document, so a true comparison needs two clean runs |
| NFR "a doubtful value is marked 'needs a check'" | A pipeline behaviour; not exercised here |
| A PDF with a real text layer | The test PDFs are pictures of text; only the renderer's code is checked for not using a text layer |
| A password-protected PDF | Not tried; expected to be refused as "not a PDF that can be opened" |
| pypdfium2 on the pod (Linux) | Only run on this Windows PC |

## Issues found

1. **An existing test now fails:** `test_bootstrap_pins_unit.py::test_lock_covers_every_runtime_dependency`. The pod's hash-locked requirements file (`infra/runpod/requirements.lock`) still lists PyMuPDF and does not list pypdfium2. It must be regenerated on Linux; until then this story cannot be deployed.
2. **The closer check (same day) shows the steps, not the values.** On the real job runner a 2-page PDF is one document of 2 pages, a JPEG and a PNG go through identical steps, and the screen and the folder run step-for-step the same on the same bytes. What the model reads, and what is stored, is still unproven.
3. **Nothing has been run on a real pipeline.** Saved documents, page counts in the database and extracted values are unproven for the listener.
4. **TrOCR is not in the codebase** (RD-S2). The story lists it among the steps; today handwriting is read by the model alone, for the screen and the folder alike.
5. **"One file is one prescription" is an assumption** in the story, built as written. It needs the product owner's confirmation.
6. **A broken image is treated differently from a broken PDF.** A `.pdf` that cannot be opened is refused by the listener (AC5). A `.png` or `.jpg` that cannot be opened still goes to the pipeline, whose quality check holds it as unreadable — it then ends as "rescan", also a data error.
7. **Test PDFs changed, and four existing tests with them.** The helpers that made PDFs with PyMuPDF now draw them with Pillow, so they are pictures of text with no text layer, in a different font. Updated: `test_im_s2 … a_crop_can_leave_a_sliver…` (its observation no longer appears), `test_ops … sample_docs_survive_scan_noise…` (the noisy vitals sheet is now classified instead of declined), `test_up_s1 … a_photo_a_pdf_and_a_scan_are_all_taken` (it uploaded a fake PDF, which the upload screen now refuses), and three listener tests. `test_dslm … builds_from_the_real_samples` failed until the generated pages were made sharp and lossless.
8. **PDFium is not thread-safe**, so all PDF rendering now runs one at a time under a lock. On a busy pod this may slow down many PDFs arriving at once; not measured.
9. **Page sizes can differ by one pixel from before** (PDFium rounds up). Not expected to matter, but boxes saved against pages rendered by PyMuPDF were made on the old sizes.
