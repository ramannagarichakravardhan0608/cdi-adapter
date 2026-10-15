# Contest: cand (candidate) against champ (champion)

**Verdict: NEED MORE DATA** - not binding: the answer key is not sealed: a rehearsal, not a decision

- need more data: precision of accepted patient values [overall] is not proven: 43 right of 43 accepted: lower bound 0.9408 does not prove 0.99 - at least 299 accepted values are needed
- need more data: precision of accepted doctor values [overall] is not proven: 37 right of 37 accepted: lower bound 0.9318 does not prove 0.99 - at least 299 accepted values are needed

Answer key synthetic-10 version 2fdd0ce868d3b38c (10 cases, NOT sealed); rules ace807d24ed8fe93, margin 0.01.

## Models

| | champion | candidate |
|---|---|---|
| model_id | Org/Champion-7B | Org/Candidate-7B |
| revision | aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa | bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb |
| prompt_version | - | - |
| licence | Apache-2.0 | Apache-2.0 |
| machine | 1x L4 | 1x L4 |

## Speed and cost

| | champion | candidate |
|---|---|---|
| seconds_per_document | 20.0000 | 22.0000 |
| seconds_p95 | 20.0000 | 22.0000 |
| gpu_memory_gib | 18.0000 | 19.0000 |
| rent_per_prescription | 0.5000 | 0.5500 |
| documents_timed | 10 | 10 |

## Precision of accepted values

- patient [overall]: champion 23/43 (lower bound 0.4114), candidate 43/43 (lower bound 0.9408) - too small: 43 right of 43 accepted: lower bound 0.9408 does not prove 0.99 - at least 299 accepted values are needed
- doctor [overall]: champion 37/37 (lower bound 0.9318), candidate 37/37 (lower bound 0.9318) - too small: 37 right of 37 accepted: lower bound 0.9318 does not prove 0.99 - at least 299 accepted values are needed

## Slices

### overall (10 cases) - 2 won, 0 tied, 0 lost

| group | metric | champion | candidate | change | |
|---|---|---|---|---|---|
| patient | precision_accepted | 0.5349 | 1.0000 | 0.4651 | win |
| patient | precision_accepted_lower_95 | 0.4114 | 0.9408 | 0.5294 | win |
| patient | coverage | 1.0000 | 1.0000 | 0.0000 | tie |
| patient | field_accuracy | 0.5349 | 1.0000 | 0.4651 | win |
| patient | f1 | 0.5349 | 1.0000 | 0.4651 | win |
| patient | invented_rate | 0.0000 | 0.0000 | 0.0000 | tie |
| doctor | precision_accepted | 1.0000 | 1.0000 | 0.0000 | tie |
| doctor | precision_accepted_lower_95 | 0.9318 | 0.9318 | 0.0000 | tie |
| doctor | coverage | 1.0000 | 1.0000 | 0.0000 | tie |
| doctor | field_accuracy | 1.0000 | 1.0000 | 0.0000 | tie |
| doctor | f1 | 1.0000 | 1.0000 | 0.0000 | tie |
| doctor | invented_rate | 0.0000 | 0.0000 | 0.0000 | tie |
| tests | precision_accepted | 1.0000 | 1.0000 | 0.0000 | tie |
| tests | precision_accepted_lower_95 | 0.8753 | 0.8753 | 0.0000 | tie |
| tests | coverage | 1.0000 | 1.0000 | 0.0000 | tie |
| tests | field_accuracy | 1.0000 | 1.0000 | 0.0000 | tie |
| tests | f1 | 1.0000 | 1.0000 | 0.0000 | tie |
| tests | invented_rate | 0.0000 | 0.0000 | 0.0000 | tie |
| preparation | precision_accepted | 1.0000 | 1.0000 | 0.0000 | tie |
| preparation | precision_accepted_lower_95 | 0.7212 | 0.7212 | 0.0000 | tie |
| preparation | coverage | 1.0000 | 1.0000 | 0.0000 | tie |
| preparation | field_accuracy | 1.0000 | 1.0000 | 0.0000 | tie |
| preparation | f1 | 1.0000 | 1.0000 | 0.0000 | tie |
| preparation | invented_rate | 0.0000 | 0.0000 | 0.0000 | tie |
| all | precision_accepted | 0.8113 | 1.0000 | 0.1887 | win |
| all | precision_accepted_lower_95 | 0.7414 | 0.9751 | 0.2338 | win |
| all | coverage | 1.0000 | 1.0000 | 0.0000 | tie |
| all | field_accuracy | 0.8113 | 1.0000 | 0.1887 | win |
| all | f1 | 0.8113 | 1.0000 | 0.1887 | win |
| all | invented_rate | 0.0000 | 0.0000 | 0.0000 | tie |
| all | strict_whole_prescription_rate | 0.0000 | 1.0000 | 1.0000 | win |
| context | prep_to_test.error_rate | 0.0000 | 0.0000 | 0.0000 | tie |
| context | test_to_reason.error_rate | 0.0000 | 0.0000 | 0.0000 | tie |

### doctor=known (6 cases) - 2 won, 0 tied, 0 lost

| group | metric | champion | candidate | change | |
|---|---|---|---|---|---|
| patient | precision_accepted | 0.5385 | 1.0000 | 0.4615 | win |
| patient | precision_accepted_lower_95 | 0.3817 | 0.9057 | 0.5240 | win |
| patient | coverage | 1.0000 | 1.0000 | 0.0000 | tie |
| patient | field_accuracy | 0.5385 | 1.0000 | 0.4615 | win |
| patient | f1 | 0.5385 | 1.0000 | 0.4615 | win |
| patient | invented_rate | 0.0000 | 0.0000 | 0.0000 | tie |
| doctor | precision_accepted | 1.0000 | 1.0000 | 0.0000 | tie |
| doctor | precision_accepted_lower_95 | 0.8905 | 0.8905 | 0.0000 | tie |
| doctor | coverage | 1.0000 | 1.0000 | 0.0000 | tie |
| doctor | field_accuracy | 1.0000 | 1.0000 | 0.0000 | tie |
| doctor | f1 | 1.0000 | 1.0000 | 0.0000 | tie |
| doctor | invented_rate | 0.0000 | 0.0000 | 0.0000 | tie |
| tests | precision_accepted | 1.0000 | 1.0000 | 0.0000 | tie |
| tests | precision_accepted_lower_95 | 0.8160 | 0.8160 | 0.0000 | tie |
| tests | coverage | 1.0000 | 1.0000 | 0.0000 | tie |
| tests | field_accuracy | 1.0000 | 1.0000 | 0.0000 | tie |
| tests | f1 | 1.0000 | 1.0000 | 0.0000 | tie |
| tests | invented_rate | 0.0000 | 0.0000 | 0.0000 | tie |
| preparation | precision_accepted | 1.0000 | 1.0000 | 0.0000 | tie |
| preparation | precision_accepted_lower_95 | 0.6488 | 0.6488 | 0.0000 | tie |
| preparation | coverage | 1.0000 | 1.0000 | 0.0000 | tie |
| preparation | field_accuracy | 1.0000 | 1.0000 | 0.0000 | tie |
| preparation | f1 | 1.0000 | 1.0000 | 0.0000 | tie |
| preparation | invented_rate | 0.0000 | 0.0000 | 0.0000 | tie |
| all | precision_accepted | 0.8154 | 1.0000 | 0.1846 | win |
| all | precision_accepted_lower_95 | 0.7242 | 0.9600 | 0.2358 | win |
| all | coverage | 1.0000 | 1.0000 | 0.0000 | tie |
| all | field_accuracy | 0.8154 | 1.0000 | 0.1846 | win |
| all | f1 | 0.8154 | 1.0000 | 0.1846 | win |
| all | invented_rate | 0.0000 | 0.0000 | 0.0000 | tie |
| all | strict_whole_prescription_rate | 0.0000 | 1.0000 | 1.0000 | win |
| context | prep_to_test.error_rate | 0.0000 | 0.0000 | 0.0000 | tie |
| context | test_to_reason.error_rate | 0.0000 | 0.0000 | 0.0000 | tie |

### doctor=unknown (4 cases) - 2 won, 0 tied, 0 lost

| group | metric | champion | candidate | change | |
|---|---|---|---|---|---|
| patient | precision_accepted | 0.5294 | 1.0000 | 0.4706 | win |
| patient | precision_accepted_lower_95 | 0.3404 | 0.8627 | 0.5223 | win |
| patient | coverage | 1.0000 | 1.0000 | 0.0000 | tie |
| patient | field_accuracy | 0.5294 | 1.0000 | 0.4706 | win |
| patient | f1 | 0.5294 | 1.0000 | 0.4706 | win |
| patient | invented_rate | 0.0000 | 0.0000 | 0.0000 | tie |
| doctor | precision_accepted | 1.0000 | 1.0000 | 0.0000 | tie |
| doctor | precision_accepted_lower_95 | 0.8472 | 0.8472 | 0.0000 | tie |
| doctor | coverage | 1.0000 | 1.0000 | 0.0000 | tie |
| doctor | field_accuracy | 1.0000 | 1.0000 | 0.0000 | tie |
| doctor | f1 | 1.0000 | 1.0000 | 0.0000 | tie |
| doctor | invented_rate | 0.0000 | 0.0000 | 0.0000 | tie |
| tests | precision_accepted | 1.0000 | 1.0000 | 0.0000 | tie |
| tests | precision_accepted_lower_95 | 0.7212 | 0.7212 | 0.0000 | tie |
| tests | coverage | 1.0000 | 1.0000 | 0.0000 | tie |
| tests | field_accuracy | 1.0000 | 1.0000 | 0.0000 | tie |
| tests | f1 | 1.0000 | 1.0000 | 0.0000 | tie |
| tests | invented_rate | 0.0000 | 0.0000 | 0.0000 | tie |
| preparation | precision_accepted | 1.0000 | 1.0000 | 0.0000 | tie |
| preparation | precision_accepted_lower_95 | 0.4250 | 0.4250 | 0.0000 | tie |
| preparation | coverage | 1.0000 | 1.0000 | 0.0000 | tie |
| preparation | field_accuracy | 1.0000 | 1.0000 | 0.0000 | tie |
| preparation | f1 | 1.0000 | 1.0000 | 0.0000 | tie |
| preparation | invented_rate | 0.0000 | 0.0000 | 0.0000 | tie |
| all | precision_accepted | 0.8049 | 1.0000 | 0.1951 | win |
| all | precision_accepted_lower_95 | 0.6856 | 0.9381 | 0.2525 | win |
| all | coverage | 1.0000 | 1.0000 | 0.0000 | tie |
| all | field_accuracy | 0.8049 | 1.0000 | 0.1951 | win |
| all | f1 | 0.8049 | 1.0000 | 0.1951 | win |
| all | invented_rate | 0.0000 | 0.0000 | 0.0000 | tie |
| all | strict_whole_prescription_rate | 0.0000 | 1.0000 | 1.0000 | win |
| context | prep_to_test.error_rate | 0.0000 | 0.0000 | 0.0000 | tie |
| context | test_to_reason.error_rate | 0.0000 | 0.0000 | 0.0000 | tie |

### script=handwritten (4 cases) - 2 won, 0 tied, 0 lost

| group | metric | champion | candidate | change | |
|---|---|---|---|---|---|
| patient | precision_accepted | 0.5294 | 1.0000 | 0.4706 | win |
| patient | precision_accepted_lower_95 | 0.3404 | 0.8627 | 0.5223 | win |
| patient | coverage | 1.0000 | 1.0000 | 0.0000 | tie |
| patient | field_accuracy | 0.5294 | 1.0000 | 0.4706 | win |
| patient | f1 | 0.5294 | 1.0000 | 0.4706 | win |
| patient | invented_rate | 0.0000 | 0.0000 | 0.0000 | tie |
| doctor | precision_accepted | 1.0000 | 1.0000 | 0.0000 | tie |
| doctor | precision_accepted_lower_95 | 0.8472 | 0.8472 | 0.0000 | tie |
| doctor | coverage | 1.0000 | 1.0000 | 0.0000 | tie |
| doctor | field_accuracy | 1.0000 | 1.0000 | 0.0000 | tie |
| doctor | f1 | 1.0000 | 1.0000 | 0.0000 | tie |
| doctor | invented_rate | 0.0000 | 0.0000 | 0.0000 | tie |
| tests | precision_accepted | 1.0000 | 1.0000 | 0.0000 | tie |
| tests | precision_accepted_lower_95 | 0.7472 | 0.7472 | 0.0000 | tie |
| tests | coverage | 1.0000 | 1.0000 | 0.0000 | tie |
| tests | field_accuracy | 1.0000 | 1.0000 | 0.0000 | tie |
| tests | f1 | 1.0000 | 1.0000 | 0.0000 | tie |
| tests | invented_rate | 0.0000 | 0.0000 | 0.0000 | tie |
| preparation | precision_accepted | 1.0000 | 1.0000 | 0.0000 | tie |
| preparation | precision_accepted_lower_95 | 0.5965 | 0.5965 | 0.0000 | tie |
| preparation | coverage | 1.0000 | 1.0000 | 0.0000 | tie |
| preparation | field_accuracy | 1.0000 | 1.0000 | 0.0000 | tie |
| preparation | f1 | 1.0000 | 1.0000 | 0.0000 | tie |
| preparation | invented_rate | 0.0000 | 0.0000 | 0.0000 | tie |
| all | precision_accepted | 0.8182 | 1.0000 | 0.1818 | win |
| all | precision_accepted_lower_95 | 0.7051 | 0.9421 | 0.2370 | win |
| all | coverage | 1.0000 | 1.0000 | 0.0000 | tie |
| all | field_accuracy | 0.8182 | 1.0000 | 0.1818 | win |
| all | f1 | 0.8182 | 1.0000 | 0.1818 | win |
| all | invented_rate | 0.0000 | 0.0000 | 0.0000 | tie |
| all | strict_whole_prescription_rate | 0.0000 | 1.0000 | 1.0000 | win |
| context | prep_to_test.error_rate | 0.0000 | 0.0000 | 0.0000 | tie |
| context | test_to_reason.error_rate | 0.0000 | 0.0000 | 0.0000 | tie |

### script=printed (6 cases) - 2 won, 0 tied, 0 lost

| group | metric | champion | candidate | change | |
|---|---|---|---|---|---|
| patient | precision_accepted | 0.5385 | 1.0000 | 0.4615 | win |
| patient | precision_accepted_lower_95 | 0.3817 | 0.9057 | 0.5240 | win |
| patient | coverage | 1.0000 | 1.0000 | 0.0000 | tie |
| patient | field_accuracy | 0.5385 | 1.0000 | 0.4615 | win |
| patient | f1 | 0.5385 | 1.0000 | 0.4615 | win |
| patient | invented_rate | 0.0000 | 0.0000 | 0.0000 | tie |
| doctor | precision_accepted | 1.0000 | 1.0000 | 0.0000 | tie |
| doctor | precision_accepted_lower_95 | 0.8905 | 0.8905 | 0.0000 | tie |
| doctor | coverage | 1.0000 | 1.0000 | 0.0000 | tie |
| doctor | field_accuracy | 1.0000 | 1.0000 | 0.0000 | tie |
| doctor | f1 | 1.0000 | 1.0000 | 0.0000 | tie |
| doctor | invented_rate | 0.0000 | 0.0000 | 0.0000 | tie |
| tests | precision_accepted | 1.0000 | 1.0000 | 0.0000 | tie |
| tests | precision_accepted_lower_95 | 0.8026 | 0.8026 | 0.0000 | tie |
| tests | coverage | 1.0000 | 1.0000 | 0.0000 | tie |
| tests | field_accuracy | 1.0000 | 1.0000 | 0.0000 | tie |
| tests | f1 | 1.0000 | 1.0000 | 0.0000 | tie |
| tests | invented_rate | 0.0000 | 0.0000 | 0.0000 | tie |
| preparation | precision_accepted | 1.0000 | 1.0000 | 0.0000 | tie |
| preparation | precision_accepted_lower_95 | 0.5258 | 0.5258 | 0.0000 | tie |
| preparation | coverage | 1.0000 | 1.0000 | 0.0000 | tie |
| preparation | field_accuracy | 1.0000 | 1.0000 | 0.0000 | tie |
| preparation | f1 | 1.0000 | 1.0000 | 0.0000 | tie |
| preparation | invented_rate | 0.0000 | 0.0000 | 0.0000 | tie |
| all | precision_accepted | 0.8065 | 1.0000 | 0.1935 | win |
| all | precision_accepted_lower_95 | 0.7118 | 0.9582 | 0.2463 | win |
| all | coverage | 1.0000 | 1.0000 | 0.0000 | tie |
| all | field_accuracy | 0.8065 | 1.0000 | 0.1935 | win |
| all | f1 | 0.8065 | 1.0000 | 0.1935 | win |
| all | invented_rate | 0.0000 | 0.0000 | 0.0000 | tie |
| all | strict_whole_prescription_rate | 0.0000 | 1.0000 | 1.0000 | win |
| context | prep_to_test.error_rate | 0.0000 | 0.0000 | 0.0000 | tie |
| context | test_to_reason.error_rate | 0.0000 | 0.0000 | 0.0000 | tie |

### suite=adversarial (1 cases) - 2 won, 0 tied, 0 lost

| group | metric | champion | candidate | change | |
|---|---|---|---|---|---|
| patient | precision_accepted | 0.3333 | 1.0000 | 0.6667 | win |
| patient | precision_accepted_lower_95 | 0.0783 | 0.5258 | 0.4475 | win |
| patient | coverage | 1.0000 | 1.0000 | 0.0000 | tie |
| patient | field_accuracy | 0.3333 | 1.0000 | 0.6667 | win |
| patient | f1 | 0.3333 | 1.0000 | 0.6667 | win |
| patient | invented_rate | 0.0000 | 0.0000 | 0.0000 | tie |
| doctor | precision_accepted | 1.0000 | 1.0000 | 0.0000 | tie |
| doctor | precision_accepted_lower_95 | 0.5965 | 0.5965 | 0.0000 | tie |
| doctor | coverage | 1.0000 | 1.0000 | 0.0000 | tie |
| doctor | field_accuracy | 1.0000 | 1.0000 | 0.0000 | tie |
| doctor | f1 | 1.0000 | 1.0000 | 0.0000 | tie |
| doctor | invented_rate | 0.0000 | 0.0000 | 0.0000 | tie |
| tests | precision_accepted | 1.0000 | 1.0000 | 0.0000 | tie |
| tests | precision_accepted_lower_95 | 0.4250 | 0.4250 | 0.0000 | tie |
| tests | coverage | 1.0000 | 1.0000 | 0.0000 | tie |
| tests | field_accuracy | 1.0000 | 1.0000 | 0.0000 | tie |
| tests | f1 | 1.0000 | 1.0000 | 0.0000 | tie |
| tests | invented_rate | 0.0000 | 0.0000 | 0.0000 | tie |
| preparation | precision_accepted | 1.0000 | 1.0000 | 0.0000 | tie |
| preparation | precision_accepted_lower_95 | 0.2698 | 0.2698 | 0.0000 | tie |
| preparation | coverage | 1.0000 | 1.0000 | 0.0000 | tie |
| preparation | field_accuracy | 1.0000 | 1.0000 | 0.0000 | tie |
| preparation | f1 | 1.0000 | 1.0000 | 0.0000 | tie |
| preparation | invented_rate | 0.0000 | 0.0000 | 0.0000 | tie |
| all | precision_accepted | 0.8000 | 1.0000 | 0.2000 | win |
| all | precision_accepted_lower_95 | 0.5408 | 0.7870 | 0.2463 | win |
| all | coverage | 1.0000 | 1.0000 | 0.0000 | tie |
| all | field_accuracy | 0.8000 | 1.0000 | 0.2000 | win |
| all | f1 | 0.8000 | 1.0000 | 0.2000 | win |
| all | invented_rate | 0.0000 | 0.0000 | 0.0000 | tie |
| all | strict_whole_prescription_rate | 0.0000 | 1.0000 | 1.0000 | win |
| context | prep_to_test.error_rate | 0.0000 | 0.0000 | 0.0000 | tie |
| context | test_to_reason.error_rate | 0.0000 | 0.0000 | 0.0000 | tie |

### suite=capability (5 cases) - 2 won, 0 tied, 0 lost

| group | metric | champion | candidate | change | |
|---|---|---|---|---|---|
| patient | precision_accepted | 0.5833 | 1.0000 | 0.4167 | win |
| patient | precision_accepted_lower_95 | 0.4177 | 0.8987 | 0.4809 | win |
| patient | coverage | 1.0000 | 1.0000 | 0.0000 | tie |
| patient | field_accuracy | 0.5833 | 1.0000 | 0.4167 | win |
| patient | f1 | 0.5833 | 1.0000 | 0.4167 | win |
| patient | invented_rate | 0.0000 | 0.0000 | 0.0000 | tie |
| doctor | precision_accepted | 1.0000 | 1.0000 | 0.0000 | tie |
| doctor | precision_accepted_lower_95 | 0.8753 | 0.8753 | 0.0000 | tie |
| doctor | coverage | 1.0000 | 1.0000 | 0.0000 | tie |
| doctor | field_accuracy | 1.0000 | 1.0000 | 0.0000 | tie |
| doctor | f1 | 1.0000 | 1.0000 | 0.0000 | tie |
| doctor | invented_rate | 0.0000 | 0.0000 | 0.0000 | tie |
| tests | precision_accepted | 1.0000 | 1.0000 | 0.0000 | tie |
| tests | precision_accepted_lower_95 | 0.7870 | 0.7870 | 0.0000 | tie |
| tests | coverage | 1.0000 | 1.0000 | 0.0000 | tie |
| tests | field_accuracy | 1.0000 | 1.0000 | 0.0000 | tie |
| tests | f1 | 1.0000 | 1.0000 | 0.0000 | tie |
| tests | invented_rate | 0.0000 | 0.0000 | 0.0000 | tie |
| preparation | precision_accepted | 1.0000 | 1.0000 | 0.0000 | tie |
| preparation | precision_accepted_lower_95 | 0.5965 | 0.5965 | 0.0000 | tie |
| preparation | coverage | 1.0000 | 1.0000 | 0.0000 | tie |
| preparation | field_accuracy | 1.0000 | 1.0000 | 0.0000 | tie |
| preparation | f1 | 1.0000 | 1.0000 | 0.0000 | tie |
| preparation | invented_rate | 0.0000 | 0.0000 | 0.0000 | tie |
| all | precision_accepted | 0.8246 | 1.0000 | 0.1754 | win |
| all | precision_accepted_lower_95 | 0.7276 | 0.9547 | 0.2271 | win |
| all | coverage | 1.0000 | 1.0000 | 0.0000 | tie |
| all | field_accuracy | 0.8246 | 1.0000 | 0.1754 | win |
| all | f1 | 0.8246 | 1.0000 | 0.1754 | win |
| all | invented_rate | 0.0000 | 0.0000 | 0.0000 | tie |
| all | strict_whole_prescription_rate | 0.0000 | 1.0000 | 1.0000 | win |
| context | prep_to_test.error_rate | 0.0000 | 0.0000 | 0.0000 | tie |
| context | test_to_reason.error_rate | 0.0000 | 0.0000 | 0.0000 | tie |

### suite=regression (4 cases) - 2 won, 0 tied, 0 lost

| group | metric | champion | candidate | change | |
|---|---|---|---|---|---|
| patient | precision_accepted | 0.5000 | 1.0000 | 0.5000 | win |
| patient | precision_accepted_lower_95 | 0.3098 | 0.8553 | 0.5455 | win |
| patient | coverage | 1.0000 | 1.0000 | 0.0000 | tie |
| patient | field_accuracy | 0.5000 | 1.0000 | 0.5000 | win |
| patient | f1 | 0.5000 | 1.0000 | 0.5000 | win |
| patient | invented_rate | 0.0000 | 0.0000 | 0.0000 | tie |
| doctor | precision_accepted | 1.0000 | 1.0000 | 0.0000 | tie |
| doctor | precision_accepted_lower_95 | 0.8380 | 0.8380 | 0.0000 | tie |
| doctor | coverage | 1.0000 | 1.0000 | 0.0000 | tie |
| doctor | field_accuracy | 1.0000 | 1.0000 | 0.0000 | tie |
| doctor | f1 | 1.0000 | 1.0000 | 0.0000 | tie |
| doctor | invented_rate | 0.0000 | 0.0000 | 0.0000 | tie |
| tests | precision_accepted | 1.0000 | 1.0000 | 0.0000 | tie |
| tests | precision_accepted_lower_95 | 0.7212 | 0.7212 | 0.0000 | tie |
| tests | coverage | 1.0000 | 1.0000 | 0.0000 | tie |
| tests | field_accuracy | 1.0000 | 1.0000 | 0.0000 | tie |
| tests | f1 | 1.0000 | 1.0000 | 0.0000 | tie |
| tests | invented_rate | 0.0000 | 0.0000 | 0.0000 | tie |
| preparation | precision_accepted | 1.0000 | 1.0000 | 0.0000 | tie |
| preparation | precision_accepted_lower_95 | 0.4250 | 0.4250 | 0.0000 | tie |
| preparation | coverage | 1.0000 | 1.0000 | 0.0000 | tie |
| preparation | field_accuracy | 1.0000 | 1.0000 | 0.0000 | tie |
| preparation | f1 | 1.0000 | 1.0000 | 0.0000 | tie |
| preparation | invented_rate | 0.0000 | 0.0000 | 0.0000 | tie |
| all | precision_accepted | 0.7949 | 1.0000 | 0.2051 | win |
| all | precision_accepted_lower_95 | 0.6711 | 0.9351 | 0.2640 | win |
| all | coverage | 1.0000 | 1.0000 | 0.0000 | tie |
| all | field_accuracy | 0.7949 | 1.0000 | 0.2051 | win |
| all | f1 | 0.7949 | 1.0000 | 0.2051 | win |
| all | invented_rate | 0.0000 | 0.0000 | 0.0000 | tie |
| all | strict_whole_prescription_rate | 0.0000 | 1.0000 | 1.0000 | win |
| context | prep_to_test.error_rate | 0.0000 | 0.0000 | 0.0000 | tie |
| context | test_to_reason.error_rate | 0.0000 | 0.0000 | 0.0000 | tie |
