"""The patient's details, as the prescription gives them (backlog EX-S1).

Name, age, sex, date of birth, phone, address, hospital number and ABHA number each become one
``patient_detail`` fact: the value exactly as written, the part of the page it was read from
(its proof) and a status. A detail the page does not give is simply absent - no fact, no review
(AC3). Nothing here matches the document to a patient: that stays with the registry lookup and
the patient the upload was filed under (AC4).

Pure functions, no I/O:

* :func:`patient_drafts` - the facts for a model's ``patient`` block;
* :func:`proof_blocks` - the OCR line(s) a value is written on. A number is only "on" a line
  that has **every digit of it, in order**: the grounding rule for numbers;
* :func:`check` - the format rules (v3): phone and ABHA lengths, a plausible age, a real date of
  birth, the digits of a number in the line it cites, and the age checked against the date of
  birth. A failure is a blocker, so the value goes to a person (AC2, AC5).

Sex and age are never worked out from a name: a value that the cited line does not carry fails
the same check as any other.
"""

from __future__ import annotations

import re
from datetime import date
from difflib import SequenceMatcher
from typing import Any

from cdi_adapter.dates import parse_date

FACT_TYPE = "patient_detail"
# payload key -> the fact's name (its local_text): fixed, lower-case, never shown as a value
FIELDS: dict[str, str] = {
    "name": "name", "age_text": "age", "sex": "sex", "dob": "date of birth", "phone": "phone",
    "address": "address", "mrn": "hospital number", "abha": "abha number",
}  # fmt: skip
ALIASES: dict[str, tuple[str, ...]] = {
    "phone": ("mobile", "mobile_no", "phone_no", "phone_number", "contact", "mob"),
    "abha": ("abha_id", "abha_number", "abha_no"),
    "age_text": ("age",),
    "dob": ("date_of_birth", "birth_date"),
    "mrn": ("hospital_number", "mr_no", "uhid"),
}
NUMBERS = ("phone", "abha number", "clinic phone")  # every digit must be in the picture
# EX-S2: a doctor's details are checked by the same rules, under their own names
EXACT_DIGITS = ("age", "date of birth", "hospital number", "registration number")
ADDRESSES = ("address", "clinic address")
MAX_AGE = 120
AGE_DOB_SLACK = 1  # years: a birthday not yet reached, or an age written a visit ago
WORD_ALIKE = 0.8  # ASSUMPTION, as in the quote check: a written word may differ by an OCR slip

_SEX = {
    "m": "M", "male": "M", "man": "M", "boy": "M",
    "f": "F", "female": "F", "woman": "F", "girl": "F",
    "o": "O", "other": "O", "others": "O", "transgender": "O", "tg": "O",
}  # fmt: skip
_AGE = re.compile(
    r"(\d{1,3})\s*(y|yr|yrs|year|years|m|mo|mon|months?|d|days?|w|wks?|weeks?)?\b", re.I
)
_SPLIT_NUMBERS = re.compile(r"\s*(?:/|,|;|\bor\b|\band\b)\s*", re.I)


def digits(text: Any) -> str:
    return re.sub(r"\D", "", str(text or ""))


def _words(text: Any) -> list[str]:
    return re.findall(r"[a-z]{3,}", str(text or "").lower())


def as_written(block: dict[str, Any] | None, key: str) -> str | None:
    """The value of one patient field, as the model wrote it, or None when it gave none."""
    if not isinstance(block, dict):
        return None
    for k in (key, *ALIASES.get(key, ())):
        v = block.get(k)
        if isinstance(v, dict):
            v = v.get("value") or v.get("text")
        if isinstance(v, int | float) and not isinstance(v, bool):
            v = str(v)
        if isinstance(v, str) and v.strip():
            return " ".join(v.split())
    return None


def patient_drafts(payload: dict[str, Any], default: float) -> list[Any]:
    """One ``patient_detail`` draft per detail the model read. ``evidence`` is the patient
    block's own: the line each value is on is found when the fact is written
    (:func:`proof_blocks`), because that needs the page's OCR text."""
    from cdi_adapter.extract.facts import FactDraft, _conf, _ev  # shared readers

    block = payload.get("patient")
    if not isinstance(block, dict):
        return []
    conf = _conf(block, default)
    out = []
    for key, label in FIELDS.items():
        value = as_written(block, key)
        if value:
            out.append(FactDraft(FACT_TYPE, label, conf, _ev(block), value_kind="text",
                                 value_text=value))  # fmt: skip
    return out


# ------------------------------------------------------------------ proof


def _numbers_of(value: str) -> list[str]:
    """"9007498749 / 9830585954" -> the two numbers; one number written in groups stays one."""
    parts = [digits(p) for p in _SPLIT_NUMBERS.split(value)]
    return [p for p in parts if p]


def _has_number(number: str, text: str) -> bool:
    """Every digit of ``number``, in order, on the line. "+91" / a leading 0 may be left off
    the page or the reading, so the last ten digits of a longer number are what must be there."""
    there = digits(text)
    return bool(number) and (number in there or (len(number) > 10 and number[-10:] in there))


def _has_word(word: str, there: list[str]) -> bool:
    return any(word == t or word in t or SequenceMatcher(None, word, t).ratio() >= WORD_ALIKE
               for t in there)  # fmt: skip


def supports(label: str, value: str, text: str) -> bool:
    """Does this line of the page carry the value?"""
    if label in NUMBERS:
        return any(_has_number(n, text) for n in _numbers_of(value))
    if label == "sex":
        want = _SEX.get(value.strip().lower().rstrip("."))
        tokens = re.findall(r"[a-z]+", text.lower())
        return bool(want) and any(_SEX.get(t) == want for t in tokens)
    if label in EXACT_DIGITS:
        wanted = re.findall(r"\d+", value)
        have = re.findall(r"\d+", text)
        if wanted:
            return all(w in have or w.lstrip("0") in [h.lstrip("0") for h in have]
                       for w in wanted)  # fmt: skip
    words, there = _words(value), _words(text)
    if not words:
        return False
    seen = sum(_has_word(w, there) for w in words)
    if label in ADDRESSES:  # an address runs over lines: a line carries part of it
        return seen >= max(1, len(words) // 3)
    return seen == len(words)


def proof_blocks(label: str, value: str, blocks: list[dict[str, Any]],
                 cited: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:  # fmt: skip
    """The OCR line(s) ``value`` is written on: looked for among the lines the model cited for
    the patient first, then on the whole page. An address may run over a few lines. No line
    means no proof - the value is then held for a person, never accepted on trust."""
    for pool in (cited or [], blocks):
        hits = [b for b in pool if supports(label, value, str(b.get("text") or ""))]
        if hits:
            return hits[:3] if label in ADDRESSES else hits[:1]
    return []


# ------------------------------------------------------------------ the rules


def age_years(text: str | None) -> float | None:
    """"40 Y" -> 40, "6 months" -> 0.5, "42" -> 42; None when no age can be read from it."""
    m = _AGE.search(str(text or ""))
    if not m:
        return None
    n, unit = int(m.group(1)), (m.group(2) or "y").lower()
    if unit.startswith("y"):
        return float(n)
    if unit.startswith("m"):
        return n / 12
    if unit.startswith("w"):
        return n / 52
    return n / 365


def _phone_ok(number: str) -> bool:
    """An Indian mobile: ten digits starting 6-9, with or without 91 / 0 in front. A landline
    with its STD code is ten or eleven digits beginning 0."""
    if len(number) == 12 and number.startswith("91"):
        number = number[2:]
    if len(number) == 11 and number.startswith("0"):
        return True
    return len(number) == 10 and number[0] in "6789"


def check(label: str, value: str | None, cited: str, others: dict[str, str] | None = None,
          on: date | None = None) -> list[tuple[str, str]]:  # fmt: skip
    """``[(code, message), ...]`` - every one a reason to send this value to a person. Empty:
    the value passes the format rules (the confidence gate still decides its status)."""
    value = (value or "").strip()
    found: list[tuple[str, str]] = []
    if not value:
        return found
    others, on = others or {}, on or date.today()
    if label == "phone":
        numbers = _numbers_of(value)
        bad = [n for n in numbers if not _phone_ok(n)]
        if bad or not numbers or re.search(r"[?*xX_]", value):
            found.append(("phone-format", f"{value!r} is not a ten-digit phone number"))
    if label == "abha number" and (len(digits(value)) != 14 or re.search(r"[?*xX_]", value)):
        found.append(("abha-format", f"{value!r} is not a 14-digit ABHA number"))
    if label in NUMBERS and cited and not found:
        lost = [n for n in _numbers_of(value) if not _has_number(n, cited)]
        if lost:
            found.append(("number-not-in-picture",
                          f"{' / '.join(lost)} is not, digit for digit, in the line it cites "
                          f"({cited[:60]!r})"))  # fmt: skip
    if label == "age":
        years = age_years(value)
        if years is None or years > MAX_AGE:
            found.append(("age-implausible", f"{value!r} is not an age between 0 and {MAX_AGE}"))
    born = None
    if label in ("age", "date of birth") and others.get("date of birth"):
        parsed = parse_date(others["date of birth"])
        born = parsed[0] if parsed else None
    if label == "date of birth":
        parsed = parse_date(value)
        if parsed is None:
            found.append(("dob-unreadable", f"{value!r} is not a date"))
        elif parsed[0] > on or on.year - parsed[0].year > MAX_AGE:
            found.append(("dob-implausible", f"{value!r} is not a possible date of birth"))
    years = age_years(others.get("age")) if others.get("age") else None
    if label in ("age", "date of birth") and born is not None and years is not None and (
        born <= on
    ):
        from_dob = on.year - born.year - ((on.month, on.day) < (born.month, born.day))
        if abs(from_dob - years) > AGE_DOB_SLACK:
            found.append(("age-dob-mismatch",
                          f"age {others['age']!r} does not agree with the date of birth "
                          f"{others['date of birth']!r} (that is {from_dob} years)"))  # fmt: skip
    if label == "clinic phone" and not 8 <= len(digits(value)) <= 12:
        found.append(("phone-format", f"{value!r} is not a phone number"))
    if label not in NUMBERS and cited and not supports(label, value, cited):
        found.append(("not-in-picture", f"{value!r} is not in the line it cites ({cited[:60]!r})"))
    return found


__all__ = ["FACT_TYPE", "FIELDS", "age_years", "as_written", "check", "digits",
           "patient_drafts", "proof_blocks", "supports"]  # fmt: skip
