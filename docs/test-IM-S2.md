# Test document — IM-S2

**Epic:** IM — Image preparation (OpenCV)
**Story ID:** IM-S2
**Title:** Straighten and crop the page safely, keeping the original untouched
**Checked on:** 2026-10-08, against the local working tree (branch `dev_chakravardhan`, with uncommitted changes)
**Test file:** `tests/test_im_s2_acceptance_unit.py` (new; **no application code was changed for this story**)
**Run:** `python -m pytest tests/test_im_s2_acceptance_unit.py -v` → **44 passed**

## Result

| AC | What it asks | Result |
|---|---|---|
| AC1 | A page photographed 12° tilted comes out with level lines; the original file is unchanged | **PASS with straightening switched on.** By default only a flat scan is deskewed; a tilted phone photo is stored as taken |
| AC2 | A sideways page is rotated upright and the rotation is recorded | **PASS with straightening switched on.** By default it is stored sideways |
| AC3 | A box found on the straightened page maps back onto the same ink in the original | **PASS** — but no stage of the pipeline uses the mapping yet |
| AC4 | When the page edges cannot be found, no change is made and the page is held for retake | **PASS with straightening switched on.** By default it is not held |
| AC5 | An upside-down page is turned upright and the rotation is recorded | **PASS with straightening switched on**, for a page with a clear left margin. By default it is stored upside down |

**All five behave as the story asks when `CDI_STRAIGHTEN=true`. That switch is off by default, so on a default installation only AC3 and the flat-scan half of AC1 are in effect.** The 44 tests pass because each default-off behaviour is pinned by a test of its own rather than hidden.

## How the tests work

The real geometry code, S1's real page step and the real job runner are used, on pictures built in the test: a rendered prescription, and "phone photographs" of it — grey paper on a dark table, tilted, with sensor noise. Each criterion is checked with the switch on and, where it matters, with the default settings. **No real phone photograph is in the repository; every photograph here is simulated.**

## Test cases

### AC1 — a 12° tilted photo comes out level; the original is unchanged

| Test case | Steps | Expected | Result |
|---|---|---|---|
| `test_ac1_a_photo_tilted_12_degrees_comes_out_with_level_lines` | Photograph the page tilted 12°; straighten it | Going in, the gaps between lines are smeared shut; coming out, they are back to those of a straight photo | PASS |
| `test_ac1_it_works_whichever_way_the_page_was_tilted` (3 cases) | Tilt −12°, 5°, 12° | Level each time; not turned as well | PASS |
| `test_ac1_the_picture_handed_in_is_not_changed` | Compare the input before and after | Identical; a new picture is returned | PASS |
| `test_ac1_the_table_is_cropped_away_and_the_page_keeps_its_shape` | Look at the result | Page proportions kept; no table left round it | PASS |
| `test_ac1_the_original_file_is_stored_under_its_own_key_and_never_rewritten` | Read the ingest code | The upload goes to its own key; the straightened copy to another | PASS |
| `test_ac1_in_s1_with_the_switch_on_the_stored_page_is_the_straightened_one` | Run S1's page step with the switch on | The stored page is cropped; the uploaded bytes are unchanged | PASS |
| `test_ac1_a_crooked_flat_scan_is_straightened_even_with_the_switch_off` | Run S1's page step, default settings, on a scan tilted 12° | Deskewed by 12° | PASS |
| `test_ac1_by_default_a_tilted_phone_photo_is_not_straightened` | Run S1's page step, default settings, on the tilted photo | Not cropped, not straightened | PASS (records the issue) |

### AC2 — a sideways page is turned upright and the turn recorded

| Test case | Steps | Expected | Result |
|---|---|---|---|
| `test_ac2_a_sideways_page_is_turned_upright` (2 cases) | Turn the page a quarter either way; straighten | Exactly the upright page comes back | PASS |
| `test_ac2_the_turn_is_recorded_with_the_page` | Read the record | Degrees turned, the step, the 3×3 matrix and the original size | PASS |
| `test_ac2_in_s1_with_the_switch_on_the_turn_is_in_what_is_saved` | Run S1's page step with the switch on | The saved page is upright; the turn is in what is saved with it | PASS |
| `test_ac2_a_sideways_phone_photo_is_cropped_and_turned` | Sideways photo on a table | Cropped, then turned upright | PASS |
| `test_ac2_an_upright_page_is_not_turned` | Straighten an upright page | Left alone | PASS |
| `test_ac2_by_default_a_sideways_page_is_stored_sideways` | Run S1's page step, default settings | Not turned | PASS (records the issue) |

### AC3 — a box on the straightened page maps back to the same ink

| Test case | Steps | Expected | Result |
|---|---|---|---|
| `test_ac3_a_box_on_the_straightened_page_lands_on_the_same_ink` (4 cases) | Tilt, photograph, turn 0/90/180/270°, straighten; carry the first line's box back | Within 6 px of where that line really is in the photograph | PASS |
| `test_ac3_the_mapped_box_holds_as_much_ink_as_the_box_it_came_from` | Count ink inside the mapped box | The same share as in the straightened box | PASS |
| `test_ac3_there_and_back_is_the_same_point` | Map points to the original and forward again | The same points | PASS |
| `test_ac3_the_matrix_that_is_saved_is_enough_to_map_back` | Map with the matrix S1 saved | Same answer as with the live one | PASS |
| `test_ac3_a_page_that_was_not_changed_maps_to_itself` | Straighten an upright page | Identity | PASS |
| `test_ac3_the_old_deskew_now_saves_its_matrix_too` | Deskew a scan with the switch off | A matrix is saved | PASS |
| `test_ac3_a_crop_can_leave_a_sliver_of_table_along_one_edge` | Look at the last two pixel columns of a cropped page | Some dark pixels there; the page itself is paper | PASS (records the observation) |
| `test_ac3_nothing_in_the_pipeline_maps_evidence_boxes_back_yet` | Read the evidence, result and OCR code | None of them uses the mapping | PASS (records the issue) |

### AC4 — edges not found: no change, held for retake

| Test case | Steps | Expected | Result |
|---|---|---|---|
| `test_ac4_a_page_running_out_of_the_picture_is_held` | Photograph with half the page out of shot | Held: "edges not found" | PASS |
| `test_ac4_no_change_is_made_to_it` | Compare before and after | Identical picture, identity matrix, not turned | PASS |
| `test_ac4_the_person_is_told_in_plain_words_to_retake_it` | Read the message | "The edges of this page could not be found… take it again" | PASS |
| `test_ac4_the_upload_job_holds_the_page_and_reads_nothing_from_it` | Send it through the job runner, switch on | Held on its row; not ingested; no stage runs | PASS |
| `test_ac4_a_good_page_in_the_same_job_goes_on` | Send a good page with it | The good page finishes; only the cut-off one is held | PASS |
| `test_ac4_a_page_that_fills_the_frame_has_no_edges_to_find_and_is_not_held` | Straighten a scan | Not held | PASS |
| `test_ac4_a_sideways_page_that_does_not_say_which_way_is_up_is_held_too` | Sideways page with every line the same length | Held: "orientation unsure"; unchanged | PASS |
| `test_ac4_by_default_a_cut_off_page_is_not_held` | Send the cut-off photo with default settings | Goes on, cut off as it is | PASS (records the issue) |

### AC5 — an upside-down page is turned upright and the turn recorded

| Test case | Steps | Expected | Result |
|---|---|---|---|
| `test_ac5_an_upside_down_page_is_turned_upright` | Turn the page 180°; straighten | Exactly the upright page | PASS |
| `test_ac5_the_half_turn_is_recorded` | Read the record | 180°, and that it was found upside down | PASS |
| `test_ac5_in_s1_with_the_switch_on_the_half_turn_is_in_what_is_saved` | Run S1's page step with the switch on | The turn is in what is saved | PASS |
| `test_ac5_an_upside_down_phone_photo_is_cropped_and_turned` | Upside-down photo on a table | Cropped, then turned | PASS |
| `test_ac5_pages_of_one_upload_turned_differently_are_each_put_right` | Three pages: upright, upside down, sideways | Each one upright | PASS |
| `test_ac5_capital_letters_do_not_fool_it` | A page written in capitals, upside down | Turned upright | PASS |
| `test_ac5_a_page_with_no_clear_margin_is_left_upside_down` | Every line the same length, upside down | Left as it is; not held | PASS (records the issue) |
| `test_ac5_by_default_an_upside_down_page_is_stored_upside_down` | Run S1's page step, default settings | Not turned | PASS (records the issue) |

## Missing tests and issues

1. **Off by default.** `CDI_STRAIGHTEN` is false, so on a default installation a sideways or upside-down page is stored that way, a tilted phone photo is not cropped or straightened, and a cut-off page is not held. Only the old small-tilt deskew for flat scans runs. Five tests pin exactly this.
2. **The mapping (AC3) is proven but unused.** The matrix is saved with every page and the two mapping functions work, but no stage carries an evidence box back to the original upload. Evidence is shown on the stored, straightened page.
3. **An upside-down page without a clear left margin is not turned** (every line the same length, a centred or fully justified page). It is left alone and not held, so it goes on upside down.
4. **A crop can leave a sliver of table one or two pixels wide along one edge** of the stored page. It moved no box in these tests, but it is dark pixels that are not writing.
5. **Every photograph is simulated.** The story's own proof asks for before/after of five real phone photographs; there are none in the repository. Real photographs (shadows, curled paper, a pale table) are untested.
6. **The page must be lighter than what it lies on.** White paper on a white table has no edge to find; it is treated as a page that fills the frame.
7. **No database today.** That the transform lands in PostgreSQL with the page was not run.
8. **Not deployed.** The test instance was unreachable on 2026-10-08.

## Summary

- **Passed:** AC1, AC2, AC3, AC4 and AC5 as functions and through S1's page step and the job runner, **with straightening switched on** — 44 of 44 tests.
- **Failed:** none of the tests. But measured against a default installation, AC2, AC4 and AC5 do not happen and AC1 happens only for flat scans, because the switch is off.
- **Missing:** a decision to switch it on, real phone photographs, the mapping being used by a stage, and handling of an upside-down page with no clear margin.
