"""Accept a handwritten value, or send it to a person - and say why (backlog RD-S3).

:func:`assess_fact` runs the checks on one handwritten line in a fixed order, and a value is
accepted only if it passes **every** one. Any disagreement or failed check means *needs a
check*, however confident a model sounds:

1. **both readers read it** (RD-S2) - one reading alone is never accepted;
2. **the readers agree** (``disagreement.compare_engines``) - numbers exactly, words the same;
3. **Qwen agrees with itself** (self-consistency, when asked for) - the same crop read again
   with different padding must give the same answer;
4. **the picture shows it** (``grounding.ground``) - every number is really in the pixels. This
   check stands above the readers: two readers agreeing never outweighs a picture that does not
   show the number (the story's non-functional rule);
5. **the word is a known one** (a word list: the medicine catalogue, the test names) - when the
   line names a medicine or a test and the word list does not know it, a person looks.

A second opinion (``adjudicate.second_opinion``) may be asked for when the readers disagree. It
is recorded beside the decision and never changes it.

Every step taken is written down (:attr:`Assessment.steps`): what was checked, what it found,
and what that meant - the record of how the decision was made. The readings are never edited:
the accepted value is a new string, made from them.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from typing import Any

from cdi_adapter.recognition import disagreement, grounding
from cdi_adapter.recognition.adjudicate import SecondOpinion

ACCEPT, REVIEW = "accept", "review"


@dataclass(frozen=True)
class WordCheck:
    status: str  # known | unknown | not_applicable
    reason: str


WordList = Callable[[str], WordCheck]


@dataclass
class Assessment:
    decision: str  # accept | review
    value: str | None  # the value accepted; for a review, the one shown first to the person
    reasons: list[str] = field(default_factory=list)  # why a person must look; empty on accept
    steps: list[dict[str, Any]] = field(default_factory=list)
    comparison: disagreement.Comparison | None = None
    second_opinion: SecondOpinion | None = None

    @property
    def review_reason(self) -> str | None:
        return "; ".join(self.reasons) if self.reasons else None

    def as_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"decision": self.decision, "value": self.value,
                               "reasons": self.reasons, "steps": self.steps,
                               "algorithm_version": disagreement.ALGORITHM_VERSION}  # fmt: skip
        if self.second_opinion is not None:
            out["second_opinion"] = asdict(self.second_opinion)
        return out


def assess_fact(readings: Mapping[str, str], *, crop_png: bytes,
                ground: Callable[[bytes, str | None], grounding.Grounding] | None = None,
                word_list: WordList | None = None, again: str | None = None,
                second_opinion: Callable[[str, str], SecondOpinion] | None = None,
                ) -> Assessment:  # fmt: skip
    """Decide one handwritten line. ``readings`` is TrOCR's and Qwen's text by reader;
    ``again`` is Qwen's second reading of the crop with other padding (self-consistency), when
    one was made; ``ground`` and ``word_list`` default to the picture check alone."""
    from cdi_adapter.recognition.engines import QWEN, TROCR

    look = ground or (lambda png, value: grounding.ground(png, value))
    out = Assessment(REVIEW, None)

    def step(check: str, passed: bool | None, found: str, **more: Any) -> None:
        out.steps.append({"check": check, "passed": passed, "found": found, **more})
        if passed is False:
            out.reasons.append(found)

    # 1-2. both readers, and do they agree
    comparison = disagreement.compare_engines(readings)
    out.comparison = comparison
    said = comparison.reason if not comparison.agrees else "TrOCR and Qwen read the same"
    step("readers", comparison.agrees, said, state=comparison.state, score=comparison.score)
    candidate = comparison.value or readings.get(QWEN) or readings.get(TROCR)
    out.value = candidate

    # the second opinion, only where the readers disagree - recorded, never acted on
    if not comparison.agrees and second_opinion and TROCR in readings and QWEN in readings:
        out.second_opinion = second_opinion(readings[TROCR], readings[QWEN])
        step("second opinion", None, out.second_opinion.note, advisory=True,
             answer=out.second_opinion.answer)  # fmt: skip

    # 3. self-consistency
    if again is not None and QWEN in readings:
        same = disagreement.compare_texts(readings[QWEN], again)
        step("self-consistency", same.agrees, "Qwen read the line the same twice" if same.agrees
             else f"Qwen read the line differently the second time: {same.reason}",
             state=same.state)  # fmt: skip

    # 4. the picture: above the readers - an agreement never outweighs it
    if candidate:
        g = look(crop_png, candidate)
        step("picture", g.passes, g.reason, status=g.status,
             expected=list(g.expected), seen=list(g.seen), reader=g.reader)  # fmt: skip

    # 5. the word lists
    if candidate and word_list is not None:
        try:
            w = word_list(candidate)
        except Exception as exc:  # noqa: BLE001 - a word list that breaks cannot vouch for it
            w = WordCheck("unknown", f"the word list could not be checked ({type(exc).__name__})")
        step("word list", None if w.status == "not_applicable" else w.status == "known",
             w.reason, status=w.status)  # fmt: skip

    out.decision = ACCEPT if comparison.agrees and not out.reasons else REVIEW
    if out.decision == REVIEW and not out.reasons:  # pragma: no cover - kept for safety
        out.reasons.append("not every check passed")
    return out


def catalogue_word_list(conn: Any) -> WordList:
    """The project's own word lists: a line that names a dosage form ("Tab", "Cap", "Syp") is a
    medicine and must be in the medicine catalogue; one that binds to a test name is known; any
    other line is not a word-list matter."""
    from cdi_adapter.catalogue.normalize import dosage_form
    from cdi_adapter.catalogue.service import identify
    from cdi_adapter.terminology.service import bind_text

    def check(text: str) -> WordCheck:
        if dosage_form(text):
            ident = identify(conn, text)
            if ident.status == "matched":
                return WordCheck("known", f"in the medicine list as {ident.display_name}")
            return WordCheck("unknown", "the medicine is not in the medicine list")
        if bind_text("lab_order", text) is not None:
            return WordCheck("known", "a known test name")
        return WordCheck("not_applicable", "names no medicine or test")

    return check
