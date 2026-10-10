# Handwriting benchmark (RD-S2)

- **TrOCR checkpoint:** `microsoft/trocr-base-handwritten`
- **Qwen model:** `Qwen/Qwen2.5-VL-7B-Instruct (http)`
- **Lines:** 200 (synthetic) - typeset text with jitter and noise, fictional content, seed 7

> These lines are NOT real handwriting. The numbers below say nothing about accuracy on prescription handwriting, which remains unmeasured.

| | A: Qwen alone | B: TrOCR alone | C: both |
|---|---|---|---|
| Lines read | 200 | 200 | - |
| Accepted | 200 | 200 | 21 |
| Coverage | 1.0000 | 1.0000 | 0.1050 |
| CER | 0.0000 | 0.0800 | 0.0000 |
| WER | 0.0000 | 0.3200 | 0.0000 |
| Critical-value accuracy | 1.0000 | 0.4850 | 1.0000 |
| Accepted and wrong | 0 | 179 | 0 |
| Accepted-value precision | 1.0000 | 0.1050 | 1.0000 |
|   lower bound (95%) | 0.9812 | 0.0697 | 0.8454 |
| Wrong, caught by disagreement | - | - | 179 |
| Seconds per line | 0.5290 | 3.4850 | 4.0140 |

**Decision:** none made. the lines were not real handwriting: this run measures the harness and the time a reader takes, nothing about accuracy on prescriptions. No decision about TrOCR is made.

Rule (ASSUMPTION - thresholds are for the owner to set before a real run): {'min_precision_gain': 0.02, 'max_seconds_per_line': 1.0, 'min_lines': 500}
