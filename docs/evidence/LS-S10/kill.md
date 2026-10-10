# Listener killed (kill -9) in the middle of a batch

**PASS** - 6 files dropped; success + error + quarantine = 6.

| Where the files ended | Files |
|---|---|
| inbox | 0 |
| processing | 0 |
| success | 6 |
| error | 0 |
| quarantine | 0 |
| rescan | 0 |
| missing | 0 |
| duplicated | 0 |

| Check | Result |
|---|---|
| success + error + quarantine equals files dropped | yes |
| none missing | yes |
| none in two places | yes |
| none read more than once | yes |
| no two files became one document | yes |
| every file is still what was dropped | yes |
| no file appeared that nobody dropped | yes |

Seconds from drop to saved, over 6 files that succeeded: median 53.7, 95th percentile 72.9, fastest 31.5, slowest 72.9.

- watched folder: /workspace/soak/kill
- listener source: local:/workspace/soak/kill
- GPU: one RTX 4090 (24 GB), one GPU worker; model Qwen2.5-VL-7B; run on 2026-10-09
- three files were in processing, their jobs already accepted; started again 20 s later
