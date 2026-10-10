# Batch size 1: 12 files dropped at once

**PASS** - 12 files dropped; success + error + quarantine = 12.

| Where the files ended | Files |
|---|---|
| inbox | 0 |
| processing | 0 |
| success | 12 |
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

Seconds from drop to saved, over 12 files that succeeded: median 139.5, 95th percentile 201.9, fastest 27.4, slowest 214.0.

- watched folder: /workspace/soak/batch1
- listener source: local:/workspace/soak/batch1
- GPU: one RTX 4090 (24 GB), one GPU worker; model Qwen2.5-VL-7B; run on 2026-10-09
- a file's time includes its wait for the files before it
