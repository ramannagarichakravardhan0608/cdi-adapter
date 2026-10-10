# Burst: 10 prescriptions in the same second (vLLM batching server, max 10 sequences, serial hf server as fallback only)

**MEASURED** on NVIDIA RTX PRO 4500 Blackwell (32623 MiB), 2026-10-10.

- 10 of 10 saved within 60 s (100%): meets the 95% target.
- Seconds from upload to saved result: median 39.9, 95th percentile 40.9, fastest 38.66, slowest 40.89.
- Lost: 0. Doubled: 0. Seen waiting for the model: 0 of 10.
- GPU memory: 0 MiB before, 27605 MiB at the most.
- Cost per document: $0.00042 ($0.366/hr for 40.89 s).

| # | Seconds | State | Waited | Attempts | Extractions | Facts | Review items | Problems |
|---|---|---|---|---|---|---|---|---|
| 0 | 39.66 | review |  | 1 | 1 | 3 | 1 |  |
| 1 | 38.66 | review |  | 1 | 1 | 2 | 1 |  |
| 2 | 40.71 | review |  | 1 | 1 | 4 | 1 |  |
| 3 | 38.67 | review |  | 1 | 1 | 2 | 1 |  |
| 4 | 40.38 | review |  | 1 | 1 | 4 | 1 |  |
| 5 | 38.76 | review |  | 1 | 1 | 2 | 1 |  |
| 6 | 39.92 | review |  | 1 | 1 | 3 | 1 |  |
| 7 | 40.89 | review |  | 1 | 1 | 4 | 1 |  |
| 8 | 40.82 | review |  | 1 | 1 | 4 | 1 |  |
| 9 | 40.79 | review |  | 1 | 1 | 4 | 1 |  |
