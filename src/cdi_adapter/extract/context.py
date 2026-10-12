"""Why each test was ordered: the reasons written on the same prescription (backlog EX-S5).

For every ordered test this lists the diagnoses, complaints and findings the prescription
itself gives - each with the text it came from and its proof - and says **how the page
connects them**, never more than the page shows:

* ``same_line``  - the reason is written on the test's own line ("T2DM - HbA1c, FBS");
* ``same_page``  - both are on the page, and no line connects them;
* ``other_page`` - the reason is on another page of the same prescription.

Nothing here says "ordered because": a test and a diagnosis on one page are shown as being on
one page (AC4). A reason is never invented - a prescription with no written reason gives an
empty context (AC2) - and a link whose reason has no proof on the page is removed (FR5).

Context is also a check, and only a check (NFR1): when reasons ARE written and the test goes
with none of them, the test is flagged ``check: does not fit`` and sent to a person. The test
is not changed, renamed or removed. Which tests go with which reasons is a plain data file a
clinician approves (``data/indication_groups.json``); a test or a reason that file does not
know is simply not judged, so the flag is raised only when every written reason is known and
none of them is one the test goes with.

Pure functions; no I/O beyond reading the data file once.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

FILE = Path(__file__).with_name("data") / "indication_groups.json"
SAME_LINE, SAME_PAGE, OTHER_PAGE = "same_line", "same_page", "other_page"
DOES_NOT_FIT = "check: does not fit"
REASON_TYPES = {"condition": "diagnosis", "symptom": "complaint", "finding": "note"}


@lru_cache
def bank(path: Path = FILE) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def version() -> str:
    return str(bank()["version"])


def _norm(text: Any) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).split())


def _test_id(written: str | None) -> str | None:
    """The lab-test master's id for a written test, or None when the master does not name it
    for certain (a doubtful match is not used to judge anything)."""
    from cdi_adapter.terminology import lab_master

    m = lab_master.match(written)
    return m.entry.internal_id if m.entry and m.status == lab_master.MATCHED else None


@lru_cache
def _groups() -> tuple[list[tuple[str, re.Pattern[str], frozenset[str]]], frozenset[str]]:
    """((group, the pattern of its reasons, the ids of its tests), the tests that fit anything).
    A test name the lab-test master does not know is left out, not guessed at."""
    out = []
    for g in bank()["groups"]:
        words = sorted((_norm(r) for r in g["reasons"]), key=len, reverse=True)
        pattern = re.compile(r"(?<![a-z0-9])(?:" + "|".join(re.escape(w) for w in words if w)
                             + r")(?![a-z0-9])")  # fmt: skip
        ids = frozenset(i for i in (_test_id(t) for t in g["tests"]) if i)
        out.append((str(g["name"]), pattern, ids))
    anything = frozenset(i for i in (_test_id(t) for t in bank()["fits_any_reason"]) if i)
    return out, anything


def groups_of_reason(text: str | None) -> set[str]:
    """The groups a written reason belongs to; empty when the file does not know it."""
    said = _norm(text)
    return {name for name, pattern, _ in _groups()[0] if said and pattern.search(said)}


def groups_of_test(written: str | None) -> set[str] | None:
    """The groups a test goes with; None when the test is not judged at all - the master does
    not name it, the file does not list it, or it is a test that fits any reason."""
    test = _test_id(written)
    groups, anything = _groups()
    if test is None or test in anything:
        return None
    found = {name for name, _, ids in groups if test in ids}
    return found or None


def fit(test: str | None, reasons: list[str]) -> bool | None:
    """Does the test go with a reason written on the prescription?

    * True  - with at least one of them;
    * False - reasons are written, every one of them is known, and it goes with none;
    * None  - not judged: no reason written, or the test or one of the reasons is unknown.
    """
    wanted = groups_of_test(test)
    known = [groups_of_reason(r) for r in reasons if _norm(r)]
    if wanted is None or not known:
        return None
    if any(wanted & g for g in known):
        return True
    return False if all(known) else None


def _relation(test: dict[str, Any], reason: dict[str, Any]) -> str:
    here = set(test.get("blocks") or [])
    if here and here & set(reason.get("blocks") or []):
        return SAME_LINE
    if test.get("page") is not None and reason.get("page") is not None and (
        test["page"] != reason["page"]
    ):
        return OTHER_PAGE
    return SAME_PAGE


def link_context(tests: list[dict[str, Any]], reasons: list[dict[str, Any]]
                 ) -> list[dict[str, Any]]:  # fmt: skip
    """``tests``: ``{"as_written", "page", "blocks"}``; ``reasons``: ``{"type", "text", "quote",
    "page", "blocks", "evidence"}`` - the diagnoses, complaints and findings of the same
    prescription. Returns, per test, ``{"context": [...], "fits": bool | None, "check": ...}``.

    Every written reason that has proof is listed with how the page connects it to the test; a
    reason known NOT to go with the test is left out of its context (it is not "its" reason),
    and one with no quote behind it is removed. ``check`` is ``"check: does not fit"`` when the
    test goes with none of the written reasons, else absent."""
    proven = [r for r in reasons if (r.get("quote") or r.get("blocks")) and _norm(r.get("text"))]
    out = []
    for t in tests:
        wanted = groups_of_test(t.get("as_written"))
        context = []
        for r in proven:
            relation = _relation(t, r)
            theirs = groups_of_reason(r["text"])
            goes = None if wanted is None or not theirs else bool(wanted & theirs)
            if goes is False and relation != SAME_LINE:
                continue  # written on the page, but known not to be this test's reason
            entry = {"type": r["type"], "text": r["text"], "relation": relation,
                     "quote": r.get("quote") or r["text"]}  # fmt: skip
            if r.get("evidence"):
                entry["evidence"] = r["evidence"]
            if goes is not None:
                entry["fits"] = goes
            context.append(entry)
        fits = fit(t.get("as_written"), [r["text"] for r in proven])
        row: dict[str, Any] = {"context": context, "fits": fits}
        if fits is False:
            row["check"] = DOES_NOT_FIT
        out.append(row)
    return out
