# Network cut for a minute while three files were being read

**PASS** - 3 files dropped; success + error + quarantine = 3.

| Where the files ended | Files |
|---|---|
| inbox | 0 |
| processing | 0 |
| success | 3 |
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

Seconds from drop to saved, over 3 files that succeeded: median 23567.4, 95th percentile 23580.0, fastest 136.9, slowest 23580.0.

- watched folder: C:\Users\CHAKRA~1\AppData\Local\Temp\ls-s10-netcut
- listener source: a local folder on a PC
- listener on a PC, reading through an SSH tunnel; the tunnel was killed every 5 s for 60 s
