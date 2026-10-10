# LS-S10 — the listener on a real GPU, under load, and broken on purpose

Run on 2026-10-09 (UTC) on a rented test host: one RTX 4090 (24 GB), one GPU worker, model
Qwen2.5-VL-7B, PostgreSQL, MinIO and Redis on the same host. Every file was a fake prescription
made by `scripts/listener_soak.py` (a made-up patient, marked "FAKE - SOAK TEST"). The listener
watched a local folder on the host and was started with the pod's own script
(`infra/runpod/start_listener.sh`).

Each case has a report (`.md`) and the same figures per file (`.json`). `driver.log` is the
timeline of the whole run.

## What was run

| Case | Files | Where they ended | Result |
|---|---|---|---|
| Soak: 200 files over 2 hours (`soak`) | 200 | 200 success | PASS: none missing, none in two places, each read once, 200 documents |
| Listener killed mid-batch (`kill`) | 6 | 6 success | PASS: three were in processing with their jobs accepted; each still read once |
| Model server stopped 5 minutes, before the fixes (`model-before-fixes`) | 6 | 6 quarantine | None lost, but none was ever read again |
| Model server stopped 5 minutes, after the fixes (`model-after-fixes`) | 6 | 6 success | PASS: read about 85 s after the server came back |
| Network cut for a minute (`network-cut`) | 3 | 3 success | PASS, after a fix: the listener had died on the first try |
| A file replaced while settling, and one while being read (`replace`) | 2 (+1) | 3 success | Correct: see the note below |
| Batch size 1 (`batch1`) | 12 | 12 success | PASS |
| Batch size 3 (`batch3`) | 12 | 12 success | PASS |
| Batch size 6 (`batch6`) | 12 | 12 success | PASS |
| First three files on the pod (`b3`) | 3 | 3 success | The run that found the lost hand-over; its times include the wait |

**The `replace` report says FAIL and that is the harness, not the listener.** A second version
of `soak-replace-0001.png` was dropped while the first was being read. The listener kept both -
the first under its name, the second as `soak-replace-0001.1.png` - and read each once. The
harness counts readings by file name, so it sees that name read twice. The file overwritten
while it was still settling was read once, as its last version.

## Seconds from drop to saved (FR4)

| Run | Files | Median | 95th percentile | Fastest | Slowest |
|---|---|---|---|---|---|
| Soak, one file every 36 s, batches of 3 with a 60 s wait | 200 | 73.8 | 111.0 | 33.2 | 118.0 |
| Batch size 1, 12 dropped at once | 12 | 139.5 | 201.9 | 27.4 | 214.0 |
| Batch size 3, 12 dropped at once | 12 | 92.9 | 144.8 | 39.0 | 144.9 |
| Batch size 6, 12 dropped at once | 12 | 109.1 | 146.2 | 72.0 | 146.2 |

In the three batch runs all 12 files were dropped together, so a file's time includes its wait
for the files ahead of it; with one GPU worker the whole 12 took about 3.5 minutes at size 1
and about 2.5 minutes at sizes 3 and 6. In the soak, part of each file's time is the listener
waiting up to 60 s for a full batch. These are printed fake prescriptions: real handwritten ones
take longer (one seen the same day took 46 s on its own).

## What the runs found, and what was fixed

1. **A job handed to the GPU worker could be lost for good.** With two jobs handed over at
   once, the one that waited had its lease renewed by the CPU worker that had just given it up,
   and the GPU worker was then refused it. Two of the first three files sat in `processing`.
   Giving a lease up now clears its owner.
2. **A retry never read the file again.** The same bytes were answered with the job that had
   already failed, so every retry returned the old failure and the file went to quarantine
   unread. A retry is now a new run.
3. **With the model server away, files used up their retries.** The listener now leaves the
   folder alone until the model server answers.
4. **A look that broke half-way ended the listener.** With the network cut during a move, the
   process died and nothing restarted it. A broken look is now given up and the next carries on.
5. **The pod's start script wrote down the wrong process.** `status` said DOWN while the
   listener ran, and `stop` stopped nothing. Fixed; start, status and gentle stop were then
   seen working on the host.

## What was not run

- **The real OneDrive through Microsoft's API (AC1).** It needs the owner's Entra client id and
  a person to sign in. The listener has run on the owner's OneDrive folder only as a folder
  synced to a PC.
- **Google Drive (AC2).** No folder or service account has been given.
- **"Too many requests" from a real drive.** Only a drive's API can answer that; it is covered
  by unit tests with a stand-in drive (LS-S1), not by a run.
- **Real prescriptions.** Fake ones only, as the story requires.
