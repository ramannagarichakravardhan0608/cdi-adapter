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

Not built yet: retrying a failed file automatically (LS-S7), the health view (LS-S9), and marking a stored document as having come from the
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

### Running, restart-safe and visible (LS-S9)

**It starts by itself.** On every pod start `start_all.sh` starts the retry agent, and the
listener too when `CDI_START_LISTENER=1` (`infra/runpod/start_listener.sh`; logs in
`/workspace/logs/listener.log` and `listener-recover.log`). The scripts have not been run on a
pod yet.

**A restart loses nothing.** What a restart needs is in the database and in the folder, not in
the process: the lease on each file, its run count, its history and the listener's heartbeat
are rows; the files are where they were. A file that was being read when the pod went away
stays in `processing` and is read again as soon as its lease has run out
(`CDI_LISTENER_LEASE_SECONDS`, 900) - by whichever listener is looking, on any pass, not only
the first after a start. The same pod coming back takes its own files at once.

**It stops gently.** On a stop signal (SIGTERM from the pod, Ctrl+C from a person) the batch
that is running finishes, no new one starts, and the command exits 0.
`start_listener.sh stop` asks first and waits up to a lease for the batch before it kills.

**It says how it is.** After every look the listener writes a heartbeat row
(`listener_heartbeat`, migration **0034**). The health view reads it:

```bash
python -m cdi_adapter.listener health        # exit 0 healthy, 4 not polling
```

and so do the page **`/listener`** (in the header, for staff who may see results) and
`GET /api/listener/health`. For each watched folder: the time of the last *good* look, the
files waiting and the age of the oldest, the files in error and in quarantine, and the files
being ignored with the reason. A listener that is alive but cannot reach its pipeline keeps
looking and stops being "good", with the reason shown.

**It raises an alert when it stops looking.** `CdiListenerNotPolling` fires when no look has
succeeded for 5 minutes (the owner's number: `300` in the rule, `CDI_LISTENER_STALE_SECONDS`
for the view). Like every alert here it reaches Alertmanager and the dashboard, and it is shown
to the staff who are signed in: every page of the web app carries a banner with the alerts firing
now (red for a page, amber for a ticket such as a file in quarantine), asked for once a minute
(`GET /api/alerts/active`, `CDI_ALERTMANAGER_URL`). An alert silenced in Alertmanager is not
shown. No email, chat or pager has been named yet, so somebody who is not at a screen is not told.

The counts of files in error and in quarantine come from the listener's table, not from a
fresh look at the folders: a file a person has taken out of `error` by hand is still counted
until it is dropped again.

### The same result as an upload, and where it came from (LS-S8)

A file read from the watched folder ends as the same database rows and the same result JSON as
one uploaded on the screen: the listener writes no clinical rows of its own, and the result is
built by one function whatever the channel. What differs is only what the result says about its
source:

| | An upload | A dropped file |
|---|---|---|
| `document.source.channel` | `upload` | `listener` |
| `document.source.drive` | none | `local`, `onedrive`, `sharepoint`, `gdrive` |
| `document.source.folder` | none | the watched folder (a path; on Google Drive, the folder's id) |
| `document.source.file_name` | the file's name | the name it was **dropped under** |

- **One result per file.** A document is stored once however often its bytes arrive. The same
  file dropped twice, or dropped after being uploaded, is one result; the first arrival is its
  source and the later ones are listed under `also_received`.
- **Each dropped file is linked to its result** (`listener_file.document_id`, migration
  **0033**), with the name it was dropped under (`original_name`) - which stays the same when a
  second file of that name is kept as `rx.1.pdf`.
- **Find a result by file name:** the box under *Progress* on the Generate EMR screen, or
  `GET /api/results/search?file=<part of the name>`. It finds uploaded and dropped files alike,
  and a dropped file that never became a result (it failed, or is in quarantine) with what
  became of it. Staff who may open results may search.
- **The original file is kept unchanged**: in the `success` folder, and in the document store.
- **Do not put a patient's name in a file name.** File names are shown in the folders, in the
  notes in `log`, in search results and in the result's source. This is a rule for the people
  who drop files (a product-owner policy); the system does not enforce it.

- **Every result is also a JSON file** (the backlog's OUT-S2 writer): one per prescription,
  named `<document id>.result.v1.json`, the same for an upload and a dropped file. It is put in
  the document store under `CDI_RESULT_OUTPUT_PREFIX` (`results/`) and, when
  `CDI_RESULT_OUTPUT_DIR` names a folder, there too. It is validated against the contract
  before a byte is written, written all at once, and identical when written again. It is
  written when a document is finished, and again whenever the result changes: when a dropped
  file is linked to it, when a reviewer corrects a value, and when an item is approved. The
  same JSON is served by the API (`/api/documents/<id>/result.json`).

### A failed file is tried again by itself, then handed to a person (LS-S7)

```bash
python -m cdi_adapter.listener recover          # the retry agent, until stopped
python -m cdi_adapter.listener recover --once   # one pass
```

Every failure is sorted into one of three kinds:

| Kind | What it is | Retried? |
|---|---|---|
| **data** | The file itself: empty, not a picture, corrupt, too blurred, too big, too many pages, not a clinical document | **Never.** The note says to rescan and drop it again |
| **transient** | The system: a timeout, a server down or busy, a rate limit, a lost connection | Yes |
| **code** | Anything else: an error nobody planned for | Yes - it may heal; if not, a person gets it |

- **The waits grow:** 60 s before the first retry, 120 s before the second, 240 s before the third
  (`CDI_LISTENER_RETRY_BASE_SECONDS` x 2^n).
- **At most four runs:** the first and `CDI_LISTENER_MAX_ATTEMPTS` (3) retries. When the last
  retry fails the file goes to `quarantine`, with a note for each of the four runs.
- **How a retry happens:** the agent moves the file from `error` back into the inbox when its
  wait is over; the listener then takes it like any other file. The agent reads nothing itself
  and keeps nothing itself - attempts and history are in `listener_file` - so it can be stopped
  and started at any time.
- **Only this drive's files:** the agent looks at rows of the drive the settings name, so files
  that failed on another drive are left alone after the drive setting changes.
- **An empty file** is given a minute (six looks) in case it is still arriving, then refused as
  a data error.
- **When a file reaches quarantine** the listener writes a `critical` log line
  (`ListenerFileQuarantined`) and adds a line to `<alert file>.quarantine.jsonl`. **Telling a
  person from that (an alert rule and a route) waits for ENT-S4.**

The listener and the agent are two processes. On a pod, `infra/runpod/start_listener.sh` starts
both, and `start_all.sh` calls it when `CDI_LISTENER_ENABLED=1`. That script has not been run.

### Filed by outcome, with a note only for a failure (LS-S6)

Open the folders and see at once what worked, what failed and why.

| Outcome | Where the file goes | Note in `log` |
|---|---|---|
| Read, and the result saved in the database | `success` | **none** |
| Failed | `error` | one, for that run |
| Failed when its retries are used up (run 4; `CDI_LISTENER_MAX_ATTEMPTS`, 3 retries) | `quarantine` | one, saying the retries are used up |
| A data error (not a scan, too large, too many pages, rescan, not a real PDF) | `error`, never run again, never quarantined | one |

- **The move to `success` comes after the save.** If the pod stops in between, the file stays in
  `processing`; on restart the listener takes it up again, the pipeline recognises the bytes it
  has already saved, and the file is only moved - the result is not saved twice.
- **One note per failed run**, named `<file>.<UTC time>.run<N>.log`, with: the reason, the kind
  of error, the batch id, the run number, what happens next, and the end of the error trace.
- **A note never holds what was read from the picture.** A reason this system wrote itself (a
  size, a page count, a picture-quality verdict) is given in full. For any other failure only
  the *kind of error* is written (`ValueError`), and the trace gives places in the code (file,
  line, function) - never the error's message, which may quote the document. The message is in
  the job and the log. The note does name the file, so a patient's name in a *file name* would
  be in it.
- **Nothing is ever deleted**, and a file moved where one of the same name already is is kept
  beside it (`rx.pdf`, `rx.1.pdf`).
- **Every move is written to the file's history** in `listener_file` (to which folder, under
  which name, when). A quarantined file has the state `quarantined` (migration **0031**).
- The folder names are settings: `CDI_LISTENER_SUCCESS`, `CDI_LISTENER_ERROR`,
  `CDI_LISTENER_QUARANTINE`, `CDI_LISTENER_LOG`.

A failed file is put back in the inbox by the retry agent (LS-S7, above) - or by a person; either
way the same file is recognised and its run number goes up.

### Batches of three (LS-S5)

So the model server is kept busy but not flooded, finished files are run in batches.

| Rule | Setting (default) |
|---|---|
| Up to this many finished files are taken together, oldest first, and read at the same time | `CDI_LISTENER_BATCH_SIZE` (3) |
| Fewer than a batch start once the oldest of them has waited this long; 0 = start whatever is there | `CDI_LISTENER_BATCH_WAIT_SECONDS` (60) |
| How often the folder is looked at | `CDI_LISTENER_POLL_SECONDS` (10) |

- The next batch starts **only when every file of this one has finished**.
- **One bad file does not stop its batch**: it goes to `error` with its note, the others carry on.
- Seven files waiting are run as 3, then 3, and the last one after its waiting time.
- `run --once` does not hold a short batch back, because it has no later pass to start it.
- Each batch has an id, written into the history of every file in it (`listener_file.history`,
  migration **0030**), together with each file's **seconds from the drop to its result**. "The
  drop" is the moment the listener first saw the file. `run` prints both; the load test
  (LS-S10, not built) will read them.

How long a lone file can wait before reading starts: the waiting time (60 s) plus up to two
looks at the folder for it to count as finished (2 x 10 s). Set these to the business need.

**How long a batch takes.** The model server reads one document at a time, 24-37 s each
(measured). The three files of a batch are handed over together but are read one after another
by the server, so a batch takes about 72-111 s today. That is arithmetic on the measured times,
not a measurement of a batch. Faster batches need the batching server of RD-S5.

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
