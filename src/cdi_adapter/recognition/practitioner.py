"""Who wrote the prescription: linking the doctor on the page to the doctor list (EX-S2).

A wrong link is worse than none, so the rules are few and strict:

1. **Registration number.** A number printed on the page that is exactly one doctor's in the
   list links that doctor (``method = "registration_number"``). The digits must be the same,
   every one; a council written on both sides must agree too. If the page also gives a name and
   it is plainly somebody else's, the number is not trusted (one misread digit can be another
   doctor's number): no link, the doctor is shown as a candidate.
2. **A safe name match.** With no number to go by, a name links only when it is the full name
   of exactly one doctor in the list (``method = "name"``). Two doctors of that name: **not
   linked**, and both are shown as candidates. A part of a name, or a name that is merely
   alike, never links.
3. Otherwise the doctor is **unknown** - a first-class answer, not an error. The rest of the
   result is saved as it would be.

Handwriting is never looked at here: only what is printed or written as text on the page.

``find_reg_numbers`` / ``match`` are pure. ``link_document`` reads the list and saves the
decision - how the doctor was identified, with what confidence, and the candidates - in
``document_practitioner``. The list itself (``practitioner``) is loaded from a file the clinic
keeps (``scripts/load_doctor_master.py``); with an empty list every doctor is unknown.

ASSUMPTION, not verified: registration numbers are 3 to 7 digits, optionally with a state
medical council's short name ("71112 (WBMC)", "KMC 12345", "MCI Registration Number: 55683").
Formats differ by council; each must be confirmed before more than digit equality is checked.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import Connection, text

REGISTRATION, NAME, UNKNOWN = "registration_number", "name", "unknown"
CONFIDENCE = {REGISTRATION: 1.0, NAME: 0.9}  # ASSUMPTION: fixed, not measured
MAX_CANDIDATES = 5

_REG_LABEL = re.compile(
    r"\b(?:reg(?:d|n|istration)?\.?(?:\s*(?:no|num|number)\b\.?|\s*#)?|"
    r"(?:mci|nmc)\s*(?:reg(?:istration)?\.?)?\s*(?:no|number)?\.?)", re.I)  # fmt: skip
_COUNCIL = re.compile(r"\b([A-Z]{1,4}MC|MCI|NMC)\b")
_NUMBER = re.compile(r"(?<![\d/])(\d{3,7})(?![\d/])")
_TITLE = re.compile(r"^\s*(?:dr|doctor|prof|professor)\b\.?\s*", re.I)


@dataclass(frozen=True)
class RegNo:
    number: str  # the digits, exactly as printed
    council: str | None  # "WBMC", when the page names one
    line: int  # index of the line it was found on
    text: str  # that line


@dataclass
class Link:
    method: str = UNKNOWN
    practitioner_id: str | None = None
    confidence: float | None = None
    registration_read: str | None = None
    name_read: str | None = None
    candidates: list[dict[str, Any]] = field(default_factory=list)
    why: str = "no doctor information on the page"

    def as_json(self) -> dict[str, Any]:
        return {"method": self.method, "practitioner_id": self.practitioner_id,
                "confidence": self.confidence, "candidates": self.candidates,
                "why": self.why}  # fmt: skip


def name_key(name: str | None) -> str:
    """"Dr. Anita SEN-ROY, MD" -> "anita sen roy": the title and what follows a comma go."""
    s = _TITLE.sub("", str(name or "")).split(",")[0]
    s = re.sub(r"\(.*?\)", " ", s)
    return " ".join(re.findall(r"[a-z]+", s.lower()))


def reg_digits(value: str | None) -> str:
    """The number of a registration as written: "Regd No 71112 (WBMC)" -> "71112"."""
    m = _NUMBER.search(_REG_LABEL.sub(" ", str(value or "")))
    return m.group(1) if m else ""


def council_of(value: str | None) -> str | None:
    m = _COUNCIL.search(str(value or "").upper())
    return m.group(1) if m else None


def find_reg_numbers(lines: list[str]) -> list[RegNo]:
    """Every registration number printed on the page: a number on a line that says it is one
    ("Reg No", "Regd. No.", "MCI Registration Number"). A phone, a date or a bill number on
    another line is never taken for one."""
    found: list[RegNo] = []
    for i, line in enumerate(lines):
        label = _REG_LABEL.search(line)
        if not label:
            continue
        rest = line[label.end():]
        m = _NUMBER.search(rest)
        if m and not any(f.number == m.group(1) for f in found):
            found.append(RegNo(m.group(1), council_of(line), i, " ".join(line.split())))
    return found


def _card(row: dict[str, Any], why: str) -> dict[str, Any]:
    return {"practitioner_id": str(row["id"]), "name": row["name"],
            "registration_no": row["registration_no"], "council": row.get("council"),
            "department": row.get("department"), "why": why}  # fmt: skip


def _same_person(a: str, b: str) -> bool:
    """Two written names that can be one doctor: the words of one are all in the other
    ("Anita Sen" / "Anita Sen Roy"). Used only to DOUBT a number, never to link."""
    ta, tb = set(a.split()), set(b.split())
    return bool(ta and tb) and (ta <= tb or tb <= ta)


def match(master: list[dict[str, Any]], reg_numbers: list[RegNo],
          name: str | None) -> Link:  # fmt: skip
    """The decision for one page. ``master`` rows: id, name, registration_no, council, ..."""
    read_name = name_key(name)
    link = Link(name_read=" ".join(str(name).split()) if name else None,
                registration_read=reg_numbers[0].number if reg_numbers else None)  # fmt: skip
    active = [m for m in master if m.get("active", True)]
    for reg in reg_numbers:
        same = [m for m in active if reg_digits(m["registration_no"]) == reg.number
                and (not reg.council or not m.get("council")
                     or str(m["council"]).upper() == reg.council)]  # fmt: skip
        if len(same) == 1:
            doctor = same[0]
            if read_name and not _same_person(read_name, name_key(doctor["name"])):
                link.candidates = [_card(doctor, "registration number matches, name does not")]
                link.why = (f"registration number {reg.number} is {doctor['name']}'s, but the "
                            f"page names {link.name_read}: not linked")  # fmt: skip
                return link
            return Link(REGISTRATION, str(doctor["id"]), CONFIDENCE[REGISTRATION], reg.number,
                        link.name_read, [], f"registration number {reg.number}")  # fmt: skip
        if len(same) > 1:
            link.candidates = [_card(m, "same registration number") for m in
                               same[:MAX_CANDIDATES]]  # fmt: skip
            link.why = f"registration number {reg.number} is on {len(same)} doctors: not linked"
            return link
    if read_name and len(read_name.split()) >= 2:
        same = [m for m in active if name_key(m["name"]) == read_name]
        if len(same) == 1 and not reg_numbers:
            return Link(NAME, str(same[0]["id"]), CONFIDENCE[NAME], None, link.name_read, [],
                        "the full name is one doctor's, and only one")  # fmt: skip
        if same:
            link.candidates = [_card(m, "same name") for m in same[:MAX_CANDIDATES]]
            link.why = (f"{len(same)} doctors are named {link.name_read}: not linked"
                        if len(same) > 1 else
                        "the name matches a doctor, but the registration number on the page "
                        "is not that doctor's: not linked")  # fmt: skip
            return link
        alike = [m for m in active if _same_person(read_name, name_key(m["name"]))]
        if alike:
            link.candidates = [_card(m, "name is alike") for m in alike[:MAX_CANDIDATES]]
            link.why = "no doctor of exactly this name; the names shown are alike: not linked"
            return link
    if link.registration_read or link.name_read:
        link.why = "not in the doctor list"
    return link


# ------------------------------------------------------------------ the database


def load_master(conn: Connection) -> list[dict[str, Any]]:
    rows = conn.execute(text(
        "SELECT id, name, registration_no, council, qualification, department, clinic_name, "
        "active FROM practitioner WHERE active ORDER BY name, id")).mappings().all()  # fmt: skip
    return [dict(r) for r in rows]


def link_document(conn: Connection, document_id: Any, *, name: str | None, lines: list[str],
                  registration: str | None = None, stamp_present: bool | None = None,
                  signature_present: bool | None = None,
                  stamp_text: str | None = None) -> Link:  # fmt: skip
    """Decide and save. ``registration`` is the number the model read, used only when no line
    of the page says one; the lines of the page come first. Saving replaces an earlier decision
    for the document (it is read again), and never fails for want of a doctor."""
    numbers = find_reg_numbers(lines)
    if not numbers and reg_digits(registration):
        numbers = [RegNo(reg_digits(registration), council_of(registration), -1,
                         str(registration))]  # fmt: skip
    link = match(load_master(conn), numbers, name)
    conn.execute(text(
        "INSERT INTO document_practitioner (document_id, practitioner_id, method, confidence, "
        "registration_read, name_read, candidates, why, stamp_present, signature_present, "
        "stamp_text) VALUES (:d, :p, :m, :c, :r, :n, CAST(:k AS jsonb), :w, :s, :g, :t) "
        "ON CONFLICT (document_id) DO UPDATE SET practitioner_id = EXCLUDED.practitioner_id, "
        "method = EXCLUDED.method, confidence = EXCLUDED.confidence, "
        "registration_read = EXCLUDED.registration_read, name_read = EXCLUDED.name_read, "
        "candidates = EXCLUDED.candidates, why = EXCLUDED.why, "
        "stamp_present = EXCLUDED.stamp_present, "
        "signature_present = EXCLUDED.signature_present, stamp_text = EXCLUDED.stamp_text, "
        "decided_at = now()"),
        {"d": str(document_id), "p": link.practitioner_id, "m": link.method,
         "c": link.confidence, "r": link.registration_read, "n": link.name_read,
         "k": json.dumps(link.candidates), "w": link.why, "s": stamp_present,
         "g": signature_present, "t": stamp_text})  # fmt: skip
    return link


def link_of(conn: Connection, document_id: Any) -> dict[str, Any] | None:
    """The saved decision for a document, with the linked doctor's name; None before any."""
    row = conn.execute(text(
        "SELECT l.*, p.name AS practitioner_name, p.registration_no AS practitioner_reg "
        "FROM document_practitioner l LEFT JOIN practitioner p ON p.id = l.practitioner_id "
        "WHERE l.document_id = :d"), {"d": str(document_id)}).mappings().first()  # fmt: skip
    return dict(row) if row else None
