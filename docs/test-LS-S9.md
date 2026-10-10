# Test document — LS-S9

**Epic:** LS — File listener (watched folder)
**Story ID:** LS-S9
**Title:** Keep the listener running, restart-safe and visible
**Checked on:** 2026-10-09, against the local working tree (branch `dev_chakravardhan`, with uncommitted changes) and by hand on the running vast.ai instance
**Test file:** `tests/test_ls_s9_acceptance_unit.py` (39 tests)
**History:** before 2026-10-09 the listener kept its files in the database and used leases, but it had no gentle stop, no heartbeat, no health view, no alert for a listener that has gone quiet, and nothing started it with the pod. All of that was built that day.
**Run:** `python -m pytest tests/test_ls_s9_acceptance_unit.py -v` → **38 passed, 1 expected-fail**
**Run with the related suites:** `tests/test_ls_s7_acceptance_unit.py` (57 passed, 1 expected-fail) and `tests/test_monitoring_unit.py` (23 passed, 2 skipped) pass

## Result

| AC | What it asks | Result |
|---|---|---|
| AC1 | The pod is restarted during a batch → no file is lost; each unfinished file is read again once its lease has expired | **PASS**, in unit tests and in a real kill-and-restart of the listener process against the running instance |
| AC2 | The listener is asked to stop during a batch → the batch finishes, then it stops | **PASS** in unit tests. Not tried on a real process |
| AC3 | The health view is opened → last good poll, files waiting, oldest waiting file, files in error, files in quarantine | **PASS** in unit tests; the `health` command was also run on the instance. The page was not opened in a browser on the instance |
| AC4 | No poll succeeds for 5 minutes → an alert is raised | **PASS.** The alert was seen firing in Prometheus and arriving in Alertmanager on the instance, and it is shown to the signed-in staff in a banner on every page (built 2026-10-09). Somebody who is not at a screen is still not told: no email, chat or pager is configured (expected-fail, ENT-S4) |

**All four ACs pass.** One expected-fail remains for telling somebody who is away from the screen; it waits for an email, chat or pager to be named.

## How the tests work

- **Unit tests.** The watched folder is a real temporary folder with the real listener loop. The pipeline is a stand-in the test can make slow or stop dead. The ledger is the in-memory one, which follows the same rules as the tables. The clock is one the test moves, so a 15-minute lease takes no time. "The pod is restarted" is a listener thrown away in the middle of a batch and a new one made on the same folder and ledger.
- **What unit tests cannot reach:** a real process being killed, the real database ledger, a real Prometheus evaluating the rule, and the start scripts on a pod.
- **By hand, on the running instance (2026-10-09).** Two checks, described below: a listener process killed while reading a file, and the listener stopped for more than five minutes with the monitoring stack running.

## Checked by hand on the running instance

### AC1 — a real kill and restart

A listener (`pod-A`, lease 90 seconds) watched a temporary folder on this PC and read through the instance's real pipeline. One synthetic prescription (a made-up patient) was dropped.

| Step | What was seen |
|---|---|
| File dropped | `ls-s9-restart-887ffb02.png` |
| `pod-A` claims it | Moved to `processing` at 12:11:50 UTC |
| `pod-A` hard-killed | The file is still in `processing`; nothing in the inbox, `success` or `error` |
| `pod-B` started on the same folder | It left the file alone until the lease ran out, then read it; done 60 seconds after `pod-B` started |
| End state | File in `success`; its row `succeeded` with no lease owner; one job with one attempt; one document |

No file was lost and none was read twice.

### AC4 — the alert, live

The project's monitoring stack was installed on the instance (`infra/monitoring/start_monitoring.sh`; Prometheus and Alertmanager up, all scrape targets up). The listener on this PC was then stopped.

| Time (UTC) | Last good poll | `CdiListenerNotPolling` |
|---|---|---|
| 12:27:27 | 278 s ago | Not active |
| 12:27:59 | Just past 300 s | Firing |
| 12:28:27 | 338 s ago | Firing; in Alertmanager with severity `page`, receiver `oncall` |
| 12:29:51 | 13 s ago (listener started again) | Cleared |

| Also checked | What was seen |
|---|---|
| `python -m cdi_adapter.listener health` during the outage | `status NOT POLLING`, last good poll 384 s ago |
| The same command afterwards | `status OK` |
| `promtool test rules cdi-alerts.test.yml` on the instance | SUCCESS |
| The `oncall` receiver in `alertmanager.yml` | Has no email, webhook or pager |
| The banner's data on the instance, listener stopped a second time | `CdiListenerNotPolling` listed (severity `page`, since 12:46:29) with its summary, next to `CdiBackupStale` |
| The same, after the listener was started again | `CdiListenerNotPolling` gone |
| `GET /api/alerts/active` without signing in | HTTP 401 |
| The banner drawn in a browser (local preview with canned alerts) | A red line "ALERT: The file listener has not polled its folder for 5 minutes." and an amber line "Notice: A file is in quarantine." under the header |

## Test cases

### AC1 — a restart during a batch loses no file

| Test case | Steps | Expected | Result |
|---|---|---|---|
| `test_ac1_a_restart_during_a_batch_loses_no_file` | The listener dies in the middle of a batch | Every file is still in the folder tree and in the ledger | PASS |
| `test_ac1_a_new_pod_reads_each_unfinished_file_once_its_lease_has_expired` | A new listener looks before and after the lease runs out | Left alone before; each unfinished file read once after | PASS |
| `test_ac1_it_is_picked_up_on_a_later_look_not_only_the_first_one_after_the_restart` | The lease runs out several looks after the restart | The file is still picked up | PASS |
| `test_ac1_the_same_pod_coming_back_takes_its_own_files_at_once` | The listener comes back under the same worker name | It takes its own files without waiting for the lease | PASS |
| `test_ac1_a_file_another_live_worker_is_reading_is_left_alone` | Another worker holds a live lease | The file is not touched | PASS |
| `test_ac1_files_dropped_while_the_pod_was_down_are_read_when_it_is_back` | Files arrive while nothing is running | Read when the listener is back | PASS |
| `test_ac1_a_lost_database_never_leaves_the_listener_waiting_for_ever` | Read the connection settings | Every connection attempt has a time limit; one given in the address is kept | PASS |
| `test_ac1_the_state_a_restart_needs_is_in_the_database_and_the_folder` | Read the code | Nothing a restart needs is kept only in memory | PASS |
| `test_ac1_the_listener_and_the_retry_agent_start_by_themselves_with_the_pod` | Read `start_all.sh` and `start_listener.sh` | The retry agent always starts; the listener when `CDI_START_LISTENER=1` | PASS |

### AC2 — a stop during a batch lets the batch finish

| Test case | Steps | Expected | Result |
|---|---|---|---|
| `test_ac2_a_stop_during_a_batch_lets_the_batch_finish_then_the_loop_ends` | Ask for a stop while a batch is being read | The batch is finished and filed; then the loop ends | PASS |
| `test_ac2_no_new_batch_is_started_after_the_stop` | More files are waiting when the stop comes | They stay in the inbox | PASS |
| `test_ac2_the_wait_between_looks_ends_at_once_when_the_stop_comes` | Ask for a stop while the listener sleeps | It ends at once, not at the end of the wait | PASS |
| `test_ac2_a_stop_signal_sets_the_stop_and_does_not_kill_the_process` | Send the stop signal | The stop flag is set; the process is not killed | PASS |
| `test_ac2_the_run_command_and_the_retry_agent_both_stop_gently` | Read the command-line code | Both commands run under the gentle stop | PASS |
| `test_ac2_the_stop_script_asks_first_and_waits_for_the_batch` | Read `start_listener.sh` | It asks first, waits up to a lease, and only then kills | PASS |

### AC3 — the health view

| Test case | Steps | Expected | Result |
|---|---|---|---|
| `test_ac3_the_health_view_shows_the_five_things_the_story_names` | Read the health of a folder with files in each state | Last good poll, files waiting, oldest waiting, in error, in quarantine | PASS |
| `test_ac3_it_also_lists_the_files_being_ignored_with_the_reason` | A file of a type that is not allowed | Listed with its reason | PASS |
| `test_ac3_the_page_draws_each_of_them` | Read the Listener page | It shows each of the five | PASS |
| `test_ac3_the_api_and_the_page_are_for_staff_who_may_see_results` | Read the access rules | Same access as opening a result | PASS |
| `test_ac3_the_health_command_prints_the_same` | Run `listener health` | The same values in text | PASS |
| `test_ac3_a_folder_with_nothing_waiting_shows_no_age` | An empty inbox | No age for the oldest waiting file | PASS |
| `test_ac3_before_any_listener_has_looked_there_is_nothing_to_show` | No listener has ever run | The command says so plainly | PASS |

### AC4 — no good poll for 5 minutes raises an alert

| Test case | Steps | Expected | Result |
|---|---|---|---|
| `test_ac4_a_listener_that_has_not_polled_for_five_minutes_is_not_healthy` | Move the clock past 5 minutes with no look | Health says not polling | PASS |
| `test_ac4_a_look_that_fails_does_not_count_as_a_good_poll` | The drive cannot be reached | The time of the last good poll does not move | PASS |
| `test_ac4_there_is_an_alert_rule_for_it` | Read the alert rules | `CdiListenerNotPolling`, severity `page`, 300 seconds, with a runbook section | PASS |
| `test_ac4_the_rule_reads_the_time_of_the_last_good_look_from_the_database` | Read the metric collector | The metric comes from the heartbeat table | PASS |
| `test_ac4_a_host_with_no_listener_raises_no_alert` | No heartbeat rows | No series, so no alert | PASS |
| `test_ac4_writing_the_heartbeat_never_fails_a_look` | The heartbeat write raises | The look still reads its files | PASS |
| `test_ac4_the_alert_is_shown_to_the_staff_who_are_signed_in` | Ask for the alerts firing now, with the not-polling alert firing | Alertmanager is asked once; the alert comes back with its name, severity, start and summary | PASS |
| `test_ac4_the_summary_shown_is_the_one_the_rule_carries` | Read the rule | Its summary is the sentence the banner shows; its severity is one the banner shows | PASS |
| `test_ac4_every_page_carries_the_banner_and_asks_once_a_minute` | Build a page; read the route and the access rule | The banner is on every page, asks every 60 seconds, is drawn as text; any signed-in staff may read it | PASS |
| `test_ac4_pages_come_first_and_one_alert_is_one_line` | A page, a ticket, and the same page twice | Pages first; one line per alert, with its earliest start | PASS |
| `test_ac4_an_alert_silenced_in_alertmanager_is_not_shown` | A silenced alert; one of another severity | Neither is shown | PASS |
| `test_ac4_with_no_alertmanager_the_page_still_opens` | Alertmanager refuses; the setting is empty | An empty answer, no error | PASS |
| `test_ac4_alertmanager_is_asked_at_most_every_fifteen_seconds` | Four asks within 16 seconds | Alertmanager is asked twice | PASS |
| `test_ac4_nothing_can_be_posted_to_the_banner_from_outside` | Read the access rules and the setting | No route takes an alert in; Alertmanager is asked on loopback | PASS |
| `test_ac4_somebody_who_is_not_at_a_screen_is_told` | Read `alertmanager.yml` | The `oncall` receiver has a channel | **EXPECTED FAIL** — no email, chat or pager is configured |

### Non-functional requirements

| Test case | Expected | Result |
|---|---|---|
| `test_the_listener_itself_uses_no_model_and_no_gpu` | The listener's own code loads no model | PASS |
| `test_between_looks_the_listener_sleeps` | It waits between looks instead of spinning | PASS |

## Missing tests

| What | Why it is missing |
|---|---|
| A whole pod restarted | Only the listener process was killed. The instance itself was not rebooted |
| `start_all.sh` / `start_listener.sh` run on a pod | Never run; their text was read by a test. The listener has only ever been run from this PC |
| A gentle stop of a real process during a real batch | Unit tests only. On Windows the stop signal is not delivered the way it is on the pod |
| The Listener page opened in a browser on the instance | The command was run there; the page was checked for what it contains |
| The automated database test of the ledger (`tests/test_listener_ledger_integration.py`) | Skipped locally because no local database is running |
| The alert reaching a person who is not at a screen | Needs an email, chat or pager to be named |
| The banner seen by a signed-in person on the instance | The banner's data was read on the instance and the banner was drawn in a local preview; nobody signed in to the instance to look at it (the password is the owner's) |
| A kill after the job was accepted by the pipeline | In the real kill test `pod-A` died before its job was accepted, so the "job already running" path was not exercised on the instance |

## Issues found

1. **Nobody away from a screen is told.** A firing alert is shown in a banner to the staff who are signed in; with nobody signed in, nobody knows. The same is true of every alert in the project.
1. **The banner shows every firing alert to every signed-in member of staff,** the front desk included: on the instance that includes `CdiBackupStale`. It needs the monitoring stack to be running; without it the banner is empty and says nothing.
1. **Found and fixed: a listener that lost its database never looked again.** When the link to the database dropped, the listener sat in one connection attempt for ever (it has no time limit by default) and had to be restarted by hand. A stack dump of a probe listener showed it there. Every connection attempt now gives up after 10 seconds (`connect_args` in `src/cdi_adapter/db.py`; this applies to the web app and the workers too). Tried again for real: with the link cut the probe kept looking (four timed-out heartbeats in 75 seconds), and with the link back it reported `OK` within seconds, with no restart.
2. **Found and fixed during the manual check: duplicate metric series.** Two listeners on the same kind of drive would have given two series with the same labels. `LISTENER_BEATS_SQL` in `src/cdi_adapter/metrics.py` now gives one row per drive kind.
3. **A listener that is retired leaves its heartbeat row behind.** A folder nobody watches any more shows as "NOT POLLING" on the health view until its row is deleted. The alert uses the newest good poll across all listeners, so one live listener hides a dead one of another folder.
4. **A file held by a dead worker waits a full lease** (15 minutes by default) before another worker reads it.
5. **The history of a recovered file does not say it was taken over.** The row shows the batch and the move to `processing`, not which worker finished it.
6. **`docs/LISTENER.md` still says in two places that the health view is "not built yet"** (lines 109 and 329). The LS-S9 section of the same guide is correct.
7. **The test file's opening note says no real restart was done.** A real kill of the process has since been done by hand; a pod has still not been restarted.
8. **`CdiBackupStale` is firing on the instance.** No backup job runs there. It is not part of this story.
9. **Left on the instance:** the monitoring stack is running, and one more synthetic test document ("Test Patient Restart") is in the workbasket with the four earlier ones.
10. **Not pushed.** Migration 0034 has run on the instance only.
