# The file listener: a watched folder on a drive chosen by settings

Backlog epic LS. This page covers **LS-S1** - choosing the drive by settings, the six folders, and
the check command. Signing in (LS-S2), picking files up (LS-S3), reading them (LS-S4) and the rest
are not built yet; where this page needs one of them it says so.

The older `cdi-ingest-watch` (`ingest/watcher.py`, one local inbox, `CDI_INBOX_DIR`) is unchanged
and still works. The listener is a separate thing beside it.

## What it is

Prescription pictures and PDFs are dropped into one folder. Which drive that folder lives on is
**one setting**, and nothing else in the listener knows the difference:

| `CDI_LISTENER_CONNECTOR` | Drive | Also needs |
|---|---|---|
| `local` (default) | a folder on this computer | `CDI_LISTENER_LOCAL_ROOT` |
| `onedrive` | a OneDrive | `CDI_LISTENER_ROOT` (folder path on the drive); a sign-in |
| `sharepoint` | a SharePoint site's document library | `CDI_GRAPH_SITE_ID`, `CDI_LISTENER_ROOT`; a sign-in |
| `gdrive` | a Google Drive folder | `CDI_GDRIVE_FOLDER_ID`; a sign-in |
| `package.module:Class` | a drive of your own | whatever your class reads |

Only one drive is active at a time. Code: `src/cdi_adapter/listener/connectors.py`.

## The six folders

The same on every drive. The check command creates any that are missing.

| Logical name | Setting (default) | Holds |
|---|---|---|
| inbox | `CDI_LISTENER_INBOX` (`inbox`) | files waiting. **Empty = the root folder itself is the inbox** |
| processing | `CDI_LISTENER_PROCESSING` (`processing`) | a file being read |
| success | `CDI_LISTENER_SUCCESS` (`success`) | read and saved |
| error | `CDI_LISTENER_ERROR` (`error`) | failed, will be tried again |
| quarantine | `CDI_LISTENER_QUARANTINE` (`quarantine`) | failed for good: a person must look |
| log | `CDI_LISTENER_LOG` (`log`) | one note per failed run |

Nothing is ever deleted and nothing is ever overwritten: a file moved onto another of the same
name is kept beside it as `name.1.ext`, `name.2.ext`, ... - on Google Drive too, which would
otherwise hold two files under one name.

## The check command

```bash
python -m cdi_adapter.listener check          # or: cdi-listener check
python -m cdi_adapter.listener check --json
```

It signs in to the drive the settings name, finds the six folders (creating the missing ones) and
prints how many files are waiting. **It processes nothing** - no file is read, moved or changed.
Run it after every change of drive, before starting the listener.

```
drive        local
watching     local:/workspace/data/listener
folders      all six present (created: success, error, quarantine, log)
waiting      2 file(s) in the inbox
  processing 0
  success    0
  error      0
  quarantine 0
  log        0
nothing was processed
```

Exit status: `0` ready; `2` sign-in is missing or has expired; `1` anything else (a wrong
setting, a folder that is not there, a drive that would not stop saying "too many requests").

## "Too many requests"

When a drive answers 429 (or 503, or Google's 403 "rate limit exceeded"), the call waits as long
as the drive asks (`Retry-After`, seconds or a date; 5 s if it does not say) and is made again.
That is not counted as a failure. Only when the waiting for one call would pass
`CDI_LISTENER_MAX_WAIT_SECONDS` (300) does the call give up.

## Signing in (LS-S2 - not built)

The two cloud connectors ask a *token source* for a bearer token on every call. LS-S2 will plug
the real sign-in in there (device code and app-only for Microsoft, service account and OAuth for
Google, with quiet renewal). Until then the only token source reads `CDI_GRAPH_ACCESS_TOKEN` /
`CDI_GDRIVE_ACCESS_TOKEN` - a token pasted in for one test run, which expires in about an hour.
Never commit either. Without one the check says `SIGN-IN NEEDED` and exits 2.

No Microsoft or Google SDK is installed: both connectors speak HTTPS through `httpx`, which the
project already uses, so there is no extra to `pip install` for either drive.

## A drive of your own

Write a class with these five methods and a `name` and `source_id`, and name it in the setting
(`CDI_LISTENER_CONNECTOR=my_package.my_module:MyConnector`). No listener code changes.

```python
class MyConnector:
    name = "mine"
    source_id = "mine:<which folder>"          # stable; stored with every file later
    def __init__(self, settings): ...          # or a classmethod from_settings(settings)
    def ensure_folders(self) -> list[str]: ... # create the missing; return their logical names
    def list(self, folder="inbox") -> list[RemoteFile]: ...
    def download(self, file) -> bytes: ...
    def move(self, file, to, *, name=None) -> RemoteFile: ...   # keep both on a name clash
    def write_note(self, name, text) -> RemoteFile: ...         # into "log"
```

A class missing any of the five is refused at start-up, by name.

## Switching drives: the runbook

Two ready-made settings files differ **only** in the drive's own settings:
`.env.onedrive.example` and `.env.gdrive.example` (a test asserts it). Neither holds a secret.

1. Stop the listener.
2. Put the new drive's settings in `.env` (copy the block from the example file). Change nothing
   else.
3. Sign in to the new drive (LS-S2).
4. Run `python -m cdi_adapter.listener check`. Read the `watching` line: it must name the new
   drive and folder.
5. Start the listener.

Files still on the old drive stay where they are; the listener no longer reads that drive. Each
drive reports a different `source_id` (`onedrive:<drive>:<folder>`, `gdrive:<folder>`,
`local:<path>`), which later stories store with every file they track so the retry agent leaves
another drive's rows alone. That retry agent (LS-S7) and its table (LS-S3) are not built yet.

### Checklist: record the check for each drive

Fill this in on the real drives (LS-S10). It has not been done: there is no sign-in yet.

| | OneDrive | Google Drive |
|---|---|---|
| Settings file used | `.env.onedrive.example` + sign-in | `.env.gdrive.example` + sign-in |
| Date, who | | |
| `drive` line | | |
| `watching` line | | |
| `folders` line (what was created) | | |
| `waiting` count matches the folder by eye? | | |
| Exit status | | |
| Only the settings file differed between the two runs? | | |

## What has and has not been proven

Proven by `tests/test_listener_connectors_unit.py`: one set of checks run against all four
(`local`, `onedrive`, `sharepoint`, `gdrive`) - folders created, files listed (with paging),
downloaded, moved, name clashes kept, notes written, root-as-inbox, renamed folders, the drive
chosen by the setting, a custom class, waiting on "too many requests", and the check command.

**The cloud drives in those tests are in-memory stand-ins**, answering the addresses and shapes
of Microsoft Graph and Google Drive v3 as documented. The real connector classes run against
them, but **no real OneDrive, SharePoint or Google Drive account has ever been called**. Only the
local drive has been run for real. Anything the stand-ins get wrong about the real services will
show up at LS-S10, not before.
