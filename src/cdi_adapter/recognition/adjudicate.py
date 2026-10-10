"""A second opinion from Qwen - advisory only (backlog RD-S3).

When the readers disagree, Qwen may be shown the crop and both readings and asked which one the
picture shows. Its answer is **recorded for the reviewer and never decides**: a disagreement
goes to a person whatever it says, and an agreement is not overturned by it either. A model
sounding sure is not evidence (ARCHITECTURE §15.3).

Unlike the reader (``engines.QwenLineEngine``), this prompt does carry the readings - that is
its job - so it is never used to *read* a line, and its answer never becomes a reading.

It can fail in any way - the model server away, a timeout, nonsense back - and nothing breaks:
:func:`second_opinion` then returns an opinion that says it failed, and the normal checks run
as they would have without it.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from cdi_adapter.logging import get_logger
from cdi_adapter.ml.client import MLClient

log = get_logger(__name__)

ADJUDICATE_PROMPT = (
    "This picture is one line of writing cut from a scanned clinical page. Two people read it "
    "differently.\nA: {a}\nB: {b}\nWhich one is exactly what is written in the picture? Answer "
    "with one word: A, B, or NEITHER."
)
ANSWERS = ("A", "B", "NEITHER")


@dataclass(frozen=True)
class SecondOpinion:
    answer: str | None  # A | B | NEITHER, or None when it failed or answered something else
    note: str
    model: str = ""
    prompt_hash: str = ""
    advisory: bool = True  # always: it is recorded, never acted on


def second_opinion(client: MLClient | None, crop_png: bytes, a: str, b: str) -> SecondOpinion:
    """Ask, record, never raise."""
    if client is None:
        return SecondOpinion(None, "no second opinion was asked for")
    prompt = ADJUDICATE_PROMPT.format(a=a, b=b)
    digest = hashlib.sha256(ADJUDICATE_PROMPT.encode()).hexdigest()
    model = str(getattr(client, "model", ""))
    try:
        said = str(client.generate(prompt, crop_png, max_tokens=4) or "")
    except Exception as exc:  # noqa: BLE001 - see the module note
        log.warning("second_opinion_failed", error=type(exc).__name__)
        return SecondOpinion(None, f"the second opinion failed ({type(exc).__name__})", model,
                             digest)  # fmt: skip
    word = re.sub(r"[^A-Z]", "", said.strip().upper().split()[0]) if said.strip() else ""
    if word not in ANSWERS:
        return SecondOpinion(None, "the second opinion gave no usable answer", model, digest)
    return SecondOpinion(word, f"the second opinion picked {word} (advisory: it decides nothing)",
                         model, digest)  # fmt: skip
