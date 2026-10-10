# Burst: 20 prescriptions in the same second (overload: 20 at once on the vLLM batching server (10 sequences))

**MEASURED** on NVIDIA RTX PRO 4500 Blackwell (32623 MiB), 2026-10-10.

- 20 of 20 saved within 60 s (100%): meets the 95% target.
- Seconds from upload to saved result: median 22.0, 95th percentile 30.5, fastest 8.23, slowest 30.5.
- Lost: 0. Doubled: 0. Seen waiting for the model: 10 of 20.
- GPU memory: 27605 MiB before, 27605 MiB at the most.
- Cost per document: $0.00016 ($0.366/hr for 30.5 s).

| # | Seconds | State | Waited | Attempts | Extractions | Facts | Review items | Problems |
|---|---|---|---|---|---|---|---|---|
| 0 | 30.48 | review | yes | 1 | 1 | 4 | 1 |  |
| 1 | 21.97 | review |  | 1 | 1 | 3 | 1 |  |
| 2 | 28.31 | review |  | 2 | 1 | 3 | 1 |  |
| 3 | 18.62 | review | yes | 1 | 1 | 2 | 1 |  |
| 4 | 21.94 | review |  | 1 | 1 | 3 | 1 |  |
| 5 | 21.09 | review |  | 1 | 1 | 3 | 1 |  |
| 6 | 21.92 | review |  | 1 | 1 | 3 | 1 |  |
| 7 | 29.48 | review | yes | 2 | 1 | 4 | 1 |  |
| 8 | 22.0 | review | yes | 1 | 1 | 3 | 1 |  |
| 9 | 30.43 | review | yes | 2 | 1 | 4 | 1 |  |
| 10 | 29.42 | review | yes | 2 | 1 | 3 | 1 |  |
| 11 | 18.57 | review |  | 1 | 1 | 2 | 1 |  |
| 12 | 30.5 | review | yes | 1 | 1 | 4 | 1 |  |
| 13 | 21.85 | review | yes | 1 | 1 | 3 | 1 |  |
| 14 | 30.18 | review | yes | 2 | 1 | 4 | 1 |  |
| 15 | 30.5 | review | yes | 1 | 1 | 4 | 1 |  |
| 16 | 8.23 | review |  | 1 | 1 | 2 | 1 |  |
| 17 | 8.31 | review |  | 1 | 1 | 2 | 1 |  |
| 18 | 21.9 | review |  | 1 | 1 | 3 | 1 |  |
| 19 | 26.15 | review |  | 1 | 1 | 4 | 1 |  |
