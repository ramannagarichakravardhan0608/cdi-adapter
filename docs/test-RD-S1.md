# Test document — RD-S1

**Epic:** RD — Reading engines working together
**Story ID:** RD-S1
**Title:** Read printed text with RapidOCR
**Checked on:** 2026-10-09, against the local working tree (branch `dev_chakravardhan`, with uncommitted changes) and by hand on the running vast.ai instance
**Test file:** `tests/test_rd_s1_acceptance_unit.py` (22 tests)
**History:** reading printed lines and saving them as append-only evidence already existed (`ocr/rapid.py`, `ocr/service.py`, the `ocr_observation` table from migration 0013). The two files the story lists as "Built" — `ocrhost/app.py` and `recognition/pipeline.py` — were not in the working tree or on any branch. Both were built on 2026-10-09 as thin layers over the existing code; how a page is read did not change.
**Run:** `python -m pytest tests/test_rd_s1_acceptance_unit.py -v` → **21 passed, 1 expected-fail**
**Run with the related suites:** the OCR, recognition, image-preparation (IM-S3), evidence-schema, jobs, classify, de-identification and listener LS-S4 unit suites all pass

## Result

| Item | What it asks | Result |
|---|---|---|
| AC1 | A printed page is read → every printed line is saved with text, position and confidence | **PASS**, in unit tests with the real reader and on the running instance |
| AC2 | The same page is read twice → the earlier lines are still there (nothing deleted) | **PASS**, in unit tests and on the real database on the instance |
| FR1 | Every printed line with its position and confidence | **PASS** |
| FR2 | Each line saved as evidence, never overwritten | **PASS** |
| FR3 | Printed lines are not sent to the handwriting readers | **PARTIAL.** True when line-by-line reading is switched on; off by default (expected-fail) |
| NFR1 | 26 lines from the sample page in 5.9 s on the first call | **Not reproduced.** The sample page is not in the repository |
| NFR2 | CPU only; no GPU memory | **PASS** |
| NFR3 | RapidOCR code and models are Apache-2.0 | **PASS for the code.** The models' licence is not stated in the package |
| TR1 | `ocr/rapid.py` (`run_rapidocr`) and `ocrhost/app.py` (`POST /ocr/rapid`, port 8079) | **PASS**; the host was built for this story |
| TR2 | Results saved through `recognition/pipeline.py` (`record_rapid_observations`) | **PASS**; built for this story |

**Both ACs pass.** One expected-fail remains, for FR3 with the settings as shipped.

## How the tests work

- **The reader is the real one.** RapidOCR on ONNX Runtime runs on this machine's CPU and reads a synthetic printed page made for the test (a clinic header, a doctor's printed name, test names). No real document is used.
- **The database is a stand-in in unit tests.** What is written is caught where it is handed to the database. The rules the database itself enforces (a row is never changed or deleted) are read from the migration.
- **The OCR host** is called through FastAPI's test client, with the real reader behind it.
- **What unit tests cannot reach:** the real `ocr_observation` table and its triggers, and the host running as its own process.
- **By hand, on the running instance (2026-10-09).** One synthetic document already on the instance was read twice with the print reader on the real database, the evidence was read back, a delete and an update were attempted, and the OCR host was started, called and stopped.

## Checked by hand on the running instance

The document was the synthetic "Test Patient Restart" prescription (one page, 8 printed lines).

| What was checked | What was seen |
|---|---|
| Evidence rows before | 8 in all, 8 current |
| First re-read | 8 lines read, 8 earlier rows marked as replaced, mean confidence 0.9589, 2.8 s |
| Rows after the first re-read | 16 in all, 8 current |
| Second re-read | 8 lines, 1.4 s |
| Rows after the second re-read | 24 in all, 8 current |
| The earlier rows' id, text, position and confidence, compared before and after | Unchanged |
| One current line | Has text, box `[127, 145, 660, 174]`, confidence 0.9773, an outline, engine `rapidocr`, version `rapidocr-onnxruntime 1.4.4`, kind `printed`, a 64-character crop fingerprint |
| RapidOCR rows without text, position or confidence | 0 |
| `DELETE` of the document's evidence | Refused by the database: "is raw evidence: it is never deleted" |
| `UPDATE` of a line's text | Refused by the database |
| Rows after the two attempts | Still 24 |
| OCR host, `POST /ocr/rapid` with the page | 8 lines, 3.1 s on the first call, each with text, box, outline, confidence and reading order |
| OCR host, `GET /healthz` | `status ok`, engine `rapidocr`, device `cpu` |
| Where the host listens | `127.0.0.1:8079` only |
| GPU processes belonging to the host | 0 |

## Test cases

### AC1 — every printed line is saved with text, position and confidence

| Test case | Steps | Expected | Result |
|---|---|---|---|
| `test_ac1_every_printed_line_of_the_page_is_read` | Read the 10-line printed page | 10 lines, each matching its printed line, in reading order | PASS |
| `test_ac1_each_line_has_its_position_on_the_page` | Look at each line's box and outline | Inside the page; four corners; top to bottom | PASS |
| `test_ac1_each_line_has_a_confidence` | Look at each line's confidence | Between 0 and 1, at or above the minimum | PASS |
| `test_ac1_every_line_is_saved_with_text_position_and_confidence` | Save the lines with `record_rapid_observations` | One row a line: text as read, box, outline, confidence, reading order, engine, version, kind `printed`, crop fingerprint | PASS |
| `test_ac1_the_table_has_a_column_for_each` | Read the insert statement | A column for each of them in `ocr_observation` | PASS |
| `test_ac1_the_pipeline_saves_the_print_readers_lines_through_this` | Read the OCR step | It reads with `run_rapidocr` and saves through `record_rapid_observations` | PASS |
| `test_ac1_the_print_reader_runs_first_on_every_page` | Read the job runner | The print pass comes before the handwriting pass; the default engine is RapidOCR | PASS |
| `test_ac1_a_line_the_reader_is_not_sure_of_is_a_setting_not_a_silent_loss` | A line at confidence 0.29 | Not kept at the default minimum of 0.30; kept when the minimum is 0 | PASS (records an issue, see below) |

### AC2 — read twice, the earlier lines are still there

| Test case | Steps | Expected | Result |
|---|---|---|---|
| `test_ac2_reading_the_page_twice_keeps_the_first_readings_lines` | Read and save the page twice | Both sets of rows are there; the first set is unchanged | PASS |
| `test_ac2_saving_only_ever_adds` | Read the saving code | It inserts; no update, delete or truncate | PASS |
| `test_ac2_a_second_reading_points_the_first_at_it_and_changes_nothing_else` | Read the code that marks earlier rows | Only the "replaced by" pointer is set; text is not touched; nothing is deleted | PASS |
| `test_ac2_the_database_itself_refuses_to_change_or_delete_a_line` | Read migration 0013 | A trigger refuses update, delete and truncate | PASS |

### FR3 — printed lines are not sent to the handwriting readers

| Test case | Steps | Expected | Result |
|---|---|---|---|
| `test_fr3_a_printed_line_is_not_sent_to_the_handwriting_reader` | A page with one printed and one handwritten line, read line by line | Only the handwritten line is sent; the printed line keeps the print reader's reading | PASS |
| `test_fr3_it_holds_with_the_settings_as_shipped` | Read the default settings | Line-by-line reading is on | **EXPECTED FAIL** — it is off by default |

### The OCR host (TR1)

| Test case | Steps | Expected | Result |
|---|---|---|---|
| `test_host_reads_a_page_and_returns_every_line_with_position_and_confidence` | `POST /ocr/rapid` with the page | The same lines as reading in-process, with box, outline, confidence, reading order | PASS |
| `test_host_says_what_it_is` | `GET /healthz` | Status, engine, device `cpu` | PASS |
| `test_host_refuses_what_it_cannot_read_in_plain_words` | Empty body; not a picture; a bad minimum; too large | 400, 422, 422, 413 | PASS |
| `test_host_listens_on_port_8079_on_this_machine_only` | Read the setting and the start command | Port 8079, bound to `127.0.0.1` | PASS |
| `test_the_pipeline_reads_through_the_host_when_it_is_told_where_it_is` | Set `CDI_OCR_HOST_URL`; read the page | The host is called; the lines are identical; the default is empty (in-process) | PASS |

### Non-functional requirements

| Test case | Expected | Result |
|---|---|---|
| `test_nfr_cpu_only_no_gpu_memory` | All three models run on the CPU provider only; the pod's lock file has no GPU build | PASS |
| `test_nfr_the_readers_code_is_apache_2` | The package declares Apache-2.0 | PASS |
| `test_nfr_a_page_is_read_in_seconds_on_a_cpu` | The 10-line page is read in under 30 s | PASS (not the story's measurement) |

## Missing tests

| What | Why it is missing |
|---|---|
| NFR1: 26 lines in 5.9 s on the sample page | The sample page is not in the repository. Seen instead: 10 lines in 3.7 s on this PC and 8 lines in 3.1 s on the instance, first call |
| The models' licence | Not stated in the package's own details; it rests on the story's check of 2026-10-03 |
| An automated database test run for this story | `tests/test_evidence_schema_integration.py` covers the table's rules but is skipped locally (no local database). The instance check above was by hand |
| A real printed clinic document | Synthetic pages only; real patient documents are not used in tests |
| A page with several pages, or a rotated or poor scan | Not tried for this story |
| The pipeline running through the host on the instance | The host was called directly there; the pipeline was left reading in-process |
| The host under several calls at once | Not tried |

## Issues found

1. **FR3 does not hold with the settings as shipped.** Line-by-line reading (IM-S3) is off by default because it costs one model call per handwritten line and that cost has not been measured. Until it is on, a page the classifier calls handwritten is sent whole to the handwriting reader, printed lines included.
2. **The two files the story calls "Built" did not exist.** `ocrhost/app.py` and `recognition/pipeline.py` were written for this story.
3. **The OCR host is not started with the pod and nothing uses it by default.** It was started by hand on the instance for the check and stopped again. The pipeline reads in-process unless `CDI_OCR_HOST_URL` is set.
4. **A line read with confidence under 0.30 is not saved** (`CDI_OCR_MIN_CONF`). RapidOCR's own cut-off is higher, so none was seen dropped by it, but "every printed line" is true only above that limit.
5. **The reader drops spaces on the synthetic page** ("CityCareClinic"). The tests compare letters and digits only. This is how it reads the test font; it was not looked into on real print.
6. **"Never overwritten" has one exception by design:** when a page is read again, each earlier row gets a pointer to the run that replaced it. Its text, position and confidence are not changed, and the database refuses any other change.
7. **One existing test was changed.** A test in `tests/test_im_s3_acceptance_unit.py` pinned a line of code that moved into `recognition/pipeline.py`; it now looks there.
8. **Left on the instance:** the synthetic "Test Patient Restart" document now has 24 evidence rows (8 current) from the two re-reads.
9. **Not pushed.** The code is on the instance but uncommitted.
