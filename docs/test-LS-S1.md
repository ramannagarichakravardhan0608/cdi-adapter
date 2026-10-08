# Test document — LS-S1

**Epic:** LS — File listener (watched folder)
**Story ID:** LS-S1
**Title:** Watch a folder on OneDrive, SharePoint, Google Drive or this computer, chosen by settings only
**Checked on:** 2026-10-08, against the local working tree (branch `dev_chakravardhan`, with uncommitted changes)
**Test file:** `tests/test_ls_s1_acceptance_unit.py` (new; written for this check)
**Application code changed for this check:** none
**Run:** `python -m pytest tests/test_ls_s1_acceptance_unit.py -v` → **37 passed, 2 expected-fail**
**Run with the two older listener suites:** `tests/test_listener_connectors_unit.py`, `tests/test_listener_service_unit.py` → **163 passed, 2 expected-fail** in total

## Result

| AC | What it asks | Result |
|---|---|---|
| AC1 | Setting says `gdrive` and a Google folder is given → the listener lists that Google folder, no code changed | **PASS against a stand-in for Google Drive.** Not run on a real Google account |
| AC2 | Setting changes from `onedrive` to `local`, listener restarts → only the local folder is read, and the retry agent leaves the OneDrive rows alone | **PARTIAL.** "Only the local folder is read" PASSES. "The retry agent leaves the OneDrive rows alone" **cannot be shown: there is no retry agent and no OneDrive rows** |
| AC3 | `success`, `error`, `quarantine`, `log` do not exist, the check command runs → they are created and no file is processed | **PASS** on all four drives (local for real, the three cloud drives against stand-ins) |
| AC4 | The drive answers "too many requests" with a wait time → the listener waits that long and tries again | **PASS against stand-ins** for OneDrive, SharePoint and Google Drive. Does not apply to the local drive |

**AC3 passes. AC1 and AC4 pass at the level that can be tested without a real cloud account. AC2 is half met: the half that depends on LS-S7 (retry agent) and LS-S3 (table of files seen) is not built.**

## How the tests work

- **This computer (`local`)** is a real folder made for each test.
- **OneDrive, SharePoint and Google Drive** are the real connector classes from `src/cdi_adapter/listener/connectors.py`, talking HTTPS to in-memory stand-ins that answer the same addresses and shapes as Microsoft Graph and Google Drive v3, and can be told to say "too many requests".
- **No real OneDrive, SharePoint or Google account is called by any test.**
- The reader (the pipeline) is replaced by a stand-in that only records which files it was handed, so no database or document store is needed.
- A gap is written as an expected failure (`xfail`, strict): the suite stays green, the gap stays on record, and the test fails loudly the day the feature is built until the mark is removed.

## Test cases

### AC1 — the setting says gdrive → that Google folder is listed, no code changed

| Test case | Steps | Expected | Result |
|---|---|---|---|
| `test_ac1_the_setting_gdrive_gives_the_google_drive_connector` | Set connector = `gdrive`, folder = `folder-123`; ask for the connector | Google Drive connector; watching `gdrive:folder-123` | PASS |
| `test_ac1_the_listener_lists_the_google_folder_it_was_given` | Put two files in the Google inbox; list it | Both files listed, through Google Drive's file listing | PASS |
| `test_ac1_it_asks_for_that_folder_and_no_other` | Watch what the connector asks Google for | First question is for the folder id from settings | PASS |
| `test_ac1_when_the_listener_starts_it_reads_what_is_in_the_google_folder` | Start the listener on a Google folder holding one file | The file is read and moved to `success` | PASS |
| `test_ac1_the_same_listener_code_runs_on_every_drive` | Run the same loop on local, OneDrive, SharePoint, Google Drive; read the loop's source | Works on all four; the loop names no drive | PASS |
| `test_ac1_switching_drives_is_a_change_of_settings_and_nothing_else` | Compare `.env.onedrive.example` with `.env.gdrive.example` | Among shared settings only `CDI_LISTENER_CONNECTOR` differs | PASS |
| `test_ac1_gdrive_without_a_folder_is_refused_in_plain_words` | Set `gdrive` with no folder id | Refused, naming `CDI_GDRIVE_FOLDER_ID` | PASS |
| `test_ac1_without_a_sign_in_the_google_folder_cannot_be_listed` | Set `gdrive` with no token | "Sign-in needed"; nothing listed (records that LS-S2 is not built) | PASS |

### AC2 — onedrive → local, restart → only local is read; retry agent leaves OneDrive rows alone

| Test case | Steps | Expected | Result |
|---|---|---|---|
| `test_ac2_after_the_switch_only_the_local_folder_is_read` | Run on OneDrive; add another OneDrive file; switch to local and restart with one local file | Only the local file is read; not one more call to OneDrive; the OneDrive file is untouched | PASS |
| `test_ac2_the_setting_decides_which_one_drive_is_built` | Ask for the connector with `onedrive`, then `local` | One drive each time, the one named | PASS |
| `test_ac2_each_drive_says_where_its_files_come_from` | Read each drive's source id | Four different ids, each starting with the drive's name | PASS |
| `test_ac2_the_run_command_follows_the_setting` | Run `run --once` with connector = `local` | The local file is read; output says `watching local:` | PASS |
| `test_ac2_there_is_a_retry_agent` | Look for a retry module in the listener | One exists | **EXPECTED FAIL — not built (LS-S7)** |
| `test_ac2_the_files_the_listener_has_seen_are_kept_with_their_drive` | Look for a `listener_file` table in the migrations | It exists | **EXPECTED FAIL — not built (LS-S3)** |

### AC3 — the check command creates the missing folders and processes nothing

| Test case | Steps | Expected | Result |
|---|---|---|---|
| `test_ac3_the_check_creates_the_four_missing_folders` (×4 drives) | Only `inbox` and `processing` exist; run the check | `success`, `error`, `quarantine`, `log` are created | PASS |
| `test_ac3_no_file_is_processed_by_the_check` (×4 drives) | Two files in the inbox; run the check | Counted as 2 waiting; both still in the inbox; nothing downloaded or moved | PASS |
| `test_ac3_the_check_never_hands_a_file_to_the_reader` | Read the check's code | No download, move or note-writing in it | PASS |
| `test_ac3_a_second_check_creates_nothing_more` (×4 drives) | Run the check twice | First creates six, second creates none | PASS |
| `test_ac3_the_check_command_says_what_it_made_and_that_nothing_was_processed` | Run `python -m cdi_adapter.listener check` on a folder with one file | Exit 0; prints the four created folders, "1 file(s) in the inbox", "nothing was processed"; file unchanged | PASS |

Also done by hand on 2026-10-08: the check was run on the real folder `C:\Users\Chakravardhan\OneDrive - InnoFissionAI\File listener` (local connector). It created the missing folders and processed nothing.

### AC4 — "too many requests" with a wait time → wait that long, try again

| Test case | Steps | Expected | Result |
|---|---|---|---|
| `test_ac4_it_waits_as_long_as_the_drive_asks_and_tries_again` (×3 cloud drives) | Drive answers 429 with `Retry-After: 7`; run the check | Waited 7 s, tried again, got the answer | PASS |
| `test_ac4_several_refusals_in_a_row_are_each_waited_out` (×3 cloud drives) | Drive refuses three times: 3, 5, 2 s | Waited 3, 5, 2 s; then succeeded | PASS |
| `test_ac4_being_told_to_wait_is_not_counted_as_a_failure` | Drive throttles while a file is being read | File still ends in `success`; nothing in failed | PASS |
| `test_ac4_the_wait_time_can_be_seconds_or_a_date` | Give the wait as seconds, as a date, and not at all | 12 s; 30 s; default 5 s | PASS |
| `test_ac4_googles_own_way_of_saying_slow_down_is_waited_on_too` | Google answers 403 "rate limit exceeded" twice | Waited 5 s twice, then succeeded | PASS |
| `test_ac4_it_does_not_wait_for_ever` | Drive keeps asking for 60 s; limit is 100 s | One wait, then gives up with "too many requests" | PASS |
| `test_ac4_an_ordinary_refusal_is_not_waited_on` | Google answers 403 "insufficient rights" | Fails at once; no waiting | PASS |

The waits in these tests are recorded, not slept through: the tests check the number of seconds asked for, not the clock.

## Missing tests

| What | Why it is missing |
|---|---|
| AC1 on a real Google Drive folder | No Google account or sign-in is available; signing in is LS-S2 (not built) |
| AC2 on a real OneDrive, then switched to local | Same: no Microsoft sign-in |
| AC2: a retry agent run after the switch, showing OneDrive rows untouched | No retry agent (LS-S7) and no table of files seen (LS-S3) to test |
| AC4 against a real drive that is really throttling | No real account; real throttling cannot be made to order |
| The long-running `run` loop (without `--once`) | Runs until stopped; only single passes are tested |

## Issues found

1. **AC2 is only half built.** There is no retry agent and no `listener_file` table, so "leaves the OneDrive rows alone" has nothing to act on. What exists is the piece they will need: every drive reports a different source id (`onedrive:…`, `gdrive:…`, `local:…`).
2. **No sign-in (LS-S2).** A cloud drive works only with a bearer token pasted into settings, which expires in about an hour. Without one the listener says `SIGN-IN NEEDED` and stops (exit 2).
3. **No real cloud drive has ever been called.** Every OneDrive, SharePoint and Google Drive result here is against a stand-in. Anything the stand-ins get wrong about the real services will only show on the real drives (LS-S10).
4. **Files left on the old drive are not reported.** After a switch, a file still waiting on OneDrive stays there silently; nothing tells the operator it is there.
5. **Not an LS-S1 defect, but relevant to a tester:** this installation watches the OneDrive folder through the `local` connector (the folder is synced to the PC), so the `onedrive` connector itself is not in use here.
