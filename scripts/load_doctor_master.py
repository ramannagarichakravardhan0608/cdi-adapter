"""Load the clinic's doctor list (backlog EX-S2): the list a prescription's doctor is linked to.

    python scripts/load_doctor_master.py doctors.csv            # add / update
    python scripts/load_doctor_master.py doctors.csv --check    # read and report, change nothing

The file is a CSV with a header row. Required columns: ``name``, ``registration_no``. Optional:
``council`` (e.g. WBMC), ``qualification``, ``department``, ``clinic_name``, ``phone``,
``active`` (yes / no; default yes). A doctor is one registration number within one council: a
row for a number already in the list updates that doctor, it never adds a second one.

Rows that cannot be used are reported and skipped, never guessed at: no name, no registration
number, a registration number with no digits, or the same number twice in the file.

Who keeps this file, and where it comes from, is the clinic's decision (an open question of the
story). Nothing is loaded by default: with an empty list every doctor is "unknown".
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from typing import Any

REQUIRED = ("name", "registration_no")
OPTIONAL = ("council", "qualification", "department", "clinic_name", "phone")


def read_rows(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """``(rows to load, problems)``. Pure: no database."""
    from cdi_adapter.recognition.practitioner import reg_digits

    rows: list[dict[str, Any]] = []
    problems: list[str] = []
    seen: set[tuple[str, str]] = set()
    with path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        header = [h.strip().lower() for h in (reader.fieldnames or [])]
        missing = [c for c in REQUIRED if c not in header]
        if missing:
            return [], [f"the file has no column {', '.join(missing)}"]
        for n, raw in enumerate(reader, start=2):
            r = {str(k).strip().lower(): " ".join(str(v or "").split()) for k, v in raw.items()}
            if not r.get("name") or not r.get("registration_no"):
                problems.append(f"line {n}: no name or no registration number - skipped")
                continue
            if not reg_digits(r["registration_no"]):
                problems.append(f"line {n}: {r['registration_no']!r} has no number - skipped")
                continue
            key = (r["registration_no"], (r.get("council") or "").upper())
            if key in seen:
                problems.append(f"line {n}: registration {key[0]} is in the file twice - skipped")
                continue
            seen.add(key)
            rows.append({"name": r["name"], "registration_no": r["registration_no"],
                         "council": (r.get("council") or "").upper() or None,
                         **{c: r.get(c) or None for c in OPTIONAL if c != "council"},
                         "active": (r.get("active") or "yes").lower() not in
                         ("no", "n", "false", "0")})  # fmt: skip
    return rows, problems


def load(rows: list[dict[str, Any]], source: str) -> int:
    from sqlalchemy import text

    from cdi_adapter.db import session_scope

    with session_scope() as conn:
        for r in rows:
            conn.execute(text(
                "INSERT INTO practitioner (name, registration_no, council, qualification, "
                "department, clinic_name, phone, active, source) VALUES (:name, "
                ":registration_no, :council, :qualification, :department, :clinic_name, :phone, "
                ":active, :source) ON CONFLICT (registration_no, coalesce(council, '')) "
                "DO UPDATE SET name = EXCLUDED.name, qualification = EXCLUDED.qualification, "
                "department = EXCLUDED.department, clinic_name = EXCLUDED.clinic_name, "
                "phone = EXCLUDED.phone, active = EXCLUDED.active, source = EXCLUDED.source, "
                "updated_at = now()"), {**r, "source": source})  # fmt: skip
    return len(rows)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("file", type=Path)
    ap.add_argument("--check", action="store_true", help="read and report; change nothing")
    args = ap.parse_args(argv)
    rows, problems = read_rows(args.file)
    for p in problems:
        print("  " + p)
    if args.check or not rows:
        print(f"{len(rows)} doctor(s) could be loaded, {len(problems)} line(s) skipped")
        return 0 if rows else 1
    print(f"loaded {load(rows, args.file.name)} doctor(s), {len(problems)} line(s) skipped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
