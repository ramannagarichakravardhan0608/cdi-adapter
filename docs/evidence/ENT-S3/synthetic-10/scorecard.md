# Accuracy scorecard - synthetic-10

Cases: 10. Gate: **PASS**. Thresholds agreed by the owner and a clinician: NO.

## doctor=known (6 cases)

| Group | Accepted | Right | Precision of accepted | 95% lower bound | Coverage | Field accuracy | Invented | Blank kept |
|---|---|---|---|---|---|---|---|---|
| patient | 25 | 24 | 96.0% | 83.9% | 92.6% | 96.2% | 3.7% | 90.0% |
| doctor | 21 | 21 | 100.0% | 88.6% | 100.0% | 95.5% | 0.0% | 100.0% |
| tests | 12 | 12 | 100.0% | 81.6% | 100.0% | 100.0% | 0.0% | - |
| preparation | 5 | 4 | 80.0% | 43.5% | 100.0% | 80.0% | 0.0% | - |

Test names: list F1 100.0%, standard-name accuracy 91.7%. Preparation: list F1 80.0%, amount exact 75.0%.

**Context errors (counted apart from reading errors):**

| Link | In the key | Given | Right | Wrong | Missing | Error rate |
|---|---|---|---|---|---|---|
| preparation -> test | 6 | 6 | 6 | 0 | 0 | 0.0% |
| test -> reason | 10 | 11 | 10 | 1 | 0 | 10.0% |

Values put in the wrong field: 0. All four groups right in the same prescription: 33.3%.

## doctor=unknown (4 cases)

| Group | Accepted | Right | Precision of accepted | 95% lower bound | Coverage | Field accuracy | Invented | Blank kept |
|---|---|---|---|---|---|---|---|---|
| patient | 16 | 16 | 100.0% | 85.5% | 94.1% | 94.1% | 0.0% | 100.0% |
| doctor | 15 | 15 | 100.0% | 84.7% | 100.0% | 100.0% | 0.0% | 100.0% |
| tests | 8 | 7 | 87.5% | 58.9% | 100.0% | 100.0% | 12.5% | - |
| preparation | 2 | 2 | 100.0% | 42.5% | 66.7% | 100.0% | 33.3% | - |

Test names: list F1 93.3%, standard-name accuracy 100.0%. Preparation: list F1 80.0%, amount exact 100.0%.

**Context errors (counted apart from reading errors):**

| Link | In the key | Given | Right | Wrong | Missing | Error rate |
|---|---|---|---|---|---|---|
| preparation -> test | 2 | 2 | 1 | 1 | 1 | 50.0% |
| test -> reason | 7 | 7 | 7 | 0 | 0 | 0.0% |

Values put in the wrong field: 1. All four groups right in the same prescription: 0.0%.

## overall (10 cases)

| Group | Accepted | Right | Precision of accepted | 95% lower bound | Coverage | Field accuracy | Invented | Blank kept |
|---|---|---|---|---|---|---|---|---|
| patient | 41 | 40 | 97.6% | 89.8% | 93.2% | 95.3% | 2.3% | 94.1% |
| doctor | 36 | 36 | 100.0% | 93.0% | 100.0% | 97.3% | 0.0% | 100.0% |
| tests | 20 | 19 | 95.0% | 80.4% | 100.0% | 100.0% | 5.0% | - |
| preparation | 7 | 6 | 85.7% | 54.8% | 87.5% | 85.7% | 12.5% | - |

Test names: list F1 97.4%, standard-name accuracy 94.7%. Preparation: list F1 80.0%, amount exact 83.3%.

**Context errors (counted apart from reading errors):**

| Link | In the key | Given | Right | Wrong | Missing | Error rate |
|---|---|---|---|---|---|---|
| preparation -> test | 8 | 8 | 7 | 1 | 1 | 12.5% |
| test -> reason | 17 | 18 | 17 | 1 | 0 | 5.9% |

Values put in the wrong field: 1. All four groups right in the same prescription: 20.0%.

## script=handwritten (4 cases)

| Group | Accepted | Right | Precision of accepted | 95% lower bound | Coverage | Field accuracy | Invented | Blank kept |
|---|---|---|---|---|---|---|---|---|
| patient | 16 | 15 | 93.8% | 76.2% | 94.1% | 88.2% | 0.0% | 100.0% |
| doctor | 15 | 15 | 100.0% | 84.7% | 100.0% | 100.0% | 0.0% | 100.0% |
| tests | 8 | 8 | 100.0% | 74.7% | 100.0% | 100.0% | 0.0% | - |
| preparation | 4 | 4 | 100.0% | 59.6% | 100.0% | 100.0% | 0.0% | - |

Test names: list F1 100.0%, standard-name accuracy 100.0%. Preparation: list F1 100.0%, amount exact 100.0%.

**Context errors (counted apart from reading errors):**

| Link | In the key | Given | Right | Wrong | Missing | Error rate |
|---|---|---|---|---|---|---|
| preparation -> test | 4 | 4 | 3 | 1 | 1 | 25.0% |
| test -> reason | 8 | 8 | 8 | 0 | 0 | 0.0% |

Values put in the wrong field: 1. All four groups right in the same prescription: 25.0%.

## script=printed (6 cases)

| Group | Accepted | Right | Precision of accepted | 95% lower bound | Coverage | Field accuracy | Invented | Blank kept |
|---|---|---|---|---|---|---|---|---|
| patient | 25 | 25 | 100.0% | 90.2% | 92.6% | 100.0% | 3.7% | 90.0% |
| doctor | 21 | 21 | 100.0% | 88.6% | 100.0% | 95.5% | 0.0% | 100.0% |
| tests | 12 | 11 | 91.7% | 69.9% | 100.0% | 100.0% | 8.3% | - |
| preparation | 3 | 2 | 66.7% | 25.4% | 75.0% | 66.7% | 25.0% | - |

Test names: list F1 95.7%, standard-name accuracy 90.9%. Preparation: list F1 57.1%, amount exact 50.0%.

**Context errors (counted apart from reading errors):**

| Link | In the key | Given | Right | Wrong | Missing | Error rate |
|---|---|---|---|---|---|---|
| preparation -> test | 4 | 4 | 4 | 0 | 0 | 0.0% |
| test -> reason | 9 | 10 | 9 | 1 | 0 | 11.1% |

Values put in the wrong field: 0. All four groups right in the same prescription: 16.7%.

## suite=adversarial (1 cases)

| Group | Accepted | Right | Precision of accepted | 95% lower bound | Coverage | Field accuracy | Invented | Blank kept |
|---|---|---|---|---|---|---|---|---|
| patient | 3 | 3 | 100.0% | 52.6% | 100.0% | 100.0% | 0.0% | 100.0% |
| doctor | 4 | 4 | 100.0% | 59.6% | 100.0% | 100.0% | 0.0% | - |
| tests | 2 | 2 | 100.0% | 42.5% | 100.0% | 100.0% | 0.0% | - |
| preparation | 1 | 1 | 100.0% | 27.0% | 100.0% | 100.0% | 0.0% | - |

Test names: list F1 100.0%, standard-name accuracy 100.0%. Preparation: list F1 100.0%, amount exact 100.0%.

**Context errors (counted apart from reading errors):**

| Link | In the key | Given | Right | Wrong | Missing | Error rate |
|---|---|---|---|---|---|---|
| preparation -> test | 1 | 1 | 1 | 0 | 0 | 0.0% |
| test -> reason | 2 | 2 | 2 | 0 | 0 | 0.0% |

Values put in the wrong field: 0. All four groups right in the same prescription: 100.0%.

## suite=capability (5 cases)

| Group | Accepted | Right | Precision of accepted | 95% lower bound | Coverage | Field accuracy | Invented | Blank kept |
|---|---|---|---|---|---|---|---|---|
| patient | 23 | 22 | 95.7% | 82.7% | 95.8% | 91.7% | 0.0% | 100.0% |
| doctor | 19 | 19 | 100.0% | 87.5% | 100.0% | 100.0% | 0.0% | 100.0% |
| tests | 11 | 10 | 90.9% | 67.7% | 100.0% | 100.0% | 9.1% | - |
| preparation | 4 | 4 | 100.0% | 59.6% | 100.0% | 100.0% | 0.0% | - |

Test names: list F1 95.2%, standard-name accuracy 90.0%. Preparation: list F1 100.0%, amount exact 100.0%.

**Context errors (counted apart from reading errors):**

| Link | In the key | Given | Right | Wrong | Missing | Error rate |
|---|---|---|---|---|---|---|
| preparation -> test | 5 | 5 | 5 | 0 | 0 | 0.0% |
| test -> reason | 9 | 10 | 9 | 1 | 0 | 11.1% |

Values put in the wrong field: 1. All four groups right in the same prescription: 20.0%.

## suite=regression (4 cases)

| Group | Accepted | Right | Precision of accepted | 95% lower bound | Coverage | Field accuracy | Invented | Blank kept |
|---|---|---|---|---|---|---|---|---|
| patient | 15 | 15 | 100.0% | 84.7% | 88.2% | 100.0% | 5.9% | 87.5% |
| doctor | 13 | 13 | 100.0% | 82.8% | 100.0% | 92.9% | 0.0% | 100.0% |
| tests | 7 | 7 | 100.0% | 72.1% | 100.0% | 100.0% | 0.0% | - |
| preparation | 2 | 1 | 50.0% | 12.1% | 66.7% | 50.0% | 33.3% | - |

Test names: list F1 100.0%, standard-name accuracy 100.0%. Preparation: list F1 40.0%, amount exact 50.0%.

**Context errors (counted apart from reading errors):**

| Link | In the key | Given | Right | Wrong | Missing | Error rate |
|---|---|---|---|---|---|---|
| preparation -> test | 2 | 2 | 1 | 1 | 1 | 50.0% |
| test -> reason | 6 | 6 | 6 | 0 | 0 | 0.0% |

Values put in the wrong field: 0. All four groups right in the same prescription: 0.0%.

## Gate

- every limit held and every claim is proven
