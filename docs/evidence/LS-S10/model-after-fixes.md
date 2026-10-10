# Model server stopped for 5 minutes - after the fixes

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

Seconds from drop to saved, over 6 files that succeeded: median 371.6, 95th percentile 390.5, fastest 352.6, slowest 390.5.

- watched folder: /workspace/soak/model3
- listener source: local:/workspace/soak/model3
- GPU: one RTX 4090 (24 GB), one GPU worker; model Qwen2.5-VL-7B; run on 2026-10-09
- the listener left the folder alone while the server was away; each retry was a new run
