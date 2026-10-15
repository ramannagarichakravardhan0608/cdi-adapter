"""The licence policy, read the same way everywhere (backlog ENT-S1, SW-S7). NOT LEGAL ADVICE.

One place says what a licence field means: the build's licence gate
(``scripts/licence_audit.py``) and the swap gate in the service (a champion, a contest, a
switch, shadow mode, fine-tuning) both ask here, so a licence cannot be "allowed" in one and
"blocked" in the other. Pure Python: the gate runs in CI with nothing installed.

* :func:`verdict_of` - ALLOWED / ALLOWED WITH NOTICES / NOT IN POLICY / REVIEW / UNKNOWN, from
  the words of a licence field and ``licences/policy.json``;
* :func:`model_problem` - why a model's licence keeps it out of use: never guessed (no words ->
  blocked); research-only, non-commercial, strong copyleft, proprietary or custom terms ->
  blocked until a decision to use it is recorded with who and when.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / "licences" / "policy.json"
ALLOWED, NOTICES, NOT_IN_POLICY, REVIEW, UNKNOWN = (
    "ALLOWED",
    "ALLOWED WITH NOTICES",
    "NOT IN POLICY",
    "REVIEW",
    "UNKNOWN",
)
RANK = {ALLOWED: 0, NOTICES: 1, NOT_IN_POLICY: 2, REVIEW: 3, UNKNOWN: 4}

# how a licence is recognised in the words of a licence field - most specific first
FAMILIES: tuple[tuple[str, str], ...] = (
    ("AGPL", r"\bAGPL|affero"),
    ("SSPL", r"\bSSPL|server side public"),
    ("RSAL", r"\bRSAL|redis source available"),
    ("LGPL", r"\bLGPL|lesser general public|library general public"),
    ("GPL", r"(?<![AL])GPL|GNU general public"),
    (
        "NON-COMMERCIAL",
        r"non-?commercial|\bCC-BY-NC|research[- ](?:only|use only|licen[cs]e)|"
        r"for research|academic use|evaluation only",
    ),
    (
        "PROPRIETARY",
        r"proprietary|commercial licen[cs]e|\bEULA\b|all rights reserved|"
        r"licen[cs]e agreement|other/proprietary",
    ),
    ("MIT", r"\bMIT\b|\bExpat\b"),
    ("BSD", r"\bBSD\b|\b0BSD\b|BSD-\d"),
    ("APACHE", r"apache"),
    ("ISC", r"\bISC\b"),
    ("POSTGRESQL", r"postgresql"),
    ("OFL", r"\bOFL\b|open font licen[cs]e"),
    ("MPL", r"\bMPL\b|mozilla public"),
    ("PSF", r"\bPSF\b|python software foundation"),
    ("UNLICENSE", r"unlicense|public domain|\bCC0\b"),
    ("ZLIB", r"\bzlib\b"),
    ("HPND", r"\bHPND\b|MIT-CMU|historical permission"),
    ("CC-BY", r"\bCC-BY\b(?!-N)|creative commons attribution"),
    ("UNICODE", r"unicode"),
    ("EPL", r"\bEPL\b|eclipse public"),
    ("BSL", r"\bBSL\b|boost software"),
    ("OWN", r"\bown work\b"),
    ("CUSTOM", r"\bcustom\b|terms of use|affiliate licen[cs]e"),
)


@lru_cache(maxsize=4)
def _policy(path: str) -> dict[str, Any]:
    return dict(json.loads(Path(path).read_text(encoding="utf-8")))


def policy(path: Path = POLICY) -> dict[str, Any]:
    return _policy(str(path))


def families(text: str | None) -> list[set[str]]:
    """The licence families named in a licence field, one set per ALTERNATIVE ("A or B" lets
    the licensee choose). An alternative in which nothing is recognised is an empty set."""
    said = " ".join(str(text or "").split())
    if not said:
        return []
    out = []
    for part in re.split(r"\s+or\s+", said, flags=re.I):
        out.append({name for name, pattern in FAMILIES if re.search(pattern, part, re.I)})
    return out


def verdict_of(text: str | None, rules: dict[str, Any] | None = None) -> str:
    """ALLOWED / ALLOWED WITH NOTICES / NOT IN POLICY / REVIEW / UNKNOWN for one licence field.
    All the licences of an alternative must be acceptable; the best alternative counts."""
    rules = rules or policy()
    allowed, notices = set(rules["allowed"]), set(rules["allowed_with_notices"])
    review = set(rules["review"])
    best = UNKNOWN
    for found in families(text):
        if not found:
            continue
        if found & review:
            one = REVIEW
        elif found - allowed - notices:
            one = NOT_IN_POLICY
        elif found & notices:
            one = NOTICES
        else:
            one = ALLOWED
        best = min(best, one, key=RANK.__getitem__)
    return best


def decided(decision: Any) -> bool:
    """A decision counts only with what was decided, by whom and when - and it says to use it."""
    return bool(
        isinstance(decision, dict)
        and decision.get("decision")
        and decision.get("by")
        and decision.get("date")
        and str(decision.get("outcome") or "use").lower() != "do not use"
    )


def model_problem(
    licence: dict[str, Any] | None, rules: dict[str, Any] | None = None
) -> str | None:
    """Why a model may not be used under this licence entry (``models/registry.json``), or None.
    The words are the licence as read and recorded (``name``); a decision to use a model whose
    licence is on the review list goes in ``decision`` with who and when."""
    licence = licence or {}
    name = str(licence.get("name") or "").strip()
    verdict = verdict_of(name, rules)
    if verdict in (ALLOWED, NOTICES):
        return None
    if verdict == UNKNOWN:
        return "its licence is UNKNOWN (nothing recognisable recorded): unknown means blocked"
    found = sorted(set().union(*families(name)) - {"MIT", "BSD", "APACHE", "ISC"})
    if decided(licence.get("decision")):
        return None
    return (f"its licence ({name}) is {verdict}"
            + (f": {', '.join(found)}" if found else "")
            + " - blocked until a decision to use it is recorded (decision, by, date)")  # fmt: skip
