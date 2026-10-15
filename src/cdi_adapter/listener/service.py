"""The file listener's loop: take each new file from the watched folder and have it read.

    python -m cdi_adapter.listener run            # watch, until stopped
    python -m cdi_adapter.listener run --once     # one pass, then stop

**Only finished, new, allowed files are picked up** (LS-S3). The folder is a shared place and
every file in it is untrusted:

1. **allowed** - only names matching ``CDI_LISTENER_PATTERNS`` (pdf, png, jpg, jpeg, tif, tiff)
   are read. Anything else, and temporary files (``~$scan.pdf``, ``scan.part``, ``scan.tmp``),
   is left where it is and **listed as ignored, with the reason**;
2. **finished** - a file is left alone until its size and version tag have been the same for
   ``CDI_LISTENER_STABLE_POLLS`` looks in a row (2), because a file being copied or synced in
   is not yet the file;
3. **one worker** - the oldest file goes first, and a worker takes it only with a lease in the
   ledger (``ledger.py``, the ``listener_file`` table), so two listeners never read one file;
4. it is moved to ``processing`` and checked before anything opens it: over
   ``CDI_LISTENER_MAX_BYTES`` (50 MB), more pages than ``CDI_MAX_PAGES``, or a picture that
   would open to an absurd size (a decompression bomb) is **refused as a data error** - filed
   under ``error`` with a note, never retried, and never half-read;
5. **new** - the sha256 of its bytes is looked up: bytes that already went through are not read
   again. The second file is filed under ``success`` as a duplicate of the first;
6. it is read by the same pipeline as an upload from the screen, then moved to ``success`` - or
   to ``error``, with a note in ``log`` saying why.

**Files are run in batches** (LS-S5), so the model server is kept busy but not flooded. Up to
``CDI_LISTENER_BATCH_SIZE`` (3) finished files are taken together, oldest first, and read at
the same time; the next batch starts only when every file of this one has finished. Fewer than
a batch start once the oldest of them has waited ``CDI_LISTENER_BATCH_WAIT_SECONDS`` (60; 0 =
start whatever is there), so a lone file is not stuck. One bad file does not stop the others in
its batch. Each batch has an id, written into the history of every file in it, and each file's
seconds from the drop to its result are recorded.

Text inside a picture is only ever data: the listener passes bytes to the pipeline and acts on
nothing a document says (the pipeline's own guard for that is RD-S4 / ENT-S2).

Nothing is deleted and nothing is overwritten (the connector keeps both on a name clash), and a
file is only ever in one of the folders.

**The folder is never touched while the pipeline cannot be reached.** Before a pass takes any
file it asks the reader whether it is ready (database, object store, the ledger's table). If
not, the pass ends there and every file stays where it was dropped.

**After a crash** a file may be left in ``processing``. The first pass of a run takes those up
again - the ones this worker held, or whose lease has run out - before anything new.

**When the sign-in to the drive has expired the listener stops** (LS-S2): a
``SignInRequired`` from the drive is never swallowed as "one file failed" - it ends the pass and
the run. Nothing secret is written to a failure note (``auth.scrub``).

Which drive the folder is on is the connector's business (``connectors.py``); this module only
calls ``list``, ``download``, ``move`` and ``write_note``. *How* a file is read is a function
handed in (:data:`Reader`), so the loop is tested without a pipeline; :func:`pipeline_reader` is
the real one.

Not here yet (later stories of the epic): retrying a failed file and quarantining it after three
tries (LS-S7), the health view that shows the ignored list (LS-S9),
and marking the stored document as having come from the listener rather than the screen (LS-S8).
"""

from __future__ import annotations

import fnmatch
import hashlib
import re
import socket
import threading
import time  # noqa: F401 - kept importable: callers and tests reach service.time
import traceback
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cdi_adapter.ingest import pages
from cdi_adapter.ingest.pages import DocumentRefused, UnsupportedDocument, sniff_mime
from cdi_adapter.listener.auth import quarantine_alert, scrub
from cdi_adapter.listener.connectors import Connector, ListenerError, RemoteFile, SignInRequired
from cdi_adapter.listener.ledger import DbLedger, Entry, Ledger, MemoryLedger
from cdi_adapter.listener.recovery import DATA, classify_error, retried, wait_seconds
from cdi_adapter.logging import get_logger

log = get_logger(__name__)

NOT_A_SCAN = "Please add a photo, PDF or scan"
# The reasons a file is never taken, as a person is shown them (LS-S3).
NOT_ALLOWED = "file type not allowed"
TEMPORARY = "temporary file"
DEFAULT_PATTERNS = ("*.pdf", "*.png", "*.jpg", "*.jpeg", "*.tif", "*.tiff")
# Data errors: the file itself is the problem, so it is refused once and never tried again.
DATA_ERRORS = frozenset({"not_a_scan", "too_large", "too_many_pages", "too_many_pixels",
                         "not_readable", "rescan", "empty"})  # fmt: skip
# LS-S7: an empty file is taken (and refused as a data error) only once it has been empty and
# unchanged for this many looks - a file just created is empty for a moment.
EMPTY_POLLS = 6
# LS-S4: a picture too poor to read is not guessed at and not tried again - it is a data error.
RESCAN = "rescan: image quality below threshold"
# Files that are never a document: an editor's lock file, a half-finished download, the
# folder's own settings file.
_IGNORED_PREFIXES = (".", "~$", "~")
_IGNORED_SUFFIXES = (".tmp", ".partial", ".crdownload", ".download", ".part", ".ini", ".lnk")


@dataclass(frozen=True)
class Outcome:
    """What reading one file came to."""

    ok: bool
    reason: str | None = None  # in plain words, when it did not go through
    kind: str | None = None  # a short code for the reason: not_a_scan, quality, failed, timeout
    job_id: str | None = None
    trace: str | None = None  # the end of the error trace: where it failed, never what was read
    document_id: str | None = None  # LS-S8: the stored document the file became (its result)


@dataclass(frozen=True)
class Reader:
    """How a file is read. ``ready`` returns ``None`` when a file can be read now, or a plain
    sentence saying why not; ``read`` takes the file's name, bytes and type."""

    read: Callable[[str, bytes, str], Outcome]
    ready: Callable[[], str | None] = lambda: None
    ledger: Ledger | None = None  # where the files it has seen are kept; None = in memory
    # LS-S8: called with a document's id once a dropped file has been linked to it. The result
    # says where it came from (drive, folder, file name), and that is only known from the link -
    # so the result file written when the document was finished is written again here.
    linked: Callable[[str], Any] = lambda document_id: None


def ignored(name: str) -> bool:
    low = name.lower()
    return low.startswith(_IGNORED_PREFIXES) or low.endswith(_IGNORED_SUFFIXES)


def patterns_of(setting: str) -> tuple[str, ...]:
    """``"*.pdf, *.PNG"`` -> ``("*.pdf", "*.png")``."""
    return tuple(p.strip().lower() for p in setting.split(",") if p.strip())


def why_ignored(name: str, patterns: tuple[str, ...] = DEFAULT_PATTERNS) -> str | None:
    """Why a file is never taken, in the words a person is shown - or ``None`` if it may be."""
    if ignored(name):
        return TEMPORARY
    if not any(fnmatch.fnmatchcase(name.lower(), p) for p in patterns):
        return NOT_ALLOWED
    return None


def _oldest(file: RemoteFile) -> tuple[datetime, str]:
    return (file.modified or datetime.max.replace(tzinfo=UTC), file.name)


@dataclass(frozen=True)
class _Checked:
    """A file of a batch after its checks: refused (``outcome``), or its bytes ready to read."""

    file: RemoteFile
    entry: Entry
    raw: bytes = b""
    sha: str | None = None
    outcome: Outcome | None = None


def review_is_live(first: Entry) -> bool:
    """The real answer to :attr:`Listener.filed`, from the review tables. When the database
    cannot say, the file is a duplicate - reading twice is the mistake to avoid."""
    if not first.document_id:
        return True
    try:
        from cdi_adapter.canon.repo import has_live_item
        from cdi_adapter.db import session_scope

        with session_scope() as conn:
            return has_live_item(conn, [str(first.document_id)])
    except Exception:  # noqa: BLE001
        return True


@dataclass
class Listener:
    """One watched folder, the reader its files go to, and the ledger of what was seen."""

    connector: Connector
    reader: Reader
    ledger: Ledger = field(default_factory=MemoryLedger)
    stable_polls: int = 2
    patterns: tuple[str, ...] = DEFAULT_PATTERNS
    max_bytes: int = 50 * 1024 * 1024
    # LS-S7: a failed file is retried max_attempts times after waits of retry_base x 2^n, so
    # it runs at most max_attempts + 1 times; the last failure moves it to quarantine (LS-S6).
    max_attempts: int = 3
    retry_base_seconds: float = 60.0
    alert: Callable[[str, int], None] | None = None  # told when a file reaches quarantine
    lease_seconds: float = 900.0
    worker: str = field(default_factory=socket.gethostname)
    # LS-S5. The waiting time here is 0 (start whatever is there) so a listener built by hand
    # behaves as it always did; the running listener takes both from settings (3 and 60 s).
    batch_size: int = 3
    batch_wait_seconds: float = 0.0
    now: Callable[[], datetime] = lambda: datetime.now(tz=UTC)
    # Is the document an earlier file became still in front of a reviewer (or approved)? If
    # not - its review was rejected or cleared away - the same bytes dropped again are read
    # again instead of being filed as a duplicate (owner, 2026-10-10). A listener built by hand
    # says yes: the same bytes are a duplicate, as they always were.
    filed: Callable[[Entry], bool] = lambda first: True
    # LS-S9: set to stop gently - the running batch finishes, no new one starts, the loop ends
    stop: threading.Event = field(default_factory=threading.Event)
    _ready_since: dict[str, datetime] = field(default_factory=dict)
    _flush: bool = False

    @property
    def max_runs(self) -> int:
        """How many times one file may run: the first run and its retries."""
        return self.max_attempts + 1

    @classmethod
    def from_settings(cls, connector: Connector, reader: Reader, settings: Any) -> Listener:
        """The listener the settings describe. The ledger is the reader's (the table, for the
        real pipeline) or, for a reader that brings none, one kept in memory."""
        return cls(connector, reader, ledger=reader.ledger or MemoryLedger(),
                   stable_polls=settings.listener_stable_polls,
                   patterns=patterns_of(settings.listener_patterns),
                   max_bytes=settings.listener_max_bytes,
                   max_attempts=settings.listener_max_attempts,
                   retry_base_seconds=settings.listener_retry_base_seconds,
                   alert=quarantine_alert(settings),
                   filed=review_is_live if reader.ledger is not None else (lambda first: True),
                   lease_seconds=settings.listener_lease_seconds,
                   worker=settings.listener_worker or socket.gethostname(),
                   batch_size=settings.listener_batch_size,
                   batch_wait_seconds=settings.listener_batch_wait_seconds)  # fmt: skip

    # ---------------------------------------------------------------- one pass

    def poll(self) -> dict[str, Any]:
        """One look at the folder. Returns what was done, for the log and the tests."""
        report: dict[str, Any] = {"ready": True, "waiting": 0, "settling": 0, "ignored": 0,
                                  "ignored_files": [], "held": 0, "succeeded": [],
                                  "duplicates": [], "failed": [], "resumed": 0,
                                  "batches": [], "timings": [], "batch_waiting": 0}  # fmt: skip
        not_ready = self.reader.ready()
        if not_ready:
            report.update(ready=False, reason=not_ready)
            self._beat(report, ok=False, reason=not_ready)
            return report
        source = self.connector.source_id
        # LS-S8: where the file came from, in words, kept with it and shown on its result
        folder = getattr(self.connector, "folder_label", None)
        # Files left in "processing" (LS-S9). On every pass, not only the first: a file whose
        # worker died is taken up as soon as its lease has run out, by whichever listener is
        # looking. A file this worker itself left behind is taken at once (the lease is its
        # own); one another live worker holds is left alone.
        for left in sorted(self.connector.list("processing"), key=_oldest):
            entry = self.ledger.find(source, left.id) or self.ledger.observe(source, left, folder)
            if not self.ledger.claim(entry, self.worker, self.lease_seconds):
                report["held"] += 1  # another worker still has it
                continue
            report["resumed"] += 1
            self._read(left, entry, report)
        finished: list[tuple[RemoteFile, Entry]] = []
        still_waiting: list[Entry] = []  # in the inbox when this pass ends
        for file in sorted(self.connector.list("inbox"), key=_oldest):  # the oldest first
            reason = why_ignored(file.name, self.patterns)
            if reason:
                report["ignored"] += 1
                report["ignored_files"].append({"file": file.name, "reason": reason})
                if self.ledger.ignore(source, file, reason):  # said once, not on every look
                    log.info("listener_file_ignored", file=file.name, reason=reason)
                continue
            report["waiting"] += 1
            entry = self.ledger.observe(source, file, folder)
            needed = self.stable_polls if file.size else max(self.stable_polls, EMPTY_POLLS)
            if entry.stable_polls < needed:
                report["settling"] += 1  # still arriving, or not yet seen unchanged often enough
                still_waiting.append(entry)
                continue
            finished.append((file, entry))
        # LS-S5: the finished files go in batches - a full one at once, a short one only when
        # its oldest file has waited long enough - and one batch ends before the next begins.
        at = self.now()
        self._ready_since = {f.id: self._ready_since.get(f.id, at) for f, _ in finished}
        # LS-S9: told to stop, the batch that is running finishes and no new one is started
        while not self.stop.is_set() and (batch := self.select_batch(finished)):
            finished = finished[len(batch) :]
            self.run_batch(batch, report)
        report["batch_waiting"] = len(finished)  # fewer than a batch, not waited long enough
        still_waiting.extend(entry for _, entry in finished)
        self._beat(report, ok=True, waiting=still_waiting, source=source)
        return report

    def _beat(self, report: dict[str, Any], *, ok: bool, waiting: list[Entry] | None = None,
              reason: str | None = None, source: str | None = None) -> None:  # fmt: skip
        """Write the heartbeat (LS-S9): this look happened, whether it succeeded, and what is
        still waiting. It is what the health view and the "not polling" alert read. Writing it
        never fails a pass - a listener that cannot say it is alive is still better alive."""
        try:
            source = source or self.connector.source_id
            left = waiting or []
            oldest = min((e.first_seen_at for e in left if e.first_seen_at), default=None)
            self.ledger.heartbeat(source, self.worker, ok=ok, waiting=len(left),
                                  oldest_waiting_at=oldest, reason=reason)  # fmt: skip
        except SignInRequired:
            raise
        except Exception as exc:  # noqa: BLE001 - see above
            log.warning("listener_heartbeat_not_written", error=type(exc).__name__)

    # ---------------------------------------------------------------- batches (LS-S5)

    def select_batch(
        self, finished: list[tuple[RemoteFile, Entry]]
    ) -> list[tuple[RemoteFile, Entry]]:
        """The next batch out of the finished files (given oldest first), or ``[]`` when
        nothing should start yet. A full batch starts at once. Fewer than a batch start when
        the one that has waited longest has waited ``batch_wait_seconds`` - or straight away
        when that is 0, and on a ``--once`` run, which has no later pass to wait for."""
        if len(finished) >= self.batch_size:
            return finished[: self.batch_size]
        if not finished:
            return []
        if self.batch_wait_seconds <= 0 or self._flush:
            return list(finished)
        longest = min(self._ready_since.get(f.id, self.now()) for f, _ in finished)
        waited = (self.now() - longest).total_seconds()
        return list(finished) if waited >= self.batch_wait_seconds else []

    def run_batch(self, batch: list[tuple[RemoteFile, Entry]], report: dict[str, Any]) -> str:
        """Run one batch to its end and return its id.

        The files are taken (lease, then moved to ``processing``) and checked one after another,
        oldest first; then **read at the same time**, each on its own thread; and the method
        returns only when every one of them has finished. A file that fails - at any step -
        is filed under ``error`` on its own; the others carry on. The batch's id is written
        into the history of every file in it."""
        batch_id = uuid.uuid4().hex[:12]
        began = self.now()
        taken: list[tuple[RemoteFile, Entry]] = []
        for position, (file, entry) in enumerate(batch, start=1):
            if not self.ledger.claim(entry, self.worker, self.lease_seconds):
                report["held"] += 1  # another worker took it first
                continue
            self.ledger.note(entry, {"event": "batch", "batch_id": batch_id, "position": position,
                                     "size": len(batch), "at": began.isoformat()})  # fmt: skip
            moved = self._take(file, entry)
            if moved is not None:
                taken.append((moved, entry))
        self._ready_since = {k: v for k, v in self._ready_since.items()
                             if k not in {f.id for f, _ in batch}}  # fmt: skip
        to_read: list[_Checked] = []
        same_bytes: list[tuple[RemoteFile, Entry]] = []
        for file, entry in taken:
            checked = self._check(file, entry)
            if checked.outcome is not None:  # refused: a data error, or it could not be fetched
                self._file(file, entry, checked.outcome, checked.sha, report, batch_id)
                continue
            first = self.ledger.processed(checked.sha or "")
            if first is not None and first.id != entry.id and self.filed(first):
                self._duplicate(file, entry, first, checked.sha or "", report, batch_id)
            elif checked.sha in {c.sha for c in to_read}:
                same_bytes.append((file, entry))  # the same picture twice in one batch
            else:
                to_read.append(checked)
        stop: SignInRequired | None = None
        if to_read:
            with ThreadPoolExecutor(max_workers=len(to_read),
                                    thread_name_prefix="listener-batch") as pool:  # fmt: skip
                running = [pool.submit(self._read_checked, c) for c in to_read]
            for checked, future in zip(to_read, running, strict=True):  # all have finished
                try:
                    outcome = future.result()
                except SignInRequired as exc:  # not this file's failure: stop after the batch
                    stop = stop or exc
                    continue
                self._file(checked.file, checked.entry, outcome, checked.sha, report, batch_id)
        for file, entry in same_bytes:  # after their twin: one result, the second points at it
            self._read(file, entry, report, batch_id)
        seconds = round((self.now() - began).total_seconds(), 3)
        report["batches"].append({"id": batch_id, "files": [f.name for f, _ in batch],
                                  "read_together": len(to_read), "seconds": seconds})  # fmt: skip
        log.info("listener_batch_done", batch=batch_id, files=len(batch),
                 read_together=len(to_read), seconds=seconds)  # fmt: skip
        if stop is not None:
            raise stop
        return batch_id

    def _take(self, file: RemoteFile, entry: Entry) -> RemoteFile | None:
        """Move a claimed file to ``processing``. ``None`` when it could not be moved: the lease
        is given back and the file is looked at again on the next pass."""
        try:
            moved = self.connector.move(file, "processing")
        except SignInRequired:
            raise
        except ListenerError as exc:  # gone, or held open by whoever is still writing it
            log.warning("listener_could_not_take", file=file.name, error=str(exc))
            self.ledger.finish(entry, "seen")
            return None
        self.ledger.moved(entry, moved)
        self._moved(entry, moved)
        return moved

    def _linked(self, document_id: str | None) -> None:
        """Tell the reader a dropped file now points at this document. Never fails the file."""
        if not document_id:
            return
        try:
            self.reader.linked(document_id)
        except Exception as exc:  # noqa: BLE001 - the file is read and filed; this is a refresh
            log.warning("listener_result_not_refreshed", document=document_id,
                        error=type(exc).__name__)  # fmt: skip

    def _moved(self, entry: Entry, to: RemoteFile) -> None:
        """Every move of a file is written to its history (LS-S6)."""
        self.ledger.note(entry, {"event": "moved", "to": to.folder, "name": to.name,
                                 "at": self.now().isoformat()})  # fmt: skip

    def _too_large(self, size: int) -> Outcome:
        mb = 1024 * 1024
        return Outcome(False, f"the file is {size / mb:.1f} MB; the limit is "
                              f"{self.max_bytes / mb:.0f} MB. It was not read.",
                       "too_large")  # fmt: skip

    def _check(self, file: RemoteFile, entry: Entry) -> _Checked:
        """Fetch the file and check it as untrusted. Nothing here opens the file further than
        its header, so a refused file is never half-read."""
        try:
            if file.size and file.size > self.max_bytes:  # judged by the listing: no download
                return _Checked(file, entry, outcome=self._too_large(file.size))
            raw = self.connector.download(file)
            sha = hashlib.sha256(raw).hexdigest()
            return _Checked(file, entry, raw, sha, self._judge(file, raw))
        except SignInRequired:  # not this file's failure: the listener must stop
            raise
        except Exception as exc:  # noqa: BLE001 - one file's failure must not stop the folder
            return _Checked(file, entry, outcome=Outcome(
                False, scrub(f"{type(exc).__name__}: {exc}"), "failed",
                trace=trace_end(exc)))  # fmt: skip

    def _read_checked(self, checked: _Checked) -> Outcome:
        """Have a checked file read. Runs on a batch's thread: it touches nothing shared, and
        whatever goes wrong comes back as this file's outcome, not as an exception."""
        try:
            name = checked.file.name
            return self.reader.read(name, checked.raw, sniff_mime(checked.raw, name))
        except SignInRequired:
            raise
        except Exception as exc:  # noqa: BLE001 - one file's failure must not stop its batch
            return Outcome(False, scrub(f"{type(exc).__name__}: {exc}"), "failed",
                           trace=trace_end(exc))  # fmt: skip

    def _read(self, file: RemoteFile, entry: Entry, report: dict[str, Any],
              batch_id: str | None = None) -> None:  # fmt: skip
        """One file on its own, start to finish: check, read, file. (A file left in
        ``processing`` by a crash, and the second of two identical files in a batch.)"""
        checked = self._check(file, entry)
        outcome = checked.outcome
        if outcome is None:
            first = self.ledger.processed(checked.sha or "")
            if first is not None and first.id != entry.id and self.filed(first):
                self._duplicate(file, entry, first, checked.sha or "", report, batch_id)
                return
            outcome = self._read_checked(checked)
        self._file(file, entry, outcome, checked.sha, report, batch_id)

    def _judge(self, file: RemoteFile, raw: bytes) -> Outcome | None:
        """The data errors: ``None`` when the file may be read, else why it is refused."""
        if not raw:
            return Outcome(False, "the file is empty (0 bytes).", "empty")
        if len(raw) > self.max_bytes:  # a drive that did not say the size when listing
            return self._too_large(len(raw))
        try:
            mime = sniff_mime(raw, file.name)
        except UnsupportedDocument:
            return Outcome(False, NOT_A_SCAN, "not_a_scan")
        try:
            pages.inspect(raw, mime)
        except DocumentRefused as exc:  # too many pages, too large to open, not a real PDF
            return Outcome(False, str(exc), exc.kind)
        except UnsupportedDocument:
            pass  # cannot be opened at all: the pipeline's picture check says so, in its words
        return None

    def _timed(self, entry: Entry, name: str, state: str, report: dict[str, Any],
               batch_id: str | None) -> None:  # fmt: skip
        """Record how long the file took, from the drop to its result (LS-S5 AC4): in the
        file's history, in the pass's report and in the log, for the load test to read.
        "The drop" is the moment the listener first saw the file."""
        at = self.ledger.now()  # the same clock the row's own times were written by
        row = self.ledger.find(entry.source_id, entry.file_id) or entry
        seen = row.first_seen_at or entry.first_seen_at
        seconds = round((at - seen).total_seconds(), 3) if seen else None
        self.ledger.note(row, {"event": state, "batch_id": batch_id, "at": at.isoformat(),
                               "seconds_from_drop": seconds})  # fmt: skip
        report["timings"].append({"file": name, "batch": batch_id, "state": state,
                                  "seconds_from_drop": seconds})  # fmt: skip
        log.info("listener_file_timing", file=name, batch=batch_id, state=state,
                 seconds_from_drop=seconds)  # fmt: skip

    def _duplicate(self, file: RemoteFile, entry: Entry, first: Entry, sha: str,
                   report: dict[str, Any], batch_id: str | None = None) -> None:  # fmt: skip
        """The same bytes as a file already read: one result exists, and this file points at
        it. It is filed with the ones that went through - nothing about it failed."""
        try:
            done = self.connector.move(file, "success")
        except SignInRequired:
            raise
        except ListenerError as exc:
            log.error("listener_could_not_file", file=file.name, error=str(exc))
            return
        self.ledger.finish(entry, "duplicate", file=done, kind="duplicate", sha256=sha,
                           reason=f"the same bytes as {first.name}", job_id=first.job_id,
                           duplicate_of=first.id, document_id=first.document_id)  # fmt: skip
        self._linked(first.document_id)  # the result now lists this arrival too
        self._moved(entry, done)
        report["duplicates"].append({"file": done.name, "same_as": first.name,
                                     "job": first.job_id})  # fmt: skip
        log.info("listener_file_duplicate", file=done.name, same_as=first.name, job=first.job_id)
        self._timed(replace(entry, file_id=done.id), done.name, "duplicate", report, batch_id)

    def _file(self, file: RemoteFile, entry: Entry, outcome: Outcome, sha: str | None,
              report: dict[str, Any], batch_id: str | None = None) -> None:  # fmt: skip
        """File the prescription by its outcome (LS-S6).

        * read, and the result saved -> ``success``. The reader says "ok" only once the
          database holds the result, so the move comes after the save; a crash in between
          leaves the file in ``processing``, and the next run finds the saved result and only
          moves the file. **Nothing is written to ``log`` for a success.**
        * failed -> ``error``, with one note in ``log``. A failure that trying again can
          help (LS-S7: transient, or unknown) waits there for the retry agent;
        * failed when its retries are used up (run ``max_attempts + 1``) -> ``quarantine``,
          with its note, and an alert. A data error is never run again, so it stays in
          ``error`` whatever the count.

        Nothing is deleted, and the connector keeps both files when the name is taken."""
        try:
            if outcome.ok:
                done = self.connector.move(file, "success")
                self.ledger.finish(entry, "succeeded", file=done, sha256=sha,
                                   job_id=outcome.job_id,
                                   document_id=outcome.document_id)  # fmt: skip
                self._linked(outcome.document_id)
                self._moved(entry, done)
                report["succeeded"].append(done.name)
                log.info("listener_file_read", file=done.name, job=outcome.job_id)
                self._timed(replace(entry, file_id=done.id), done.name, "succeeded", report,
                            batch_id)  # fmt: skip
                return
            error_class = classify_error(outcome.kind, outcome.reason)
            refused = error_class == DATA  # the file itself is wrong: never retried
            run = self.ledger.failures(entry, sha) + 1  # which run of this file just failed
            used_up = not refused and run >= self.max_runs
            to = "quarantine" if used_up else "error"
            state = "quarantined" if used_up else "refused" if refused else "failed"
            failed = self.connector.move(file, to)
            reason = scrub(outcome.reason or "")
            self.ledger.finish(entry, state, file=failed, kind=outcome.kind, reason=reason,
                               sha256=sha, job_id=outcome.job_id)  # fmt: skip
            self._moved(entry, failed)
            stamp = self.now().strftime("%Y%m%dT%H%M%SZ")
            self.connector.write_note(
                f"{failed.name}.{stamp}.run{run}.log",
                failure_note(
                    failed,
                    outcome,
                    batch_id=batch_id,
                    run=run,
                    max_runs=self.max_runs,
                    to=to,
                    retry_in=wait_seconds(run, self.retry_base_seconds),
                ),
            )
            retry = retried(error_class) and not used_up
            report["failed"].append({"file": failed.name, "kind": outcome.kind,
                                     "reason": outcome.reason, "retry": retry, "run": run,
                                     "to": to, "class": error_class})  # fmt: skip
            log.warning("listener_file_failed", file=failed.name, kind=outcome.kind,
                        reason=reason, job=outcome.job_id, retry=retry, run=run, to=to,
                        error_class=error_class)  # fmt: skip
            if used_up and self.alert is not None:
                self.alert(failed.name, run)
            self._timed(replace(entry, file_id=failed.id), failed.name, state, report, batch_id)
        except SignInRequired:
            raise
        except ListenerError as exc:
            log.error("listener_could_not_file", file=file.name, error=str(exc))

    # ---------------------------------------------------------------- until stopped

    def run(self, *, interval: float, once: bool = False,
            sleep: Callable[[float], Any] | None = None) -> dict[str, Any]:  # fmt: skip
        """Watch the folder. ``once`` looks as many times as a file needs to be seen unchanged
        (``stable_polls``), ``interval`` apart - only the last look can take a new file - and
        stops; it does not hold a short batch back, since no later pass would start it.

        Without ``once`` it runs until :attr:`stop` is set (LS-S9: a stop signal): the batch
        that is running finishes, no new one starts, and the last pass's report is returned.
        The wait between looks ends at once when the stop comes."""
        sleep = sleep or self.stop.wait
        self.connector.ensure_folders()
        if once:
            self._flush = True
        report = self.poll() if once else self._pass(None)
        if once:
            for _ in range(max(1, self.stable_polls - 1)):
                if not (report["ready"] and report["settling"]):
                    break
                sleep(interval)
                later = self.poll()
                later["resumed"] += report["resumed"]
                for key in ("succeeded", "duplicates", "failed", "batches", "timings"):
                    later[key] = report[key] + later[key]
                report = later
            return report
        while not self.stop.is_set():
            sleep(interval)
            if self.stop.is_set():
                break
            report = self._pass(report)
        log.info("listener_stopped_gently", worker=self.worker)
        return report

    def _pass(self, last: dict[str, Any] | None) -> dict[str, Any]:
        """One look, in the loop that never ends. A look that breaks half-way - the database
        gone in the middle of a batch, the network cut - must not end the listener: found under
        test (LS-S10), where it died and nothing started it again. The look is given up, said
        so in the log, and the next one carries on; a file it had taken is still in
        ``processing`` under this worker's name and is picked up again there (LS-S9). Only a
        lost sign-in still stops it, as before: no later look can succeed without a person."""
        try:
            return self.poll()
        except SignInRequired:
            raise
        except Exception as exc:  # noqa: BLE001 - see above
            log.error("listener_pass_failed", worker=self.worker, error=type(exc).__name__,
                      action="carrying on with the next look")  # fmt: skip
            why = f"the look failed ({type(exc).__name__})"
            report: dict[str, Any] = {**(last or {}), "ready": False, "reason": why}
            self._beat(report, ok=False, reason=why)
            return report


# What the person is told to do, by kind of failure. A data error is never run again.
_AGAIN = "Rescan and drop it again."
_NEXT = {"not_a_scan": "This file is not a photo, a PDF or a scan, so it was not read.",
         "quality": "Take the picture again and drop the new one into the folder.",
         "empty": f"This is a data error: the file will not be tried again. {_AGAIN}",
         "rescan": "This is a data error: the file will not be tried again. Scan or photograph "
                   f"the prescription again. {_AGAIN}",
         "not_readable": "This is a data error: the file will not be tried again. It is named "
                         f"like a PDF but is not one that can be opened. {_AGAIN}",
         "too_large": "This is a data error: the file will not be tried again. Send a smaller "
                      f"scan. {_AGAIN}",
         "too_many_pages": "This is a data error: the file will not be tried again. Split it "
                           "and drop one prescription per file.",
         "too_many_pixels": f"This is a data error: the file will not be tried again. {_AGAIN}",
         "not_clinical": "This is a data error: the file will not be tried again. It was not "
                         "read as a clinical document.",
         "mismatch": "This is a data error: the file will not be tried again. It does not "
                     "belong to the patient it was sent for.",
         "timeout": "The file may still be read: look for it under its job number."}  # fmt: skip
# The kinds whose reason is written by this system from technical facts (a size, a page count,
# a picture-quality verdict). Any other reason is an error message from somewhere in the
# pipeline, and an error message can quote what was read from the picture.
_REASON_IS_OURS = frozenset({*DATA_ERRORS, "quality", "timeout", "not_clinical", "mismatch"})
_ERROR_NAME = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception|Timeout|Lost|Fault))\b")


def trace_end(exc: BaseException, frames: int = 6) -> str:
    """The end of the error trace: which error, and the last places the code was in - file,
    line and function. **Not the error's message and not the line of code**: either can carry
    a value read from the picture, and a failure note must not (LS-S6)."""
    walked = traceback.extract_tb(exc.__traceback__)[-frames:]
    places = [f"{Path(f.filename).name}:{f.lineno} in {f.name}" for f in walked]
    return "\n".join([*places, type(exc).__name__])


def note_reason(outcome: Outcome) -> str:
    """The reason as it may be written in a note. A reason this system wrote itself is given
    in full. For any other failure only the *kind of error* is given: the message stays in the
    job and the log, where access is controlled, because it may quote the document."""
    reason = outcome.reason or ""
    if outcome.kind in _REASON_IS_OURS:
        return scrub(reason) or "not given"
    named = _ERROR_NAME.match(reason)
    what = named.group(1) if named else "the pipeline failed"
    return f"{what} (the message is not written here: it may quote the document)"


def failure_note(file: RemoteFile, outcome: Outcome, *, batch_id: str | None = None,
                 run: int = 1, max_runs: int = 4, to: str = "error",
                 retry_in: float | None = None) -> str:  # fmt: skip
    """The note left in ``log`` for one failed run (LS-S6): the reason, the kind of error, the
    batch, which run it was, what happens next, and the end of the error trace. It holds
    reasons and technical details only - never a name or a value read from the picture."""
    kind = outcome.kind or "failed"
    error_class = classify_error(outcome.kind, outcome.reason)
    if to == "quarantine":
        then = (f"This was run {run} of {max_runs}: the retries are used up, so the file is in "
                "quarantine. It will not be tried again until a person looks at it.")  # fmt: skip
    elif error_class == DATA:
        then = _NEXT.get(kind, f"This is a data error: the file will not be tried again. {_AGAIN}")
    else:
        soon = f"in about {retry_in:.0f} s" if retry_in is not None else "after a wait"
        then = (f"{_NEXT[kind]} " if kind in _NEXT else "") + (
            f"This was run {run} of {max_runs}. The file is in error and will be tried again "
            f"by itself {soon} (retry {run} of {max_runs - 1}). If the last retry fails it is "
            "moved to quarantine.")  # fmt: skip
    lines = [f"file:    {file.name}", f"reason:  {note_reason(outcome)}", f"kind:    {kind}",
             f"class:   {error_class}", f"batch:   {batch_id or 'none'}",
             f"run:     {run} of {max_runs}"]  # fmt: skip
    if outcome.job_id:
        lines.append(f"job:     {outcome.job_id}")
    lines.append(f"next:    {then}")
    if outcome.trace:
        lines.append("trace:   " + scrub(outcome.trace).replace("\n", "\n         "))
    return "\n".join(lines) + "\n"


_failure_note = failure_note  # the name the backlog uses


# ------------------------------------------------------------------ the real reader


def pipeline_reader(settings: Any = None) -> Reader:
    """Read a file exactly as an upload from the screen is read: one job in the job store,
    run by the same runner. The file is done when its job has finished."""
    from cdi_adapter.config import get_settings

    s = settings or get_settings()
    ledger = DbLedger()

    def ready() -> str | None:
        # First knock on each door, and give up after a few seconds: with the database down a
        # full connection attempt can wait for minutes, and the listener must be able to say
        # "not now" and leave the folder alone straight away.
        for what, url, default_port in (("database", s.database_url, 5432),
                                        ("document store", s.s3_endpoint_url, 9000)):  # fmt: skip
            closed = _port_closed(url, default_port)
            if closed:
                return f"the {what} cannot be reached ({closed})"
        # LS-S10: the model server too. With it stopped for five minutes every file in flight
        # failed, was retried into the same wall and ended in quarantine. Now the folder is
        # left alone until it answers: a file waits in the inbox instead of using up its tries.
        # (Several replicas may be listed: one that answers is enough. No server in stub mode.)
        if s.mlserve_backend != "stub":
            replicas = [u.strip() for u in s.mlserve_url.split(",") if u.strip()]
            closed_all = [_port_closed(u, 8077) for u in replicas]
            if replicas and all(closed_all):
                return f"the model server cannot be reached ({closed_all[0]})"
        from sqlalchemy import text

        from cdi_adapter.db import session_scope
        from cdi_adapter.storage import get_store

        try:
            with session_scope() as conn:
                conn.execute(text("SELECT 1"))
        except Exception as exc:  # noqa: BLE001 - any failure means "not now"
            return f"the database cannot be reached ({type(exc).__name__})"
        try:
            if not get_store().healthy():  # SW-S1: through the store's interface
                return "the document store cannot be reached"
        except Exception as exc:  # noqa: BLE001
            return f"the document store cannot be reached ({type(exc).__name__})"
        return ledger.ready()  # LS-S3: the listener_file table

    def read(name: str, raw: bytes, mime: str) -> Outcome:
        from cdi_adapter.webapp import jobstore

        job = jobstore.submit([(name, raw, mime)], actor=jobstore.LISTENER_ACTOR)
        # LS-S10: a retry must be a new run. The same bytes are answered with the job that
        # already took them (E9-S3) - right after a restart, when that job is still running or
        # went through; wrong for a retry, where it is the job that FAILED, finished and never
        # to run again: every retry got the old failure back and the file went to quarantine
        # without being read a second time. So a job that comes back already failed is passed
        # over, and the file is submitted under a key made from that job - the same key again
        # if this retry is itself interrupted, so one retry is still one run.
        for _ in range(MAX_RETRY_CHAIN):
            if not spent(job):
                break
            job = jobstore.submit([(name, raw, mime)], actor=jobstore.LISTENER_ACTOR,
                                  given_key=f"listener-retry:{job.id}")  # fmt: skip
        try:
            job = jobstore.wait(job.id, timeout=s.listener_job_timeout_s)
        except TimeoutError:
            return Outcome(False, f"not finished after {s.listener_job_timeout_s:.0f} s",
                           "timeout", job.id)  # fmt: skip
        return outcome_of(job)

    def linked(document_id: str) -> None:
        from cdi_adapter import result

        result.refresh_files([document_id])

    return Reader(read=read, ready=ready, ledger=ledger, linked=linked)


def _port_closed(url: str, default_port: int, timeout: float = 3.0) -> str | None:
    """``None`` if something answers at the address in ``url`` (the first one, when several are
    given), else a short reason. A quick knock, not a login."""
    import socket
    from urllib.parse import urlsplit

    parts = urlsplit(url.split(",")[0].strip())
    host, port = parts.hostname or "127.0.0.1", parts.port or default_port
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return None
    except OSError as exc:
        return f"nothing answers at {host}:{port} - {type(exc).__name__}"


MAX_RETRY_CHAIN = 12  # far more than max_attempts: only so that the loop cannot run for ever


def spent(job: Any) -> bool:
    """True for a job that has already ended in a failure worth retrying: handed back for the
    same bytes, it would never run again (see ``pipeline_reader``). A job still running, one
    that went through, and one refused for what the document is (never retried) are not."""
    if getattr(job, "state", "running") == "running":
        return False
    return outcome_of(job).kind == "failed"


def outcome_of(job: Any) -> Outcome:
    """A finished job, as the listener files it: through, or why not."""
    doc = job.docs[0]
    if doc.status == "done" and job.state != "mismatch":
        # LS-S8: the document the file became - the link from the dropped file to its result
        return Outcome(True, job_id=job.id, document_id=getattr(doc, "document_id", None))
    if doc.status == "quality_hold":  # LS-S4: a data error - rescan, never retried
        held = getattr(doc, "quality", None) or {}
        why = held.get("message") or doc.error or held.get("code")
        return Outcome(False, f"{RESCAN} - {why}" if why else RESCAN, "rescan", job.id)
    if doc.status == "skipped":
        return Outcome(False, f'read as "{doc.doc_type}", which is not a clinical document',
                       "not_clinical", job.id)  # fmt: skip
    if job.state == "mismatch":
        return Outcome(False, "the document does not belong to the expected patient",
                       "mismatch", job.id)  # fmt: skip
    return Outcome(False, doc.error or f"ended as {doc.status}", "failed", job.id)
