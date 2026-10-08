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
tries (LS-S7), batches of three (LS-S5), the health view that shows the ignored list (LS-S9),
and marking the stored document as having come from the listener rather than the screen (LS-S8).
"""

from __future__ import annotations

import fnmatch
import hashlib
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from cdi_adapter.ingest import pages
from cdi_adapter.ingest.pages import DocumentRefused, UnsupportedDocument, sniff_mime
from cdi_adapter.listener.auth import scrub
from cdi_adapter.listener.connectors import Connector, ListenerError, RemoteFile, SignInRequired
from cdi_adapter.listener.ledger import DbLedger, Entry, Ledger, MemoryLedger
from cdi_adapter.logging import get_logger

log = get_logger(__name__)

NOT_A_SCAN = "Please add a photo, PDF or scan"
# The reasons a file is never taken, as a person is shown them (LS-S3).
NOT_ALLOWED = "file type not allowed"
TEMPORARY = "temporary file"
DEFAULT_PATTERNS = ("*.pdf", "*.png", "*.jpg", "*.jpeg", "*.tif", "*.tiff")
# Data errors: the file itself is the problem, so it is refused once and never tried again.
DATA_ERRORS = frozenset({"not_a_scan", "too_large", "too_many_pages", "too_many_pixels",
                         "not_readable", "rescan"})  # fmt: skip
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


@dataclass(frozen=True)
class Reader:
    """How a file is read. ``ready`` returns ``None`` when a file can be read now, or a plain
    sentence saying why not; ``read`` takes the file's name, bytes and type."""

    read: Callable[[str, bytes, str], Outcome]
    ready: Callable[[], str | None] = lambda: None
    ledger: Ledger | None = None  # where the files it has seen are kept; None = in memory


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


@dataclass
class Listener:
    """One watched folder, the reader its files go to, and the ledger of what was seen."""

    connector: Connector
    reader: Reader
    ledger: Ledger = field(default_factory=MemoryLedger)
    stable_polls: int = 2
    patterns: tuple[str, ...] = DEFAULT_PATTERNS
    max_bytes: int = 50 * 1024 * 1024
    lease_seconds: float = 1200.0
    worker: str = field(default_factory=socket.gethostname)
    now: Callable[[], datetime] = lambda: datetime.now(tz=UTC)
    _resumed: bool = False

    @classmethod
    def from_settings(cls, connector: Connector, reader: Reader, settings: Any) -> Listener:
        """The listener the settings describe. The ledger is the reader's (the table, for the
        real pipeline) or, for a reader that brings none, one kept in memory."""
        return cls(connector, reader, ledger=reader.ledger or MemoryLedger(),
                   stable_polls=settings.listener_stable_polls,
                   patterns=patterns_of(settings.listener_patterns),
                   max_bytes=settings.listener_max_bytes,
                   lease_seconds=settings.listener_lease_seconds,
                   worker=settings.listener_worker or socket.gethostname())  # fmt: skip

    # ---------------------------------------------------------------- one pass

    def poll(self) -> dict[str, Any]:
        """One look at the folder. Returns what was done, for the log and the tests."""
        report: dict[str, Any] = {"ready": True, "waiting": 0, "settling": 0, "ignored": 0,
                                  "ignored_files": [], "held": 0, "succeeded": [],
                                  "duplicates": [], "failed": [], "resumed": 0}  # fmt: skip
        not_ready = self.reader.ready()
        if not_ready:
            report.update(ready=False, reason=not_ready)
            return report
        source = self.connector.source_id
        if not self._resumed:  # the first pass of a run: files a crash left in "processing"
            self._resumed = True
            for left in sorted(self.connector.list("processing"), key=_oldest):
                entry = self.ledger.find(source, left.id) or self.ledger.observe(source, left)
                if not self.ledger.claim(entry, self.worker, self.lease_seconds):
                    report["held"] += 1  # another worker still has it
                    continue
                report["resumed"] += 1
                self._read(left, entry, report)
        for file in sorted(self.connector.list("inbox"), key=_oldest):  # the oldest first
            reason = why_ignored(file.name, self.patterns)
            if reason:
                report["ignored"] += 1
                report["ignored_files"].append({"file": file.name, "reason": reason})
                if self.ledger.ignore(source, file, reason):  # said once, not on every look
                    log.info("listener_file_ignored", file=file.name, reason=reason)
                continue
            report["waiting"] += 1
            entry = self.ledger.observe(source, file)
            if not file.size or entry.stable_polls < self.stable_polls:
                report["settling"] += 1  # still arriving, or not yet seen unchanged often enough
                continue
            if not self.ledger.claim(entry, self.worker, self.lease_seconds):
                report["held"] += 1  # another worker took it first
                continue
            self._take(file, entry, report)
        return report

    def _take(self, file: RemoteFile, entry: Entry, report: dict[str, Any]) -> None:
        try:
            moved = self.connector.move(file, "processing")
        except SignInRequired:
            raise
        except ListenerError as exc:  # gone, or held open by whoever is still writing it
            log.warning("listener_could_not_take", file=file.name, error=str(exc))
            self.ledger.finish(entry, "seen")  # give the lease back: looked at again next pass
            return
        self.ledger.moved(entry, moved)
        self._read(moved, entry, report)

    def _too_large(self, size: int) -> Outcome:
        mb = 1024 * 1024
        return Outcome(False, f"the file is {size / mb:.1f} MB; the limit is "
                              f"{self.max_bytes / mb:.0f} MB. It was not read.",
                       "too_large")  # fmt: skip

    def _read(self, file: RemoteFile, entry: Entry, report: dict[str, Any]) -> None:
        """Check the file as untrusted, then have it read. Nothing here opens the file further
        than its header until every check has passed, so a refused file is never half-read."""
        sha: str | None = None
        try:
            if file.size and file.size > self.max_bytes:  # judged by the listing: no download
                outcome = self._too_large(file.size)
            else:
                raw = self.connector.download(file)
                sha = hashlib.sha256(raw).hexdigest()
                refused = self._judge(file, raw)
                if refused is not None:
                    outcome = refused
                else:
                    first = self.ledger.processed(sha)
                    if first is not None and first.id != entry.id:
                        self._duplicate(file, entry, first, sha, report)
                        return
                    outcome = self.reader.read(file.name, raw, sniff_mime(raw, file.name))
        except SignInRequired:  # not this file's failure: the listener must stop
            raise
        except Exception as exc:  # noqa: BLE001 - one file's failure must not stop the folder
            outcome = Outcome(False, scrub(f"{type(exc).__name__}: {exc}"), "failed")
        self._file(file, entry, outcome, sha, report)

    def _judge(self, file: RemoteFile, raw: bytes) -> Outcome | None:
        """The data errors: ``None`` when the file may be read, else why it is refused."""
        if len(raw) > self.max_bytes:  # a drive that did not say the size when listing
            return self._too_large(len(raw))
        try:
            mime = sniff_mime(raw, file.name)
        except UnsupportedDocument:
            return Outcome(False, NOT_A_SCAN, "not_a_scan")
        try:
            pages.inspect(raw, mime)
        except DocumentRefused as exc:  # too many pages, or too large to open safely
            return Outcome(False, str(exc), exc.kind)
        except UnsupportedDocument:
            pass  # cannot be opened at all: the pipeline's picture check says so, in its words
        return None

    def _duplicate(self, file: RemoteFile, entry: Entry, first: Entry, sha: str,
                   report: dict[str, Any]) -> None:  # fmt: skip
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
                           duplicate_of=first.id)  # fmt: skip
        report["duplicates"].append({"file": done.name, "same_as": first.name,
                                     "job": first.job_id})  # fmt: skip
        log.info("listener_file_duplicate", file=done.name, same_as=first.name, job=first.job_id)

    def _file(self, file: RemoteFile, entry: Entry, outcome: Outcome, sha: str | None,
              report: dict[str, Any]) -> None:  # fmt: skip
        """Put the file where its outcome says, and say why if it did not go through."""
        try:
            if outcome.ok:
                done = self.connector.move(file, "success")
                self.ledger.finish(entry, "succeeded", file=done, sha256=sha,
                                   job_id=outcome.job_id)  # fmt: skip
                report["succeeded"].append(done.name)
                log.info("listener_file_read", file=done.name, job=outcome.job_id)
                return
            failed = self.connector.move(file, "error")
            reason = scrub(outcome.reason or "")
            refused = outcome.kind in DATA_ERRORS  # the file itself is wrong: never retried
            self.ledger.finish(entry, "refused" if refused else "failed", file=failed,
                               kind=outcome.kind, reason=reason, sha256=sha,
                               job_id=outcome.job_id)  # fmt: skip
            stamp = self.now().strftime("%Y%m%dT%H%M%SZ")
            self.connector.write_note(f"{failed.name}.{stamp}.log", failure_note(failed, outcome))
            report["failed"].append({"file": failed.name, "kind": outcome.kind,
                                     "reason": outcome.reason, "retry": not refused})  # fmt: skip
            log.warning("listener_file_failed", file=failed.name, kind=outcome.kind,
                        reason=reason, job=outcome.job_id, retry=not refused)  # fmt: skip
        except SignInRequired:
            raise
        except ListenerError as exc:
            log.error("listener_could_not_file", file=file.name, error=str(exc))

    # ---------------------------------------------------------------- until stopped

    def run(self, *, interval: float, once: bool = False,
            sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:  # fmt: skip
        """Watch the folder. ``once`` looks as many times as a file needs to be seen unchanged
        (``stable_polls``), ``interval`` apart - only the last look can take a new file - and
        stops."""
        self.connector.ensure_folders()
        report = self.poll()
        if once:
            for _ in range(max(1, self.stable_polls - 1)):
                if not (report["ready"] and report["settling"]):
                    break
                sleep(interval)
                later = self.poll()
                later["resumed"] += report["resumed"]
                for key in ("succeeded", "duplicates", "failed"):
                    later[key] = report[key] + later[key]
                report = later
            return report
        while True:  # pragma: no cover - the long-running loop
            sleep(interval)
            report = self.poll()


def failure_note(file: RemoteFile, outcome: Outcome) -> str:
    """The note left in ``log`` for a file that did not go through: why, and what to do."""
    advice = {"not_a_scan": "This file is not a photo, a PDF or a scan, so it was not read.",
              "quality": "Take the picture again and drop the new one into the folder.",
              "rescan": "This is a data error: the file will not be tried again. Scan or "
                        "photograph the prescription again and drop the new file.",
              "not_readable": "This is a data error: the file will not be tried again. It is "
                              "named like a PDF but is not one that can be opened.",
              "too_large": "This is a data error: the file will not be tried again. Send a "
                           "smaller scan.",
              "too_many_pages": "This is a data error: the file will not be tried again. "
                                "Split it and drop one prescription per file.",
              "too_many_pixels": "This is a data error: the file will not be tried again.",
              "timeout": "The file may still be read: look for it under its job number before "
                         "sending it again."}.get(outcome.kind or "", "")  # fmt: skip
    lines = [f"file:    {file.name}", f"reason:  {scrub(outcome.reason or 'not given')}",
             f"kind:    {outcome.kind or 'failed'}"]  # fmt: skip
    if outcome.job_id:
        lines.append(f"job:     {outcome.job_id}")
    if advice:
        lines.append(f"next:    {advice}")
    return "\n".join(lines) + "\n"


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
        from sqlalchemy import text

        from cdi_adapter.db import session_scope
        from cdi_adapter.storage import get_store

        try:
            with session_scope() as conn:
                conn.execute(text("SELECT 1"))
        except Exception as exc:  # noqa: BLE001 - any failure means "not now"
            return f"the database cannot be reached ({type(exc).__name__})"
        try:
            store = get_store()
            store.client.head_bucket(Bucket=store.bucket)
        except Exception as exc:  # noqa: BLE001
            return f"the document store cannot be reached ({type(exc).__name__})"
        return ledger.ready()  # LS-S3: the listener_file table

    def read(name: str, raw: bytes, mime: str) -> Outcome:
        from cdi_adapter.webapp import jobstore

        job = jobstore.submit([(name, raw, mime)], actor=jobstore.LISTENER_ACTOR)
        try:
            job = jobstore.wait(job.id, timeout=s.listener_job_timeout_s)
        except TimeoutError:
            return Outcome(False, f"not finished after {s.listener_job_timeout_s:.0f} s",
                           "timeout", job.id)  # fmt: skip
        return outcome_of(job)

    return Reader(read=read, ready=ready, ledger=ledger)


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


def outcome_of(job: Any) -> Outcome:
    """A finished job, as the listener files it: through, or why not."""
    doc = job.docs[0]
    if doc.status == "done" and job.state != "mismatch":
        return Outcome(True, job_id=job.id)
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
