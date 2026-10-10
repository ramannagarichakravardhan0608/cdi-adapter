# Model server stopped for 5 minutes during a batch

**PASS** - 6 files dropped; success + error + quarantine = 6.

| Where the files ended | Files |
|---|---|
| inbox | 0 |
| processing | 0 |
| success | 0 |
| error | 0 |
| quarantine | 6 |
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

- watched folder: /workspace/soak/model
- listener source: local:/workspace/soak/model
- stopped while the first batch was in processing; started again after 300 s
- failure notes written: 24
