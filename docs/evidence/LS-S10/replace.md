# A file replaced while it settles, and one replaced while it is being read

**FAIL** - 2 files dropped; success + error + quarantine = 2.

| Where the files ended | Files |
|---|---|
| inbox | 0 |
| processing | 0 |
| success | 2 |
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
| none read more than once | NO |
| no two files became one document | yes |
| every file is still what was dropped | yes |
| no file appeared that nobody dropped | yes |

Seconds from drop to saved, over 2 files that succeeded: median 35.5, 95th percentile 35.5, fastest 35.5, slowest 35.5.

| File | Problem |
|---|---|
| soak-replace-0001.png | read 2 times |

- watched folder: /workspace/soak/replace
- listener source: local:/workspace/soak/replace
- GPU: one RTX 4090 (24 GB), one GPU worker; model Qwen2.5-VL-7B; run on 2026-10-09
- the second version of the second file is kept beside the first as soak-replace-0001.1.png and read as its own document
