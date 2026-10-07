"""The listener's commands: ``python -m cdi_adapter.listener check`` (backlog LS-S1).

``check`` signs in to the drive the settings name, finds the six folders - creating any that are
missing - and prints how many files are waiting. **It processes nothing**: no file is read,
moved or changed. It is what an operator runs after changing ``CDI_LISTENER_CONNECTOR``, to see
the listener is pointed at the right place before it is started.

Exit status: 0 the drive is ready; 2 sign-in is missing or has expired; 1 anything else.
"""

from __future__ import annotations

import argparse
import json
import sys

from cdi_adapter.listener import connectors


def _check(as_json: bool) -> int:
    try:
        result = connectors.check(connectors.get_connector())
    except connectors.SignInRequired as exc:
        print(f"SIGN-IN NEEDED: {exc}", file=sys.stderr)
        return 2
    except connectors.ListenerError as exc:
        print(f"NOT READY: {exc}", file=sys.stderr)
        return 1
    if as_json:
        print(json.dumps(result, indent=2))
        return 0
    print(f"drive        {result['connector']}")
    print(f"watching     {result['source_id']}")
    made = result["folders_created"]
    print(f"folders      all six present{' (created: ' + ', '.join(made) + ')' if made else ''}")
    print(f"waiting      {result['waiting']} file(s) in the inbox")
    for folder, count in result["in_folders"].items():
        print(f"  {folder:<11}{count}")
    print("nothing was processed")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="cdi-listener", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)
    chk = sub.add_parser("check", help="sign in, find the folders, count what is waiting")
    chk.add_argument("--json", action="store_true", help="print the result as JSON")
    args = ap.parse_args(argv)
    return _check(args.json)


if __name__ == "__main__":
    raise SystemExit(main())
