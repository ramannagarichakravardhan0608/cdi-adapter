# OUT-S3 — one upload gives one result: what is guaranteed, and by what

Written 2026-10-13. Status: **proven with the real job runner against a stand-in database and
model** (`tests/test_out_s3_acceptance_unit.py`). **Not yet proven on a running system** — the
kill-test (`scripts/chaos_upload.py`) has not been run; no instance was up.

## The guarantee

| What happens | What you get | What does it |
|---|---|---|
| The same upload is sent again (retry, double tap, timeout and resend) | The first job is answered again. Nothing new is stored, queued or read. | One idempotency key per upload (`webapp/jobstore.py::idempotency_key`): the client's `Idempotency-Key` header, or the SHA-256 of every file plus the sender and the patient context. The key is unique in `web_job`; two copies arriving at the same moment are decided by that index. |
| The same picture is sent in another upload (another file name, another person, another day) | The same document, and the result it already has. It is **not** read a second time. | The document's fingerprint is the SHA-256 of its bytes (`ingest/service.py`). `JobRunner._reuse` then answers the page with the existing result when that document is in front of a reviewer or approved. |
| …but its review was cleared away | It is read again (owner's rule, 2026-10-10). Still one document and one result — the new reading replaces the old. | `canon/repo.py::has_live_item` |
| …or the new upload names a different patient | It is read again, so the identity check runs. | `JobRunner._reuse` |
| The web app stops in the middle of a job | The job is finished by whichever instance comes up. Pages already finished are kept and not read again; a page that was read but not yet filed continues from there; the rest start again from the stored upload. | The upload is stored (files, then the job row) **before** it is acknowledged. Each running job holds a lease that a heartbeat renews; a stopped instance's lease runs out and the orphan sweep of any instance claims the job (`PgJobStore.orphans`, `JobRunner.sweep`, `_resume`). A runner that lost its lease writes nothing more. |
| The upload could not be stored | It is refused (`NotAccepted`), never acknowledged. | `jobstore.submit` |
| One file of a batch cannot be read | That page is marked `error` with the reason; every other page finishes; the job ends in `review`. | `JobRunner._phase1` / `_after_extract` catch per document. |
| A job stops its runner every time | After `CDI_JOB_MAX_ATTEMPTS` (3) it is given up, moved to the dead-letter table and no longer retried. | `JobRunner.run_stored`, `PgJobStore.dead_letter` |

## What is NOT guaranteed

* **A page being read when the web app stops is read again** after the restart. The result is
  still one (the second reading replaces the first), but the model's time is spent twice.
* **The same picture twice inside one upload** is one document and one result, but both copies
  may be read before either has a result. The upload's result marks the second as `duplicate`.
* **Two different uploads of the same picture arriving at the same moment** can both be read:
  the "already has a result" rule only sees a result that exists.
* Nothing here covers losing the database or the document store themselves (that is the backup
  and restore work, ENT-S4).

## How to prove it on a running system

    CDI_LOAD_PASSWORD=... python scripts/chaos_upload.py --host http://127.0.0.1:8888 \
        --user <intake account> --files 20 --corrupt 1 \
        --kill-cmd "<stops the web app>" --restart-cmd "bash infra/runpod/start_apptier.sh" \
        --report docs/evidence/OUT-S3/chaos_upload.json

It draws 20 made-up pages (no real patient data), sends them, sends them all again, stops the
web app, starts it, waits, sends them a third time, and prints the counts with PASS / FAIL for:
a repeated send answered with the first job; one job per upload; no job lost; no document
doubled; one result per readable file; each unreadable file reported with its reason.

In the stand-in rehearsal those counts are: 20 files, 40 sends, 20 jobs, 0 unfinished,
19 documents read, 19 distinct, 19 results, 1 reported unreadable.
