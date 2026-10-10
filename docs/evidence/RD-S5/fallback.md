# Burst: 10 prescriptions in the same second (batching server STOPPED: documents to the serial hf fallback)

**MEASURED** on NVIDIA RTX PRO 4500 Blackwell (32623 MiB), 2026-10-10.

- 4 of 10 saved within 60 s (40%): does NOT meet the 95% target.
- Seconds from upload to saved result: median 63.7, 95th percentile 115.8, fastest 18.46, slowest 115.85.
- Lost: 0. Doubled: 0. Seen waiting for the model: 1 of 10.
- GPU memory: 0 MiB before, 17833 MiB at the most.
- Cost per document: $0.00118 ($0.366/hr for 115.85 s).

| # | Seconds | State | Waited | Attempts | Extractions | Facts | Review items | Problems |
|---|---|---|---|---|---|---|---|---|
| 0 | 83.74 | review |  | 1 | 1 | 4 | 1 |  |
| 1 | 63.68 | review |  | 1 | 1 | 4 | 1 |  |
| 2 | 73.63 | review |  | 1 | 1 | 4 | 1 |  |
| 3 | 92.82 | review |  | 1 | 1 | 3 | 1 |  |
| 4 | 18.46 | review |  | 1 | 1 | 3 | 1 |  |
| 5 | 30.44 | review | yes | 1 | 1 | 5 | 1 |  |
| 6 | 53.77 | review |  | 1 | 1 | 4 | 1 |  |
| 7 | 115.85 | review |  | 1 | 1 | 4 | 1 |  |
| 8 | 42.55 | review |  | 1 | 1 | 5 | 1 |  |
| 9 | 105.35 | review |  | 1 | 1 | 5 | 1 |  |
