"""The lab-test master: every ordered test gets a standard name (backlog EX-S3).

``data/lab_tests.csv`` is the list - one row per orderable test, panel or study, with the ways
it is written (aliases), its kind, its specimen and, where one is recorded, its code. The file
is **versioned** (``# version:`` in its head); that version is saved with every document's
terminology run, so a result says which list named its tests.

Matching is a cascade, strict first (NFR1):

1. **exact** - the written text is the canonical name;
2. **alias** - it is one of the listed ways of writing it;
3. **normalised** - the same after the words around a test are taken off ("S. creatinine",
   "TSH level", "for CBC");
4. **fuzzy** - close to a listed way of writing it. This is only ever ``doubtful``: it goes to
   a person. And it is never tried where a small difference is another test: a text with a
   digit in it ("T3" / "T4", "B12") must have the same digits, and a short abbreviation is
   never matched by likeness at all ("TSML" is not "TSH").

Anything else is **not matched** - an allowed, honest answer. The text as written is always
kept, a test is never invented, and a **panel is never expanded** into its tests: a panel is
one row here and one item in the result.

For a test that is doubtful or not matched, :func:`candidates` offers the closest rows of the
list; the reviewer picks one or says "none of these".

A row with no code has none assigned yet - its standard name is ours (the internal vocabulary).
No code in the file was invented for this story; see the file's own header.
"""

from __future__ import annotations

import csv
import difflib
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

FILE = Path(__file__).with_name("data") / "lab_tests.csv"
INTERNAL_SYSTEM = "https://careflow.clinic/lab-test"  # a row's own id, when it has no code
LOINC = "http://loinc.org"
KINDS = ("test", "panel", "study")
MATCHED, DOUBTFUL, NOT_MATCHED = "matched", "doubtful", "not_matched"
FUZZY_CUTOFF = 0.86  # as the seed lookup used
FUZZY_MIN_LETTERS = 6  # ASSUMPTION: an abbreviation shorter than this is never matched by likeness
CANDIDATES = 3

_HBA1C = re.compile(r"\bhb\s*a\s*1\s*c\b|\bhba\s*1c\b|\bhb1ac\b")
_PREFIX = re.compile(r"^(?:s|sr|serum|blood|b|test|tests|for|do|get|repeat|check|plasma)\s+")
_SUFFIX = re.compile(r"\s+(?:test|tests|level|levels|report|estimation|done|urgent|urgently|"
                     r"stat|today|asap)$")
_URGENT = re.compile(r"\b(urgent|urgently|stat|immediately|today|asap|emergency)\b", re.I)


@dataclass(frozen=True)
class Entry:
    internal_id: str
    name: str
    kind: str
    specimen: str | None
    code_system: str | None
    code: str | None
    aliases: tuple[str, ...] = field(default_factory=tuple)
    reviewed: bool = False

    @property
    def system(self) -> str:
        return self.code_system or INTERNAL_SYSTEM

    @property
    def standard_code(self) -> str:
        return self.code or self.internal_id

    def standard(self) -> dict[str, Any]:
        return {"name": self.name, "code_system": self.system, "code": self.standard_code}


@dataclass(frozen=True)
class Match:
    status: str  # matched | doubtful | not_matched
    how: str | None = None  # exact | alias | normalised | fuzzy
    entry: Entry | None = None


_INITIALS = re.compile(r"\b(?:[a-z]\.){2,}[a-z]?\b\.?")


def normalize(text: str | None) -> str:
    t = _HBA1C.sub("hba1c", (text or "").lower())
    # initials written with stops are the abbreviation: "C.B.C" -> "cbc", "E.S.R." -> "esr"
    t = _INITIALS.sub(lambda m: m.group(0).replace(".", ""), t)
    t = re.sub(r"[^a-z0-9%/ ]+", " ", t)
    return " ".join(t.split())


def order_key(key: str) -> str:
    """A test as ordered, without the words around it: "s creatinine" -> "creatinine"."""
    prev = None
    while prev != key:
        prev, key = key, _SUFFIX.sub("", _PREFIX.sub("", key)).strip()
    return key


@lru_cache
def load(path: Path = FILE) -> tuple[str, tuple[Entry, ...]]:
    """``(version, entries)``. Refuses a file two rows of which claim the same way of writing a
    test: one written text must never mean two tests."""
    version = "unversioned"
    lines = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        if raw.startswith("#"):
            if raw.lower().startswith("# version:"):
                version = raw.split(":", 1)[1].strip()
            continue
        lines.append(raw)
    entries = []
    for r in csv.DictReader(lines):
        kind = (r["kind"] or "").strip()
        if kind not in KINDS:
            raise ValueError(f"{r['internal_id']}: kind {kind!r} is not one of {KINDS}")
        entries.append(Entry(
            r["internal_id"].strip(), r["canonical_name"].strip(), kind,
            r["specimen"].strip() or None, r["code_system"].strip() or None,
            r["code"].strip() or None,
            tuple(a.strip() for a in r["aliases"].split("|") if a.strip()),
            r["review_status"].strip().lower() == "reviewed"))  # fmt: skip
    seen: dict[str, str] = {}
    for e in entries:
        for way in {normalize(e.name), *(normalize(a) for a in e.aliases)}:
            if way in seen and seen[way] != e.internal_id:
                raise ValueError(f"{way!r} is listed for {seen[way]} and {e.internal_id}")
            seen[way] = e.internal_id
    return version, tuple(entries)


def version() -> str:
    return load()[0]


@lru_cache
def _index() -> tuple[dict[str, Entry], dict[str, Entry], dict[str, Entry]]:
    names: dict[str, Entry] = {}
    aliases: dict[str, Entry] = {}
    by_code: dict[str, Entry] = {}
    for e in load()[1]:
        names[normalize(e.name)] = e
        for a in e.aliases:
            aliases.setdefault(normalize(a), e)
        by_code[e.standard_code] = e
        by_code[e.internal_id] = e
    return names, aliases, by_code


@lru_cache
def _bared() -> dict[str, Entry]:
    """Every listed way of writing a test, with the words around it taken off - what a written
    text is compared with for likeness ("liver functon" against "liver function")."""
    names, aliases, _ = _index()
    out: dict[str, Entry] = {}
    for way, entry in (*aliases.items(), *names.items()):
        out.setdefault(order_key(way), entry)
        out.setdefault(way, entry)
    return out


def by_code(code: str | None) -> Entry | None:
    return _index()[2].get(str(code)) if code else None


def _digits(text: str) -> list[str]:
    return re.findall(r"\d+", text)


def _alike(key: str, ways: list[str], n: int, cutoff: float) -> list[str]:
    """The listed ways of writing that are close to ``key`` and safe to compare it with: the
    same digits, in the same order (a number is never "close" to another number)."""
    return [w for w in difflib.get_close_matches(key, ways, n=max(n * 4, 8), cutoff=cutoff)
            if _digits(w) == _digits(key)][:n]  # fmt: skip


def match(text: str | None) -> Match:
    names, aliases, _ = _index()
    key = normalize(text)
    if not key:
        return Match(NOT_MATCHED)
    if key in names:
        return Match(MATCHED, "exact", names[key])
    if key in aliases:
        return Match(MATCHED, "alias", aliases[key])
    bare = order_key(key)
    # the same, written with or without the stroke: "urine r/e" / "urine re"
    for way in (bare, bare.replace("/", ""), bare.replace("/", " "), bare.rstrip("% ")):
        way = " ".join(way.split())
        if way != key and (way in names or way in aliases):
            return Match(MATCHED, "normalised", names.get(way) or aliases[way])
    # a study with where it is to be done: "MRI brain", "CECT abdomen", "X-ray chest PA". The
    # study is named; the site stays in the text as written. Only a study - a test's name
    # followed by more words is another test ("T3 T4 TSH" is not "T3").
    words = bare.split()
    for n in range(len(words) - 1, 0, -1):
        lead = " ".join(words[:n])
        entry = names.get(lead) or aliases.get(lead)
        if entry is not None and entry.kind == "study":
            return Match(MATCHED, "normalised", entry)
    if len(re.sub(r"[^a-z]", "", bare)) >= FUZZY_MIN_LETTERS:
        bared = _bared()
        close = _alike(bare, list(bared), 1, FUZZY_CUTOFF)
        if close:
            return Match(DOUBTFUL, "fuzzy", bared[close[0]])
    return Match(NOT_MATCHED)


def candidates(text: str | None, n: int = CANDIDATES) -> list[dict[str, Any]]:
    """The closest rows of the list for a text that did not match cleanly, best first. May be
    empty: "none of these" is always an answer the reviewer can give."""
    names, aliases, _ = _index()
    key = order_key(normalize(text))
    if not key:
        return []
    out: list[Entry] = []
    ways = [*names, *aliases]
    first = [w for w in ways if w.startswith(key) or key.startswith(w)] if len(key) >= 3 else []
    for way in [*first, *_alike(key, ways, n * 2, 0.6)]:
        e = names.get(way) or aliases[way]
        if e not in out:
            out.append(e)
    return [{"internal_id": e.internal_id, **e.standard(), "kind": e.kind} for e in out[:n]]


def urgency_of(text: str | None) -> str | None:
    """"urgent" when the order says so in words; None otherwise - "routine" is not assumed."""
    return "urgent" if _URGENT.search(text or "") else None


def describe(text: str | None, code: str | None = None) -> dict[str, Any]:
    """What the result and the Reviewer show for one ordered test. ``code`` is the code saved on
    the fact: when a person has chosen one, it wins over what the text alone would match."""
    m = match(text)
    chosen = by_code(code)
    entry = chosen or (m.entry if m.status != NOT_MATCHED else None)
    status = MATCHED if chosen and (m.entry is None or m.entry == chosen and m.status == MATCHED
                                    or m.status != MATCHED) and code else m.status  # fmt: skip
    if chosen and m.status == DOUBTFUL and m.entry == chosen:
        status = DOUBTFUL  # the saved code is the doubtful one: still for a person to confirm
    out: dict[str, Any] = {
        "as_written": text, "match": status, "how": m.how if entry is m.entry else "chosen",
        "kind": entry.kind if entry else None,
        "specimen": entry.specimen if entry else None,
        "urgency": urgency_of(text),
        "standard": entry.standard() if entry and status != NOT_MATCHED else None,
    }  # fmt: skip
    if status != MATCHED:
        out["candidates"] = candidates(text)
    return out


def coverage(rows: list[dict[str, str]]) -> dict[str, Any]:
    """The answer-key report (AC4). Each row: ``as_written`` and ``expected`` - the internal id
    or code the text should be named as, or empty when it should NOT be matched (an unknown
    abbreviation, something that is not a test).

    * ``share_matched``  - of the texts that have a right answer, how many were given a name;
    * ``share_correct``  - of those same texts, how many were given the RIGHT name;
    * a doubtful (fuzzy) match is counted apart: it goes to a person, it is not a "match".
    """
    known = [r for r in rows if (r.get("expected") or "").strip()]
    unknown = [r for r in rows if not (r.get("expected") or "").strip()]
    matched = correct = doubtful = 0
    wrong: list[dict[str, Any]] = []
    missed: list[str] = []
    for r in known:
        m = match(r["as_written"])
        want = by_code(r["expected"].strip())
        if m.status == MATCHED:
            matched += 1
            if want is not None and m.entry == want:
                correct += 1
            else:
                wrong.append({"as_written": r["as_written"], "expected": r["expected"],
                              "got": m.entry.standard_code if m.entry else None})  # fmt: skip
        elif m.status == DOUBTFUL:
            doubtful += 1
            missed.append(r["as_written"])
        else:
            missed.append(r["as_written"])
    false_names = [r["as_written"] for r in unknown if match(r["as_written"]).status == MATCHED]
    n = len(known)
    return {
        "master_version": version(), "master_rows": len(load()[1]),
        "texts": len(rows), "with_a_right_answer": n, "should_not_match": len(unknown),
        "matched": matched, "correct": correct, "doubtful": doubtful,
        "share_matched": round(matched / n, 4) if n else None,
        "share_correct": round(correct / n, 4) if n else None,
        "wrong": wrong, "not_matched": missed,
        "named_but_should_not_be": false_names,
    }  # fmt: skip
