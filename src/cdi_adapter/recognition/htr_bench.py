"""The handwriting benchmark: is a second reader worth having? (backlog RD-S2)

Three set-ups on the same labelled line crops:

* **A** - Qwen alone: its reading is accepted;
* **B** - TrOCR alone: its reading is accepted;
* **C** - both, with the disagreement rule: a line is accepted only when the two readings are
  the same (:func:`same`); otherwise it goes to review.

For each: character error rate, word error rate and critical-value accuracy (the numbers on the
line - a dose, a strength, a frequency - all read right). For C also: how many wrong readings
were still accepted, and how many were caught by the disagreement.

The report (:func:`render`) names the checkpoint and says what the lines were. **A run on
synthetic lines decides nothing** about real prescription handwriting, and the report says so:
``decide`` returns no decision unless the lines were real, de-identified handwriting. The
thresholds in :class:`Rule` are an ASSUMPTION for the owner to set before a real run.

``scripts/bench_htr.py`` runs it.
"""

from __future__ import annotations

import io
import math
import random
import re
import time
from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from cdi_adapter.recognition.engines import EngineDown, LineEngine

SETUPS = {"A": "Qwen alone", "B": "TrOCR alone", "C": "both, with the disagreement rule"}


@dataclass(frozen=True)
class Line:
    """One labelled line: the crop, and what is really written on it."""

    id: str
    png: bytes
    text: str


@dataclass
class Read:
    """One line as the benchmark saw it."""

    id: str
    truth: str
    trocr: str | None = None  # None: the reader was not run, or was down for this line
    qwen: str | None = None
    trocr_seconds: float = 0.0
    qwen_seconds: float = 0.0


@dataclass(frozen=True)
class Rule:
    """The decision rule, fixed before the run. ASSUMPTION: the owner sets these numbers."""

    min_precision_gain: float = 0.02  # C's accepted-value precision (lower bound) over A's
    max_seconds_per_line: float = 1.0  # what TrOCR may add to a line
    min_lines: int = 500  # fewer real lines than this decide nothing


# ---------------------------------------------------------------- measures


def normalize(text: str | None) -> str:
    """How two readings are compared: letter case and spacing do not count."""
    return " ".join(str(text or "").lower().split())


def same(a: str | None, b: str | None) -> bool:
    """The disagreement rule of set-up C: both read something, and the same thing."""
    return bool(normalize(a)) and normalize(a) == normalize(b)


def edits(truth: Sequence[Any], read: Sequence[Any]) -> int:
    """Levenshtein distance: insertions, deletions and substitutions."""
    previous = list(range(len(read) + 1))
    for i, t in enumerate(truth, 1):
        current = [i]
        for j, r in enumerate(read, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (t != r)))
        previous = current
    return previous[-1]


def cer(pairs: Sequence[tuple[str, str]]) -> float | None:
    """Character error rate over (truth, reading) pairs: edits / characters of truth."""
    total = sum(len(normalize(t)) for t, _ in pairs)
    return None if not total else round(
        sum(edits(normalize(t), normalize(r)) for t, r in pairs) / total, 4)  # fmt: skip


def wer(pairs: Sequence[tuple[str, str]]) -> float | None:
    """Word error rate over (truth, reading) pairs."""
    total = sum(len(normalize(t).split()) for t, _ in pairs)
    if not total:
        return None
    wrong = sum(edits(normalize(t).split(), normalize(r).split()) for t, r in pairs)
    return round(wrong / total, 4)


_NUMBER = re.compile(r"\d+(?:[.,/:-]\d+)*")


def critical_values(text: str) -> Counter[str]:
    """The numbers on a line: a strength, a dose, a frequency such as 1-0-1, a date."""
    return Counter(_NUMBER.findall(str(text or "")))


def critical_accuracy(pairs: Sequence[tuple[str, str]]) -> float | None:
    """Of the lines that carry a number, the share on which every number was read right."""
    carrying = [(t, r) for t, r in pairs if critical_values(t)]
    if not carrying:
        return None
    right = sum(critical_values(t) == critical_values(r) for t, r in carrying)
    return round(right / len(carrying), 4)


def wilson_lower(right: int, total: int, z: float = 1.96) -> float | None:
    """Lower end of the 95% interval of right/total: what can be claimed from this many."""
    if not total:
        return None
    p = right / total
    centre = p + z * z / (2 * total)
    spread = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total))
    return round(max(0.0, (centre - spread) / (1 + z * z / total)), 4)


# ---------------------------------------------------------------- running


def run(lines: Sequence[Line], trocr: LineEngine | None, qwen: LineEngine | None) -> list[Read]:
    """Read every line with each reader that was given. Each is handed the crop alone."""
    out: list[Read] = []
    for line in lines:
        read = Read(line.id, line.text)
        for engine, name in ((trocr, "trocr"), (qwen, "qwen")):
            if engine is None:
                continue
            started = time.perf_counter()
            try:
                setattr(read, name, engine.read(line.png).text)
            except EngineDown:
                setattr(read, name, None)
            setattr(read, f"{name}_seconds", round(time.perf_counter() - started, 3))
        out.append(read)
    return out


def _alone(reads: Sequence[Read], which: str) -> dict[str, Any] | None:
    got = [(r.truth, getattr(r, which)) for r in reads if getattr(r, which) is not None]
    if not got:
        return None  # the reader was not run
    right = sum(normalize(t) == normalize(x) for t, x in got)
    seconds = [getattr(r, f"{which}_seconds") for r in reads if getattr(r, which) is not None]
    return {"lines": len(reads), "read": len(got), "accepted": len(got),
            "coverage": round(len(got) / len(reads), 4),
            "cer": cer(got), "wer": wer(got), "critical_value_accuracy": critical_accuracy(got),
            "accepted_right": right, "accepted_wrong": len(got) - right,
            "accepted_precision": round(right / len(got), 4),
            "accepted_precision_lower": wilson_lower(right, len(got)),
            "seconds_per_line": round(sum(seconds) / len(seconds), 3)}  # fmt: skip


def _both(reads: Sequence[Read]) -> dict[str, Any] | None:
    if not any(r.trocr is not None for r in reads) or not any(r.qwen is not None for r in reads):
        return None
    accepted = [r for r in reads if same(r.trocr, r.qwen)]
    review = [r for r in reads if not same(r.trocr, r.qwen)]
    pairs = [(r.truth, r.qwen or "") for r in accepted]
    right = sum(normalize(r.truth) == normalize(r.qwen) for r in accepted)
    # a wrong reading "caught": at least one reader was wrong, and the line went to review
    caught = sum(1 for r in review
                 if normalize(r.trocr) != normalize(r.truth)
                 or normalize(r.qwen) != normalize(r.truth))  # fmt: skip
    return {"lines": len(reads), "accepted": len(accepted), "sent_to_review": len(review),
            "coverage": round(len(accepted) / len(reads), 4) if reads else None,
            "cer": cer(pairs), "wer": wer(pairs),
            "critical_value_accuracy": critical_accuracy(pairs),
            "accepted_right": right, "accepted_wrong": len(accepted) - right,
            "accepted_precision": round(right / len(accepted), 4) if accepted else None,
            "accepted_precision_lower": wilson_lower(right, len(accepted)),
            "wrong_caught_by_disagreement": caught,
            "seconds_per_line": round(sum(r.trocr_seconds + r.qwen_seconds for r in reads)
                                      / len(reads), 3) if reads else None}  # fmt: skip


def decide(setups: dict[str, Any], *, data: str, rule: Rule) -> dict[str, Any]:
    """Keep TrOCR or not - by the rule fixed before the run, and only on real handwriting."""
    a, b, c = setups.get("A"), setups.get("B"), setups.get("C")
    if data != "real":
        return {"decision": None, "reason": "the lines were not real handwriting: this run "
                "measures the harness and the time a reader takes, nothing about accuracy on "
                "prescriptions. No decision about TrOCR is made."}  # fmt: skip
    if not (a and b and c):
        return {"decision": None, "reason": "all three set-ups must be run to decide"}
    if c["lines"] < rule.min_lines:
        return {
            "decision": None,
            "reason": f"{c['lines']} lines; the rule needs at least {rule.min_lines}",
        }
    gain = (c["accepted_precision_lower"] or 0) - (a["accepted_precision_lower"] or 0)
    cost = b["seconds_per_line"]
    keep = gain >= rule.min_precision_gain and cost <= rule.max_seconds_per_line
    return {"decision": "keep TrOCR" if keep else "remove TrOCR (plan B: Qwen alone, RD-S3)",
            "precision_gain_lower_bound": round(gain, 4), "trocr_seconds_per_line": cost,
            "reason": f"accepted-value precision (lower bound) C - A = {gain:+.4f} against a "
                      f"required {rule.min_precision_gain:+.4f}; TrOCR adds {cost} s a line "
                      f"against a limit of {rule.max_seconds_per_line} s"}  # fmt: skip


def report(reads: Sequence[Read], *, checkpoint: str, qwen_model: str, data: str, source: str,
           rule: Rule | None = None) -> dict[str, Any]:  # fmt: skip
    """Everything the run measured. ``data`` is ``real`` (de-identified handwriting) or
    ``synthetic``; ``source`` says where the lines came from."""
    rule = rule or Rule()
    setups = {"A": _alone(reads, "qwen"), "B": _alone(reads, "trocr"), "C": _both(reads)}
    return {"story": "RD-S2", "checkpoint": checkpoint, "qwen_model": qwen_model, "data": data,
            "source": source, "lines": len(reads), "setups": setups, "rule": asdict(rule),
            "rule_is": "ASSUMPTION - thresholds are for the owner to set before a real run",
            **{"verdict": decide(setups, data=data, rule=rule)}}  # fmt: skip


def _cell(v: Any) -> str:
    return "not run" if v is None else f"{v:.4f}" if isinstance(v, float) else str(v)


def render(rep: dict[str, Any]) -> str:
    """The report a person reads: the checkpoint, what the lines were, the three error rates."""
    rows = [("Lines read", "read"), ("Accepted", "accepted"), ("Coverage", "coverage"),
            ("CER", "cer"), ("WER", "wer"), ("Critical-value accuracy", "critical_value_accuracy"),
            ("Accepted and wrong", "accepted_wrong"),
            ("Accepted-value precision", "accepted_precision"),
            ("  lower bound (95%)", "accepted_precision_lower"),
            ("Wrong, caught by disagreement", "wrong_caught_by_disagreement"),
            ("Seconds per line", "seconds_per_line")]  # fmt: skip
    s = rep["setups"]
    out = ["# Handwriting benchmark (RD-S2)", "",
           f"- **TrOCR checkpoint:** `{rep['checkpoint']}`",
           f"- **Qwen model:** `{rep['qwen_model']}`",
           f"- **Lines:** {rep['lines']} ({rep['data']}) - {rep['source']}", ""]  # fmt: skip
    if rep["data"] != "real":
        out += ["> These lines are NOT real handwriting. The numbers below say nothing about "
                "accuracy on prescription handwriting, which remains unmeasured.", ""]  # fmt: skip
    out += ["| | A: Qwen alone | B: TrOCR alone | C: both |", "|---|---|---|---|"]
    for label, key in rows:
        cells = []
        for name in "ABC":
            setup = s.get(name)
            cells.append("not run" if setup is None else
                         "-" if key not in setup else _cell(setup[key]))  # fmt: skip
        out.append(f"| {label} | {' | '.join(cells)} |")
    v = rep["verdict"]
    out += ["", f"**Decision:** {v['decision'] or 'none made'}. {v['reason']}", "",
            f"Rule ({rep['rule_is']}): {rep['rule']}", ""]  # fmt: skip
    return "\n".join(out)


# ---------------------------------------------------------------- synthetic lines

_DRUGS = ("Paracetamol", "Amoxicillin", "Pantoprazole", "Cetirizine", "Metformin", "Atorvastatin",
          "Azithromycin", "Domperidone", "Rabeprazole", "Telmisartan")  # fmt: skip
_FORMS = ("Tab", "Cap", "Syp", "Inj")
_FREQ = ("1-0-1", "1-1-1", "0-0-1", "1-0-0")


@dataclass
class _Maker:
    rng: random.Random
    fonts: list[Any] = field(default_factory=list)


def synthetic_lines(n: int, *, seed: int = 7) -> list[Line]:
    """``n`` typeset prescription-like lines with jitter and noise. Fictional content; no real
    handwriting and no real patient. Good for timing readers and proving the harness - not for
    judging them."""
    from PIL import Image, ImageDraw, ImageFilter, ImageFont

    rng = random.Random(seed)
    out: list[Line] = []
    for i in range(n):
        strength, days = rng.choice((5, 10, 20, 40, 250, 500)), rng.choice((3, 5, 7, 10))
        text = (f"{rng.choice(_FORMS)} {rng.choice(_DRUGS)} {strength} mg "
                f"{rng.choice(_FREQ)} x {days} days")  # fmt: skip
        font = ImageFont.load_default(size=rng.randint(26, 34))
        width = int(font.getlength(text)) + 40
        im = Image.new("L", (width, 64), 255)
        ImageDraw.Draw(im).text((20 + rng.randint(-6, 6), 12 + rng.randint(-5, 5)), text, fill=20,
                                font=font)  # fmt: skip
        im = im.rotate(rng.uniform(-1.5, 1.5), fillcolor=255, resample=Image.Resampling.BICUBIC)
        im = im.filter(ImageFilter.GaussianBlur(rng.uniform(0.0, 0.8)))
        # noise from the seeded generator, so the same seed gives the same pictures
        noise = Image.frombytes("L", im.size, rng.randbytes(im.size[0] * im.size[1]))
        im = Image.blend(im, noise, 0.08)
        buf = io.BytesIO()
        im.convert("RGB").save(buf, format="PNG")
        out.append(Line(f"synthetic-{i:04d}", buf.getvalue(), text))
    return out
