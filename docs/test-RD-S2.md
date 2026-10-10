# Test document — RD-S2

**Epic:** RD — Reading engines working together
**Story ID:** RD-S2
**Title:** Handwriting: TrOCR and Qwen each read every line on their own
**Checked on:** 2026-10-09, against the local working tree (branch `dev_chakravardhan`, with uncommitted changes) and by hand on the running vast.ai instance with the real TrOCR checkpoint and the real Qwen model
**Test files:** `tests/test_rd_s2_acceptance_unit.py` (34 tests), `tests/test_htr_label_kit_unit.py` (37 tests, the labelling kit)
**History:** none of the files the story lists as "Built" existed in the working tree or on any branch (`recognition/engines.py`, `POST /ocr/trocr`, `recognize_page`, `bench_htr.py`). All were built on 2026-10-09, on top of the line crops of IM-S3 and the evidence table of RD-S1.
**Run:** `python -m pytest tests/test_rd_s2_acceptance_unit.py -v` → **33 passed, 1 expected-fail**
**Run with the related suites:** the RD-S1, OCR, image-preparation (IM-S3), schema, evidence-schema, resolution, recognition and jobs unit suites all pass

## Result

| AC | What it asks | Result |
|---|---|---|
| AC1 | A handwritten line is read → two readings (TrOCR and Qwen) are saved, each from the same crop | **PASS**, in unit tests and on the instance's real database with both real models |
| AC2 | The Qwen prompt is inspected → it contains no text from the other reader (test exists) | **PASS**; the tests exist |
| AC3 | The TrOCR host is down → the line has one reading and is sent to review | **PASS**, in unit tests and on the instance with the host unreachable |
| AC4 | English handwritten lines are benchmarked → the report names the checkpoint and gives error rates for TrOCR alone, Qwen alone and both | **PARTIAL, waiting on a person.** The harness is built and its report does both, and the kit that makes real labelled lines is built. It has been run on synthetic lines only: nobody has yet typed what 500 real handwritten lines say (expected-fail) |

**Three ACs pass; AC4 is partial.** Accuracy on real English prescription handwriting is unmeasured, and no decision about TrOCR is made.

## How the tests work

- **The two readers in unit tests are stand-ins** that record exactly what they were handed. The TrOCR checkpoint is not downloaded for a unit test and Qwen needs the GPU host.
- **So the unit tests prove the plumbing:** the same crop goes to both, nothing passes between them, both readings are saved, a missing reading sends the line to review, and the benchmark's arithmetic and wording are right.
- **They prove nothing about how well either reader reads handwriting.** That is the benchmark's job.
- **What unit tests cannot reach:** the real checkpoint loading and reading, the real model gateway, the real evidence table.
- **By hand, on the running instance (2026-10-09).** With the owner's go-ahead the checkpoint `microsoft/trocr-base-handwritten` was downloaded to the instance. Three things were then run there and are quoted below: both real readers on the same crops; the pipeline with two readers on the real database; the same with the TrOCR host unreachable. Last, the benchmark was run on 200 synthetic lines.

## Checked by hand on the running instance

### Both real readers on the same crop

TrOCR ran on the OCR host (CPU); Qwen ran through the model gateway (GPU). The lines were synthetic typeset lines.

| Truth | TrOCR | Qwen |
|---|---|---|
| `Inj Paracetamol 20 mg 1-0-1 x 5 days` | `Inj Paracetamol 20 mg 1-0-1 x5 days .` (confidence 0.8864) | `Inj Paracetamol 20 mg 1-0-1 x 5 days` (0.55) |
| `Syp Paracetamol 20 mg 0-0-1 x 10 days` | `Syp Paracetamol 20 mg 0-0-1 x 10 days` (0.8717) | `Syp Paracetamol 20 mg 0-0-1 x 10 days` (0.55) |
| `Inj Azithromycin 10 mg 1-1-1 x 7 days` | `Inj Azithromycin 10 mg 1-1-1 x7 days` (0.936) | `Inj Azithromycin 10 mg 1-1-1 x 7 days` (0.55) |

The OCR host held no GPU process.

### The pipeline with two readers, on the real database (AC1)

The synthetic "Test Patient Restart" document (8 typeset lines) was read with `CDI_LINE_REGIONS`, `CDI_LINE_READERS` and `CDI_TWO_READERS` on. **Its lines are printed, so for this check only the check script treated every line as handwritten**, to have them sent to the readers.

| What was checked | What was seen |
|---|---|
| Lines sent | 8; both readers read all 8; 23.8 s for the page |
| Current evidence rows | 8 from `trocr`, 8 from `vlm` (Qwen) |
| Crops with two readings / with one | 8 / 0 |
| A pair of rows | Same crop fingerprint; TrOCR `ref. LS.S9-887F.B02` (0.8124), Qwen `Ref: LS-S9-887FFB02` (0.55) — each saved as read |
| Version on each row | `microsoft/trocr-base-handwritten`; `Qwen/Qwen2.5-VL-7B-Instruct` |
| Prompt fingerprint | On Qwen's rows only |
| Reason for review | None on any row |
| Earlier rows of the document | All kept (40 rows in all after this run) |

### The TrOCR host unreachable (AC3)

The same read, with the host address pointed at a port nothing listens on and a 5-second limit.

| What was checked | What was seen |
|---|---|
| The page | Finished in 16.3 s; not held up |
| The print reader | Logged "host unreachable, reading in-process" and read the page itself |
| Lines | 8 sent; 0 with both readings; 8 with one |
| Current evidence rows | 8 from `vlm`, none from `trocr` |
| Reason on every row | `one reading: trocr is down (the TrOCR host did not answer (ConnectError))` |

### The benchmark on 200 synthetic lines (AC4, the harness only)

Typeset text with jitter and noise, fictional content. **Not handwriting.**

| | A: Qwen alone | B: TrOCR alone | C: both |
|---|---|---|---|
| Lines read | 200 | 200 | - |
| Accepted | 200 | 200 | 21 |
| Coverage | 1.0000 | 1.0000 | 0.1050 |
| CER | 0.0000 | 0.0800 | 0.0000 |
| WER | 0.0000 | 0.3200 | 0.0000 |
| Critical-value accuracy | 1.0000 | 0.4850 | 1.0000 |
| Accepted and wrong | 0 | 179 | 0 |
| Accepted-value precision | 1.0000 | 0.1050 | 1.0000 |
| Lower bound (95%) | 0.9812 | 0.0697 | 0.8454 |
| Wrong, caught by disagreement | - | - | 179 |
| Seconds per line | 0.529 | 3.485 | 4.014 |

The report names the checkpoint (`microsoft/trocr-base-handwritten`), says the lines are not real handwriting, and makes no decision. It is saved at `docs/bench/htr-synthetic/report.md`.

In three TrOCR misses looked at, the differences were spacing and stray punctuation (`x5 days .` for `x 5 days`) and one dropped digit (`1-1-x3` for `1-1-1 x 3`).

## Test cases

### AC1 — two readings saved, each from the same crop

| Test case | Steps | Expected | Result |
|---|---|---|---|
| `test_ac1_both_readers_are_given_the_same_crop` | Read one handwritten line | Each reader is handed that line's crop, and only that | PASS |
| `test_ac1_two_readings_are_saved_each_with_that_crops_fingerprint` | Build the evidence rows | Two rows; the same crop fingerprint; each reader's own text, confidence and version | PASS |
| `test_ac1_a_reading_is_never_changed_to_match_the_other` | The readers disagree on a digit | Both saved as read | PASS |
| `test_ac1_only_handwritten_lines_are_sent` | A printed, a handwritten and a mixed line | The printed line goes to neither reader | PASS |
| `test_ac1_the_pipeline_saves_them_through_the_evidence_table` | Read the OCR step | It calls `recognize_page`, builds the rows and only inserts | PASS |
| `test_ac1_the_print_readers_line_gives_way_only_to_a_line_that_was_read` | One line read, one read by neither | Only the read line replaces its print reading | PASS |
| `test_ac1_the_checkpoint_is_in_the_settings_and_reading_twice_is_off_until_set` | Read the default settings | Checkpoint `microsoft/trocr-base-handwritten`; two readers off | PASS (records an issue, see below) |
| `test_ac1_trocr_on_the_host_is_sent_the_crop_and_nothing_else` | TrOCR through the host | `POST /ocr/trocr` with the crop bytes; no text travels with it | PASS |
| `test_ac1_the_host_has_the_trocr_route_and_names_the_checkpoint` | Call the host's route | Text, confidence, the checkpoint's name; an empty body is refused | PASS |
| `test_ac1_trocr_reads_on_the_cpu` | Read the loader | The model is put on the CPU | PASS |

### AC2 — Qwen's prompt holds nothing from the other reader

| Test case | Steps | Expected | Result |
|---|---|---|---|
| `test_ac2_qwens_prompt_holds_nothing_from_the_other_reader` | TrOCR reads a marker text; capture what Qwen is sent | The prompt is the fixed sentence, whole; the marker is not in it | PASS |
| `test_ac2_the_prompt_is_one_fixed_sentence_with_nowhere_to_put_a_reading` | Read the prompt and the engine | No placeholder; never formatted | PASS |
| `test_ac2_a_reader_is_handed_the_crop_and_nothing_else` | Read each reader's signature | One input: the crop. Readers are started before any has answered | PASS |
| `test_ac2_qwen_is_given_the_picture_of_the_line_only` | Capture the image Qwen is sent | The line's crop | PASS |
| `test_ac2_the_prompts_fingerprint_is_kept_with_the_reading` | Read one line with Qwen | The reading carries the prompt's fingerprint and the model's name | PASS |

### AC3 — the TrOCR host is down

| Test case | Steps | Expected | Result |
|---|---|---|---|
| `test_ac3_with_the_trocr_host_down_the_line_has_qwens_reading_and_a_review_reason` | The host refuses the connection | One reading (Qwen); one row, carrying "one reading: trocr is down" | PASS |
| `test_ac3_a_value_resting_on_that_reading_goes_to_review` | Resolve a value whose evidence carries the reason | Outcome `review`, cause "single-reading"; without the reason, no such signal | PASS |
| `test_ac3_with_the_host_down_the_print_reader_reads_the_page_itself` | The host refuses the print reader too | The page is read in-process | PASS |
| `test_ac3_a_slow_reader_does_not_hold_the_page` | TrOCR takes 3 s; the limit is 0.3 s | The page returns in under 2 s; "did not answer in 0.3 s" | PASS |
| `test_ac3_it_is_the_same_whichever_reader_is_down` | Qwen is down instead | TrOCR's reading alone, with the reason | PASS |
| `test_ac3_a_reader_that_breaks_or_reads_nothing_counts_as_missing` | One reader breaks, the other returns nothing | "no reading", with both reasons; no row is written | PASS |
| `test_ac3_a_broken_engine_is_reported_as_down_not_raised` | The gateway times out | Reported as "down", not an unhandled error | PASS |
| `test_ac3_the_reason_has_a_column_and_only_new_rows_get_it` | Read migration 0035 and the insert | A `review_reason` column; no earlier row is touched | PASS |

### AC4 — the benchmark and its report

| Test case | Steps | Expected | Result |
|---|---|---|---|
| `test_ac4_the_report_names_the_checkpoint_and_gives_the_three_error_rates` | Render a report | The checkpoint's name; CER and WER for A, B and C | PASS |
| `test_ac4_the_figures_are_right` | Four lines with known mistakes | CER, WER, critical-value accuracy, accepted-and-wrong, caught-by-disagreement and times as worked out by hand | PASS |
| `test_ac4_the_measures_themselves` | Edit distance, comparison rule, numbers on a line, lower bound | Known answers | PASS |
| `test_ac4_a_run_on_synthetic_lines_says_so_and_decides_nothing` | A synthetic run | "NOT real handwriting"; no decision | PASS |
| `test_ac4_the_decision_rule_is_fixed_before_the_run_and_marked_an_assumption` | 600 real-labelled lines, cheap and slow TrOCR | Keep when it gains and is cheap; remove (plan B) when too slow; too few lines decide nothing | PASS |
| `test_ac4_a_reader_that_was_not_run_is_shown_as_not_run` | Only Qwen was run | B and C shown as "not run" | PASS |
| `test_ac4_the_synthetic_lines_are_typeset_fictional_and_repeatable` | Make the lines twice with one seed | Identical text and pictures | PASS |
| `test_ac4_the_harness_runs_and_writes_the_report` | Run `bench_htr.py --synthetic 6` with stand-in readers | `report.md` and `report.json` written, naming the checkpoint | PASS |
| `test_ac4_real_lines_are_refused_until_they_are_declared_de_identified` | A manifest without `--deidentified` | Refused; nothing written | PASS |
| `test_ac4_the_benchmark_has_been_run_on_real_handwriting` | Look for a report on real lines | It exists | **EXPECTED FAIL** — no labelled real lines exist |

### What is said about trust

| Test case | Expected | Result |
|---|---|---|
| `test_the_code_says_plainly_that_no_reader_is_trusted_yet` | The module states: not yet trusted, IAM, non-commercial, ENT-S1; two readers off | PASS |

## The labelling kit (built 2026-10-09, for AC4)

The benchmark needs 500-1,000 real handwritten lines with their true text. `scripts/htr_label_kit.py` makes them, with a person in the middle:

1. `cut` - cuts every handwritten line out of prescriptions the owner chooses, into a kit folder with a `label.html` page. Printed lines are left out; so is any line the print reader can read as carrying a person.
2. A person opens `label.html`, types what each line says, ticks *skip* for a line with a name, phone or ID on it (or one nobody can read), and saves `labels.csv`.
3. `build` - keeps only lines that were typed, not skipped, and whose typed text carries no identity, and writes the manifest `bench_htr.py` reads. It says how many lines are still needed.

| Test group (`tests/test_htr_label_kit_unit.py`) | What it shows | Result |
|---|---|---|
| Cutting (5 tests) | Only handwritten lines are cut; lines about a person are left out; a crop is named by its pixels and nothing records the file it came from; cutting again keeps what was typed; a bad file does not stop the rest; the real line finder leaves a printed page alone | PASS |
| What counts as a person (25 cases) | Names with a title, age and sex, phone, Aadhaar, UHID, email, address and similar are recognised; ordinary medicine and test lines are not | PASS |
| The typing page (1 test) | Each crop with a box and a skip tick; saves the sheet; sends nothing anywhere | PASS |
| Building (4 tests) | Only typed, unskipped, identity-free lines are kept and copied; the second screen can leave a line out; the benchmark reads what is written; 500 is "enough" | PASS |
| Never in the repository (2 tests) | A kit or a lines folder inside the repository is refused; the script cuts, builds and refuses | PASS |

The typing page was also opened in a browser with a small synthetic kit: four crops shown, text typed, a skip ticked, and the saved sheet had the right rows (quotes and commas in typed text kept intact).

## Missing tests

| What | Why it is missing |
|---|---|
| The benchmark on 500–1,000 real, de-identified English handwritten lines (FR7, AC4) | The kit to make them is built; nobody has typed the lines yet. Everything about accuracy on prescriptions is unmeasured |
| The kit's `cut` on real handwritten prescriptions | Not run: choosing the prescriptions is the owner's step. The real line finder was run on a typeset page only |
| A decision about TrOCR (FR5, FR8) | Needs the real benchmark. The rule's thresholds are placeholders for the owner to set |
| Real handwriting through the pipeline | The instance check used typeset lines forced to "handwritten". No real handwritten line has been read by TrOCR here |
| A hosted model as a reference (FR6) | Optional; needs the owner's privacy and terms decision. Not built |
| An automated database test for the two rows and the review reason | Not written; checked by hand on the instance |
| The review reason seen on a value in the reviewer screen | Tested at the resolution step in unit tests; not looked at on the instance |
| TrOCR in-process (no host) with the real checkpoint | The instance check used the host. In-process loading was run once directly while fixing the loader |
| Critical-value accuracy on real doses | Synthetic only |

## Issues found

1. **Accuracy on real prescription handwriting is unmeasured.** The checkpoint is an English one trained on IAM, which is general handwriting. No reader is trusted on prescriptions until the real benchmark is run.
2. **Two readers are off by default** (`CDI_TWO_READERS`), for that reason and because the checkpoint's licence is a review item: the weights are marked MIT but were tuned on IAM, which is non-commercial research data (ENT-S1). The checkpoint was downloaded to the test instance only.
3. **Nothing compares the two readings yet.** They are saved side by side. Until a comparison step exists, a value resting on readings from two engines is sent to review by the existing rule, whether they agree or not.
4. **Qwen's confidence is not a measure.** The model gateway returns no token probabilities, so every Qwen reading carries the fixed 0.55 the pipeline has always given it. TrOCR's confidence is its own (the mean probability of the tokens it chose).
5. **On the synthetic lines, exact comparison accepted only 21 of 200.** TrOCR mostly differed in spacing and stray punctuation. Whether the comparison should forgive that is a decision for the comparison step, not made here.
6. **TrOCR is slow on the CPU:** 3.5 s a line against Qwen's 0.5 s (the story measured 4.5 s and 0.4 s). The placeholder rule's limit is 1.0 s a line.
7. **Found and fixed on the instance: the checkpoint would not load.** This version of transformers does not build the checkpoint's tokenizer automatically; the loader now falls back to the Roberta tokenizer.
8. **Found and fixed on the instance: with the OCR host down the whole page failed.** The print reader and TrOCR share the host. The print reader now reads in-process when the host cannot be reached.
9. **A reader that is late is left running.** The line moves on after the limit, but the late call is not cancelled; under a long outage these could pile up for the length of a timeout each.
10. **Readers are waited for one line at a time.** A page of 60 handwritten lines with one reader down costs up to 60 timeouts (20 s each by default) before it finishes.
11. **The OCR host is not started with the pod.** It was started by hand for the check and stopped again.
12. **The kit's automatic screen for lines about a person is patterns only on this PC.** It catches labelled or titled names, ages, phones and IDs. A bare first name with no title is not caught by it; the person's *skip* tick is the guard for that. The project's own de-identifier is used as a second screen where its model is installed (it is not on this PC; `build` reports whether it ran).
12. **Not done in this story:** removal of the 3B fallback Qwen (RD-S4), reading twice with different padding (RD-S3, plan B), any fine-tuning (FT-S3).
13. **Left on the instance:** the TrOCR checkpoint in the model cache; migration 0035 applied; the synthetic "Test Patient Restart" document back on a normal reading, with 56 evidence rows (8 current).
14. **Not pushed.** The code is on the instance but uncommitted.
