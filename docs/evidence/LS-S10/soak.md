# Soak: 200 fake prescriptions over 2 hours

**PASS** - 200 files dropped; success + error + quarantine = 200.

| Where the files ended | Files |
|---|---|
| inbox | 0 |
| processing | 0 |
| success | 200 |
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

Seconds from drop to saved, over 200 files that succeeded: median 73.8, 95th percentile 111.0, fastest 33.2, slowest 118.0.

- watched folder: /workspace/soak/soak
- listener source: local:/workspace/soak/soak
- GPU: one RTX 4090 (24 GB), one GPU worker; model Qwen2.5-VL-7B; run on 2026-10-09
- batches of 3, 60 s wait for a full batch (the settings as shipped); one drop every 36 s
