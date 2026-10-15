"""The internal canonical vocabulary, and SNOMED CT / ICD-10 only where they are licensed
(backlog SW-S6 FR4, NFR1, AC3).

No customer needs SNOMED CT or ICD-10 to use the product. By default a diagnosis, symptom,
procedure, medicine or allergen is saved as what the page said (``local_text``) plus its
canonical term from this vocabulary (``code_display``), named by the vocabulary's own id
(``code_system`` = :data:`INTERNAL_SYSTEM`, ``code`` = e.g. ``condition:type-2-diabetes-mellitus``)
- with no external code. LOINC (free to use) stays on lab tests and vital signs.

``CDI_LICENSED_CODE_SYSTEMS`` names what the customer holds a licence for: ``snomed`` (SNOMED CT),
``icd10`` (ICD-10), comma separated. Only then is a term also given that code - the licensed
adapter, which today is the curated map in ``terminology/seed.py`` (curated, not checked
against a licensed release; E3-S1 replaces it with the customer's own release).

The vocabulary itself is ``terminology/data/canonical_vocabulary.csv`` (the columns of the
story: internal_id, canonical_name, synonyms, abbreviations, type, source, loinc_code,
snomed_code_optional, icd_code_optional). It is generated from the seed's names and aliases
(``python -m cdi_adapter.terminology.canonical build``) and ships with the SNOMED and ICD
columns EMPTY: no SNOMED or ICD content is in the default install's vocabulary. The terms in it
are to be reviewed by a clinician (the story's open question).
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
import sys
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from cdi_adapter.config import get_settings
from cdi_adapter.terminology import seed

INTERNAL_SYSTEM = "https://careflow.clinic/term"
FILE = Path(__file__).with_name("data") / "canonical_vocabulary.csv"
COLUMNS = (
    "internal_id",
    "canonical_name",
    "synonyms",
    "abbreviations",
    "type",
    "source",
    "loinc_code",
    "snomed_code_optional",
    "icd_code_optional",
)
CODE_SYSTEMS = {"snomed": seed.SNOMED, "icd10": seed.ICD10}
SOURCE = "seed list (curated; to be reviewed by a clinician)"
# which seed table a term comes from, and the type it is in the vocabulary
TABLES: tuple[tuple[str, tuple[seed.Concept, ...]], ...] = (
    ("condition", seed.CONDITIONS),
    ("symptom", seed.SYMPTOMS),
    ("procedure", seed.PROCEDURES),
    ("lab", seed.LABS),
    ("vital_sign", seed.VITALS),
    ("medicine", seed.DRUGS),
    ("allergen", seed.ALLERGENS),
    ("lab", seed.LAB_PANELS),
    ("study", seed.ORDERED_STUDIES),
)
_ABBREVIATION = re.compile(r"^[a-z0-9]{2,5}$")


@dataclass(frozen=True)
class Term:
    internal_id: str
    canonical_name: str
    synonyms: tuple[str, ...]
    abbreviations: tuple[str, ...]
    type: str
    source: str
    loinc_code: str | None
    snomed_code_optional: str | None
    icd_code_optional: str | None


def licensed_systems() -> set[str]:
    """The external code systems this customer holds a licence for
    (``CDI_LICENSED_CODE_SYSTEMS``)."""
    named = {x.strip().lower() for x in get_settings().licensed_code_systems.split(",")}
    return named & set(CODE_SYSTEMS)


def licensed(name: str) -> bool:
    return name in licensed_systems()


def unknown_systems() -> list[str]:
    """Names in ``CDI_LICENSED_CODE_SYSTEMS`` that are not code systems (a typo is not a
    licence)."""
    named = {x.strip().lower() for x in get_settings().licensed_code_systems.split(",")}
    return sorted(n for n in named if n and n not in CODE_SYSTEMS)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def internal_id(kind: str, name: str) -> str:
    return f"{kind}:{_slug(name)}"


def _terms() -> Iterator[tuple[seed.Concept, Term]]:
    seen: set[str] = set()
    for kind, table in TABLES:
        for c in table:
            tid = internal_id(kind, c.display)
            if tid in seen:
                continue
            seen.add(tid)
            aliases = [a for a in c.aliases if a.strip()]
            yield (
                c,
                Term(
                    tid,
                    c.display,
                    tuple(a for a in aliases if not _ABBREVIATION.match(a)),
                    tuple(a for a in aliases if _ABBREVIATION.match(a)),
                    kind,
                    SOURCE,
                    c.code if c.system == seed.LOINC else None,
                    None,
                    None,
                ),
            )


@lru_cache
def _by_code() -> dict[tuple[str, str], Term]:
    """The seed's (system, code) -> the term: how a bound concept is given its internal id."""
    found: dict[tuple[str, str], Term] = {}
    for c, t in _terms():  # the first table wins: a medicine stays a medicine
        found.setdefault((c.system, c.code), t)
    return found


@lru_cache
def _icd_by_term() -> dict[str, str]:
    return {t.internal_id: c.icd10 for c, t in _terms() if c.icd10}


def build() -> str:
    """The vocabulary file's content: every term, the SNOMED and ICD columns left empty."""
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(COLUMNS)
    for _, t in sorted(_terms(), key=lambda ct: ct[1].internal_id):
        w.writerow(
            [
                t.internal_id,
                t.canonical_name,
                "|".join(t.synonyms),
                "|".join(t.abbreviations),
                t.type,
                t.source,
                t.loinc_code or "",
                "",
                "",
            ]
        )
    return out.getvalue()


@lru_cache
def load(path: Path = FILE) -> tuple[Term, ...]:
    rows = list(csv.DictReader(path.read_text(encoding="utf-8").splitlines()))
    return tuple(
        Term(
            r["internal_id"],
            r["canonical_name"],
            tuple(filter(None, r["synonyms"].split("|"))),
            tuple(filter(None, r["abbreviations"].split("|"))),
            r["type"],
            r["source"],
            r["loinc_code"] or None,
            r["snomed_code_optional"] or None,
            r["icd_code_optional"] or None,
        )
        for r in rows
    )


def version(path: Path = FILE) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def term(internal: str) -> Term | None:
    return next((t for t in load() if t.internal_id == internal), None)


def present(concept: seed.Concept) -> seed.Concept:
    """The concept as it is SAVED for this customer: a SNOMED CT concept without a SNOMED
    licence becomes its canonical term (internal id, canonical name), with no external code
    and no ICD-10. LOINC concepts are kept as they are."""
    if concept.system != seed.SNOMED:
        return concept
    t = _by_code().get((concept.system, concept.code))
    icd = concept.icd10 if licensed("icd10") else None
    if licensed("snomed"):
        return seed.Concept(concept.system, concept.code, concept.display, concept.aliases, icd)
    if t is None:  # not in the vocabulary: by its name, never by its SNOMED code
        return seed.Concept(
            INTERNAL_SYSTEM,
            internal_id("term", concept.display),
            concept.display,
            concept.aliases,
            icd,
        )
    return seed.Concept(INTERNAL_SYSTEM, t.internal_id, t.canonical_name, concept.aliases, icd)


def icd10_for(code: str | None) -> str | None:
    """The ICD-10 code of a saved term (by its SNOMED code or its internal id) - only where
    ICD-10 is licensed."""
    if not code or not licensed("icd10"):
        return None
    if code in _icd_by_term():
        return _icd_by_term()[code]
    t = _by_code().get((seed.SNOMED, code))
    return _icd_by_term().get(t.internal_id) if t else None


def route_coding(route: str | None) -> dict[str, str] | None:
    """A medicine's route as a SNOMED CT coding - only where SNOMED CT is licensed."""
    found = seed.ROUTE_SCT.get(str(route or "").lower())
    if not found or not licensed("snomed"):
        return None
    return {"system": seed.SNOMED, "code": found[0], "display": found[1]}


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["build"]:
        FILE.write_text(build(), encoding="utf-8", newline="\n")
        print(f"{FILE} ({len(load.__wrapped__(FILE))} terms, version {version()})")
        return 0
    if args[:1] == ["check"]:
        same = FILE.read_text(encoding="utf-8") == build()
        print("the vocabulary file is up to date" if same else "OUT OF DATE: run build")
        return 0 if same else 1
    print(__doc__.split("\n\n")[0])
    return 2


if __name__ == "__main__":
    sys.exit(main())
