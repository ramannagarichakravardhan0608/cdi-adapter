# Burst: 10 prescriptions in the same second (GPU worker killed (SIGKILL) 6 s in, started again 5 s later)

**MEASURED** on NVIDIA RTX PRO 4500 Blackwell (32623 MiB), 2026-10-10.

- 10 of 10 saved within 60 s (100%): meets the 95% target.
- Seconds from upload to saved result: median 45.2, 95th percentile 47.2, fastest 17.73, slowest 47.16.
- Lost: 0. Doubled: 0. Seen waiting for the model: 3 of 10.
- GPU memory: 27605 MiB before, 27605 MiB at the most.
- Cost per document: $0.00048 ($0.366/hr for 47.16 s).

- Fault: {'killed_pid': 20576, 'killed_at': 1791613894.7687564, 'restarted_pid': 22526, 'restarted_at': 1791613899.7693117}

| # | Seconds | State | Waited | Attempts | Extractions | Facts | Review items | Problems |
|---|---|---|---|---|---|---|---|---|
| 0 | 17.73 | review | yes | 1 | 1 | 3 | 1 |  |
| 1 | 45.21 | review |  | 2 | 1 | 2 | 1 |  |
| 2 | 18.62 | review | yes | 1 | 1 | 4 | 1 |  |
| 3 | 17.73 | review | yes | 1 | 1 | 3 | 1 |  |
| 4 | 46.17 | review |  | 2 | 1 | 3 | 1 |  |
| 5 | 47.16 | review |  | 2 | 1 | 4 | 1 |  |
| 6 | 45.22 | review |  | 2 | 1 | 2 | 1 |  |
| 7 | 46.19 | review |  | 2 | 1 | 3 | 1 |  |
| 8 | 45.75 | review |  | 2 | 1 | 3 | 1 |  |
| 9 | 45.17 | review |  | 2 | 1 | 2 | 1 |  |
