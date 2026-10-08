# Test document — LS-S2

**Epic:** LS — File listener (watched folder)
**Story ID:** LS-S2
**Title:** Sign in to the drive safely (no password handled by us)
**Checked on:** 2026-10-08, against the local working tree (branch `dev_chakravardhan`, with uncommitted changes)
**Test file:** `tests/test_listener_auth_unit.py`
**History:** nothing for this story existed before 2026-10-08 (only a bearer token pasted into settings). The sign-in was built that day in `src/cdi_adapter/listener/auth.py`, with these tests.
**Run:** `python -m pytest tests/test_listener_auth_unit.py -v` → **55 passed, 2 expected-fail**
**Run with the other listener, logging and secret-scan suites:** **271 passed, 3 skipped, 4 expected-fail** (the 3 skipped and the other 2 expected-fail belong to other stories)

## Result

| AC | What it asks | Result |
|---|---|---|
| AC1 | A device-code sign-in was done once, the pod restarts → the listener starts without asking for a code again (token cache on the persistent disk) | **PASS against a stand-in for Microsoft's sign-in service.** Not run on a real Microsoft account or a real pod |
| AC2 | The sign-in has expired, the listener polls → it stops, writes the reason to its log and raises an alert instead of retrying forever | **PASS for stopping, logging and raising the alert. PARTIAL for delivery:** the alert is a critical log line and an alert file; paging a person waits for ENT-S4 |
| AC3 | A failure note and the listener log are searched for tokens, secrets and passwords → none are found | **PASS** |
| AC4 | The Entra app registration is reviewed, its permissions listed → only delegated `Files.ReadWrite` or `Sites.Selected`, nothing broader | **PASS in code** (anything broader is refused and not stored). **The review itself has not been done: there is no Entra app registration yet** |

**AC3 passes. AC1 passes at the level that can be tested without a real Microsoft account. AC2 and AC4 each have one part that is outside this code: alert delivery (ENT-S4) and the review of a real app registration.**

## How the tests work

- **Microsoft:** the real MSAL library and the real sign-in class talk to an in-memory stand-in for Microsoft's sign-in service, handed to MSAL through its own HTTP hook. The device-code sign-in, the token cache file, quiet renewal and a refused renewal are MSAL's real behaviour; only the far end is a stand-in.
- **Google:** the real service-account class signs a request with a key made in the test; a stand-in for Google's token service checks the signature.
- **The drives** are the same in-memory stand-ins for Microsoft Graph and Google Drive used by the LS-S1 tests. The local drive is a real temporary folder.
- **No real Microsoft or Google account is called by any test.** No real token cache, alert file or `.env` sign-in setting is read or written: `tests/conftest.py` points them all at a temporary folder.
- A gap is written as an expected failure (`xfail`, strict): the suite stays green, the gap stays on record, and the test fails loudly the day the gap is closed until the mark is removed.

## Test cases

### AC1 — signed in once, restart → no code asked again

| Test case | Steps | Expected | Result |
|---|---|---|---|
| `test_ac1_the_first_sign_in_shows_an_address_and_a_code` | Run the one-time sign-in | A web address and a short code are shown; "No password is typed here"; signed in as the person | PASS |
| `test_ac1_the_token_cache_is_written_to_the_path_in_settings` | Sign in; look at the path in `CDI_GRAPH_TOKEN_CACHE` | The cache file is there and holds the renewable token; the default path is `/workspace/.cdi/graph_token_cache.json` | PASS |
| `test_ac1_after_a_restart_no_code_is_asked_for_again` | Sign in; start a new sign-in object with only the file on disk; ask for a token | A token is returned; no device code was requested | PASS |
| `test_ac1_after_a_restart_the_listener_starts_and_reads_a_file` | Sign in; "restart"; start the listener on a OneDrive stand-in holding one file | The file is read and moved to `success`; no code asked | PASS |
| `test_ac1_a_token_that_has_run_out_is_renewed_quietly` | Sign in with a short-lived token; ask for a token twice after a restart | Renewed each time without a code; the renewed sign-in is written back to the cache file | PASS |
| `test_ac1_with_no_cache_file_it_says_to_run_the_login_command` | Ask for a token when nobody has signed in | "Sign-in needed … run `python -m cdi_adapter.listener login`" (missing, not expired) | PASS |

### AC2 — sign-in expired → stop, log the reason, raise an alert, do not retry forever

| Test case | Steps | Expected | Result |
|---|---|---|---|
| `test_ac2_an_expired_sign_in_ends_the_pass` | Microsoft refuses the renewal; the listener looks at the folder | The pass ends with "sign-in has expired"; the waiting file is not moved to `error` | PASS |
| `test_ac2_it_is_not_tried_again_and_again` | Same, with the long-running loop | Microsoft is asked once, refuses once, the loop stops | PASS |
| `test_ac2_a_sign_in_lost_half_way_through_a_file_stops_too` (×2: while moving, while downloading) | The sign-in dies in the middle of taking a file | The listener stops; nothing is filed under `error`, no note in `log` | PASS |
| `test_ac2_the_run_command_stops_logs_the_reason_and_raises_an_alert` | Run `run --once` with an expired sign-in | Exit 2; prints `LISTENER STOPPED` and `SIGN-IN EXPIRED`; log line `listener_stopped` with Microsoft's reason; critical log line `listener_alert`; alert file written with the reason and what to do | PASS |
| `test_ac2_a_clean_start_takes_the_old_alert_away` | An alert file exists; the listener starts with a working sign-in | Exit 0; the alert file is gone | PASS |
| `test_ac2_the_check_command_says_expired_not_just_missing` | Run `check` with an expired sign-in | Exit 2; `SIGN-IN EXPIRED:` | PASS |
| `test_ac2_the_alert_reaches_a_person` | Look for a `ListenerSignInExpired` rule in the alert rules | It exists | **EXPECTED FAIL — waits for ENT-S4** |

### AC3 — no tokens, secrets or passwords in a failure note or the listener log

| Test case | Steps | Expected | Result |
|---|---|---|---|
| `test_ac3_a_failure_note_holds_no_token_secret_or_password` | A file fails with an error message full of secrets (bearer token, sign-in token, client secret, refresh token, password, pre-signed address) | The note in `log` keeps the reason, with every secret replaced by `[removed]` | PASS |
| `test_ac3_the_listener_log_holds_no_token_secret_or_password` | Same failure through the `run` command; search everything it printed | The failure is logged; none of the secrets appear | PASS |
| `test_ac3_every_line_the_listener_logs_is_scrubbed` | Log a line with a token in the text and one inside a nested value | Both removed; ordinary values kept | PASS |
| `test_ac3_signing_in_and_expiring_leave_nothing_secret_in_the_output` | Sign in, expire, run; search the output and the alert file | No token anywhere except the cache file | PASS |
| `test_ac3_no_password_is_ever_sent_asked_for_or_stored` | Look at every form sent to Microsoft, the cache file, the code and the settings | No password or username field; no password sign-in; no password prompt in the code; no password setting | PASS |
| `test_ac3_secret_settings_do_not_show_when_settings_are_printed` | Print settings holding a client secret and a token | Neither is shown | PASS |
| `test_ac3_what_scrub_takes_out` (×8) | Bearer token, sign-in token, `access_token`, `client_secret`, `password`, pre-signed address, private key, Google token | Each is removed | PASS |
| `test_ac3_scrub_leaves_ordinary_text_alone` | An ordinary failure reason; a known secret value | Ordinary text unchanged; the known value removed | PASS |
| `test_ac3_sign_in_material_is_kept_out_of_git` | Read `.gitignore` and the two example settings files | `/secrets/`, `.cdi/`, token caches, service-account keys and `.env` are ignored; the examples hold no secret value | PASS |

### AC4 — only delegated Files.ReadWrite or Sites.Selected, nothing broader

| Test case | Steps | Expected | Result |
|---|---|---|---|
| `test_ac4_a_persons_sign_in_asks_for_files_readwrite_and_nothing_else` | Sign in; look at what was asked of Microsoft | `Files.ReadWrite` plus the sign-in basics only | PASS |
| `test_ac4_a_sharepoint_sign_in_asks_for_sites_selected` | Sign in with the SharePoint drive | `Sites.Selected` | PASS |
| `test_ac4_the_permissions_the_sign_in_carries_are_listed` | Sign in; ask who is signed in | Permissions listed: `Files.ReadWrite` | PASS |
| `test_ac4_a_sign_in_with_anything_broader_is_refused_and_not_kept` (×5: `Files.ReadWrite.All`, `Sites.ReadWrite.All`, `Mail.Read`, `User.Read`, `Files.Read.All`) | Microsoft grants a broader permission | Refused, naming the extra permission; no cache file written | PASS |
| `test_ac4_a_service_identity_may_only_have_sites_selected` | App-only sign-in with `Sites.Selected`; then with `Files.ReadWrite.All` added | First works; second refused by name | PASS |
| `test_ac4_the_allowed_list_is_exactly_the_two` | Read the allowed list | Exactly `Files.ReadWrite` and `Sites.Selected` | PASS |
| `test_ac4_the_app_registration_has_been_reviewed` | Look for the filled-in review record in `docs/LISTENER.md` | "Permissions listed" is filled in | **EXPECTED FAIL — no app registration exists to review** |

### Functional requirements beyond the ACs

| Test case | Steps | Expected | Result |
|---|---|---|---|
| `test_app_only_sign_in_with_a_secret_keeps_nothing_on_disk` | Service sign-in with a secret file | Token obtained; nothing cached on disk | PASS |
| `test_app_only_sign_in_needs_a_key` | Service sign-in with no certificate or secret | "Sign-in needed", naming the settings | PASS |
| `test_a_refused_service_sign_in_stops_the_listener_like_an_expired_one` | Microsoft says the secret has expired | Treated as an expired sign-in | PASS |
| `test_a_wrong_setting_is_said_in_plain_words` | `CDI_GRAPH_AUTH=password`; no tenant id | Refused in plain words | PASS |
| `test_google_service_account_gets_a_token_with_its_key` | Sign in with a service-account key | Signed request accepted; token returned | PASS |
| `test_google_token_is_kept_until_shortly_before_it_runs_out` | Ask twice; then a minute before expiry | One request, then a quiet renewal | PASS |
| `test_google_drive_is_listed_with_the_service_account` | Run the check on a Google Drive stand-in with the service account | Folder listed; service account chosen by settings | PASS |
| `test_a_refused_google_key_is_an_expired_sign_in_and_shows_no_key` | Google refuses the key | Expired sign-in; no key material in the message | PASS |
| `test_a_missing_or_damaged_google_key_file_is_said_in_plain_words` | No key file; a file that is not a service-account key | "Sign-in needed" in plain words | PASS |
| `test_google_describe_says_which_address_to_share_the_folder_with` | Ask who is signed in | The account's address; no key | PASS |
| `test_the_login_command_does_the_one_time_sign_in` | Run `login` | Exit 0; address and code shown; who is signed in and with what; no token printed | PASS |
| `test_the_login_command_on_a_local_folder_says_none_is_needed` | Run `login` with the local drive | "none needed for this drive" | PASS |
| `test_the_login_command_refuses_a_too_broad_sign_in` | Run `login` when Microsoft grants `Files.ReadWrite.All` | Exit 1; `NOT SIGNED IN`, naming the permission | PASS |
| `test_the_check_command_says_who_is_signed_in` | Run `check` when signed in | Exit 0; a `sign-in` line with the person and permission | PASS |
| `test_the_check_command_says_when_nobody_has_signed_in` | Run `check` before any sign-in | Exit 2; `SIGN-IN NEEDED`, pointing to `login` | PASS |
| `test_a_pasted_test_token_still_works_and_needs_no_sign_in` | Put a test token in settings | It is used as before | PASS |

Also done by hand on 2026-10-08: `python -m cdi_adapter.listener login` on this PC's local folder → "none needed for this drive", exit 0.

## Missing tests

| What | Why it is missing |
|---|---|
| AC1 on a real Microsoft account and a real pod restart | No Entra app registration (client id, tenant id) exists yet |
| AC2 with a sign-in that has really expired | Same; a real expiry cannot be made to order |
| AC2: a person actually being alerted | Alert delivery (Alertmanager rule and route) waits for ENT-S4 |
| AC4: the real app registration's permissions listed | No app registration to review |
| Service sign-in with a certificate | Only the secret path is tested; the certificate path is written but not exercised |
| Google Drive with a real service account | No Google service account or shared folder |
| Google OAuth for a person | Not built (only the service account) |

## Issues found

1. **No real sign-in has ever been done.** Every Microsoft and Google result here is against a stand-in. Anything the stand-ins get wrong about the real services will only show with a real app registration and service account.
2. **The alert does not reach a person yet.** It is a critical log line and a file (`CDI_LISTENER_ALERT_FILE`). Paging waits for ENT-S4.
3. **The Entra review (AC4) is not done.** The code refuses anything broader than the two permissions, but the review of the registration is a person's job; a table to record it is in `docs/LISTENER.md`.
4. **A default app registration will be refused.** Entra adds `User.Read` to new registrations; the listener treats that as "broader" and refuses the sign-in until it is removed. This is deliberate, to meet "nothing broader".
5. **A service identity only works with SharePoint.** For a person's OneDrive it would need `Files.ReadWrite.All`, which the story does not allow.
6. **Not installable on the pod yet.** MSAL and PyJWT are in a new `listener` extra in `pyproject.toml` and installed in the local environment, but not in the pod's hash-locked requirements file.
7. **Google OAuth for a person is not built.**
8. **Nothing stops a supervisor from restarting the listener.** The listener exits with status 2 and does not retry; if something is later set up to restart it automatically, that must not restart on status 2.
