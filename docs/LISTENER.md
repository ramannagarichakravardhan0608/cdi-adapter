# The file listener: a watched folder on a drive chosen by settings

Backlog epic LS. This page covers **LS-S1** - choosing the drive by settings, the six folders, and
the check command - **LS-S2** - signing in without handling a password - and the listener's
basic loop: take each new file, have it read, file it by outcome. Retries and quarantine (LS-S7),
batches (LS-S5) and the rest are not built yet; where this page needs one of them it says so.

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

## Running the listener

```bash
python -m cdi_adapter.listener run            # watch the folder until stopped (Ctrl+C)
python -m cdi_adapter.listener run --once     # one pass, then stop
```

What happens to a file dropped into the inbox:

1. it is left alone until it has **stopped changing** - the same size and date on two looks in a
   row (the listener looks every `CDI_LISTENER_POLL_SECONDS`, 10 by default). A file OneDrive is
   still syncing in, or a copy still in progress, is not read half-arrived;
2. it is moved to `processing`;
3. it is read by the same pipeline as an upload from the screen;
4. it is moved to `success` - or to `error`, with a note in `log` saying why.

| What was dropped | Where it ends up |
|---|---|
| A photo, PDF or scan that is read | `success` |
| A file whose type is not allowed (a `.docx`, a spreadsheet) | **left where it is**, listed as ignored: "file type not allowed" |
| The same bytes as a file already read (the same picture dropped twice) | `success`, as a duplicate of the first: **not read again**, one result |
| A file over `CDI_LISTENER_MAX_BYTES` (50 MB) | `error`, data error "too_large": not downloaded, **never retried** |
| A PDF or TIFF with more pages than `CDI_MAX_PAGES` (10) | `error`, data error "too_many_pages": refused whole, **no page is read**, never retried |
| A picture that would open to more than `CDI_MAX_IMAGE_PIXELS` (a decompression bomb) | `error`, data error "too_many_pixels": refused unopened, never retried |
| A picture too poor to read (blurred, too dark, too small, far away) | `error`, data error "rescan: image quality below threshold - <why>": **not guessed at, never retried** |
| A file named `.pdf` that is not a PDF that can be opened | `error`, data error "not_readable": never retried |
| A file the pipeline fails on, or does not finish in `CDI_LISTENER_JOB_TIMEOUT_S` (900 s) | `error`, note with the reason and the job number |
| A lock file or half-finished download (`~$scan.pdf`, `scan.part`, `scan.tmp`), `desktop.ini` | left where it is, listed as ignored: "temporary file" |
| An empty placeholder, or a file still changing | left where it is until it has stopped changing |

**The folder is never touched while the pipeline cannot be reached.** Before a pass takes any
file it checks that the database and the document store answer (a few seconds at most). If
they do not, it prints `PIPELINE NOT REACHABLE`, takes nothing and - with `--once` - exits with
status 3. Every file stays where it was dropped and is read once the pipeline is back.

**After a crash**, a file left in `processing` is taken up again on the first pass of the next
run. Sending the same file twice is safe: the pipeline recognises an upload it has already
accepted by its bytes.

Not built yet: retrying a failed file and quarantining it after three tries (LS-S7), batches of
three (LS-S5), the health view (LS-S9), and marking a stored document as having come from the
listener (LS-S8). Code: `src/cdi_adapter/listener/service.py`.

### Read exactly as an upload from the screen (LS-S4)

A dropped file is handed, bytes untouched, to the same function an upload from the screen goes
through (`jobstore.submit`), and the same runner takes it through the same steps: picture
quality check, save, text reading, classification, form filling, rules. The listener has no
reading steps of its own. **The one difference is what is recorded as the source**: the stored
document's channel is `listener` instead of `upload`.

- **One file is one prescription**, all its pages together (an assumption to confirm with the
  product owner). A 2-page PDF is one document with 2 pages.
- **A PDF becomes one picture per page** at `CDI_PAGE_DPI` (200); an image is used as it is. A
  PDF is always read as pictures, like a scan - a text layer, if there is one, is not used.
- **PDF pages are rendered by pypdfium2** (`src/cdi_adapter/ingest/pdf.py`), not PyMuPDF, whose
  AGPL licence blocks a sold product. Nothing imports PyMuPDF any more. **The pod's hash-locked
  requirements file still lists it and must be regenerated before deploying.**
- Handwriting is read by whatever the screen uses. The story names TrOCR; there is no TrOCR in
  this codebase yet (RD-S2), so today that is the model alone, for the screen and the folder
  alike.

Not yet shown on a running pipeline: that a stored listener document has the right pages, and
that one file through the screen and through the folder gives identical values. Both need the
database, the document store and the model.

### Only finished, new, allowed files (LS-S3)

| Rule | Setting (default) |
|---|---|
| A file is taken only when its size and version tag have been the same for this many looks in a row | `CDI_LISTENER_STABLE_POLLS` (2) |
| Only these names are read; anything else is ignored with a reason | `CDI_LISTENER_PATTERNS` (`*.pdf,*.png,*.jpg,*.jpeg,*.tif,*.tiff`) |
| A larger file is refused as a data error | `CDI_LISTENER_MAX_BYTES` (50 MB) |
| A document with more pages is refused whole - also for uploads from the screen | `CDI_MAX_PAGES` (10; an assumption, change it if prescriptions here run longer) |
| A picture or page that would open larger is refused unopened | `CDI_MAX_IMAGE_PIXELS` (150 million) |
| How long one worker may hold a file before another may take it over | `CDI_LISTENER_LEASE_SECONDS` (1200) |

The oldest file goes first. Every file the listener sees is one row in the `listener_file` table
(`listener/ledger.py`, migration **0029**): how often it was seen unchanged, the lease, the
sha256 of its bytes, what became of it and why, and the drive it is on. **Run
`python -m alembic upgrade head` before starting the listener**; without the table `run` says so
and takes nothing.

To see the files that will never be read, and why:

```bash
python -m cdi_adapter.listener ignored
```

`run` prints the same list. The health view that will show it is LS-S9.

Text inside a picture is only ever data: the listener hands bytes to the pipeline and acts on
nothing a document says. The pipeline's own guard against instructions hidden in a document is
RD-S4 / ENT-S2, not this story.

### This installation: a OneDrive folder synced to the PC

Set on 2026-10-08 in the local `.env` (which is not in git):

```
CDI_LISTENER_CONNECTOR=local
CDI_LISTENER_LOCAL_ROOT="C:/Users/Chakravardhan/OneDrive - InnoFissionAI/File listener"
CDI_LISTENER_INBOX=
```

A OneDrive folder that is synced to the PC is simply a folder on this computer, so the `local`
connector is used: no sign-in, and OneDrive does the syncing in both directions. The folder
**itself** is the inbox (the inbox name is empty) - drop a file into `File listener` and it is
read. The check command created `processing`, `success`, `error`, `quarantine` and `log` inside
it; they sync to OneDrive like everything else, so the outcome of a file can be seen from any
device. Use forward slashes in the path: a backslash inside a quoted `.env` value is an escape.

The listener has to be **running on this PC, with the pipeline running too** (database,
document store, model). It is not a Windows service yet: it runs for as long as the command
does.

## "Too many requests"

When a drive answers 429 (or 503, or Google's 403 "rate limit exceeded"), the call waits as long
as the drive asks (`Retry-After`, seconds or a date; 5 s if it does not say) and is made again.
That is not counted as a failure. Only when the waiting for one call would pass
`CDI_LISTENER_MAX_WAIT_SECONDS` (300) does the call give up.

## Signing in (LS-S2)

**No password is typed into, stored by or logged by this system.** A person signs in on
Microsoft's own page; a service identity proves itself with a key. What is kept is a token, which
can be taken away without anyone changing a password. Code: `src/cdi_adapter/listener/auth.py`.

```bash
python -m cdi_adapter.listener login          # the one-time sign-in
```

| Drive | How | Settings | Kept on disk |
|---|---|---|---|
| OneDrive, a person | **device code**: `login` prints a web address and a short code; open it on any device, type the code, sign in there | `CDI_GRAPH_AUTH=device`, `CDI_GRAPH_CLIENT_ID`, `CDI_GRAPH_TENANT_ID` | the MSAL token cache, `CDI_GRAPH_TOKEN_CACHE` (`/workspace/.cdi/graph_token_cache.json`) |
| OneDrive / SharePoint, a service identity | **app only**, once an administrator has approved it | `CDI_GRAPH_AUTH=app` and a certificate (`CDI_GRAPH_CLIENT_CERT_FILE` + `CDI_GRAPH_CLIENT_CERT_THUMBPRINT`) or a secret (`CDI_GRAPH_CLIENT_SECRET_FILE`) | nothing: a token is asked for with the key when needed |
| Google Drive | a **service account** the folder is shared with | `CDI_GDRIVE_SERVICE_ACCOUNT_FILE` (the JSON key, e.g. `/workspace/secrets/gdrive_service_account.json`) | nothing |

The device-code token is renewed quietly and written back to the cache file, which is on the
persistent disk: **after a restart the listener starts without asking for a code again.**

Needs, outside this repository: a Microsoft Entra app registration (client id, tenant id) with
"Allow public client flows" switched on for device code; for Google, a service account and its
key, and the folder shared with the account's address (`login` prints it). Install the sign-in
libraries with `pip install 'cdi-adapter[listener]'` (MSAL and PyJWT) - they are **not yet in the
pod's hash-locked requirements file**.

Not built: Google's OAuth for a person (only the service account).

### Only the permission needed

A person's sign-in asks for delegated `Files.ReadWrite` (their own OneDrive); SharePoint asks for
`Sites.Selected` (one site). Every token is checked when it arrives: one that carries anything
else - `Files.ReadWrite.All`, `Sites.ReadWrite.All`, even `User.Read` - is **refused by name and
not stored**. So an app registration with too much permission cannot be used by accident. A
service identity can therefore only be used with `Sites.Selected`.

The review of the app registration itself is a person's job in the Entra portal. Record it here:

| | |
|---|---|
| App registration (name, client id) | |
| Reviewed by, date | |
| Permissions listed | |
| "Allow public client flows" on? | |

### When the sign-in is missing or has expired

`check` says which: `SIGN-IN NEEDED` (nobody has signed in) or `SIGN-IN EXPIRED`, exit 2.

`run` **stops**. It does not ask again: it writes `listener_stopped` with the reason to its log,
raises an alert, prints `LISTENER STOPPED` and exits 2. A file it was part-way through stays in
`processing` and is taken up on the next run. The alert is two things: a `critical` log line
`listener_alert` (shipped to Loki like every line) and an alert file,
`CDI_LISTENER_ALERT_FILE` (`/workspace/logs/listener_alert.json`), which stays until the listener
next starts cleanly. **Paging a person from it (an Alertmanager rule) waits for ENT-S4.**

### Nothing secret in a note or a log

Failure notes and every line the listener logs are scrubbed of bearer tokens, sign-in tokens,
keys, secrets and passwords (`auth.scrub`) - including the pre-signed download addresses a drive
hands out. Secret settings are never shown when settings are printed. `.gitignore` keeps
`/secrets/`, `.cdi/`, `*token_cache*.json` and `*service_account*.json` out of git.

For a test run only, a bearer token can still be pasted into `CDI_GRAPH_ACCESS_TOKEN` /
`CDI_GDRIVE_ACCESS_TOKEN`; it expires in about an hour. Never commit either.

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
3. Sign in to the new drive: `python -m cdi_adapter.listener login`.
4. Run `python -m cdi_adapter.listener check`. Read the `watching` line: it must name the new
   drive and folder.
5. Start the listener.

Files still on the old drive stay where they are; the listener no longer reads that drive. Each
drive reports a different `source_id` (`onedrive:<drive>:<folder>`, `gdrive:<folder>`,
`local:<path>`), which later stories store with every file they track so the retry agent leaves
another drive's rows alone. That retry agent (LS-S7) and its table (LS-S3) are not built yet.

### Checklist: record the check for each drive

Fill this in on the real drives (LS-S10). It has not been done: there is no Entra app
registration or Google service account to sign in with yet.

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

Proven by `tests/test_listener_auth_unit.py` (LS-S2): the real MSAL library against a stand-in
for Microsoft's sign-in service - device-code sign-in, the cache file, no code after a restart,
quiet renewal, stopping with an alert when renewal is refused, refusing broad permissions,
nothing secret in notes or logs - and a Google service account against a stand-in that checks
the signature.

**The cloud drives and sign-in services in those tests are in-memory stand-ins**, answering the addresses and shapes
of Microsoft Graph and Google Drive v3 as documented. The real connector classes run against
them, but **no real OneDrive, SharePoint or Google Drive account has ever been called**. Only the
local drive has been run for real. Anything the stand-ins get wrong about the real services will
show up at LS-S10, not before.
