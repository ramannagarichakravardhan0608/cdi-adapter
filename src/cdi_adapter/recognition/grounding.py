"""Is the number really in the picture? (backlog RD-S3: the picture check)

Two readers agreeing is not proof: both can be wrong the same way. So every number of a value
about to be accepted is looked for in the pixels of the line it came from, by a third reader
that has nothing to do with the other two - RapidOCR, the print reader, run on the crop alone.

* **supported** - each number of the value is among the numbers the picture reads as;
* **contradicted** - the picture reads, and a number of the value is not in it;
* **unreadable** - the picture check could read no number at all, so it cannot confirm one;
* **not applicable** - the value has no number.

Only *supported* and *not applicable* let a value through. *Unreadable* is not taken as a
"yes": the story's rule is never to guess, so a number nobody could confirm goes to a person.
The print reader is weak on handwriting, so on real handwritten lines that will be common -
which is why its rate is watched, and why the check can be replaced by a better one without
changing anything that uses it (``read`` is any function from a picture to text).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field

from cdi_adapter.recognition.disagreement import numbers

SUPPORTED, CONTRADICTED, UNREADABLE, NOT_APPLICABLE = (
    "supported", "contradicted", "unreadable", "not_applicable")  # fmt: skip
PASSES = (SUPPORTED, NOT_APPLICABLE)

Read = Callable[[bytes], str]


@dataclass(frozen=True)
class Grounding:
    status: str
    reason: str
    expected: tuple[str, ...] = ()
    seen: tuple[str, ...] = field(default=())
    reader: str = "rapidocr"

    @property
    def passes(self) -> bool:
        return self.status in PASSES


PAD_PX = 40  # paper added round the crop before the print reader looks at it


def read_with_rapidocr(crop_png: bytes) -> str:
    """The print reader on one line crop, every line it sees, however unsure. The crop gets
    more paper round it first: cut tight, the print reader joins the edge of the line to the
    text (it read "a40" for "40" in testing); with a margin it reads it as written."""
    from cdi_adapter.ocr import rapid
    from cdi_adapter.recognition.engines import repad

    return " ".join(line.text for line in rapid.read_here(repad(crop_png, PAD_PX), min_conf=0.0))


def ground(crop_png: bytes, value: str | None, *, read: Read = read_with_rapidocr,
           reader: str = "rapidocr") -> Grounding:  # fmt: skip
    """Look for each number of ``value`` in the picture. Never raises: a check that breaks is
    a check that could not confirm."""
    expected = numbers(value)
    if not expected:
        return Grounding(NOT_APPLICABLE, "the value has no number to look for", reader=reader)
    try:
        seen = numbers(read(crop_png))
    except Exception as exc:  # noqa: BLE001 - see above
        return Grounding(UNREADABLE, f"the picture check failed ({type(exc).__name__})",
                         expected, reader=reader)  # fmt: skip
    if not seen:
        why = "the picture check read no number on the line, so it cannot confirm "
        return Grounding(UNREADABLE, why + " ".join(expected), expected, reader=reader)
    missing = Counter(expected) - Counter(seen)
    if missing:
        absent = " ".join(sorted(missing.elements()))
        why = f"{absent} is not in the picture (it reads {' '.join(seen)})"
        return Grounding(CONTRADICTED, why, expected, seen, reader)
    return Grounding(SUPPORTED, "every number is in the picture", expected, seen, reader)
