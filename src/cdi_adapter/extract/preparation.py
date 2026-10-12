"""Lab preparation instructions, as written and as small structured items (backlog EX-S4).

"fasting 12 hrs", "morning sample", "2 hrs after meal", "stop biotin", "bring previous reports":
each becomes one ``preparation`` fact - the **original words always kept**, and beside them its
type (a fixed list), its value when a number is written, and **which test(s) it applies to**.

The strict rule (FR5 / FR8): only what is written. Nothing here knows that a test "usually"
needs fasting; HbA1c ordered with no preparation written gets none. A line is a preparation
only when it is written as one - the phrases are plain data a clinician can read
(``data/preparation_phrases.json``) - and not when it merely mentions fasting ("fasting blood
sugar 140", "was fasting during Ramadan", a medicine "on empty stomach").

Which test a note applies to is decided by where it is written, never by what the test is:

* it says so - "all tests in the morning" -> ``all``;
* it is written with ONE test, on that test's own line or part of a line
  ("HbA1c, FBS - fasting 12 hrs" -> FBS) -> that test;
* it is written with two tests at once, or on a line of its own when two or more tests are
  ordered -> ``unclear``, and the item goes to a person. A note on its own with exactly one
  test ordered can only be that test's.

Numbers are checked (NFR1): a duration that is not a clean number ("1Z hrs"), or not a possible
one, is flagged; so is one whose digits are not in the line it cites. Each flag sends the item
to review.

Pure functions; no I/O beyond reading the phrase file once.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

FACT_TYPE = "preparation"
FILE = Path(__file__).with_name("data") / "preparation_phrases.json"
ALL, UNCLEAR = "all", "unclear"
TESTS = "tests: "  # how a list of tests is kept in the fact's value_text
SEP = " | "

_SLOTS = {
    "{n}": r"(?P<n>[0-9A-Za-z]{1,3}(?:\s*-\s*[0-9A-Za-z]{1,3})?)",
    "{hours}": r"(?:hours?|hrs?|hr|h)\b\.?",
    "{meal}": r"(?:meals?|food|breakfast|lunch|dinner)",
    "{word}": r"\S+",
}
_NUMBER = re.compile(r"^\d{1,2}$")
_RANGE = re.compile(r"^(\d{1,2})\s*-\s*(\d{1,2})$")
_EDGES = " \t-–—:;,.()[]"
# a line that is a medicine: its own direction ("on empty stomach") is not a lab preparation
_MEDICINE_LINE = re.compile(r"^\s*(?:tab|tabs|tablet|cap|caps|capsule|syp|syr|syrup|inj|"
                            r"injection)\b\.?\s+\S", re.I)  # fmt: skip


def _trim(text: str) -> str:
    return text.strip(_EDGES)

_VALUE = re.compile(r"\d+(?:\.\d+)?\s*(?:mg|mmol|g/dl|%|mg/dl)", re.I)


@dataclass(frozen=True)
class Prep:
    type: str
    text: str  # the words of the instruction, as written
    value: float | None = None
    unit: str | None = None
    low: float | None = None  # a written range: "10-12 hrs"
    high: float | None = None
    problems: tuple[str, ...] = field(default_factory=tuple)
    start: int = 0  # where the instruction begins in the line it was found in
    end: int = 0


@lru_cache
def bank(path: Path = FILE) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def version() -> str:
    return str(bank()["version"])


def _regex(phrase: str) -> re.Pattern[str]:
    out = []
    for part in re.split(r"(\{[a-z]+\})", phrase.strip().lower()):
        if part in _SLOTS:
            out.append(_SLOTS[part])
        elif part:
            out.append(r"[\s\-]+".join(re.escape(w) for w in re.split(r"[\s\-]+", part) if w)
                       if part.strip() else "")  # fmt: skip
            if part.startswith((" ", "-")):
                out[-1] = r"[\s\-]*" + out[-1]
            if part.endswith((" ", "-")):
                out[-1] += r"[\s\-]*"
    return re.compile(r"(?<![a-z0-9])" + "".join(out) + r"(?![a-z0-9])", re.I)


@lru_cache
def _rules() -> list[tuple[str, re.Pattern[str]]]:
    """(type, pattern), the longest phrase first within a type; the types in the file's order
    of care - a fasting or a collection phrase is read before a general timing one."""
    b = bank()
    out = []
    for kind in ("fasting", "collection", "timing", "hold_medicine", "diet", "bring_documents"):
        for phrase in sorted(b["phrases"].get(kind, []), key=len, reverse=True):
            out.append((kind, _regex(phrase)))
    return out


@lru_cache
def _not_preparation() -> re.Pattern[str]:
    words = sorted(bank()["not_preparation"], key=len, reverse=True)
    return re.compile(r"(?<![a-z])(?:" + "|".join(re.escape(w) for w in words) + r")(?![a-z])",
                      re.I)  # fmt: skip


@lru_cache
def _all_tests() -> re.Pattern[str]:
    words = sorted(bank()["applies_to_all"], key=len, reverse=True)
    return re.compile(r"(?<![a-z])(?:" + "|".join(re.escape(w) for w in words) + r")(?![a-z])",
                      re.I)  # fmt: skip


def parse(text: str | None, *, guard: bool = True) -> Prep | None:
    """The preparation instruction written in ``text``, or None when it is not one. With
    ``guard`` (the default) a line that only mentions fasting - a result, a history, a medicine's
    own direction - is not one."""
    line = " ".join(str(text or "").split())
    if not line:
        return None
    if guard and (_not_preparation().search(line) or _VALUE.search(line)
                  or _MEDICINE_LINE.match(line)):  # fmt: skip
        return None
    for kind, pattern in _rules():
        m = pattern.search(line)
        if not m:
            continue
        value: float | None = None
        low = high = None
        unit = None
        problems: list[str] = []
        said = m.groupdict().get("n")
        if said is not None:
            after = m.group(0)[m.start("n") - m.start() :]
            unit = "hours" if re.search(r"hours?|hrs?|hr|h\b", after, re.I) else None
            said = said.strip()
            if _NUMBER.match(said):
                value = float(said)
            elif r := _RANGE.match(said):
                low, high = float(r.group(1)), float(r.group(2))
            else:
                problems.append("number-unclear")
            lo, hi = bank()["plausible"].get(kind, [None, None])
            numbers = [v for v in (value, low, high) if v is not None]
            if lo is not None and any(not lo <= v <= hi for v in numbers):
                problems.append("number-implausible")
            if low is not None and high is not None and low >= high:
                problems.append("number-implausible")
        return Prep(kind, line, value, unit, low, high, tuple(problems), m.start(), m.end())
    bare = _trim(line).lower()
    if bare == "fasting":  # "FBS (fasting)": the one word, as the whole of a note
        return Prep("fasting", line, start=0, end=len(line))
    return None


# ------------------------------------------------------------------ which test it applies to


def _named_tests(text: str) -> list[str]:
    """The tests named in a piece of text, as written, in order ("FBS/PPBS" -> both)."""
    from cdi_adapter.terminology import lab_master

    found: list[str] = []
    for part in re.split(r"\s*(?:[/,+&]|\band\b)\s*", text):
        part = part.strip(" .:-()")
        if part and lab_master.match(part).status != lab_master.NOT_MATCHED:
            found.append(part)
    if not found and text.strip(" .:-()") and (
        lab_master.match(text.strip(" .:-()")).status != lab_master.NOT_MATCHED
    ):
        found.append(text.strip(" .:-()"))
    return found


def _coded(item: Any) -> str:
    if isinstance(item, dict):
        for k in ("text", "display", "name"):
            if isinstance(item.get(k), str):
                return item[k].strip()
        return ""
    return str(item or "").strip()


def _note(item: Any, text: str, prep: Prep, applies: Any) -> dict[str, Any]:
    ev = item.get("evidence") if isinstance(item, dict) else None
    conf = item.get("confidence") if isinstance(item, dict) else None
    return {"text": text, "applies_to": applies, "evidence": ev if isinstance(ev, list) else [],
            "confidence": conf if isinstance(conf, int | float) else None,
            "type": prep.type}  # fmt: skip


def with_preparation(payload: dict[str, Any]) -> dict[str, Any]:
    """The answer with its preparation notes found (``_preparation``), each with what it applies
    to. A note written on a test's line is taken off that line - the test is then named as the
    test - and a line of advice that is only a preparation note moves here. Nothing is added
    that is not written; with nothing written there is no ``_preparation`` at all."""
    from cdi_adapter.extract.facts import _items, split_orders

    orders = _items(payload, "investigations_advised")
    advice = _items(payload, "advice")
    given = _items(payload, "investigation_preparation")
    if not (orders or advice or given):
        return payload
    notes: list[dict[str, Any]] = []
    kept_orders: list[Any] = []
    # 1. a note written on a test's own line: "FBS - fasting 12 hrs", "FBS/PPBS fasting 12 hrs"
    for item in orders:
        parts = []
        for part in split_orders(_coded(item)) or [_coded(item)]:
            prep = parse(part, guard=False) if part else None
            before = _trim(part[: prep.start]) if prep else ""
            if prep is None or not before or not _named_tests(before):
                parts.append(part)  # no note, or the "note" is the test's own name
                continue
            tests = _named_tests(before)
            words = _trim(part[prep.start:])
            inner = parse(words, guard=False) or prep
            notes.append(_note(item, words, inner, tests if len(tests) == 1 else UNCLEAR))
            parts.append(before)
        text = ", ".join(p for p in parts if p)
        if text:
            kept_orders.append({**item, "text": text} if isinstance(item, dict) else text)
    ordered = [t for item in kept_orders for t in split_orders(_coded(item))]
    # 2. a line of its own - in the advice, or given by the model as a preparation
    kept_advice: list[Any] = []
    seen = {" ".join(n["text"].lower().split()) for n in notes}
    for source, items in (("advice", advice), ("given", given)):
        for item in items:
            line = _coded(item)
            prep = parse(line)
            if prep is None:
                if source == "advice":
                    kept_advice.append(item)
                continue
            key = " ".join(line.lower().split())
            if key in seen:
                continue
            seen.add(key)
            named = _named_tests(line[: prep.start] + " " + line[prep.end:])
            if _all_tests().search(line):
                applies: Any = ALL
            elif len(named) == 1:
                applies = named
            elif len(named) > 1:
                applies = UNCLEAR
            elif len(ordered) == 1:
                applies = [ordered[0]]  # the only test there is
            elif ordered:
                applies = UNCLEAR
            else:
                # no test is ordered and none is named: an instruction about something else
                # (how to take a medicine, how to live) - not a LAB preparation
                if source == "advice":
                    kept_advice.append(item)
                continue
            notes.append(_note(item, line, prep, applies))
    if not notes:
        return payload
    out = {**payload, "_preparation": notes}
    if orders:
        out["investigations_advised"] = kept_orders
    if advice:
        out["advice"] = kept_advice
    return out


def applies_text(applies: Any) -> str:
    """How ``applies_to`` is kept on the fact: "all", "unclear", or "tests: FBS | HbA1c"."""
    if applies in (ALL, UNCLEAR):
        return str(applies)
    return TESTS + SEP.join(str(a) for a in applies)


def applies_of(value_text: str | None) -> Any:
    said = (value_text or "").strip()
    if said in (ALL, UNCLEAR):
        return said
    if said.startswith(TESTS):
        return [t.strip() for t in said[len(TESTS):].split("|") if t.strip()]
    return UNCLEAR  # not said: never assumed


def preparation_drafts(payload: dict[str, Any], default: float) -> list[Any]:
    from cdi_adapter.extract.facts import FactDraft

    out = []
    for n in payload.get("_preparation") or []:
        prep = parse(n["text"], guard=False)
        if prep is None:
            continue
        conf = n["confidence"] if n.get("confidence") is not None else default
        out.append(FactDraft(FACT_TYPE, n["text"], float(conf), list(n.get("evidence") or []),
                             value_kind="text", value_text=applies_text(n["applies_to"]),
                             value_num=prep.value,
                             value_unit_ucum="h" if prep.unit == "hours" else None))  # fmt: skip
    return out


def describe(local_text: str | None, value_text: str | None) -> dict[str, Any]:
    """What the result and the Reviewer show for one preparation fact: the words, and beside
    them the type, the value and what it applies to. The type is one of the fixed list or
    ``other``; a value that was not cleanly written is null, with the problem named."""
    prep = parse(local_text, guard=False)
    applies = applies_of(value_text)
    out: dict[str, Any] = {
        "text": local_text, "type": prep.type if prep else "other",
        "value": prep.value if prep else None, "unit": prep.unit if prep else None,
        "applies_to": applies, "problems": list(prep.problems) if prep else [],
    }  # fmt: skip
    if prep and prep.low is not None:
        out["range"] = [prep.low, prep.high]
    return out


def check(local_text: str | None, value_text: str | None, cited: str) -> list[tuple[str, str]]:
    """``[(code, message), ...]`` - each a reason this item goes to a person."""
    seen = describe(local_text, value_text)
    found: list[tuple[str, str]] = []
    if "number-unclear" in seen["problems"]:
        found.append(("preparation-number-unclear",
                      f"the number in {local_text!r} is not clearly a number"))  # fmt: skip
    if "number-implausible" in seen["problems"]:
        found.append(("preparation-number-implausible",
                      f"the number in {local_text!r} is not a possible one"))  # fmt: skip
    numbers = re.findall(r"\d+", str(local_text or ""))
    if cited and numbers and not found:
        there = re.findall(r"\d+", cited)
        lost = [n for n in numbers if n not in there]
        if lost:
            said = f"{' / '.join(lost)} is not in the line it cites ({cited[:60]!r})"
            found.append(("preparation-number-not-in-picture", said))
    if seen["applies_to"] == UNCLEAR:
        found.append(("preparation-applies-unclear",
                      "it is not clear which test this applies to"))  # fmt: skip
    return found
