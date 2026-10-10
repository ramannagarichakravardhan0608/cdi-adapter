"""Do the two handwriting readers say the same thing? (backlog RD-S3; ARCHITECTURE §15.3)

TrOCR and Qwen read the same line crop on their own (RD-S2). :func:`compare_texts` puts the two
readings side by side under two rules:

* **numbers must match exactly** - a strength, a dose, a frequency. Before they are compared,
  the letters a handwriting reader is known to put in a number's place are read as the digits
  they stand for, inside a number only: ``4O`` is ``40``, ``l0`` is ``10``. Nothing else is
  forgiven: ``40`` against ``4`` is a different dose.
* **names must be the same word** once letter case, spacing and punctuation are set aside. How
  alike two different names are is measured and recorded (it tells a slip from another
  medicine), but any difference sends the line to a person: ``Telma`` against ``Telmikind`` is
  another product, and ``Telma`` against ``Telrna`` is for a person to settle, not a guess.

The result is one of the three states of the ``ocr_agreement`` table (migration 0018): ``AGREE``
(the value can stand), ``PARTIAL`` (close: a slip), ``MATERIAL`` (a different value). Only
``AGREE`` lets a value through. The readings themselves are never changed: the agreed value is
a new string, made from them.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from difflib import SequenceMatcher

ALGORITHM_VERSION = "rd-s3.v1"
AGREE, PARTIAL, MATERIAL = "AGREE", "PARTIAL", "MATERIAL"

# Letters that stand in a number's place in handwriting reading - read as digits only when they
# sit in a run that holds a real digit. Deliberately few: S, B, Z and g are also letters of
# units and names ("5mg" against "Smg"), so they are left as they are.
CONFUSABLE = str.maketrans({"O": "0", "o": "0", "l": "1", "I": "1", "|": "1"})
_RUN = re.compile(r"(?<![A-Za-z])[OolI|]*\d[\dOolI|]*(?:[.,/:-][\dOolI|]+)*")

# How alike two different names must be to count as a slip (PARTIAL) rather than another word
# (MATERIAL). ASSUMPTION, recorded with every comparison: both go to a person, so this changes
# the label a reviewer sees, never whether a value is accepted. To be set from measurement
# (ENT-S3) once real readings exist.
NAME_PARTIAL = 0.75


@dataclass(frozen=True)
class Comparison:
    state: str  # AGREE | PARTIAL | MATERIAL
    score: float  # 0..1: how alike the two readings are
    reason: str
    value: str | None = None  # the agreed value, when they agree
    numbers: tuple[str, ...] = ()
    version: str = ALGORITHM_VERSION

    @property
    def agrees(self) -> bool:
        return self.state == AGREE


def numbers(text: str | None) -> tuple[str, ...]:
    """The numbers on a line, in order, with stand-in letters read as digits: ``"Tab 4O mg
    1-O-1"`` -> ``("40", "1-0-1")``."""
    return tuple(m.group(0).translate(CONFUSABLE).replace(",", ".")
                 for m in _RUN.finditer(str(text or "")))  # fmt: skip


def name_of(text: str | None) -> str:
    """The words of a line without its numbers, as compared: lower case, no punctuation."""
    without = _RUN.sub(" ", str(text or ""))
    return " ".join(re.sub(r"[^a-z]+", " ", without.lower()).split())


def _substitutions(text: str) -> int:
    """How many stand-in letters a reading has inside its numbers."""
    return sum(sum(c in "OolI|" for c in m.group(0)) for m in _RUN.finditer(text))


def _canonical(text: str) -> str:
    """The reading with its numbers written as digits - a new string; the reading is untouched."""
    out = _RUN.sub(lambda m: m.group(0).translate(CONFUSABLE), text)
    return " ".join(out.split())


def compare_texts(a: str | None, b: str | None) -> Comparison:
    """Compare two readings of one line."""
    a, b = " ".join(str(a or "").split()), " ".join(str(b or "").split())
    if not a or not b:
        return Comparison(MATERIAL, 0.0, "a reader read nothing")
    na, nb = name_of(a), name_of(b)
    alike = round(SequenceMatcher(None, na, nb).ratio(), 4) if (na or nb) else 1.0
    numa, numb = numbers(a), numbers(b)
    if numa != numb:
        shown = f"{' '.join(numa) or 'none'} against {' '.join(numb) or 'none'}"
        return Comparison(MATERIAL, round(min(alike, 0.5), 4), f"the numbers differ: {shown}")
    if na != nb:
        state = PARTIAL if alike >= NAME_PARTIAL else MATERIAL
        why = "a slip in" if state == PARTIAL else "different"
        reason = (f"{why} words: {na!r} against {nb!r} "
                  f"(alike {alike:.2f}; a slip from {NAME_PARTIAL:.2f})")  # fmt: skip
        return Comparison(state, alike, reason, numbers=numa)
    # the same words and the same numbers: the value is the reading that needed no stand-in
    # letters read as digits (on a tie, the second - Qwen's - as written)
    value = a if _substitutions(a) < _substitutions(b) else b
    return Comparison(AGREE, 1.0, "the same words and the same numbers",
                      value=_canonical(value), numbers=numa)  # fmt: skip


def compare_engines(readings: Mapping[str, str]) -> Comparison:
    """Compare TrOCR's reading with Qwen's (``engines.TROCR`` and ``engines.QWEN``)."""
    from cdi_adapter.recognition.engines import QWEN, TROCR

    if TROCR not in readings or QWEN not in readings:
        missing = " and ".join(n for n in (TROCR, QWEN) if n not in readings)
        return Comparison(MATERIAL, 0.0, f"only one reader read the line ({missing} did not)")
    return compare_texts(readings[TROCR], readings[QWEN])
