# Test document — IM-S1

**Epic:** IM — Image preparation (OpenCV)
**Story ID:** IM-S1
**Title:** Check every picture first (blur, glare, dark, small, sideways)
**Last updated:** 2026-10-08 (after AC2, AC3 and AC4 were dealt with)
**Checked on:** 2026-10-08, against the local working tree (branch `dev_chakravardhan`, with uncommitted changes)
**Test file:** `tests/test_im_s1_acceptance_unit.py`
**History:** the first check changed no application code and found AC3 unbuilt, AC2 off by default and a defect in AC4 (28 passed, 8 expected-fail). All three were then dealt with the same day.
**Run:** `python -m pytest tests/test_im_s1_acceptance_unit.py -v` → **51 passed, 1 expected-fail**

## Result

| AC | What it asks | Result |
|---|---|---|
| AC1 | A sharp page passes and all measurements are saved | **PASS** |
| AC2 | A blurred page is held with reason "too blurry" | **PASS** — holding is now on by default |
| AC3 | The answer-key set is run and the false-reject rate is reported with the thresholds used | **PASS for the runner and the report; the real rate is still missing** — there is no answer-key set of real photographs |
| AC4 | A high-resolution photo taken from far away is held with "move closer" even though the file is large | **PASS** (was a defect; fixed 2026-10-08) |

**AC1, AC2 and AC4 pass. AC3's tool is built and tested, but the number it exists to produce cannot be produced until someone supplies labelled real photographs — that one test stays an expected failure.**

## What changed in the code

| AC | Change | Where |
|---|---|---|
| AC2 | The picture check is **on by default** (`CDI_QUALITY_CHECK`, was off). `CDI_QUALITY_CHECK=false` switches it off. | `config.py` |
| AC3 | A runner that takes an answer key (which pictures a person can read), runs the check on each and reports the false-reject rate, the false-accept rate, the reasons, the pictures and the thresholds used. | `recognition/quality_eval.py`, `scripts/quality_answer_key.py` |
| AC4 | The check now also measures sharpness **where the writing is**. Tiny writing that is sharp there is called "move closer" before the whole-frame "blank" and "blurry" checks are reached. One new limit, `CDI_QUALITY_MIN_WRITING_SHARPNESS` (500). | `recognition/quality.py`, `config.py` |

## How the tests work

The real picture check runs on pictures built in the test: a rendered twelve-line prescription, a blurred copy, and 12-megapixel "far away" frames with the page a fifth of its size in the middle. "Saved" is proved by running S1's own render-and-store step with the database and object store replaced by recorders. The answer-key runner is run on a five-picture key written by the test.

## Test cases

### AC1 — a sharp page passes and all measurements are saved

| Test case | Steps | Expected | Result |
|---|---|---|---|
| `test_ac1_a_sharp_page_passes` | Check a sharp page | Passes; nothing to report | PASS |
| `test_ac1_every_measurement_is_taken` | Measure it | All measurements present (size, sharpness, ink, soft ink, glare, paper tone, text height, sideways flag) | PASS |
| `test_ac1_the_measurements_are_saved_with_the_page` | Run S1's store step | The page row carries the measurements, the verdict and the limits | PASS |
| `test_ac1_what_is_saved_is_plain_data_the_database_can_hold` | Round-trip what is saved through JSON | Unchanged | PASS |
| `test_ac1_the_measurements_are_of_the_page_as_uploaded` | Read the store step | Measured on the uploaded page, not the cleaned-up copy | PASS |
| `test_ac1_measurements_are_saved_even_while_holding_is_switched_off` | Store a page with `CDI_QUALITY_CHECK=false` | Measurements saved; marked "not enforced" | PASS |
| `test_ac1_a_page_that_fails_is_saved_too` | Store a blurred page | Saved as failed/blurred; the page itself is still stored | PASS |

**Limit:** that the row really lands in PostgreSQL is `tests/test_recognition_integration.py`, written but never run (no local database).

### AC2 — a blurred page is held with reason "too blurry"

| Test case | Steps | Expected | Result |
|---|---|---|---|
| `test_ac2_a_blurred_page_is_held` | Check a blurred page | Held, reason code `blurred` | PASS |
| `test_ac2_the_reason_given_is_too_blurry` | Read the message | Contains "too blurry"; sharpness is below the limit | PASS |
| `test_ac2_it_is_called_blurry_however_strong_the_blur` (4 cases) | Blur at four strengths | "blurred" every time, never "blank" | PASS |
| `test_ac2_the_same_page_sharp_is_not_held` | Check the sharp original | Passes | PASS |
| `test_ac2_the_upload_job_holds_it_before_anything_is_read` | Read the job runner | The page is held before ingest | PASS |
| `test_ac2_holding_is_on_by_default` | Check a blurred page with **no setting at all** | Held, "too blurry"; recorded as enforced | PASS |
| `test_ac2_it_can_still_be_switched_off` | Check with `quality_check=False` | Not held; still recorded as blurred | PASS |
| `test_ac2_the_default_is_stated_where_an_operator_will_read_it` | Read the settings file | The default and how to switch it off are written there | PASS |

**What turning it on means.** Every upload is now checked, with limits that are starting values, not tuned on real photographs. A picture under 700 px on its short side, or one the limits misjudge, will be held and the person asked to retake it. Watch the weekly retake report (`scripts/quality_report.py`), and set `CDI_QUALITY_CHECK=false` if good pictures are being turned away.
The full message is "This photo is too blurry to read. Hold the phone still, let it focus, and take it again."

### AC3 — false-reject rate on the answer-key set, with the thresholds used

| Test case | Steps | Expected | Result |
|---|---|---|---|
| `test_ac3_every_saved_record_carries_the_thresholds_it_was_judged_with` | Read a saved record | The eight limits are saved with it | PASS |
| `test_ac3_a_page_can_be_rejudged_under_other_thresholds_from_what_was_saved` | Re-judge saved measurements under a stricter limit | The verdict changes, without the picture | PASS |
| `test_ac3_the_answer_key_run_reports_the_false_reject_rate` | Run a key of 3 readable and 2 unreadable pictures | 0 of 3 readable held (0 %), 0 of 2 unreadable let through | PASS |
| `test_ac3_the_report_states_the_thresholds_used` | Read the report; run again with another limit | The report lists the limits it was judged with | PASS |
| `test_ac3_a_limit_set_too_high_shows_up_as_false_rejects` | Raise the sharpness limit to 5000 | 3 of 3 readable held (100 %), reason "blurred", the three pictures named | PASS |
| `test_ac3_a_limit_set_too_low_shows_up_as_false_accepts` | Lower three limits to 0 | False-reject 0 %, and unreadable pictures let through are counted | PASS |
| `test_ac3_the_rate_is_given_by_document_type` | Read the per-type table | A rate for each document type | PASS |
| `test_ac3_the_run_judges_even_where_holding_is_switched_off` | Run with the check off | Still judged | PASS |
| `test_ac3_the_counting_is_right_on_its_own` | Count 8 readable (2 held) and 2 unreadable (1 let through) | 25 % false-reject, 50 % false-accept; nothing sent → no rate | PASS |
| `test_ac3_there_is_a_command_that_runs_the_check_over_an_answer_key` | Run the command, as text and as JSON | Prints the rate, the thresholds and the per-type table | PASS |
| `test_ac3_the_report_names_the_pictures_it_wrongly_held` | Print a report with false rejects | Names each picture and its reason | PASS |
| `test_ac3_there_is_an_answer_key_set_of_real_photographs_to_run` | Look for a real labelled set under `data/` | It exists | **FAIL** — there is none |

**How to run it**

```
python scripts/quality_answer_key.py --key path/to/answer_key.json
python scripts/quality_answer_key.py --key path/to/answer_key.json --json
```

The key is a JSON list, with paths relative to the key file:

```json
[{"image": "photos/rx_001.jpg", "readable": true,  "doc_type": "prescription"},
 {"image": "photos/rx_002.jpg", "readable": false, "doc_type": "prescription"}]
```

**Output on the three sample scans** (run on 2026-10-08; these are clean scans, not photographs, so the rate says nothing about the limits):

```
Picture check on the answer key: 3 picture(s)

  readable pictures          3
  held by the check          0
  FALSE-REJECT RATE          0.0 %

  unreadable pictures        0
  let through by the check   0
  false-accept rate          -

  Thresholds used
    min_side_px             700
    min_sharpness           20.0
    min_ink                 0.0005
    max_ink                 0.9
    min_text_px             8.0
    max_glare               0.05
    paper_white             250.0
    min_writing_sharpness   500.0
```

**What is still missing for AC3:** labelled real photographs (ENT-S3). Without them the false-reject rate the criterion asks for does not exist. The sample key used above lives in `data/samples/`, which is not in git.

### AC4 — a far-away, high-resolution photo is held with "move closer"

| Test case | Steps | Expected | Result |
|---|---|---|---|
| `test_ac4_the_far_away_photos_really_are_large` | Measure two 4000 × 3000 far-away frames | 12 megapixels, not "too small", writing under 8 px | PASS |
| `test_ac4_the_tiny_writing_is_measured` | Compare text height far and near | About 4 px far away, 12 px or more close | PASS |
| `test_ac4_a_far_away_photo_is_never_let_through` | Check both frames | Both held | PASS |
| `test_ac4_a_page_full_of_writing_is_told_to_move_closer` | Check a dense page far away | Held, "Move closer…" | PASS |
| `test_ac4_sharpness_alone_would_have_let_it_through` | Remove the text rule and re-judge | The picture would pass | PASS |
| `test_ac4_the_same_page_filling_the_frame_is_not_held` | Check the page close up | Passes | PASS |
| `test_ac4_the_limit_is_a_setting` | Read the setting | 8 px by default, changeable | PASS |
| `test_ac4_the_tiny_text_reading_is_saved_like_any_other` | Store the far-away page | Saved as `text_too_small` | PASS |
| `test_ac4_a_prescription_photographed_from_far_away_is_told_to_move_closer` | Check the 12-line prescription far away | "Move closer" | PASS (was FAIL: "No writing could be found") |
| `test_ac4_it_is_told_to_move_closer_at_every_distance` (4 cases) | The prescription at 15 %, 20 %, 25 %, 30 % of the frame | "Move closer" each time | PASS (was FAIL: "blank" / "too blurry") |
| `test_ac4_the_picture_is_measured_where_the_writing_is` | Compare whole-frame and in-the-writing numbers | Too little ink by the frame; plainly sharp inside the writing | PASS |
| `test_ac4_a_far_away_photo_that_is_also_blurred_is_still_called_blurry` | Blur the far-away photo | Not "move closer" — the fault is focus | PASS |
| `test_ac4_a_blurred_close_up_page_is_never_told_to_move_closer` (4 cases) | Blur the close-up page at four strengths | "blurred" every time | PASS |
| `test_ac4_a_blank_page_is_still_blank` | Check a blank page | "blank" | PASS |
| `test_ac4_a_page_with_only_a_line_or_two_is_not_told_to_move_closer` | Check a two-line page close up | Not "move closer" | PASS |
| `test_ac4_the_new_limit_is_a_setting_and_is_saved_with_every_record` | Read the setting and a saved record | 500 by default; saved with each record | PASS |

**The defect and the fix.** Ink share and sharpness were measured over the whole frame and judged before text height. A sparse page in a large frame has about 0.014 % ink (minimum 0.05 %), so it was called blank before the text rule was reached. Now the check also measures sharpness inside the box that holds the writing:

| Picture | Sharpness over the frame | Sharpness where the writing is |
|---|---|---|
| Prescription far away (15–30 % of the frame) | 11–84 | 12,800–26,000 |
| Same, blurred | 0.1–1 | 57–330 |
| Close-up page, blurred | 0.2–18 | 2–156 |
| Sample scans | 546–571 | 747–791 |

Tiny writing that is sharp where it is (500 or more) is called "move closer"; a picture that is not sharp there is still "too blurry". Nothing changes for a picture whose writing is of normal size.

## Missing tests and issues

1. **AC3 has no real answer-key set.** The runner works; the rate it is for cannot be produced until labelled real photographs exist. One test stays expected-fail (strict) for this.
2. **The check is now on with untuned limits.** It will hold pictures the limits misjudge. The limits were measured on rendered pages and sample scans only.
3. **All pictures in these tests are synthetic** — rendered pages on a white background. A real far-away photo has a table round the page and uneven light; the writing box is found against the paper tone, which may be wrong when the paper is a small part of the picture. Not known until real photographs are tried.
4. **The new limit (500) comes from the measurements in the table above**, not from real photographs.
5. **Tests that feed placeholder bytes through the job runner now run with the check off** (`tests/conftest.py`), since their "files" are not pictures. Eight orchestration suites are affected; what they test is unchanged.
6. **Database-backed tests were not run.** That measurements land in PostgreSQL, and that the integration suites still pass with the check on by default, has not been run.
7. **Not deployed.** The test instance was unreachable on 2026-10-08.

## Summary

- **Passed:** AC1, AC2, AC4, and AC3's runner and report — 51 of 52 acceptance tests.
- **Failed:** one test — AC3's real answer-key set does not exist, so the false-reject rate on real photographs cannot be reported.
- **Changed behaviour:** the picture check is now on by default.
- **Missing:** labelled real photographs, limits tuned on them, a run of the database suites, and deployment.
