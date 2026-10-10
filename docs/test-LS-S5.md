# Test document — LS-S5

**Epic:** LS — File listener (watched folder)
**Story ID:** LS-S5
**Title:** Run files in batches of three
**Checked on:** 2026-10-09, against the local working tree (branch `dev_chakravardhan`, with uncommitted changes)
**Test files:** `tests/test_ls_s5_acceptance_unit.py` (unit), one added test in `tests/test_listener_ledger_integration.py` (needs PostgreSQL)
**History:** before 2026-10-09 the listener read finished files one after another, as soon as each was finished. Batches, the waiting time, the batch id and the timings were built that day.
**Run (unit):** `python -m pytest tests/test_ls_s5_acceptance_unit.py -v` → **28 passed, 1 expected-fail** (run three times, same result)
**Run (database):** the history test in `tests/test_listener_ledger_integration.py` → **skipped — PostgreSQL was not running on this PC. Not run.**
**Run with the other listener suites:** LS-S1, LS-S3, LS-S4, sign-in, connectors, loop, schema and secret-scan suites all pass

## Result

| AC | What it asks | Result |
|---|---|---|
| AC1 | 7 finished files wait → 3, then 3, then waits up to 60 s for the last one | **PASS** |
| AC2 | One of the 3 files in a batch is corrupt → the other 2 finish and the corrupt one goes to `error` | **PASS** |
| AC3 | Waiting time is 0 and 2 files wait → they start at once | **PASS** |
| AC4 | A batch of 3 runs → seconds from drop to saved are recorded per file for the load test (LS-S10) | **PASS for recording.** The load test that reads them (LS-S10) is not built, and no batch has been timed on the real model server |

**All four ACs pass at unit level. What is outstanding: the database column has not been exercised, and the timings have only ever been produced by a stand-in reader.**

## How the tests work

- The watched folder is a real temporary folder, through the real `local` connector, with the real listener loop running on it.
- The reader (the pipeline) is a stand-in that takes a short moment over each file and records when it started and ended. "At the same time" and "only when the whole batch has finished" are checked from those recorded times.
- The clock the listener reads is one the test moves, so "waits 60 s" takes no real time. The in-memory ledger reads the same clock.
- Files are real, different one-page PDFs, each given a different date so "oldest first" means something.
- **Nothing here measures how long a real batch takes.** The model server reads one document at a time (24–37 s each, measured earlier), so three files handed over together still take about three times as long as one.

## Test cases

### AC1 — 7 files: 3, then 3, then the last one waits up to 60 s

| Test case | Steps | Expected | Result |
|---|---|---|---|
| `test_ac1_seven_files_go_three_then_three_then_the_last_one_waits` | Drop 7 files; poll twice | Two batches: files 1–3, then 4–6; six succeed; file 7 still in the inbox, reported as waiting | PASS |
| `test_ac1_the_last_one_starts_when_it_has_waited_sixty_seconds` | Keep polling every 10 s | Nothing at 10–50 s; at 60 s file 7 runs as a batch of one | PASS |
| `test_ac1_the_second_batch_starts_only_when_the_whole_first_has_finished` | Drop 6 files; compare recorded times | The earliest start in batch 2 is after the latest end in batch 1; never more than 3 being read at once | PASS |
| `test_ac1_the_files_of_a_batch_run_at_the_same_time` | Drop 3 files, each taking 0.15 s to read | All three reading periods overlap; the batch takes less than 3 × 0.15 s | PASS |
| `test_ac1_the_oldest_files_are_in_the_first_batch` | Drop 7 files with dates in reverse order | Batches follow the dates, oldest first; the newest is the one left waiting | PASS |
| `test_ac1_a_file_that_arrives_while_one_waits_fills_the_batch_sooner` | 2 files wait; a third arrives | The batch of 3 runs as soon as the third is finished, well before 60 s | PASS |

### AC2 — one corrupt file: the other 2 finish, it goes to `error`

| Test case | Steps | Expected | Result |
|---|---|---|---|
| `test_ac2_a_corrupt_file_goes_to_error_and_the_other_two_finish` | Batch of 3 where the middle one is named `.pdf` but is not a PDF | One batch; 2 in `success`; the corrupt one in `error` as `not_readable`; it never reached the pipeline | PASS |
| `test_ac2_a_file_the_pipeline_fails_on_does_not_stop_the_others` | The pipeline holds the middle file ("rescan") | 2 succeed, 1 failed | PASS |
| `test_ac2_a_crash_while_reading_one_file_does_not_stop_the_others` | Reading the middle file raises an error | 2 succeed; 1 failed with the reason; one note in `log` | PASS |
| `test_ac2_the_batch_still_ends_and_the_next_one_runs` | 6 files, one in the first batch crashes | Both batches run; 5 succeed, 1 failed | PASS |
| `test_ac2_all_three_bad_is_three_files_in_error_and_nothing_stuck` | All three crash | 3 in `error`; inbox and `processing` empty | PASS |

### AC3 — waiting time 0, 2 files: they start at once

| Test case | Steps | Expected | Result |
|---|---|---|---|
| `test_ac3_with_no_waiting_time_two_files_start_at_once` | Waiting time 0; drop 2 files; poll twice | Both run as one batch on the look they become finished; no time passed | PASS |
| `test_ac3_with_a_waiting_time_the_same_two_files_wait` | Same files, waiting time 60 | No batch; 2 reported as waiting; nothing read | PASS |
| `test_ac3_with_no_waiting_time_a_lone_file_starts_at_once_too` | Waiting time 0; one file | Read at once | PASS |
| `test_ac3_the_batch_size_and_the_waiting_time_are_settings` | Read the defaults; build a listener from other settings | Defaults 3, 60 s, poll 10 s; the listener uses what the settings say | PASS |
| `test_ac3_a_batch_size_of_one_runs_files_one_after_another` | Batch size 1; 3 files | Three batches of one, in order, never two at once | PASS |

### AC4 — seconds from drop to saved, per file

| Test case | Steps | Expected | Result |
|---|---|---|---|
| `test_ac4_seconds_from_drop_to_saved_are_recorded_for_every_file` | 3 files; 10 s until finished; each reading moves the clock 30 s | Every file: state `succeeded`, the batch's id, 100 s from drop | PASS |
| `test_ac4_the_seconds_are_kept_with_the_file_and_carry_the_batch_id` | Read each file's history in the ledger | Two entries: taken into the batch (id, position, size, time) and finished (id, seconds from drop) | PASS |
| `test_ac4_a_file_that_failed_is_timed_too` | One of three crashes | All three have seconds recorded, with their own states | PASS |
| `test_ac4_the_time_a_lone_file_waited_for_its_batch_is_in_its_seconds` | One file waits out the 60 s | 70 s from drop (10 s to be finished + 60 s waiting) | PASS |
| `test_ac4_the_run_command_prints_the_batch_and_the_seconds` | Run `run --once` on 3 files | Prints "3 file(s), 3 read together" and each file's seconds; three timing lines and one batch line in the log | PASS |
| `test_ac4_the_table_has_a_history_column_for_it` | Read migration 0030 and the ledger code | The `history` column is added after 0029; the ledger appends to it | PASS |
| `test_ac4_the_load_test_reads_them` | Look for the listener load test | It exists | **EXPECTED FAIL — LS-S10 is not built** |

### The rules behind them

| Test case | Steps | Expected | Result |
|---|---|---|---|
| `test_select_batch_by_itself` | Call the selection with 0, 5 and 2 files at 0, 59 and 60 s | Nothing; the first 3; nothing; nothing; both | PASS |
| `test_a_once_run_does_not_hold_a_short_batch_back` | `run --once` with 2 files and a 60 s waiting time | Both are read: there is no later pass to start them | PASS |
| `test_the_same_picture_twice_in_one_batch_is_still_one_result` | Two identical files and one other in a batch | The second identical file is not read; it is filed as a duplicate | PASS |
| `test_each_batch_has_its_own_id` | 6 files, two batches | Two different ids; each file's history carries its own batch's id | PASS |
| `test_a_lost_sign_in_during_a_batch_lets_the_others_finish_then_stops` | The sign-in expires while one file of three is being read | The other two are filed; the listener then stops; the third stays in `processing` | PASS |
| `test_the_memory_ledger_is_safe_from_several_threads` | 200 threads use the ledger at once | 200 correct rows | PASS |

### Database test — SKIPPED, PostgreSQL not running

| Test case | What it would prove |
|---|---|
| `test_history_keeps_the_batch_id_and_the_timing_in_order` | Migration 0030's `history` column stores the batch entry and the finished entry, in order, on the real table |

## Missing tests

| What | Why it is missing |
|---|---|
| The `history` column on a real database, and migration 0030 itself | PostgreSQL is not running on this PC; a skipped test is not a passed test |
| A batch of 3 timed on the real model server | The reader is a stand-in. The real server reads one document at a time |
| The load test reading the recorded seconds | LS-S10 is not built |
| Two listener processes each running batches on one folder | Needs the database; the lease is covered by LS-S3's tests only |
| The long-running loop over many minutes | Tests drive single looks with a moved clock |
| OneDrive / Google Drive | Local folder only; the cloud connectors are thread-safe by design but batches were not run against their stand-ins |

## Issues found

1. **A batch is not faster yet.** Three files are handed to the pipeline together, but the model server reads one document at a time (24–37 s each). A batch of 3 should take about 72–111 s — arithmetic on measured single-file times, not a measurement. Faster batches need the batching server of RD-S5.
2. **"The drop" is when the listener first saw the file**, not the file's own date: a file copied into the folder keeps its old date. The recorded seconds can therefore be short by up to one poll interval (10 s).
3. **How long a lone file waits:** up to 60 s waiting time plus up to two looks (2 × 10 s) to count as finished. These are settings; set them to the business need.
4. **`run --once` does not wait** for a short batch. This is deliberate — it has no later pass — but it means `--once` behaves differently from the running listener.
5. **Each file's job has its own timeout.** A batch ends only when its slowest file ends; with the default job timeout (900 s) one stuck file can hold the next batch back for up to 15 minutes.
6. **Files of a batch are taken and checked one after another, then read together.** Downloading from a slow drive is therefore not parallel; only the reading is.
7. **Migration 0030 is new** and follows 0029. The listener's database ledger needs it; without it, writing a batch id fails.
8. **Not deployed, not pushed.** The instance still runs the listener code without batches.
