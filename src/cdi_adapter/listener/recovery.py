"""The retry agent: a failed file is tried again by itself - when trying again can help
(backlog LS-S7).

    python -m cdi_adapter.listener recover          # run until stopped
    python -m cdi_adapter.listener recover --once   # one pass, then stop

A failure is one of three kinds (:func:`classify_error`):

* **data** - the file itself is the problem: empty, not a picture, corrupt, too blurred, too
  big, too many pages, not a clinical document. The same bytes will fail the same way, so the
  file is **never retried**; its note says to rescan and drop it again.
* **transient** - the system was the problem: a timeout, a server that was down or busy, a
  rate limit, a lost connection. **Retried.**
* **code** - anything else: an error nobody planned for. It may be a fault that heals or a bug
  that does not, and there is no telling which from here, so it is **retried too** - and if
  it never passes, the file ends in ``quarantine`` where a person sees it.

**The waits grow**: ``CDI_LISTENER_RETRY_BASE_SECONDS`` x 2 to the power n - 60 s before the
first retry, 120 s before the second, 240 s before the third. After
``CDI_LISTENER_MAX_ATTEMPTS`` (3) retries have failed - four runs in all - the listener moves
the file to ``quarantine`` and raises an alert.

**How a retry happens.** The agent does not read anything itself. When a failed file's wait is
over it moves the file from ``error`` back into the inbox and marks its row as waiting again;
the listener then takes it like any other file. The row keeps its history, so the listener
knows which run this is. Only rows of the drive the settings name are looked at
(``source_id``), so files that failed on another drive are left alone.

Attempts and history live in the ``listener_file`` table; this module keeps nothing itself, so
it can be stopped and started at any time.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from cdi_adapter.listener.connectors import Connector, ListenerError, SignInRequired
from cdi_adapter.listener.ledger import FAILED_RUNS, Entry, Ledger
from cdi_adapter.logging import get_logger

log = get_logger(__name__)

DATA, TRANSIENT, CODE = "data", "transient", "code"
# The kinds of failure that are the file's own fault. Retrying the same bytes cannot help.
DATA_KINDS = frozenset(
    {
        "empty",
        "not_a_scan",
        "not_readable",
        "too_large",
        "too_many_pages",
        "too_many_pixels",
        "rescan",
        "quality",
        "not_clinical",
        "mismatch",
    }
)
# Errors that say "not now" rather than "never": by the error's name ...
_TRANSIENT_NAME = re.compile(
    r"(Timeout|TimedOut|Connect|Connection|Unavailable|Busy|Thrott|RateLimit|TooManyRequests"
    r"|OperationalError|InterfaceError|LeaseLost|NotAccepted|BrokenPipe|RemoteProtocol"
    r"|ReadError|WriteError|PoolTimeout|OutOfMemory|EndpointConnection|ServerError)"
)
# ... or by what it says
_TRANSIENT_WORDS = re.compile(
    r"(?i)(timed? ?out|temporar|unavailable|\bbusy\b|rate limit|too many requests"
    r"|connection (refused|reset|aborted|closed)|not reachable|cannot be reached"
    r"|\b(429|502|503|504)\b|out of memory|try again|not finished after)"
)


def classify_error(kind: str | None, reason: str | None) -> str:
    """Sort a failure into ``data``, ``transient`` or ``code`` - see the module's notes."""
    if kind in DATA_KINDS:
        return DATA
    if kind == "timeout":
        return TRANSIENT
    text = reason or ""
    name = text.split(":", 1)[0].strip()
    if _TRANSIENT_NAME.search(name) or _TRANSIENT_WORDS.search(text):
        return TRANSIENT
    return CODE


_classify_error = classify_error  # the name the backlog uses


def retried(error_class: str) -> bool:
    """Is a failure of this kind tried again?"""
    return error_class != DATA


def wait_seconds(failed_runs: int, base: float) -> float:
    """How long to wait after the ``failed_runs``-th failed run before the next: the base for
    the first, then twice as long each time (60, 120, 240 with a base of 60)."""
    return float(base) * (2 ** max(0, failed_runs - 1))


def _failed_runs(row: Entry) -> list[dict[str, Any]]:
    return [e for e in row.history if e.get("event") in FAILED_RUNS]


@dataclass
class RetryAgent:
    """Puts failed files back in the inbox when their wait is over."""

    connector: Connector
    ledger: Ledger
    max_attempts: int = 3
    base_seconds: float = 60.0
    # LS-S9: set to stop gently - the pass that is running finishes, the loop ends
    stop: threading.Event = field(default_factory=threading.Event)

    @classmethod
    def from_settings(cls, connector: Connector, ledger: Ledger, settings: Any) -> RetryAgent:
        return cls(connector, ledger, max_attempts=settings.listener_max_attempts,
                   base_seconds=settings.listener_retry_base_seconds)  # fmt: skip

    def pass_once(self) -> dict[str, Any]:
        """One look at the failed files. Returns what was done."""
        report: dict[str, Any] = {"retried": [], "waiting": [], "missing": []}
        source = self.connector.source_id
        rows = self.ledger.failed(source)
        if not rows:
            return report
        in_error = {f.id: f for f in self.connector.list("error")}
        now = self.ledger.now()
        for row in rows:
            runs = _failed_runs(row)
            if not runs or len(runs) > self.max_attempts:
                continue  # nothing to go on, or its retries are used up (the listener's to file)
            last = datetime.fromisoformat(str(runs[-1]["at"]))
            wait = wait_seconds(len(runs), self.base_seconds)
            waited = (now - last).total_seconds()
            if waited < wait:
                report["waiting"].append({"file": row.name, "retry": len(runs),
                                          "in_seconds": round(wait - waited, 1)})  # fmt: skip
                continue
            file = in_error.get(row.file_id)
            if file is None:  # someone moved or removed it: not ours to chase
                report["missing"].append(row.name)
                continue
            try:
                back = self.connector.move(file, "inbox")
            except SignInRequired:
                raise
            except ListenerError as exc:
                log.warning("listener_retry_could_not_move", file=row.name, error=str(exc))
                continue
            self.ledger.finish(row, "seen", file=back)  # waiting again; the history is kept
            self.ledger.note(
                row,
                {
                    "event": "retry",
                    "attempt": len(runs),
                    "at": now.isoformat(),
                    "waited_seconds": round(waited, 1),
                    "name": back.name,
                },
            )
            report["retried"].append(
                {
                    "file": back.name,
                    "retry": len(runs),
                    "of": self.max_attempts,
                    "waited_seconds": round(waited, 1),
                }
            )
            log.info("listener_file_retry", file=back.name, retry=len(runs),
                     of=self.max_attempts, waited_seconds=round(waited, 1))  # fmt: skip
        return report

    def run(self, *, interval: float, once: bool = False,
            sleep: Callable[[float], Any] | None = None) -> dict[str, Any]:  # fmt: skip
        sleep = sleep or self.stop.wait
        report = self.pass_once()
        while not once and not self.stop.is_set():
            sleep(interval)
            if self.stop.is_set():
                break
            report = self.pass_once()
        return report
