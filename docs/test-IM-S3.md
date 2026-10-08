# Test document — IM-S3

**Epic:** IM — Image preparation (OpenCV)
**Story ID:** IM-S3
**Title:** Find the text lines and say which are printed and which are handwritten
**Last updated:** 2026-10-08 (after AC2, AC3 and AC4 were dealt with)
**Checked on:** 2026-10-08, against the local working tree (branch `dev_chakravardhan`, with uncommitted changes)
**Test file:** `tests/test_im_s3_acceptance_unit.py`
**History:** the first check changed no application code and found that lines were labelled and could be cut, but nothing sent a line to a reader, and there was no answer key (32 passed, 5 expected-fail). Line-by-line reading was built the same day.
**Run:** `python -m pytest tests/test_im_s3_acceptance_unit.py -v` → **49 passed, 1 expected-fail**

## Result

| AC | What it asks | Result |
|---|---|---|
| AC1 | A printed header and a handwritten body → header lines labelled printed, body lines handwritten | **PASS** on the test page. On one real photographed prescription: no wrong label, but about a third of the lines are left "uncertain" |
| AC2 | A line that cannot be classified is labelled uncertain and goes to both readers | **PASS with line-by-line reading switched on** (was partial; built 2026-10-08). Off by default |
| AC3 | The answer-key set is run; line recall and label accuracy are reported by document type | **PASS for the counter, the command and a way to make a key; the real numbers are still missing** — there is no answer-key set |
| AC4 | Lines of different heights are cut; every crop meets the crop standard and its size is recorded | **PASS with line-by-line reading switched on** (was partial; built 2026-10-08). Off by default |

**AC1, AC2 and AC4 pass at the level tested here. AC3's tools are built, but the numbers they exist to produce cannot be produced until someone supplies real pages with hand-drawn lines — that one test stays an expected failure.**

## What changed in the code

| AC | Change | Where |
|---|---|---|
| AC2 | **Line-by-line reading.** With `CDI_LINE_READERS` and `CDI_LINE_REGIONS` both on, each line goes to the reader its label names: a printed line keeps the print reader's reading; a handwritten line is read by the handwriting reader *instead*; an uncertain or mixed line is read by *both*. | `ocr/service.py` (`read_lines`), `ocr/vlm_ocr.py` (`read_line_crop`), `config.py` |
| AC4 | The crop handed to the handwriting reader is cut to the crop standard, and each crop's size, padding and enlargement are recorded with the OCR run (`metrics.line_crops`). Each reading is stored with the fingerprint of its crop. | `ocr/service.py` |
| AC3 | `scripts/measure_regions.py --draft` writes a draft answer key from what the module finds, for a person to correct. A draft nobody has reviewed is refused by `--key`. | `scripts/measure_regions.py` |

Also: a page that was read line by line is not read again as a whole page (`webapp/jobs.py`).

## How the tests work

The real line finder, the real OCR-stage step that reads lines, and the real upload-job helper are used, on pictures built in the test. The handwriting reader (the model) is replaced by a stand-in that "reads" every crop it is given and keeps them, so the tests see exactly which lines were sent and what was sent. The repository holds **no real handwriting**, so handwriting in the tests is printed words leaned over and made uneven. The real-page numbers below came from running `scripts/measure_regions.py` on pictures on this machine that are not in the repository, judged by eye.

## Test cases

### AC1 — header lines printed, body lines handwritten

| Test case | Steps | Expected | Result |
|---|---|---|---|
| `test_ac1_every_line_of_the_page_is_found` | Find the lines on a page with a printed header and a handwritten body | All header and body lines found, top to bottom | PASS |
| `test_ac1_header_lines_are_labelled_printed` | Label the header lines | All "printed" | PASS |
| `test_ac1_body_lines_are_labelled_handwritten` | Label the body lines | All "handwritten" | PASS |
| `test_ac1_a_fully_printed_page_has_no_handwritten_line` | Label a printed page | No "handwritten" | PASS |
| `test_ac1_the_numbers_behind_each_label_are_kept` | Read each line's features | Stroke direction, letter-height spread, stroke width, letter count | PASS |
| `test_ac1_the_ocr_stage_counts_the_lines_of_each_kind_on_every_upload` | Run the OCR stage's line step | Counts of each kind recorded with the run | PASS |
| `test_ac1_with_line_regions_on_each_reading_is_stored_with_its_lines_label` | Match readings to lines with the switch on | Each reading carries its line's label | PASS |
| `test_ac1_by_default_the_labels_are_counted_but_not_stored_or_acted_on` | Read the default and the OCR code | `CDI_LINE_REGIONS` is off; readings keep the blanket "printed" | PASS (records the issue) |
| `test_ac1_a_failure_finding_lines_never_fails_the_page` | Make the line finder crash | OCR goes on without it | PASS |

**On real pages** (run 2026-10-08; judged by eye):

| Page | Lines found | Printed | Handwritten | Uncertain | Wrong label |
|---|---|---|---|---|---|
| Neat handwritten prescription with a printed letterhead | 52 | 2 | 32 | 18 | 0 |
| Hurried handwriting, shadow, Kannada letterhead | 45 | 1 | 6 | 38 | 1 |
| Three printed sample scans (together) | 31 | 24 | 0 | 7 | 0 |

### AC2 — an unclassifiable line is uncertain and goes to both readers

| Test case | Steps | Expected | Result |
|---|---|---|---|
| `test_ac2_a_line_too_short_to_measure_is_labelled_uncertain` | Label one letter's worth of a line | "uncertain" | PASS |
| `test_ac2_short_upright_handwriting_is_uncertain_not_printed` | Label "TSH", "HS", "1-0-1" | "uncertain", never "printed" | PASS |
| `test_ac2_writing_too_small_to_measure_is_uncertain_not_guessed` | Label a page shrunk to a quarter | Never "handwritten" | PASS |
| `test_ac2_an_unsure_print_reader_makes_a_printed_line_uncertain` | Give a printed line a low print-reader confidence | "uncertain" | PASS |
| `test_ac2_an_uncertain_line_is_marked_for_both_readers` | Read the label-to-reader table | uncertain and mixed → print and handwriting | PASS |
| `test_ac2_nothing_is_dropped_every_line_found_has_a_label_and_a_reader` | Label every line of a page | Each has a label and at least one reader | PASS |
| `test_ac2_an_uncertain_line_goes_to_both_readers` | Make the print reader unsure of line 1; read the page line by line | Line 1 is uncertain, the handwriting reader reads it, and the print reading is **kept** | PASS |
| `test_ac2_a_handwritten_line_goes_to_the_handwriting_reader_instead` | Read a page with a printed header and handwritten body | Every body line is read by the handwriting reader and its print reading left out; every header line keeps its print reading | PASS |
| `test_ac2_a_printed_line_is_not_sent_to_the_handwriting_reader` | Read a printed page | Only lines that are not plainly printed are sent | PASS |
| `test_ac2_the_handwriting_reader_is_given_the_lines_own_crop` | Look at what the reader received | Each line's own crop, in reading order, with the one-line prompt | PASS |
| `test_ac2_nothing_is_dropped_when_the_handwriting_reader_fails_or_reads_nothing` | Make the reader fail on one line and return nothing for another | Both lines keep the print reader's reading | PASS |
| `test_ac2_a_page_that_is_all_uncertain_cannot_make_endless_calls` | Cap the page at 2 lines | 2 calls; the rest counted as "over the cap" and keep their print reading | PASS |
| `test_ac2_the_reading_is_tidied_to_one_line` | Read a crop | One clean line; empty when nothing was read | PASS |
| `test_ac2_the_pipeline_sends_each_line_to_the_reader_its_label_names` | Read the OCR stage | It routes by the label's readers and stores each handwriting reading as its own row | PASS (was FAIL) |
| `test_ac2_there_is_a_reader_that_takes_one_line` | Look for a line reader | `read_line_crop` | PASS (was FAIL) |
| `test_ac2_line_by_line_reading_is_off_unless_both_switches_are_on` | Read the defaults | Both off; cap 60 lines a page | PASS (records the issue) |
| `test_ac2_a_page_read_line_by_line_is_not_read_whole_as_well` | Ask the upload job whether to run the whole-page handwriting pass | No, once the lines were read one by one | PASS |

**How it works.** On the print reader's pass, S3 finds the lines, labels them, and — with line-by-line reading on — cuts every line that is not plainly printed and hands it to the handwriting reader with a prompt that asks for that one line. The handwriting reading is stored as its own observation (engine `vlm`, the line's label, the line's box, the crop's fingerprint). For a handwritten line the print reader's reading is left out; for an uncertain or mixed line both readings are kept, so the later stages see two engines on that line.

### AC3 — recall and label accuracy by document type, on the answer key

| Test case | Steps | Expected | Result |
|---|---|---|---|
| `test_ac3_recall_is_the_share_of_drawn_lines_that_were_found` | Score 2 found lines against 3 drawn | Recall 67 %, label accuracy 100 % | PASS |
| `test_ac3_sent_to_both_is_counted_apart_from_right_and_from_wrong` | Score right / uncertain / wrong | 1 right, 1 safe, 1 wrong | PASS |
| `test_ac3_results_are_reported_by_document_type` | Score pages of two document types | A row per type | PASS |
| `test_ac3_the_command_runs_an_answer_key_and_prints_the_table` | Run `--key` on a key written by the test | A table with recall % and label % by document type; also as JSON | PASS |
| `test_ac3_a_missed_line_and_a_wrong_label_show_in_the_table` | A key with a wrong label and a line that is not there | Recall below 100 %; wrong labels counted | PASS |
| `test_ac3_a_draft_key_can_be_made_for_a_person_to_correct` | Run `--draft` on a page | A key listing every line found, marked "not reviewed" | PASS |
| `test_ac3_a_draft_nobody_has_reviewed_cannot_be_scored` | Run `--key` on the draft; then mark it reviewed and run again | Refused as a draft; accepted once reviewed | PASS |
| `test_ac3_there_is_an_answer_key_set_of_real_pages_with_drawn_lines` | Look for a real key under `data/` | It exists | **FAIL** — there is none |

**How to make and run a key**

```
python scripts/measure_regions.py page1.png page2.jpg --draft answer_key.json --draw out
#  -> a person opens the boxed copies in out/, corrects labels and boxes, adds missed lines,
#     and sets "reviewed": true on each page
python scripts/measure_regions.py --key answer_key.json
```

A draft is what the module itself found, so scoring it unreviewed would report 100 % and mean nothing; the command refuses it.

### AC4 — every crop meets the crop standard and its size is recorded

| Test case | Steps | Expected | Result |
|---|---|---|---|
| `test_ac4_lines_of_every_height_are_cut_to_the_standard` (4 cases) | Cut every line at full size and shrunk to 60 %, 35 %, 20 % | Every crop meets the standard | PASS |
| `test_ac4_the_standard_is_a_minimum_height_padding_and_a_cap_on_enlarging` | Read the standard | 32 px minimum height, 15 % padding (at least 4 px), at most 4× | PASS |
| `test_ac4_a_crop_keeps_paper_round_the_line_so_no_stroke_is_cut` | Cut one line | Paper on all four sides | PASS |
| `test_ac4_a_very_small_line_is_enlarged_before_it_is_handed_over` | Cut a line from a page shrunk to 30 % | Enlarged to the minimum height | PASS |
| `test_ac4_the_size_of_every_crop_is_recorded` | Read each crop's record | Position, padded position, padding, scale, both sizes, fingerprint | PASS |
| `test_ac4_the_fingerprint_is_of_the_pages_own_pixels_not_of_the_enlargement` | Compare the fingerprint | It is of the pixels as they are on the page | PASS |
| `test_ac4_a_line_at_the_page_edge_is_clipped_not_refused` | Cut at the page edge and outside it | Clipped; outside is refused | PASS |
| `test_ac4_every_crop_sent_to_a_reader_meets_the_standard` | Read pages at full size, 35 % and 20 % line by line; decode what the reader actually received | Each picture sent is the recorded size and meets the standard | PASS |
| `test_ac4_the_size_of_every_crop_sent_is_recorded_with_the_run` | Read the crop records and the OCR stage | Each record has its sizes, padding, scale, label and whether it was read; kept in the run's metrics with its page number | PASS (was FAIL) |
| `test_ac4_a_small_line_is_enlarged_before_the_reader_sees_it` | Read a page shrunk to 30 % line by line | Every crop sent is enlarged to at least 32 px | PASS |
| `test_ac4_each_reading_is_stored_with_the_fingerprint_of_its_crop` | Match readings to crops | Each reading carries its crop's fingerprint | PASS |
| `test_ac4_crops_are_cut_only_when_lines_are_read_one_by_one` | Read the OCR stage | No crop is cut unless line-by-line reading is on | PASS |
| `test_ac4_the_crop_records_are_kept_out_of_the_audit_row_and_the_log` | Read the OCR stage | The records go with the run, not into every audit line | PASS |

**Where the size is recorded.** With the OCR run, in `pipeline_run.metrics.line_crops`: one entry per crop sent, with the page number, the line's box, the padded box, the padding, the enlargement, the size on the page, the size sent, the label and whether it was read. No database change was needed.

## Missing tests and issues

1. **No answer-key set (AC3).** The counter, the command and the draft maker are built; real pages with hand-drawn lines do not exist. One test stays expected-fail (strict) for this.
2. **Line-by-line reading is off by default** (`CDI_LINE_READERS`, which also needs `CDI_LINE_REGIONS`). It is one model call per line that is not plainly printed — 18 on the neat real page, 38 on the hard one — and whether that reads better than one whole-page call has not been measured. The story's own rule is that routing must prove its worth by ablation.
3. **It has never been run with the real model or a real database.** The handwriting reader in these tests is a stand-in. How well the model reads a single line crop — and whether it invents text for an unreadable sliver — is unknown.
4. **There is one handwriting reader, not two.** The story speaks of "the two handwriting readers"; only the vision model exists in this code.
5. **An uncertain line now has two readings**, one from each engine. The later stages were written for one reading per line; how extraction treats the same line read twice has not been checked on real data.
6. **Crop records live in the run's metrics**, not in their own table. They can be sliced by crop size from there, but there is no query or report that does so yet.
7. **On real pages many lines are "uncertain"**, and print in a non-Latin script is called handwritten (one Kannada line).
8. **Only the web app's job runner and the OCR stage were changed.** The Celery chain reads through the same OCR stage, so it gets line-by-line reading too when the switches are on; that path was not exercised.
9. **Not deployed.** The test instance was unreachable on 2026-10-08.

## Summary

- **Passed:** AC1, AC2, AC4, and AC3's counter, command and draft maker — 49 of 50 acceptance tests.
- **Failed:** one test — AC3's real answer-key set does not exist, so recall and label accuracy on real pages cannot be reported.
- **Conditional:** AC2 and AC4 happen only with line-by-line reading switched on; it is off by default.
- **Missing:** real pages with hand-drawn lines, a run with the real model and database, a measurement of whether line-by-line reading is worth its cost, and a second handwriting reader.
