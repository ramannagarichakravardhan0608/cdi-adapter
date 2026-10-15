# The answer-key set and the accuracy gate (backlog ENT-S3)

"Accurate" is a measured number here, not a claim. A set of prescriptions with hand-checked
answers is read by the system; a scorer compares what it produced with the answers; a gate
fails the change when a score falls.

**What is in this folder today**

| | |
|---|---|
| `synthetic-10/` | Ten **made-up** prescriptions with made-up system outputs that carry planted mistakes. It proves the scorer and the gate work. **It is not a measurement of the product** - no picture was read. |
| `thresholds.json` | The product's thresholds. **Not agreed yet**: the limits are empty and the 99% target is marked "to confirm". |
| the real set | **Does not exist yet.** It needs de-identified prescriptions and two clinicians (see "What people have to do"). |

## The folder format

```
<set>/
  manifest.json                 name, suite (capability | regression), sealed: true|false
  <case id>/
    answer.json                 the settled answer key
    answer.<labeller>.json      each labeller's own, independent key - kept, never edited
    image.png  or  page-1.png   the picture, identity removed
    result.json                 what the system produced (written by a run)
```

`answer.json` (`answer.v1`):

```json
{
  "schema_version": "answer.v1",
  "case_id": "rx-0001",
  "slices": {"script": "handwritten", "doctor": "unknown", "doctor_group": "clinic-b",
             "suite": "capability"},
  "patient": {"name": "…", "age_text": "45 Y", "sex": "male", "dob": null,
              "phone": "9876543210", "address": null},
  "doctor":  {"name": "…", "registration_no": "71112", "qualification": "MBBS, MD",
              "department": null},
  "tests": [{"as_written": "HbA1c", "standard_code": "4548-4",
             "reasons": ["Type 2 diabetes mellitus"]}],
  "preparation": [{"text": "fasting 12 hrs", "type": "fasting", "amount": 12, "unit": "h",
                   "applies_to": ["Lipid profile"]}],
  "labelling": {"labellers": ["A", "B"], "disagreements": 1,
                "adjudicated_by": "C", "adjudicated_on": "2026-11-02"},
  "identity": {"method": "deidentified", "tool": "cdi_adapter.deid 1.0",
               "checked_by": "D", "checked_on": "2026-11-02", "readable_identity": false}
}
```

- **A field that is absent or illegible is `null`** - never left out. "Leave it blank" is then
  a tested answer: the system is marked down for inventing a value there.
- `applies_to` is a list of test names, `"all"` or `"unclear"`.
- `doctor_group` keeps test and training data apart: a doctor or clinic in the training data
  must not be in the test set (`keys.split_leaks`).

## The commands

```
python -m cdi_adapter.accuracy check-set   evaluation/<set>
python -m cdi_adapter.accuracy agreement   evaluation/<set> <labeller A> <labeller B>
python -m cdi_adapter.accuracy collect     evaluation/<set> --host https://… --user <account>
python -m cdi_adapter.accuracy score       evaluation/<set> --thresholds evaluation/thresholds.json \
        --baseline <last accepted scorecard.json> --out report/ --history evaluation/history.jsonl
python -m cdi_adapter.accuracy history     evaluation/history.jsonl --html history.html
python -m cdi_adapter.accuracy proof       --target 0.99 --correct 299 --n 300
python -m cdi_adapter.accuracy ablation    without-trocr/scorecard.json with-trocr/scorecard.json --stage trocr
```

`score` exits **0** when the gate passes, **1** when a limit, a claim or the allowed drop
fails, **2** when the set may not be used at all.

## What is measured

For each of the four groups - patient, doctor, tests, preparation - overall and per slice
(printed / handwritten, known / unknown doctor, suite):

| Number | Meaning |
|---|---|
| **Precision of accepted values** | Of what the system accepted by itself, how much is right. The headline. Always with its sample size and 95% lower bound. |
| **Coverage** | How much of what it produced it accepted by itself. High precision with tiny coverage is not useful. |
| Field accuracy / recall | Right values out of the values in the key. |
| List precision, F1 | For tests and preparation: the right items, and all of them. |
| Standard-name accuracy | A test read right can still be named as the wrong standard test. |
| Invented rate | Values given that are not on the page. |
| Blank kept | How often an absent field was rightly left blank. |
| Digits exact | Phone and registration numbers, digit for digit. |
| Calibration gap | Does "confidence 0.9" mean right nine times in ten? |
| All four right | Prescriptions where every group and every link is right. |

**Context is counted apart from reading.** A fasting note read perfectly but attached to the
wrong test is a context error, not a reading error. The report has its own table for
preparation → test links, test → reason links and values put in the wrong field. Links are
judged only between items that were themselves read, so an error is never counted twice.

The scorer is deterministic: exact match after one fixed normalisation (Unicode NFC, case,
spaces). No model grades anything. `transcript.txt` lists what it decided, errors first - read
it, because a grader can be wrong too.

## What "99% proven" means

A measured 99% is a sample. The claim passes only when the **95% lower bound** is at or above
the target - Wilson's bound, as the Accuracy Plan shows it, and the exact bound as well.

| Accepted values | Right | Observed | 95% lower bound | 99% proven? |
|---|---|---|---|---|
| 300 | 300 | 100% | 99.11% | yes |
| 300 | 299 | 99.67% | 98.52% | not yet |
| 1000 | 995 | 99.5% | 98.98% | not yet |
| 2000 | 1990 | 99.5% | 99.17% | yes |

A slice needs **299 accepted values with no error at all** before 99% can be proven; with any
error, more. Each slice that carries a claim needs its own.

## The gate

`thresholds.json` holds three things, written **before** the run:

- **limits** - a score below its `min` (or an error rate above its `max`) fails the change;
- **claims** - a target for the precision of accepted values, proven or not as above;
- **allowed_drop** - how far a score may fall against the last accepted run.

`"agreed"` records the product owner, the clinician and the date. A release (`--release`)
refuses thresholds nobody signed. A **sealed** set (`"sealed": true` in its manifest) is
scored only with `--release`, and every opening is written to `openings.jsonl`.

## What people have to do

None of this can be done by the tool:

1. **Collect** 30-50 real, hard prescriptions first (from real failures), then grow to 300+.
2. **Remove the identity** from every picture (`python -m cdi_adapter.deid scans …`) and have a
   person look at each one and record it in `identity`.
3. **Label twice**: two clinicians write `answer.<name>.json` independently.
4. **Settle** every disagreement `agreement` lists, and record who settled it.
5. **Agree the thresholds** - owner and clinician - and fill in `"agreed"`.
6. **Run it** against a host (`collect`, then `score`) after every change.
