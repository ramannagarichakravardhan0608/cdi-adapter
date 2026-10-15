# Contest: cand (candidate) against champ (champion)

**Verdict: KEEP CHAMPION** - not binding: the answer key is not sealed: a rehearsal, not a decision

- keep champion: slice script=handwritten: patient.precision_accepted dropped from 0.5294 to 0.2941, more than the margin 0.01
- keep champion: slice script=handwritten: all.field_accuracy dropped from 0.8182 to 0.7045, more than the margin 0.01
- keep champion: slice suite=adversarial: patient.precision_accepted dropped from 0.3333 to 0.0000, more than the margin 0.01
- keep champion: slice suite=adversarial: all.field_accuracy dropped from 0.8000 to 0.6000, more than the margin 0.01
- keep champion: precision of accepted doctor values [overall]: 0.9730 (lower bound 0.8875) against the champion's 1.0000 (0.9318)

Answer key synthetic-10 version 2fdd0ce868d3b38c (10 cases, NOT sealed); rules e0f13523cbfd9384, margin 0.01.

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

- patient [overall]: champion 23/43 (lower bound 0.4114), candidate 31/43 (lower bound 0.5979) - holds
- doctor [overall]: champion 37/37 (lower bound 0.9318), candidate 36/37 (lower bound 0.8875) - lower: 0.9730 (lower bound 0.8875) against the champion's 1.0000 (0.9318)

## Slices

### overall (10 cases) - 2 won, 0 tied, 0 lost

| group | metric | champion | candidate | change | |
|---|---|---|---|---|---|
| patient | precision_accepted | 0.5349 | 0.7209 | 0.1860 | win |
| patient | precision_accepted_lower_95 | 0.4114 | 0.5979 | 0.1865 | win |
| patient | coverage | 1.0000 | 1.0000 | 0.0000 | tie |
| patient | field_accuracy | 0.5349 | 0.7209 | 0.1860 | win |
| patient | f1 | 0.5349 | 0.7209 | 0.1860 | win |
| patient | invented_rate | 0.0000 | 0.0000 | 0.0000 | tie |
| doctor | precision_accepted | 1.0000 | 0.9730 | -0.0270 | loss |
| doctor | precision_accepted_lower_95 | 0.9318 | 0.8875 | -0.0443 | loss |
| doctor | coverage | 1.0000 | 1.0000 | 0.0000 | tie |
| doctor | field_accuracy | 1.0000 | 0.9730 | -0.0270 | loss |
| doctor | f1 | 1.0000 | 0.9730 | -0.0270 | loss |
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
| all | precision_accepted | 0.8113 | 0.8774 | 0.0660 | win |
| all | precision_accepted_lower_95 | 0.7414 | 0.8154 | 0.0740 | win |
| all | coverage | 1.0000 | 1.0000 | 0.0000 | tie |
| all | field_accuracy | 0.8113 | 0.8774 | 0.0660 | win |
| all | f1 | 0.8113 | 0.8774 | 0.0660 | win |
| all | invented_rate | 0.0000 | 0.0000 | 0.0000 | tie |
| all | strict_whole_prescription_rate | 0.0000 | 0.6000 | 0.6000 | win |
| context | prep_to_test.error_rate | 0.0000 | 0.0000 | 0.0000 | tie |
| context | test_to_reason.error_rate | 0.0000 | 0.0000 | 0.0000 | tie |

### doctor=known (6 cases) - 2 won, 0 tied, 0 lost

| group | metric | champion | candidate | change | |
|---|---|---|---|---|---|
| patient | precision_accepted | 0.5385 | 0.7692 | 0.2308 | win |
| patient | precision_accepted_lower_95 | 0.3817 | 0.6120 | 0.2303 | win |
| patient | coverage | 1.0000 | 1.0000 | 0.0000 | tie |
| patient | field_accuracy | 0.5385 | 0.7692 | 0.2308 | win |
| patient | f1 | 0.5385 | 0.7692 | 0.2308 | win |
| patient | invented_rate | 0.0000 | 0.0000 | 0.0000 | tie |
| doctor | precision_accepted | 1.0000 | 0.9545 | -0.0455 | loss |
| doctor | precision_accepted_lower_95 | 0.8905 | 0.8197 | -0.0707 | loss |
| doctor | coverage | 1.0000 | 1.0000 | 0.0000 | tie |
| doctor | field_accuracy | 1.0000 | 0.9545 | -0.0455 | loss |
| doctor | f1 | 1.0000 | 0.9545 | -0.0455 | loss |
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
| all | precision_accepted | 0.8154 | 0.8923 | 0.0769 | win |
| all | precision_accepted_lower_95 | 0.7242 | 0.8127 | 0.0885 | win |
| all | coverage | 1.0000 | 1.0000 | 0.0000 | tie |
| all | field_accuracy | 0.8154 | 0.8923 | 0.0769 | win |
| all | f1 | 0.8154 | 0.8923 | 0.0769 | win |
| all | invented_rate | 0.0000 | 0.0000 | 0.0000 | tie |
| all | strict_whole_prescription_rate | 0.0000 | 0.6667 | 0.6667 | win |
| context | prep_to_test.error_rate | 0.0000 | 0.0000 | 0.0000 | tie |
| context | test_to_reason.error_rate | 0.0000 | 0.0000 | 0.0000 | tie |

### doctor=unknown (4 cases) - 2 won, 0 tied, 0 lost

| group | metric | champion | candidate | change | |
|---|---|---|---|---|---|
| patient | precision_accepted | 0.5294 | 0.6471 | 0.1176 | win |
| patient | precision_accepted_lower_95 | 0.3404 | 0.4486 | 0.1083 | win |
| patient | coverage | 1.0000 | 1.0000 | 0.0000 | tie |
| patient | field_accuracy | 0.5294 | 0.6471 | 0.1176 | win |
| patient | f1 | 0.5294 | 0.6471 | 0.1176 | win |
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
| all | precision_accepted | 0.8049 | 0.8537 | 0.0488 | win |
| all | precision_accepted_lower_95 | 0.6856 | 0.7411 | 0.0555 | win |
| all | coverage | 1.0000 | 1.0000 | 0.0000 | tie |
| all | field_accuracy | 0.8049 | 0.8537 | 0.0488 | win |
| all | f1 | 0.8049 | 0.8537 | 0.0488 | win |
| all | invented_rate | 0.0000 | 0.0000 | 0.0000 | tie |
| all | strict_whole_prescription_rate | 0.0000 | 0.5000 | 0.5000 | win |
| context | prep_to_test.error_rate | 0.0000 | 0.0000 | 0.0000 | tie |
| context | test_to_reason.error_rate | 0.0000 | 0.0000 | 0.0000 | tie |

### script=handwritten (4 cases) - 0 won, 0 tied, 2 lost

| group | metric | champion | candidate | change | |
|---|---|---|---|---|---|
| patient | precision_accepted | 0.5294 | 0.2941 | -0.2353 | loss |
| patient | precision_accepted_lower_95 | 0.3404 | 0.1512 | -0.1892 | loss |
| patient | coverage | 1.0000 | 1.0000 | 0.0000 | tie |
| patient | field_accuracy | 0.5294 | 0.2941 | -0.2353 | loss |
| patient | f1 | 0.5294 | 0.2941 | -0.2353 | loss |
| patient | invented_rate | 0.0000 | 0.0000 | 0.0000 | tie |
| doctor | precision_accepted | 1.0000 | 0.9333 | -0.0667 | loss |
| doctor | precision_accepted_lower_95 | 0.8472 | 0.7492 | -0.0979 | loss |
| doctor | coverage | 1.0000 | 1.0000 | 0.0000 | tie |
| doctor | field_accuracy | 1.0000 | 0.9333 | -0.0667 | loss |
| doctor | f1 | 1.0000 | 0.9333 | -0.0667 | loss |
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
| all | precision_accepted | 0.8182 | 0.7045 | -0.1136 | loss |
| all | precision_accepted_lower_95 | 0.7051 | 0.5822 | -0.1229 | loss |
| all | coverage | 1.0000 | 1.0000 | 0.0000 | tie |
| all | field_accuracy | 0.8182 | 0.7045 | -0.1136 | loss |
| all | f1 | 0.8182 | 0.7045 | -0.1136 | loss |
| all | invented_rate | 0.0000 | 0.0000 | 0.0000 | tie |
| all | strict_whole_prescription_rate | 0.0000 | 0.0000 | 0.0000 | tie |
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

### suite=adversarial (1 cases) - 0 won, 0 tied, 2 lost

| group | metric | champion | candidate | change | |
|---|---|---|---|---|---|
| patient | precision_accepted | 0.3333 | 0.0000 | -0.3333 | loss |
| patient | precision_accepted_lower_95 | 0.0783 | 0.0000 | -0.0783 | loss |
| patient | coverage | 1.0000 | 1.0000 | 0.0000 | tie |
| patient | field_accuracy | 0.3333 | 0.0000 | -0.3333 | loss |
| patient | f1 | 0.3333 | 0.0000 | -0.3333 | loss |
| patient | invented_rate | 0.0000 | 0.0000 | 0.0000 | tie |
| doctor | precision_accepted | 1.0000 | 0.7500 | -0.2500 | loss |
| doctor | precision_accepted_lower_95 | 0.5965 | 0.3561 | -0.2403 | loss |
| doctor | coverage | 1.0000 | 1.0000 | 0.0000 | tie |
| doctor | field_accuracy | 1.0000 | 0.7500 | -0.2500 | loss |
| doctor | f1 | 1.0000 | 0.7500 | -0.2500 | loss |
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
| all | precision_accepted | 0.8000 | 0.6000 | -0.2000 | loss |
| all | precision_accepted_lower_95 | 0.5408 | 0.3516 | -0.1891 | loss |
| all | coverage | 1.0000 | 1.0000 | 0.0000 | tie |
| all | field_accuracy | 0.8000 | 0.6000 | -0.2000 | loss |
| all | f1 | 0.8000 | 0.6000 | -0.2000 | loss |
| all | invented_rate | 0.0000 | 0.0000 | 0.0000 | tie |
| all | strict_whole_prescription_rate | 0.0000 | 0.0000 | 0.0000 | tie |
| context | prep_to_test.error_rate | 0.0000 | 0.0000 | 0.0000 | tie |
| context | test_to_reason.error_rate | 0.0000 | 0.0000 | 0.0000 | tie |

### suite=capability (5 cases) - 2 won, 0 tied, 0 lost

| group | metric | champion | candidate | change | |
|---|---|---|---|---|---|
| patient | precision_accepted | 0.5833 | 0.7500 | 0.1667 | win |
| patient | precision_accepted_lower_95 | 0.4177 | 0.5845 | 0.1668 | win |
| patient | coverage | 1.0000 | 1.0000 | 0.0000 | tie |
| patient | field_accuracy | 0.5833 | 0.7500 | 0.1667 | win |
| patient | f1 | 0.5833 | 0.7500 | 0.1667 | win |
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
| all | precision_accepted | 0.8246 | 0.8947 | 0.0702 | win |
| all | precision_accepted_lower_95 | 0.7276 | 0.8091 | 0.0816 | win |
| all | coverage | 1.0000 | 1.0000 | 0.0000 | tie |
| all | field_accuracy | 0.8246 | 0.8947 | 0.0702 | win |
| all | f1 | 0.8246 | 0.8947 | 0.0702 | win |
| all | invented_rate | 0.0000 | 0.0000 | 0.0000 | tie |
| all | strict_whole_prescription_rate | 0.0000 | 0.6000 | 0.6000 | win |
| context | prep_to_test.error_rate | 0.0000 | 0.0000 | 0.0000 | tie |
| context | test_to_reason.error_rate | 0.0000 | 0.0000 | 0.0000 | tie |

### suite=regression (4 cases) - 2 won, 0 tied, 0 lost

| group | metric | champion | candidate | change | |
|---|---|---|---|---|---|
| patient | precision_accepted | 0.5000 | 0.8125 | 0.3125 | win |
| patient | precision_accepted_lower_95 | 0.3098 | 0.6121 | 0.3023 | win |
| patient | coverage | 1.0000 | 1.0000 | 0.0000 | tie |
| patient | field_accuracy | 0.5000 | 0.8125 | 0.3125 | win |
| patient | f1 | 0.5000 | 0.8125 | 0.3125 | win |
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
| all | precision_accepted | 0.7949 | 0.9231 | 0.1282 | win |
| all | precision_accepted_lower_95 | 0.6711 | 0.8224 | 0.1513 | win |
| all | coverage | 1.0000 | 1.0000 | 0.0000 | tie |
| all | field_accuracy | 0.7949 | 0.9231 | 0.1282 | win |
| all | f1 | 0.7949 | 0.9231 | 0.1282 | win |
| all | invented_rate | 0.0000 | 0.0000 | 0.0000 | tie |
| all | strict_whole_prescription_rate | 0.0000 | 0.7500 | 0.7500 | win |
| context | prep_to_test.error_rate | 0.0000 | 0.0000 | 0.0000 | tie |
| context | test_to_reason.error_rate | 0.0000 | 0.0000 | 0.0000 | tie |
