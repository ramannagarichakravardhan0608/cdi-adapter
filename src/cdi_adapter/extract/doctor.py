"""The prescribing doctor, as the page's letterhead gives it (owner, 2026-10-10: the Reviewer
showed nothing about the doctor).

The model's answer carries ``prescriber`` - a name - and nothing was ever shown from it: only
facts reach the Reviewer, and a doctor is not a fact. :func:`doctor_of` gathers what the page
says: the name (the model's, else the first letterhead line that starts with "Dr"), and - read
straight from the OCR lines beside that name - the qualification and the registration number.

Pure, and read-only: it is shown to the reviewer, never stored, coded or promoted. Nothing is
filled in that a line of the page does not say.
"""

from __future__ import annotations

import re
from typing import Any

_DR_LINE = re.compile(r"^\s*(?:dr|doctor)\b\.?\s*[A-Za-z]", re.I)
_DEGREE = re.compile(
    r"\b(?:M\.?B\.?B\.?S|M\.?D|M\.?S|D\.?N\.?B|D\.?M|M\.?Ch|B\.?D\.?S|M\.?D\.?S|B\.?A\.?M\.?S|"
    r"B\.?H\.?M\.?S|F\.?R\.?C\.?S|M\.?R\.?C\.?P|D\.?G\.?O|D\.?C\.?H|Dip(?:loma)?)\b\.?"
)
_REGISTRATION = re.compile(r"\breg(?:d|n|istration)?\b\.?\s*(?:no\b\.?|number\b|#)?\s*[:.\-]?\s*"
                           r"(.*\d.*)$", re.I)  # fmt: skip
NEAR = 6  # the letterhead: how many lines after the name are looked at


def _name(value: Any) -> str | None:
    if isinstance(value, dict):
        value = next((value[k] for k in ("name", "text", "display") if value.get(k)), None)
    text = " ".join(str(value or "").split())
    return text or None


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]{3,}", text.lower()) if w not in ("doctor",)}


def doctor_of(payload: dict[str, Any] | None, lines: list[str]) -> dict[str, Any]:
    """``{"name", "qualification", "registration", "source"}`` for one document; a value the
    page does not give is None. ``source`` says where the name came from: ``extraction`` (the
    model's answer) or ``letterhead`` (a "Dr ..." line of the page)."""
    name = _name((payload or {}).get("prescriber"))
    source = "extraction" if name else None
    at = None
    if name:  # where on the page that name is written
        at = next((i for i, t in enumerate(lines) if _words(name) and _words(name) <= _words(t)),
                  None)  # fmt: skip
    if at is None:
        first = next((i for i, t in enumerate(lines) if _DR_LINE.match(t)), None)
        if name is None and first is not None:
            name, source = " ".join(lines[first].split()), "letterhead"
        at = first if at is None and name is not None else at
    qualification = registration = None
    if at is not None:
        for text in lines[at + 1 : at + 1 + NEAR]:
            if _DR_LINE.match(text):
                break  # another doctor's block
            if registration is None and _REGISTRATION.search(text):
                registration = " ".join(text.split())
            elif qualification is None and _DEGREE.search(text):
                qualification = " ".join(text.split())
    return {"name": name, "qualification": qualification, "registration": registration,
            "source": source}  # fmt: skip


# ------------------------------------------------------------------ EX-S2: the doctor's details

FACT_TYPE = "doctor_detail"
# the model's field -> the fact's name (its local_text): fixed, lower-case
FIELDS: dict[str, str] = {
    "name": "name", "registration_no": "registration number", "qualification": "qualification",
    "department": "department", "clinic_name": "clinic name",
    "clinic_address": "clinic address", "clinic_phone": "clinic phone",
}  # fmt: skip
_ALIASES: dict[str, tuple[str, ...]] = {
    "registration_no": ("reg_no", "registration", "registration_number", "regn_no"),
    "qualification": ("qualifications", "degree", "degrees"),
    "department": ("speciality", "specialty", "specialisation", "specialization"),
}
_YES = {"yes", "y", "true", "present", "1"}
_NO = {"no", "n", "false", "absent", "0", "none"}


def _said(value: Any) -> str | None:
    if isinstance(value, dict):
        value = value.get("value") or value.get("text")
    if isinstance(value, int | float) and not isinstance(value, bool):
        value = str(value)
    text = " ".join(str(value).split()) if isinstance(value, str) else ""
    return text or None


def yes_no(value: Any) -> bool | None:
    """True / False for a stated yes or no; None when the answer does not say."""
    if isinstance(value, bool):
        return value
    said = str(value).strip().lower() if value is not None else ""
    return True if said in _YES else False if said in _NO else None


def model_details(payload: dict[str, Any] | None) -> dict[str, str]:
    """What the model wrote about the doctor: ``prescriber_details`` (and the older
    ``prescriber`` name), each value as written. A field it left empty is not in the result."""
    payload = payload or {}
    block = payload.get("prescriber_details")
    block = block if isinstance(block, dict) else {}
    clinic: dict[str, Any] = block["clinic"] if isinstance(block.get("clinic"), dict) else {}
    flat = {**block, **{f"clinic_{k}": v for k, v in clinic.items()}}
    out: dict[str, str] = {}
    for key, label in FIELDS.items():
        for k in (key, *_ALIASES.get(key, ())):
            value = _said(flat.get(k))
            if value:
                out[label] = value
                break
    if "name" not in out and (name := _name(payload.get("prescriber"))):
        out["name"] = name
    return out


def doctor_details(payload: dict[str, Any] | None, lines: list[str]) -> dict[str, str]:
    """The doctor's details for one page: the model's answer, and - for the name, the
    qualification and the registration number, which a letterhead prints in a known shape -
    the page's own lines where the model gave none. Never a value neither of them has."""
    from cdi_adapter.recognition.practitioner import find_reg_numbers

    out = model_details(payload)
    seen = doctor_of({"prescriber": out.get("name")}, lines)
    if "name" not in out and seen["name"]:
        out["name"] = seen["name"]
    if "qualification" not in out and seen["qualification"]:
        out["qualification"] = seen["qualification"]
    if "registration number" not in out and (found := find_reg_numbers(lines)):
        reg = found[0]
        out["registration number"] = reg.number + (f" ({reg.council})" if reg.council else "")
    return {label: out[label] for label in FIELDS.values() if label in out}


def with_stamp(payload: dict[str, Any], image: bytes | None,
               blocks: list[dict[str, Any]]) -> dict[str, Any]:  # fmt: skip
    """The answer with ``_stamp`` beside it when a stamp is found **in the picture**
    (``recognition.stamp``): where it is, its shape and ink, and the OCR lines that sit inside
    it - the stamp's own text, read as its own region (FR6)."""
    from cdi_adapter.recognition.stamp import find_stamps, lines_inside

    stamps = find_stamps(image)
    if not stamps:
        return payload
    first = stamps[0]
    inside = lines_inside(first, blocks)
    payload["_stamp"] = {
        "present": True, **first.as_json(), "count": len(stamps), "lines": inside,
        "text": " ".join(" ".join(str(blocks[i].get("text") or "").split()) for i in inside)
        or None,
    }  # fmt: skip
    return payload


def stamp_of(payload: dict[str, Any] | None) -> dict[str, Any]:
    """``stamp_present`` / ``signature_present`` (True, False, or None when nothing says) and
    the stamp's own text, kept apart from the letterhead's.

    Two sources for the stamp. One found in the picture is a **yes**, whatever the model wrote.
    Nothing found in the picture is not a no (a black or outline-less stamp is not looked
    for there), so then the model's answer stands - yes, no, or silence."""
    block = (payload or {}).get("prescriber_details")
    block = block if isinstance(block, dict) else {}
    seen = (payload or {}).get("_stamp")
    seen = seen if isinstance(seen, dict) and seen.get("present") else {}
    return {"stamp_present": True if seen else yes_no(block.get("stamp_present")),
            "signature_present": yes_no(block.get("signature_present")),
            "stamp_text": _said(seen.get("text")) or _said(block.get("stamp_text"))}  # fmt: skip


def with_doctor(payload: dict[str, Any], lines: list[str]) -> dict[str, Any]:
    """The answer with ``_doctor`` beside it: the details as they will be saved. Kept apart
    from the model's own fields, like every other note on an answer. The lines inside a stamp
    are not the letterhead: they are left out of what the letterhead is read from (FR6)."""
    stamp = payload.get("_stamp")
    in_stamp = set(stamp.get("lines") or []) if isinstance(stamp, dict) else set()
    lines = ["" if i in in_stamp else line for i, line in enumerate(lines)]
    details = doctor_details(payload, lines)
    if details:
        payload["_doctor"] = details
    return payload


def doctor_drafts(payload: dict[str, Any], default: float) -> list[Any]:
    """One ``doctor_detail`` draft per detail. The line each value is on is found when the fact
    is written (it needs the page's OCR text), as for a patient detail."""
    from cdi_adapter.extract.facts import FactDraft, _conf, _ev

    details = payload.get("_doctor")
    if not isinstance(details, dict):
        details = model_details(payload)
    block = payload.get("prescriber_details")
    block = block if isinstance(block, dict) else {}
    conf = _conf(block, default)
    return [FactDraft(FACT_TYPE, label, conf, _ev(block), value_kind="text",
                      value_text=str(details[label]))
            for label in FIELDS.values() if details.get(label)]  # fmt: skip
