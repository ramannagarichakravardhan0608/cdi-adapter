# Test document — LS-S7

**Epic:** LS — File listener (watched folder)
**Story ID:** LS-S7
**Title:** Retry a failed file 3 times, then hand it to a person
**Checked on:** 2026-10-09, against the local working tree (branch `dev_chakravardhan`, with uncommitted changes)
**Test file:** `tests/test_ls_s7_acceptance_unit.py`; the alert rule is also covered by `tests/test_monitoring_unit.py`
**History:** nothing for this story existed before 2026-10-09. A failed file stayed in `error` until someone put it back by hand. The retry agent, the sorting of failures, the growing waits, the empty-file rule and the quarantine alert were built that day.
**Run:** `python -m pytest tests/test_ls_s7_acceptance_unit.py -v` → **56 passed, 1 expected-fail** (run three times, same result)
**Run with the related suites:** monitoring 23 passed, 2 skipped (no `promtool` on this PC); LS-S1, LS-S6, sign-in, loop and secret-scan suites all pass

## Result

| AC | What it asks | Result |
|---|---|---|
| AC1 | The model server is down for 3 minutes → the file is retried after 60 s, then 120 s, and succeeds once the server is back | **PASS** |
| AC2 | A file fails on every run → after the 3rd retry it is in `quarantine` with a note for each of the 4 runs | **PASS** |
| AC3 | An empty file is dropped → it is not retried and the note says to rescan | **PASS** |
| AC4 | A file reaches `quarantine`, the alert rule runs → a person is told (ENT-S4) | **PARTIAL.** The alert rule exists and fires; **no on-call channel is configured, so nobody is told unless they look** |

**AC1, AC2 and AC3 pass. AC4 is built as far as the code goes; the missing piece is a decision — where alerts should be sent.**

## How the tests work

- The watched folder is a real temporary folder, through the real `local` connector. The real listener loop and the real retry agent run on it, sharing one ledger as they share one table in real life.
- The reader (the pipeline) is a stand-in with a "model server" the test can switch off for a set time, or make fail always.
- The clock is one the test moves, in steps of 10 seconds (one look at the folder), so waits of 60, 120 and 240 seconds take no real time. On each step the agent looks first, then the listener.
- The ledger is the in-memory one, which follows the same rules as the `listener_file` table.
- **Not exercised:** the listener and the agent as two separate processes on a real database; the alert rule through `promtool`; a person actually receiving an alert.

## Test cases

### AC1 — server down 3 minutes: retried after 60 s, then 120 s, then read

| Test case | Steps | Expected | Result |
|---|---|---|---|
| `test_ac1_a_server_down_for_three_minutes_retried_at_60_then_120_then_read` | Server down for 180 s; drop a file; let 400 s pass | Three runs: the second about 60 s after the first, the third about 120 s after the second; the third finds the server back; file in `success`; no alert | PASS |
| `test_ac1_the_waits_are_60_then_120_exactly_as_the_agent_measures_them` | Read the file's history | Two retries, waited 60.0 s and 120.0 s | PASS |
| `test_ac1_it_is_not_retried_before_its_wait_is_over` | Look every 10 s for 50 s after the first failure | Still in `error`, still one run; the agent reports "retry 1 in 10 s" | PASS |
| `test_ac1_each_failed_run_left_a_note_and_the_success_left_none` | Read the notes | Two notes (runs 1 and 2), each saying when it will be tried again; class `transient`; none for the success | PASS |
| `test_ac1_the_file_is_the_same_file_after_its_retries` | Compare the file in `success` with what was dropped | Identical | PASS |
| `test_ac1_other_files_are_read_while_one_waits_for_its_retry` | One file fails; another arrives while it waits | The second is read at once; the first is read after its wait | PASS |

### AC2 — fails on every run: quarantine after the 3rd retry, a note for each of 4 runs

| Test case | Steps | Expected | Result |
|---|---|---|---|
| `test_ac2_a_file_that_always_fails_is_in_quarantine_after_the_third_retry` | The server always times out; let 700 s pass | Exactly four runs; file in `quarantine`; nothing left in `error`, `success` or the inbox | PASS |
| `test_ac2_there_is_a_note_for_each_of_the_four_runs` | Read the `log` folder | Four notes, `run1` to `run4`; the last says the retries are used up and a person must look | PASS |
| `test_ac2_the_three_waits_were_60_120_and_240_seconds` | Read the history and the run times | Waits of 60.0, 120.0 and 240.0 s | PASS |
| `test_ac2_it_is_never_run_a_fifth_time` | Let another hour pass | Still four runs; the agent has nothing to do | PASS |
| `test_ac2_the_row_says_quarantined_and_keeps_the_whole_story` | Read the file's row | State `quarantined`; history: failed, retry, failed, retry, failed, retry, quarantined | PASS |
| `test_ac2_an_unplanned_error_is_retried_and_quarantined_the_same_way` | The pipeline raises a programming error every time | Four runs, then `quarantine`; notes say class `code` | PASS |

### AC3 — empty file: not retried, note says to rescan

| Test case | Steps | Expected | Result |
|---|---|---|---|
| `test_ac3_an_empty_file_fails_is_not_retried_and_the_note_says_to_rescan` | Drop a 0-byte file; wait 2 minutes, then an hour | In `error`; never sent to be read; one note: "the file is empty", class `data`, "will not be tried again", "Rescan and drop it again."; never retried, never quarantined | PASS |
| `test_ac3_an_empty_file_is_given_a_minute_before_it_is_called_empty` | A file is empty for 40 s, then its contents arrive | Not refused while empty; read once it has arrived | PASS |
| `test_ac3_corrupt_and_oversized_files_are_not_retried_either` (×2: not a real PDF, a 60-page PDF) | Drop it; wait 10 minutes | In `error`, class `data`, never retried, never sent to be read | PASS |
| `test_ac3_a_picture_too_blurred_to_read_is_not_retried` | The pipeline holds the picture as too blurry | Read once, never again; note says to rescan | PASS |
| `test_ac3_a_file_over_the_size_limit_is_not_retried` | A file over the size limit | In `error`, never retried; note says to rescan | PASS |

### AC4 — quarantine: a person is told

| Test case | Steps | Expected | Result |
|---|---|---|---|
| `test_ac4_an_alert_is_raised_when_a_file_reaches_quarantine` | A file fails four times | No alert while it is being retried; one alert when it reaches quarantine | PASS |
| `test_ac4_the_alert_is_a_critical_log_line_and_a_line_in_the_alert_list` | Raise the alert for two files | A critical log line `ListenerFileQuarantined` with the file and run count; both files in the alert list file | PASS |
| `test_ac4_a_clean_start_does_not_hide_a_quarantine_alert` | Clear the sign-in alert, as a clean start does | The quarantine alert list is still there | PASS |
| `test_ac4_the_running_listener_is_given_the_alert` | Build the listener from settings | It has the alert wired in | PASS |
| `test_ac4_a_data_error_raises_no_alert` | A not-a-PDF fails | No alert | PASS |
| `test_ac4_there_is_an_alert_rule_for_a_quarantined_file` | Read the alert rules and the runbook | Rule `CdiListenerFileQuarantined`, severity `ticket`, with a runbook section | PASS |
| `test_ac4_the_rule_reads_a_number_that_comes_from_the_listeners_table` | Feed the metric three rows | Reports files by state and how many were quarantined in the last 24 h; the web app exposes it | PASS |
| `test_ac4_a_database_that_cannot_be_read_gives_no_number_and_no_error` | The database read fails | No metric, no error | PASS |
| `test_ac4_the_alert_goes_where_every_other_alert_goes` | Read the Alertmanager route | It goes to the `oncall` receiver | PASS |
| `test_ac4_the_on_call_receiver_reaches_somebody` | Look for an email, chat or pager behind `oncall` | There is one | **EXPECTED FAIL — no on-call channel has been named (ENT-S4)** |

Also in `tests/test_monitoring_unit.py`: the rule is in the list of alerts, has a firing case and a quiet case, uses only metrics the system exposes, and has a runbook section with numbered steps — all PASS. The two tests that run the rule through `promtool` were **skipped** (the tool is not installed on this PC).

### Sorting failures, settings and the agent on its own

| Test case | Steps | Expected | Result |
|---|---|---|---|
| `test_failures_are_sorted_into_data_transient_and_code` (×21) | Sort a list of failures | Empty, corrupt, blurred, too big, not clinical, wrong patient → `data`; timeouts, refused connections, 503, 429, out of memory, lost lease → `transient`; anything else → `code` | PASS |
| `test_only_data_errors_are_not_retried` | Ask which kinds are retried | `data` no; `transient` and `code` yes | PASS |
| `test_the_waits_double_from_the_base` | Base 60 and base 5 | 60, 120, 240 and 5, 10, 20 | PASS |
| `test_the_number_of_retries_and_the_base_wait_are_settings` | Read the defaults; run with 1 retry and a 20 s base | Defaults 3 and 60 s; with 1 retry the file is quarantined after 2 runs, having waited 20 s | PASS |
| `test_the_agent_leaves_another_drives_rows_alone` | A file failed on one drive; an agent for another drive looks at the same ledger | It does nothing; the file is untouched | PASS |
| `test_a_failed_file_someone_took_out_of_error_is_reported_not_chased` | A person removes the failed file | The agent reports it missing | PASS |
| `test_the_agent_keeps_nothing_itself_so_it_can_be_restarted` | Replace the agent with a new one mid-wait | The file is still retried on time and read | PASS |
| `test_the_recover_command_makes_one_pass` | Run `python -m cdi_adapter.listener recover --once` when a retry is due | Prints "retried 1 file(s) … retry 1 of 3"; the file is back in the inbox | PASS |
| `test_the_retry_agent_is_started_with_the_listener` | Read the start scripts | `start_listener.sh` starts both; `start_all.sh` calls it when `CDI_LISTENER_ENABLED=1` | PASS |

## Missing tests

| What | Why it is missing |
|---|---|
| The listener and the agent as two processes on a real database | PostgreSQL is not running on this PC; the tests share one in-memory ledger |
| The agent's database queries (`failed`, `now`) | Same |
| The alert rule run through `promtool` | The tool is not installed here; those two tests were skipped |
| A person actually receiving the alert | No on-call channel is configured |
| A real model-server outage | The outage is simulated by the stand-in reader |
| `infra/runpod/start_listener.sh` run on a pod | Only its shell syntax was checked |
| Retries on OneDrive / Google Drive | Local folder only |

## Issues found

1. **Nobody is told unless they look (AC4).** The alert reaches Alertmanager and the Grafana dashboard, but the `oncall` receiver has no email, chat or pager behind it. This is true of every alert in the system. It needs a channel to be named.
2. **Unplanned errors are retried.** The story names data errors (never retried) and temporary problems (retried); it does not say what to do with a programming error. They are retried like temporary problems, so a file that never passes ends in quarantine with a person. The alternative — straight to quarantine — is a one-line change if preferred.
3. **Four runs before quarantine, not three.** LS-S6 on its own quarantined on the third failure. The setting `CDI_LISTENER_MAX_RUNS` is replaced by `CDI_LISTENER_MAX_ATTEMPTS` (3 retries).
4. **A retry is at most one look late.** The agent and the listener each look every 10 seconds, so a 60 s wait becomes a run about 70 s after the failure.
5. **An empty file is refused after about a minute** (six looks). Before this story an empty file was left in the folder forever.
6. **The alert clears itself after 24 hours**, or when the file is moved back to the inbox — so a file nobody deals with does not leave the alert on forever, but can also go unnoticed after a day.
7. **A slow outage ends in quarantine.** The three waits add up to seven minutes. If the pipeline is down longer than that, every file dropped in that time is quarantined and has to be moved back by hand.
8. **Two processes must both be running.** If only the listener runs, failed files stay in `error` and are never retried. The listener currently running on the owner's PC is the earlier code, without the agent.
9. **Not deployed, not pushed.** The start script that runs both on a pod has never been run.
