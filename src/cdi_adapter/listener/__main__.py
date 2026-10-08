"""The listener's commands (backlog epic LS).

    python -m cdi_adapter.listener login          # the one-time sign-in to the drive (LS-S2)
    python -m cdi_adapter.listener check          # is the folder ready? processes nothing
    python -m cdi_adapter.listener run            # watch the folder and read every new file
    python -m cdi_adapter.listener run --once     # one pass, then stop
    python -m cdi_adapter.listener ignored        # files in the folder that will never be read

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

Exit status: 0 ready / ran; 2 sign-in is missing or has expired; 3 ``run --once`` found the
pipeline unreachable; 1 anything else.
"""

from __future__ import annotations

import argparse
import json
import sys

from cdi_adapter.listener import connectors


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
        report = listener.run(interval=wait, once=once)
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
    if report["ignored_files"]:
        print(f"ignored      {len(report['ignored_files'])} file(s): left where they are")
        for item in report["ignored_files"]:
            print(f"  {item['file']}: {item['reason']}")
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
    ign = sub.add_parser("ignored", help="list the files that will never be read, and why")
    ign.add_argument("--json", action="store_true", help="print the list as JSON")
    args = ap.parse_args(argv)
    if args.command == "run":
        return _run(args.once, args.interval)
    if args.command == "login":
        return _login()
    if args.command == "ignored":
        return _ignored(args.json)
    return _check(args.json)


if __name__ == "__main__":
    raise SystemExit(main())
