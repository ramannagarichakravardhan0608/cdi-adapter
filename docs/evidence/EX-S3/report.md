# EX-S3 - coverage of the lab-test master

**MEASURED** on 2026-10-12 with master `lab-master.2026-10-12.1` (133 rows), on the answer key `answer_key.csv`: 50 prescriptions, 146 ordered tests as written.

| | Count | Share |
|---|---|---|
| Tests with a right answer | 139 |  |
| **Matched** (given a standard name) | 127 | **91.4%** |
| **Correct** (given the right name) | 127 | **91.4%** |
| Matched to the wrong test | 0 | 0.0% |
| Doubtful (sent to a person) | 4 | 2.9% |
| Not matched | 8 | 5.8% |
| Texts that should NOT be named | 7 |  |
| ...of which were named anyway | 0 |  |

## Not matched, or doubtful

- `Vit D3`
- `Lipid Profile (fasting)`
- `Complete Haemogram`
- `Dengue NS1 Ag`
- `NT pro BNP`
- `S. Creat`
- `Blood sugar (F)`
- `Blood sugar (PP)`
- `Lipid prof.`
- `Liver functon test`
- `M.P`
- `Bl. urea`

## What this does and does not show

- The answer key is synthetic: test names written the way prescriptions write them, keyed by hand. It is not a sample of a real lab's orders.
- It measures the list and the matching rules only - not how well a page is read.
- No row of the master has been reviewed by a clinician yet, and rows without a code have none assigned: 'correct' means the right ROW, not a verified LOINC code.
