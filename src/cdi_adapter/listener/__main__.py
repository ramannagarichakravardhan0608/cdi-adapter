"""The listener's commands (backlog epic LS).

    python -m cdi_adapter.listener login          # the one-time sign-in to the drive (LS-S2)
    python -m cdi_adapter.listener check          # is the folder ready? processes nothing
    python -m cdi_adapter.listener run            # watch the folder and read every new file
    python -m cdi_adapter.listener run --once     # one pass, then stop
    python -m cdi_adapter.listener ignored        # files in the folder that will never be read
    python -m cdi_adapter.listener recover        # the retry agent: failed files, tried again
    python -m cdi_adapter.listener health         # is the listener alive, and what is waiting?

``check`` signs in to the drive the settings name, finds the six folders - creating any that are
missing - and prints how many files are waiting. **It processes nothing**: no file is read,
moved or changed. It is what an operator runs after changing ``CDI_LISTENER_CONNECTOR``.

``run`` is the listener itself (``service.py``): every new file in the inbox is read by the same
pipeline as an upload from the screen, then filed under ``success`` or ``error``. If the
pipeline cannot be reached it says so and leaves every file where it is.

``login`` is the one-time sign-in (``auth.py``): for a person's OneDrive it prints a web address
and a short code to type on Microsoft's own page. No password is ever typed here.

When the sign-in is missing or has expired, ``check`` says so and ``run`` **stops**: it writes
the reason to its log, raises an alert and exits 2. It does not keep asking.

``run`` and ``recover`` stop gently (LS-S9): on a stop signal the batch that is running finishes,
no new one starts, and the command exits 0.

Exit status: 0 ready / ran; 2 sign-in is missing or has expired; 3 ``run --once`` found the
pipeline unreachable; 4 ``health`` found a listener that is not polling; 1 anything else.
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from cdi_adapter.listener import connectors


@contextmanager
def _stops_gently(running: Any) -> Iterator[None]:
    """While this runs, a stop signal (SIGTERM from the pod, Ctrl+C from a person) does not
    kill the process: it sets ``running.stop``, so the batch that is running finishes and the
    loop then ends by itself (LS-S9). A second signal is not needed and changes nothing."""

    def asked(signum: int, frame: Any) -> None:
        if not running.stop.is_set():
            print("stopping: the running batch will finish first", file=sys.stderr)
        running.stop.set()

    before = {}
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            before[sig] = signal.signal(sig, asked)
        except (ValueError, OSError):  # not the main thread, or not on this platform
            pass
    try:
        yield
    finally:
        for sig, old in before.items():
            signal.signal(sig, old)


def _health(as_json: bool) -> int:
    """The health view (LS-S9), read from the database the listener writes to: last good
    look, files waiting, files in error and quarantine, age of the oldest waiting file.
    Exit 0 when every watched folder is healthy, 4 when one is not or none has reported."""
    from cdi_adapter.config import get_settings
    from cdi_adapter.listener import service

    settings = get_settings()
    ledger = service.pipeline_reader(settings).ledger or service.MemoryLedger()
    try:
        rows = ledger.health(stale_seconds=settings.listener_stale_seconds)
    except Exception as exc:  # noqa: BLE001 - "cannot tell" is an answer, not a crash
        print(f"CANNOT TELL: the database cannot be read ({type(exc).__name__})", file=sys.stderr)
        return 3
    if as_json:
        print(json.dumps(rows, indent=2))
    else:
        if not rows:
            print("no listener has reported yet")
        for row in rows:
            print(f"watching     {row['source_id']}  (worker {row['worker']})")
            print(f"  status         {'OK' if row['healthy'] else 'NOT POLLING'}"
                  + (f" - {row['reason']}" if row["reason"] else ""))  # fmt: skip
            print(f"  last good poll {row['last_good_poll_at'] or 'never'}"
                  + (f"  ({row['seconds_since_good_poll']:.0f} s ago)"
                     if row["seconds_since_good_poll"] is not None else ""))  # fmt: skip
            oldest = row["oldest_waiting_seconds"]
            print(f"  waiting        {row['waiting']} file(s)"
                  + (f", the oldest for {oldest:.0f} s" if oldest is not None else ""))  # fmt: skip
            print(f"  in error       {row['error']}")
            print(f"  in quarantine  {row['quarantine']}")
            print(f"  ignored        {len(row['ignored'])}")
    return 0 if rows and all(r["healthy"] for r in rows) else 4


def _logging() -> None:
    """The listener's log, with tokens, keys and passwords taken out of every line."""
    from cdi_adapter.listener import auth
    from cdi_adapter.logging import configure

    configure(extra=(auth.scrub_event,))


def _needed(exc: connectors.SignInRequired) -> int:
    expired = isinstance(exc, connectors.SignInExpired)
    print(f"{'SIGN-IN EXPIRED' if expired else 'SIGN-IN NEEDED'}: {exc}", file=sys.stderr)
    return 2


def _signed_in_line(who: dict[str, object]) -> str:
    line = f"sign-in      {who['how']}"
    if who.get("as"):
        line += f", as {who['as']}"
    if who.get("permissions"):
        line += f" ({', '.join(who['permissions'])})"  # type: ignore[arg-type]
    return line


def _login() -> int:
    from cdi_adapter.config import get_settings
    from cdi_adapter.listener import auth

    settings = get_settings()
    try:
        sign_in = auth.sign_in_for(settings)
        if sign_in is None:
            print(f"drive        {settings.listener_connector}")
            print("sign-in      none needed for this drive")
            return 0
        who = sign_in.login(print)
    except connectors.SignInRequired as exc:
        return _needed(exc)
    except connectors.ListenerError as exc:
        print(f"NOT SIGNED IN: {exc}", file=sys.stderr)
        return 1
    print(f"drive        {settings.listener_connector}")
    print(_signed_in_line(who))
    if who.get("token_cache"):
        print(f"kept in      {who['token_cache']} (renewed quietly; no code is asked for again)")
    print("no password was typed into, stored by or logged by this system")
    return 0


def _check(as_json: bool) -> int:
    from cdi_adapter.config import get_settings
    from cdi_adapter.listener import auth

    try:
        settings = get_settings()
        sign_in = auth.sign_in_for(settings)
        if sign_in is not None:
            sign_in()  # a token, or the reason there is none - before any folder is touched
        result = connectors.check(connectors.get_connector(settings))
        if sign_in is not None:
            result["sign_in"] = sign_in.describe()
    except connectors.SignInRequired as exc:
        return _needed(exc)
    except connectors.ListenerError as exc:
        print(f"NOT READY: {exc}", file=sys.stderr)
        return 1
    if as_json:
        print(json.dumps(result, indent=2))
        return 0
    print(f"drive        {result['connector']}")
    print(f"watching     {result['source_id']}")
    if "sign_in" in result:
        print(_signed_in_line(result["sign_in"]))
    made = result["folders_created"]
    print(f"folders      all six present{' (created: ' + ', '.join(made) + ')' if made else ''}")
    print(f"waiting      {result['waiting']} file(s) in the inbox")
    for folder, count in result["in_folders"].items():
        print(f"  {folder:<11}{count}")
    print("nothing was processed")
    return 0


def _run(once: bool, interval: float | None) -> int:
    from cdi_adapter.config import get_settings
    from cdi_adapter.listener import auth, service
    from cdi_adapter.logging import get_logger

    _logging()
    settings = get_settings()
    wait = settings.listener_poll_seconds if interval is None else interval
    try:
        connector = connectors.get_connector(settings)
        reader = service.pipeline_reader(settings)
        listener = service.Listener.from_settings(connector, reader, settings)
        print(f"watching     {connector.source_id}")
        auth.clear_alert(settings)  # the drive answered: an old alert no longer holds
        with _stops_gently(listener):
            report = listener.run(interval=wait, once=once)
        if listener.stop.is_set():
            print("stopped: the running batch finished, nothing was left half-read")
    except connectors.SignInRequired as exc:
        # The sign-in is missing or has run out. Stop here: asking again cannot help, and a
        # listener that keeps asking hides the fact that nothing is being read.
        expired = isinstance(exc, connectors.SignInExpired)
        reason = auth.scrub(str(exc), auth.known_secrets(settings))
        get_logger(__name__).error("listener_stopped", why="sign_in_expired" if expired
                                   else "sign_in_missing", reason=reason)  # fmt: skip
        auth.raise_alert(settings, "ListenerSignInExpired" if expired
                         else "ListenerSignInMissing", reason)  # fmt: skip
        print("LISTENER STOPPED. No file is being read until someone signs in again.",
              file=sys.stderr)  # fmt: skip
        return _needed(exc)
    except connectors.ListenerError as exc:
        print(f"NOT READY: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:  # pragma: no cover - the operator stopped it
        print("stopped")
        return 0
    if not report["ready"]:
        print(f"PIPELINE NOT REACHABLE: {report['reason']}. No file was taken; every file is "
              "still where it was dropped.", file=sys.stderr)  # fmt: skip
        return 3
    print(f"read         {len(report['succeeded'])} file(s) -> success")
    for name in report["succeeded"]:
        print(f"  {name}")
    if report["duplicates"]:
        print(f"already read {len(report['duplicates'])} file(s): the same bytes as an earlier "
              "file, not read again")  # fmt: skip
        for item in report["duplicates"]:
            print(f"  {item['file']}: same as {item['same_as']}")
    print(f"not read     {len(report['failed'])} file(s) -> error (a note is in log)")
    for item in report["failed"]:
        print(f"  {item['file']}: {item['reason']}")
    if report["settling"]:
        print(f"still arriving  {report['settling']} file(s): left for the next pass")
    for batch in report["batches"]:
        print(f"batch        {batch['id']}: {len(batch['files'])} file(s), "
              f"{batch['read_together']} read together, {batch['seconds']} s")  # fmt: skip
    for item in report["timings"]:
        print(f"  {item['file']}: {item['seconds_from_drop']} s from drop to {item['state']}")
    if report["ignored_files"]:
        print(f"ignored      {len(report['ignored_files'])} file(s): left where they are")
        for item in report["ignored_files"]:
            print(f"  {item['file']}: {item['reason']}")
    return 0


def _recover(once: bool, interval: float | None) -> int:
    """The retry agent (LS-S7): put failed files back in the inbox when their wait is over."""
    from cdi_adapter.config import get_settings
    from cdi_adapter.listener import recovery, service

    _logging()
    settings = get_settings()
    wait = settings.listener_poll_seconds if interval is None else interval
    try:
        connector = connectors.get_connector(settings)
        reader = service.pipeline_reader(settings)
        not_ready = reader.ready()
        if not_ready:
            print(f"PIPELINE NOT REACHABLE: {not_ready}. Nothing was retried.", file=sys.stderr)
            return 3
        ledger = reader.ledger or service.MemoryLedger()
        agent = recovery.RetryAgent.from_settings(connector, ledger, settings)
        with _stops_gently(agent):
            report = agent.run(interval=wait, once=once)
    except connectors.SignInRequired as exc:
        return _needed(exc)
    except connectors.ListenerError as exc:
        print(f"NOT READY: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:  # pragma: no cover - the operator stopped it
        print("stopped")
        return 0
    print(f"retried      {len(report['retried'])} file(s) -> back in the inbox")
    for item in report["retried"]:
        print(f"  {item['file']}: retry {item['retry']} of {item['of']}")
    print(f"waiting      {len(report['waiting'])} file(s): not yet time")
    for item in report["waiting"]:
        print(f"  {item['file']}: retry {item['retry']} in {item['in_seconds']} s")
    return 0


def _ignored(as_json: bool) -> int:
    """The files in the inbox that will never be read, each with the reason (LS-S3). A look at
    the folder as it is now: nothing is read, moved or changed."""
    from cdi_adapter.config import get_settings
    from cdi_adapter.listener import service

    settings = get_settings()
    try:
        connector = connectors.get_connector(settings)
        patterns = service.patterns_of(settings.listener_patterns)
        found = [{"file": f.name, "reason": why} for f in connector.list("inbox")
                 if (why := service.why_ignored(f.name, patterns))]  # fmt: skip
    except connectors.SignInRequired as exc:
        return _needed(exc)
    except connectors.ListenerError as exc:
        print(f"NOT READY: {exc}", file=sys.stderr)
        return 1
    if as_json:
        print(json.dumps(found, indent=2))
        return 0
    print(f"ignored      {len(found)} file(s) in the inbox will not be read")
    for item in found:
        print(f"  {item['file']}: {item['reason']}")
    print(f"allowed      {settings.listener_patterns}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="cdi-listener", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("login", help="the one-time sign-in to the drive; no password is typed here")
    chk = sub.add_parser("check", help="sign in, find the folders, count what is waiting")
    chk.add_argument("--json", action="store_true", help="print the result as JSON")
    run = sub.add_parser("run", help="watch the folder and read every new file")
    run.add_argument("--once", action="store_true", help="one pass, then stop")
    run.add_argument("--interval", type=float, help="seconds between looks at the folder")
    rec = sub.add_parser("recover", help="the retry agent: try failed files again")
    rec.add_argument("--once", action="store_true", help="one pass, then stop")
    rec.add_argument("--interval", type=float, help="seconds between passes")
    hlt = sub.add_parser("health", help="is the listener alive, and what is waiting?")
    hlt.add_argument("--json", action="store_true", help="print the health as JSON")
    ign = sub.add_parser("ignored", help="list the files that will never be read, and why")
    ign.add_argument("--json", action="store_true", help="print the list as JSON")
    args = ap.parse_args(argv)
    if args.command == "run":
        return _run(args.once, args.interval)
    if args.command == "login":
        return _login()
    if args.command == "ignored":
        return _ignored(args.json)
    if args.command == "recover":
        return _recover(args.once, args.interval)
    if args.command == "health":
        return _health(args.json)
    return _check(args.json)


if __name__ == "__main__":
    raise SystemExit(main())
