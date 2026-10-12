"""Payload -> fact drafts (ARCHITECTURE §5 S4 "Payload -> facts"). Pure functions, no I/O.

Per-``doc_type`` handlers (``_facts_prescription``, ``_facts_lab``, ``_facts_vitals``,
``_facts_discharge``, ``_facts_radiology``, ``_facts_opd_note``) built on tolerant readers:

* ``_qty(x)`` accepts a ``{value, unit, evidence}`` object, a bare number, or a string such as
  ``"500 mg"`` / ``"138/86"``.
* ``_coded_text(x)`` accepts ``{text|code|display|name}`` or a plain string.
* Vitals are de-duplicated within a document and only emitted when a numeric value was actually
  read; ``_VITAL_ALIAS`` normalises pulse -> heart_rate etc. Blood pressure is one fact
  ("138/86"); its LOINC 8480-6 / 8462-4 codes are the FHIR components.
* Medication detail: strength parsed out of the drug name (``METPURE XL 50 MG`` -> ``Metpure XL``
  / 50 / mg, preferring a dose unit over a pen's fill volume); frequency from the dose-slot column
  (``1-0-1`` -> 2/day, ``BD`` -> 2/day, ``TWICE IN A YEAR`` -> 0.006/day) with ``x 15 days`` read
  as a *duration*; SC route inferred for pens / insulin injections.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from cdi_adapter.catalogue.normalize import DOSAGE_FORMS, LEADING_FORMS
from cdi_adapter.terminology.parse import duration_days, freq_per_day, is_prn, to_ucum

NUM = re.compile(r"^\s*([-+]?\d+(?:\.\d+)?)\s*([^\d\s].*)?$")
_RANGE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(?:-|–|to)\s*(\d+(?:\.\d+)?)\s*$")
_BP = re.compile(r"^\s*(\d{2,3})\s*/\s*(\d{2,3})")

_VITAL_ALIAS = {
    "pulse": "heart_rate", "pr": "heart_rate", "heart rate": "heart_rate", "hr": "heart_rate",
    "heart_rate": "heart_rate", "pulse rate": "heart_rate",
    "bp": "blood_pressure", "blood pressure": "blood_pressure", "blood_pressure": "blood_pressure",
    "temp": "temperature", "temperature": "temperature",
    "spo2": "spo2", "sp02": "spo2", "oxygen saturation": "spo2", "o2 sat": "spo2",
    "rr": "respiratory_rate", "respiratory rate": "respiratory_rate",
    "resp rate": "respiratory_rate",
    "respiratory_rate": "respiratory_rate",
    "wt": "body_weight", "weight": "body_weight", "body weight": "body_weight",
    "body_weight": "body_weight",
    "ht": "body_height", "height": "body_height", "body_height": "body_height",
    "bmi": "bmi", "body mass index": "bmi",
    "rbs": "blood_glucose", "grbs": "blood_glucose", "blood sugar": "blood_glucose",
}  # fmt: skip
_VITAL_TEXT = {
    "heart_rate": "heart rate", "temperature": "body temperature", "spo2": "oxygen saturation",
    "respiratory_rate": "respiratory rate", "body_weight": "body weight",
    "body_height": "body height", "bmi": "body mass index", "blood_glucose": "blood glucose",
}  # fmt: skip

# dosage-form words: the one shared table (catalogue/normalize.py), catalogue form names;
# "T" / "C" / "Cr" are a form only as the first word of a line ("T Dytor", not "Vitamin C")
_FORMS = DOSAGE_FORMS
_LEAD_FORMS = {**DOSAGE_FORMS, **LEADING_FORMS}
_STRENGTH = re.compile(r"(\d+(?:\.\d+)?)\s*(mg|mcg|µg|g|gm|iu|units?|ml)(?:\s*/\s*ml)?\b", re.I)
# where the drug name ends and the schedule begins
_SCHEDULE = re.compile(
    r"\s+(?:\d(?:\.\d)?-\d(?:\.\d)?-\d(?:\.\d)?|od|bd|bid|tds|tid|qid|hs|sos|stat|x\s*\d|"
    r"for\s+\d|after\b|before\b|once\b|twice\b|thrice\b|daily\b)",
    re.I,
)
_DURATION_IN_TEXT = re.compile(r"(?:x|for)\s*\d+\s*\w+", re.I)
# when to take it, not how it enters the body: a model that writes "after food" / "empty stomach"
# as the route has put an instruction in the wrong field (2026-09-25 check); it moves to
# instructions unless the text also names a real route
_WHEN_TO_TAKE = re.compile(r"\b(food|meals?|breakfast|lunch|dinner|supper|stomach|bed ?time|"
                           r"night|morning|evening|milk|water)\b", re.I)  # fmt: skip
_A_ROUTE = re.compile(r"\b(oral|orally|p\.?o\.?|i\.?v\.?|i\.?m\.?|s\.?c\.?|subcut\w*|topical|"
                      r"local|inhal\w*|nebul\w*|nasal|nostrils?|sublingual|rectal|vaginal|"
                      r"transdermal|eye|ear)\b", re.I)  # fmt: skip
_DOSE_UNITS = ("mg", "mcg", "µg", "g", "gm", "iu", "unit", "units")


@dataclass
class MedDraft:
    drug_text: str
    form: str | None = None
    strength_num: float | None = None
    strength_unit: str | None = None
    dose_num: float | None = None
    dose_unit_ucum: str | None = None
    route: str | None = None
    frequency_code: str | None = None
    frequency_per_day: float | None = None
    duration_days: float | None = None
    prn: bool = False
    instructions: str | None = None
    intent: str = "order"


@dataclass
class FactDraft:
    fact_type: str
    local_text: str
    confidence_extract: float
    evidence: list[str] = field(default_factory=list)
    value_kind: str | None = None
    value_num: float | None = None
    value_unit_ucum: str | None = None
    value_text: str | None = None
    value_low_num: float | None = None
    value_high_num: float | None = None
    ref_range_low: float | None = None
    ref_range_high: float | None = None
    ref_range_text: str | None = None
    abnormal_flag: str | None = None
    clinical_status: str | None = None
    medication: MedDraft | None = None


# ------------------------------------------------------------------ tolerant readers


def _num(s: Any) -> float | None:
    try:
        return float(str(s).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _qty(x: Any) -> tuple[float | None, str | None, str | None]:
    """-> (value_num, unit, value_text). Non-numeric strings come back as text."""
    unit = None
    if isinstance(x, dict):
        unit = x.get("unit")
        x = x.get("value")
    if x is None or isinstance(x, bool):
        return None, unit, None
    if isinstance(x, int | float):
        return float(x), unit, None
    s = str(x).strip()
    if not s:
        return None, unit, None
    if _BP.match(s):
        return None, unit, s
    if m := NUM.match(s):
        tail = (m.group(2) or "").strip() or None
        return float(m.group(1)), unit or tail, None
    return None, unit, s


def _coded_text(x: Any) -> str | None:
    if isinstance(x, dict):
        for k in ("text", "display", "name", "code"):
            if isinstance(x.get(k), str) and x[k].strip():
                return x[k].strip()
        return None
    if isinstance(x, str) and x.strip():
        return x.strip()
    return None


def _ev(x: Any) -> list[str]:
    ev = x.get("evidence") if isinstance(x, dict) else None
    return [str(e) for e in ev] if isinstance(ev, list) else []


def _conf(x: Any, default: float) -> float:
    c = x.get("confidence") if isinstance(x, dict) else None
    return float(c) if isinstance(c, int | float) and not isinstance(c, bool) else default


def _items(payload: dict[str, Any], key: str) -> list[Any]:
    v = payload.get(key)
    return v if isinstance(v, list) else ([] if v is None else [v])


# ------------------------------------------------------------------ builders


_JOINED = re.compile(r"\s*(?:[,;+/]|\band\b)\s*", re.I)


def split_joined(fact_type: str, t: str) -> list[str]:
    """One written line naming several diagnoses ("Type 2 diabetes mellitus, Hypertension") ->
    one text per diagnosis, but only when the whole line codes to nothing and EVERY part codes
    on its own (so "Diabetes mellitus, type 2" or "Fracture, left radius" stay one text). One
    uncoded Condition for two diseases was what the download carried (2026-09-25)."""
    from cdi_adapter.terminology.service import bind_text  # the seed lookup only, no I/O

    parts = [p for p in _JOINED.split(t) if p]
    if len(parts) < 2 or bind_text(fact_type, t) is not None:
        return [t]
    return parts if all(bind_text(fact_type, p) for p in parts) else [t]


def _coded_facts(payload, key: str, fact_type: str, default: float) -> list[FactDraft]:
    out = []
    for item in _items(payload, key):
        t = _coded_text(item)
        for part in split_joined(fact_type, t) if t and fact_type == "condition" else [t]:
            if part:
                out.append(FactDraft(fact_type, part, _conf(item, default), _ev(item),
                                     value_kind=None))  # fmt: skip
    return out


_ORDER_SPLIT = re.compile(r"\s*(?:[,;+]|\band\b)\s*", re.I)


def split_orders(text: str | None) -> list[str]:
    """One advised test per item: "CBC, LFT, RFT" / "T3 + T4 + TSH" -> each test. A slash is
    part of a test's name ("Urine R/M", "Urine C/S"), so it never splits."""
    return [t.strip(" .:-") for t in _ORDER_SPLIT.split(text or "") if t.strip(" .:-")]


def _order_facts(payload, default: float) -> list[FactDraft]:
    """The tests a prescription advises (owner request 2026-09-28): ``lab_order`` facts, as
    written, one per test, with the evidence of the line they were read from."""
    out = []
    for item in _items(payload, "investigations_advised"):
        for part in split_orders(_coded_text(item)):
            out.append(FactDraft("lab_order", part, _conf(item, default), _ev(item),
                                 value_kind=None))  # fmt: skip
    return out


def _vital_facts(payload, default: float, seen: set[str] | None = None) -> list[FactDraft]:
    seen = set() if seen is None else seen
    out: list[FactDraft] = []
    for item in _items(payload, "vitals"):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip().lower().replace("_", " ")
        kind = _VITAL_ALIAS.get(name) or _VITAL_ALIAS.get(name.replace(" ", "_"))
        if kind is None:
            continue
        num, unit, txt = _qty(item.get("value"))
        if kind == "heart_rate" and _BP.match(txt or ""):
            kind = "blood_pressure"  # "110/70 mmHg" written under the wrong label: two numbers
        unit = unit or item.get("unit")
        ev, conf = _ev(item) or _ev(item.get("value")), _conf(item, default)
        if kind == "blood_pressure":
            # one BP fact ("138/86"); FHIR carries systolic / diastolic as its two components
            m = _BP.match(txt or "")
            if not m or kind in seen:
                continue
            seen.add(kind)
            out.append(FactDraft("vital_sign", "blood pressure", conf, ev, value_kind="text",
                                 value_text=f"{m.group(1)}/{m.group(2)}",
                                 value_unit_ucum="mm[Hg]"))  # fmt: skip
            continue
        if num is None or kind in seen:  # only emitted when a number was actually read
            continue
        # a height in kilograms (or a weight in centimetres) is the neighbouring line's value
        # copied into an empty one: not a reading of this vital
        wrong = {"body_height": ("kg", "kgs", "g"), "body_weight": ("cm", "cms", "m", "ft")}
        if str(unit or "").strip().lower() in wrong.get(kind, ()):
            continue
        seen.add(kind)
        out.append(FactDraft("vital_sign", _VITAL_TEXT.get(kind, kind), conf, ev,
                             value_kind="quantity", value_num=num,
                             value_unit_ucum=to_ucum(unit) or unit))  # fmt: skip
    return out


def _lab_facts(payload, key: str, default: float) -> list[FactDraft]:
    out = []
    for item in _items(payload, key):
        if not isinstance(item, dict) or not _coded_text(item.get("name")):
            continue
        num, unit, txt = _qty(item.get("value"))
        unit = unit or item.get("unit")
        f = FactDraft("lab_result", _coded_text(item.get("name")) or "", _conf(item, default),
                      _ev(item) or _ev(item.get("value")))  # fmt: skip
        if num is not None:
            f.value_kind, f.value_num, f.value_unit_ucum = "quantity", num, to_ucum(unit) or unit
        elif txt:
            f.value_kind, f.value_text = "text", txt
        else:
            continue
        rr = item.get("ref_range")
        if isinstance(rr, str) and rr.strip():
            if m := _RANGE.match(rr):
                f.ref_range_low, f.ref_range_high = float(m.group(1)), float(m.group(2))
            f.ref_range_text = rr.strip()
        flag = str(item.get("flag") or "").strip().upper()[:1]
        if flag in ("H", "L"):
            f.abnormal_flag = flag
        out.append(f)
    return out


def _allergy_facts(payload, default: float) -> list[FactDraft]:
    out = []
    for item in _items(payload, "allergies"):
        sub = item.get("substance") if isinstance(item, dict) else item
        if isinstance(sub, str) and sub.strip():
            f = FactDraft("allergy", sub.strip(), _conf(item, default), _ev(item))
            if isinstance(item, dict) and item.get("reaction"):
                f.value_kind, f.value_text = "text", str(item["reaction"])
            out.append(f)
    return out


def _procedure_facts(payload, key: str, default: float) -> list[FactDraft]:
    out = []
    for item in _items(payload, key):
        name = item.get("name") if isinstance(item, dict) else item
        if isinstance(name, str) and name.strip():
            f = FactDraft("procedure", name.strip(), _conf(item, default), _ev(item))
            if isinstance(item, dict) and item.get("findings"):
                f.value_kind, f.value_text = "text", str(item["findings"])
            out.append(f)
    return out


def reslot_name(item: dict[str, Any]) -> dict[str, Any]:
    """The 7B sometimes writes only the dosage form as ``drug_name`` and the medicine into
    ``strength`` ("CAP" + "LOPACA CV 200 MG", owner's cardiology OPD sheet, 2026-09-28): the name
    is put back together, so the catalogue and the medication agent get a medicine to look up.
    Nothing is invented: the two written values are joined and the strength parsed from them."""
    name = str(item.get("drug_name") or "").strip()
    strength = str(item.get("strength") or "").strip()
    if name.lower().rstrip(".") in _LEAD_FORMS and re.search(
        r"[A-Za-z]{3,}", _STRENGTH.sub("", strength)
    ):
        return {**item, "drug_name": f"{name} {strength}", "strength": ""}
    return item


def parse_medication(item: dict[str, Any]) -> MedDraft:
    raw_name = str(item.get("drug_name") or "").strip()
    tokens = raw_name.split()
    form = None
    if tokens and tokens[0].lower().rstrip(".") in _LEAD_FORMS:
        form = _LEAD_FORMS[tokens[0].lower().rstrip(".")]
        tokens = tokens[1:]
    elif len(tokens) > 1 and tokens[0].lower().rstrip(".:") in _MISREAD_FORMS:
        # "Ing Rejunex": "Inj" as the reader spelt it. No medicine's name begins with it
        form = _MISREAD_FORMS[tokens[0].lower().rstrip(".:")]
        tokens = tokens[1:]
    elif len(tokens) > 1 and _UNREAD_FORM.match(tokens[0]) and (
        tokens[0].lower() not in _NOT_A_FORM
    ):
        # "Rel. Zincoilt": a form word the reader got wrong ("Tab."). It is no part of the
        # medicine's name; the line as written stays in the fact's local_text
        tokens = tokens[1:]
    name = _SCHEDULE.split(" " + " ".join(tokens), maxsplit=1)[0].strip()
    # a form word later in the name ("... PEN", "... PFS") describes the device, not the drug
    kept: list[str] = []
    for w in name.split():
        later = _FORMS.get(w.lower().rstrip(".")) or _DEVICE_FORMS.get(w.lower().rstrip("."))
        if later and kept:
            form = form or later
            continue
        kept.append(w)
    name = " ".join(kept)

    strength = item.get("strength")
    if isinstance(strength, str):
        strength = strength.strip(" ()[]")  # "(25)", "(5mg)": brackets are how it is written
    s_num, s_unit, _ = _qty(strength)
    if s_num == 0:
        s_num = s_unit = None  # "00": no medicine has a strength of nothing
    found = [(float(n), u.lower()) for n, u in _STRENGTH.findall(name)]
    if s_num is None and found:
        dose_like = [f for f in found if f[1] in _DOSE_UNITS]
        s_num, s_unit = (dose_like or found)[0]  # a dose unit beats a pen's fill volume (ml)
    name = _STRENGTH.sub("", name)
    name = re.sub(r"\s+", " ", name).strip(" -,/")
    drug = " ".join(w if (w.isupper() and len(w) <= 3) or not w.isalpha() else w.title()
                    for w in name.split())  # fmt: skip

    text_all = " ".join(str(item.get(k) or "") for k in ("frequency", "instructions", "duration"))
    text_all = f"{text_all} {raw_name}"
    freq_text = item.get("frequency") or None
    fpd = freq_per_day(freq_text) if freq_text else freq_per_day(text_all)
    if fpd is None and (times := _N_X_M.match(str(freq_text or ""))):
        fpd = float(times.group(2))  # "1x1": one, once a day; "1x2": one, twice a day
    route = item.get("route")
    if form == "injection" and isinstance(route, str) and _ORAL.match(route):
        route = None  # an injection is not taken by mouth: a usual value the page does not give
    instructions = item.get("instructions") or None
    if isinstance(route, str) and route.strip() and not _A_ROUTE.search(route):
        # "BBF", "before lunch", "(after dinner)": when to take it, not how it enters the body.
        # It is kept, as written, with the instructions
        said = route.strip(" ()/")
        if said and said.lower() not in str(instructions or "").lower():
            instructions = "; ".join(x for x in (instructions, said) if x)
        route = None
    route = route.strip(" /()") or None if isinstance(route, str) else route
    if not route and (form in ("injection", "pen") or re.search(r"\b(pen|insulin)\b", raw_name,
                                                                re.I)):  # fmt: skip
        route = (
            "SC"
            if re.search(r"\b(pen|insulin|s\.?c\.?)\b", raw_name + text_all, re.I)
            else ("injection")
        )
    d_num, d_unit, _ = _qty(item.get("dose"))
    return MedDraft(
        drug_text=drug or raw_name,
        form=form,
        strength_num=s_num,
        strength_unit=(s_unit or None) and str(s_unit).lower(),
        dose_num=d_num,
        dose_unit_ucum=to_ucum(d_unit) or d_unit,
        route=route,
        frequency_code=str(freq_text) if freq_text else None,
        frequency_per_day=fpd,
        duration_days=duration_days(item.get("duration")) or (
            duration_days(dm.group(0)) if (dm := _DURATION_IN_TEXT.search(text_all)) else None
        ),
        prn=is_prn(text_all),
        instructions=instructions,
    )  # fmt: skip


# the dose written after the name ("Cap. Sonpraz 1 cap", "Tab. Montina fx 1 tab at bedtime"):
# where the name ends. "ml" is left out - a pen's fill volume belongs to its name.
_DOSE_IN_NAME = re.compile(
    r"\s+((?:\d+(?:\.\d+)?|\d\s*/\s*\d|½|half|one|two)\s*"
    r"(?:tab|tabs|tablet|tablets|cap|caps|capsule|capsules|tsp|tbsp|puffs?|drops?|sachets?))\b"
    r"\.?(.*)$",
    re.I,
)
_LIST_MARK = re.compile(r"^[\s=\-–•*·>]+|^\s*\d{1,2}\s*[.)]\s+")
_UNREAD_FORM = re.compile(r"^[A-Za-z]{2,4}\.$")
_NOT_A_FORM = {"vit.", "dr.", "co.", "st.", "mr.", "no.", "sr.", "ds.", "xr.", "er.", "cr."}
# "Inj" as a reader spells it at the head of a line, and a device word after the name
_MISREAD_FORMS = {"ing": "injection", "lnj": "injection", "1nj": "injection", "inj": "injection"}
_DEVICE_FORMS = {"pfs": "injection"}  # pre-filled syringe
_ORAL = re.compile(r"^\s*(?:p\.?o\.?|oral(?:ly)?|by mouth)\s*$", re.I)
# "1x1", "1 x 2", "½x3": how many, times a day
_N_X_M = re.compile(r"^\s*(\d+(?:\.\d+)?|½)\s*[x×*]\s*(\d)\s*$", re.I)
_SLOTS = re.compile(r"^\s*\d(?:\.\d)?\s*-\s*\d(?:\.\d)?\s*-\s*\d(?:\.\d)?(?:\s*-\s*\d)?\s*$")
_DOSE_FIRST = re.compile(
    r"^\s*((?:\d+(?:\.\d+)?|½)\s*(?:tabs?|tablets?|caps?|capsules?|tsp|puffs?|drops?))\b\.?\s+(?=\S)",
    re.I,
)
_ARROW = re.compile(r"^[\s\-–—=>→~]+")
_LEAD_DURATION = re.compile(
    r"^(?:x|for)?\s*\d+(?:\s*-\s*\d+)?\s*(?:d|days?|wks?|weeks?|mths?|months?)\b", re.I
)


def tidy_medication(item: dict[str, Any]) -> dict[str, Any]:
    """One medicine line with each written part in its own field (owner, 2026-10-10: names came
    out as "Sonpraz 1" and "Audeflu (#5) 1", with "5 days" as the frequency).

    * ``drug_name`` ends where the dose begins: "Cap. Sonpraz 1 cap" -> "Cap. Sonpraz", and the
      "1 cap" becomes the dose when the dose field is empty;
    * a frequency that is only a number of days is the duration; a duration or frequency that
      only says when to take it ("after meals", "after lunch") is an instruction;
    * ``instructions`` keeps the whole direction as written ("1 tab at bedtime x 5 days") when
      the model gave none.

    Nothing is invented: every value was written by the model for this line, and the line's
    OCR text stays its evidence."""
    item = dict(item)
    name = _LIST_MARK.sub("", str(item.get("drug_name") or "")).strip()
    tail = ""
    if m := _DOSE_IN_NAME.search(name):
        name, tail = name[: m.start()].strip(), m.group(2).strip(" ,;")
        if not str(item.get("dose") or "").strip():
            item["dose"] = m.group(1).strip()
    item["drug_name"] = name
    freq, dur = (str(item.get(k) or "").strip() for k in ("frequency", "duration"))
    when: list[str] = []
    dose_said = str(item.get("dose") or "").strip()
    if _A_ROUTE.fullmatch(freq) and not str(item.get("route") or "").strip():
        item["route"], freq = freq, ""  # "SC" written where the frequency goes
    if _SLOTS.match(dose_said) and freq_per_day(freq) is None:
        # "1-0-0" given as the dose: it is the day's pattern (morning-noon-night). What stood in
        # the frequency is the days ("X 30 days"), or is kept as written ("X Day 1")
        if not freq or _LEAD_DURATION.match(_ARROW.sub("", freq)):
            dur = dur or _ARROW.sub("", freq)
        else:
            when.append(freq)
        freq, item["dose"] = dose_said, ""
    elif not dose_said and (lead := _DOSE_FIRST.match(freq)):
        # "1 tab OD": the dose written in front of the frequency
        item["dose"], freq = lead.group(1).strip(), freq[lead.end() :].strip()
    if dur and duration_days(dur) is None and _WHEN_TO_TAKE.search(dur):
        when.append(dur.strip("() "))
        dur = ""
    extra = ""
    # "-> 30d", "- 30d Night", "5 days": the days are the duration, a word after them says when
    if freq and (lead := _LEAD_DURATION.match(_ARROW.sub("", freq))):
        rest = _ARROW.sub("", freq)[lead.end() :].strip(" ,;")
        if not dur:
            dur = lead.group(0).strip()
        freq = ""
        if rest and _WHEN_TO_TAKE.search(rest):
            when.insert(0, rest.strip("() "))
        else:
            extra = rest  # kept as written, in the instructions
    if freq and freq_per_day(freq) is None and _WHEN_TO_TAKE.search(freq) and not is_prn(freq):
        when.insert(0, freq.strip("() "))
        freq = ""
    dose = str(item.get("dose") or "").strip()
    if not freq and (times := _N_X_M.match(dose)):
        item["dose"], freq = times.group(1), dose  # "1x1": the dose is 1, taken once a day
    item["frequency"], item["duration"] = freq, dur
    if not str(item.get("instructions") or "").strip():
        days = f"x {dur}" if dur[:1].isdigit() else dur
        written = [dose, "" if freq == dose else freq, *when, days, tail, extra]
        seen: list[str] = []
        for part in written:  # the model often repeats the dose's tail in its own fields
            if part and not any(part.lower() in s.lower() for s in seen):
                seen.append(part)
        item["instructions"] = " ".join(seen) if len(seen) > 1 or when or tail else ""
    return item


# a line that only carries on the one above it: it starts with an amount, or is only a date
_CARRIES_ON = re.compile(
    r"^\s*\d+(?:\.\d+)?\s*(?:ml|mg|mcg|g|iu|units?|tabs?|caps?|tsp|puffs?|drops?)\b", re.I
)
_ONLY_A_DATE = re.compile(
    r"^\s*(?:\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}|(?:\d{1,2}\s+)?(?:jan|feb|mar|apr|may|jun|jul|aug|"
    r"sep|sept|oct|nov|dec)[a-z]*\.?,?\s+\d{2,4}|(?:19|20)\d{2})\s*$",
    re.I,
)
_LEAD_NUMBER = re.compile(r"^\s*(\d{1,2})\s+(?=[A-Za-z])")
LISTS = ("advice", "investigations_advised")


def _joined(prev: Any, item: Any) -> dict[str, Any]:
    """``item`` added to the end of ``prev``: one written entry, with the evidence of both lines
    and the lower of their confidences."""
    base = dict(prev) if isinstance(prev, dict) else {"text": str(prev)}
    key = next((k for k in ("text", "display", "name") if isinstance(base.get(k), str)), "text")
    base[key] = f"{_coded_text(prev)} {_coded_text(item)}"
    base["evidence"] = [*_ev(prev), *(e for e in _ev(item) if e not in _ev(prev))]
    confs = [x["confidence"] for x in (prev, item)
             if isinstance(x, dict) and isinstance(x.get("confidence"), int | float)]  # fmt: skip
    if confs:
        base["confidence"] = min(confs)
    return base


def tidy_lists(payload: dict[str, Any]) -> dict[str, Any]:
    """The written entries of a list, one item each (owner, 2026-10-10: two vaccines came out as
    four lines, and "Bladder Training" as "17 Bladder Training").

    * A line that starts with an amount ("0.5 ml IM Deltoid every year") or is only a date
      ("June 2023") carries on the line above it, and is joined to it.
    * The same number in front of two or more entries of one list ("17 Bladder Training",
      "17 Kegel exercise") is the list's mark - an arrow or bullet read as digits - and is taken
      off: a numbered list does not repeat a number. A number seen once is left alone.

    The OCR lines stay the evidence, so the reviewer still sees what was read."""
    out = payload
    for key in LISTS:
        items = _items(payload, key)
        if len(items) < 2:
            continue
        texts = [_coded_text(i) or "" for i in items]
        leads = [m.group(1) if (m := _LEAD_NUMBER.match(t)) else None for t in texts]
        marks = {n for n in leads if n and leads.count(n) > 1}
        tidy: list[Any] = []
        for item, t, lead in zip(items, texts, leads, strict=True):
            if lead in marks:
                clean = _LEAD_NUMBER.sub("", t)
                item = {**item, "text": clean} if isinstance(item, dict) else clean
                t = clean
            if tidy and t and (_CARRIES_ON.match(t) or _ONLY_A_DATE.match(t)):
                tidy[-1] = _joined(tidy[-1], item)
            else:
                tidy.append(item)
        if tidy != items:
            out = {**out, key: tidy}
    return out


# an advice line that is a medicine: it starts with a dosage form and a name
_MED_LINE = re.compile(
    r"^[\s=\-–•*·>]*(?:tab|tabs|tablet|cap|caps|capsule|syp|syr|syrup|inj|injection|susp|oint)"
    r"\b\.?\s+(?!(?:site|sites|to|at|in|on|of|the|and|should|must|is|are|if|for|with|after|"
    r"before|form)\b)[A-Za-z]",
    re.I,
)
_FOLLOW_UP = re.compile(
    r"\b(follow[\s-]?up|f/u|review|r/w|re-?visit|re-?check|come back|next visit|report back|"
    r"with (?:the )?reports?)\b",
    re.I,
)


def advice_kind(text: str | None) -> str:
    """``follow_up`` for a line about coming back ("Review after 5 days", "R/w with reports"),
    ``advice`` for home care (rest, gargle, fluids). Both are stored as advice facts; this is how
    a page tells them apart."""
    return "follow_up" if _FOLLOW_UP.search(text or "") else "advice"


def _name_words(text: str) -> list[str]:
    words = re.findall(r"[a-z]{3,}", text.lower())
    return [w for w in words if w not in _LEAD_FORMS]


def split_advice(payload: dict[str, Any]) -> dict[str, Any]:
    """Medicines out of ``advice`` (owner, 2026-10-10: every medicine of a handwritten
    prescription was also listed under Advice & follow-up).

    The model repeats a medicine line under advice when the Rx section mixes home care and
    drugs. An advice line that starts with a dosage form and a name is a medicine: it is dropped
    when ``medications`` already has that line (same evidence, or the same name), and otherwise
    moved there at a low confidence so a reviewer sees it - never lost, never left as advice."""
    advice = _items(payload, "advice")
    if not advice:
        return payload
    meds = [m for m in _items(payload, "medications")
            if isinstance(m, dict) and str(m.get("drug_name") or "").strip()]  # fmt: skip
    kept: list[Any] = []
    moved: list[dict[str, Any]] = []
    for a in advice:
        t = _coded_text(a) or ""
        words = set(_name_words(t))
        twin = any(
            (first := _name_words(str(m["drug_name"]))[:1]) and first[0] in words
            and (_MED_LINE.match(t) or set(_ev(a)) & set(_ev(m)))
            for m in meds
        )  # fmt: skip
        if twin:
            continue
        if _MED_LINE.match(t):
            moved.append({"drug_name": _LIST_MARK.sub("", t).strip(), "evidence": _ev(a),
                          "confidence": min(_conf(a, 0.5), 0.5)})  # fmt: skip
            continue
        kept.append(a)
    if len(kept) == len(advice):
        return payload
    return {**payload, "advice": kept, "medications": [*_items(payload, "medications"), *moved]}


_TEST_SPLIT = re.compile(r"\s*(?:[/,+&\[\]()]|\band\b)\s*", re.I)
_TEST_CUE = re.compile(r"\b(tests?|check|repeat|investigat\w*|ix|reports?|do|get|advised)\b", re.I)


_INTAKE = re.compile(r"\b(take|taken|eat|drink|avoid|diet|rich|supplements?|tab|tabs|tablets?|"
                     r"cap|caps|capsules?|syp|syrup)\b", re.I)  # fmt: skip


def _a_test(text: str) -> str | None:
    """The test ``text`` names, as written: the whole of it, or the words of it that are a test
    ("To review after 2wks of HbA1c" -> "HbA1c"). None when it names none."""
    from cdi_adapter.terminology.service import bind_text  # the seed lookup only, no I/O

    words = text.split()
    for n in range(min(len(words), 3), 0, -1):  # the longest name first
        for start in range(len(words) - n + 1):  # words may lead up to it, or follow it
            name = " ".join(words[start : start + n])
            if bind_text("lab_order", name) is not None:
                return name
    return None


def tests_in_advice(payload: dict[str, Any]) -> dict[str, Any]:
    """The tests an advice line orders, as investigations too (owner, 2026-10-10: "To review
    after 2 wks with HbA1c / FBS / PPBS / S. Lipase" stayed one line of advice and no test).

    A line is read for tests when it lists them: two or more of its parts ("/", ",", "+", "and")
    are tests the terminology knows. Then every part of that list is a test - one the lookup
    does not know (a misread, or a rare test) is kept as written at a low confidence, for the
    reviewer. A line naming one known test counts only when it also says so ("check", "repeat",
    "test"): "Take calcium" orders nothing. The advice line itself stays where it is - it still
    says when to come back - and a test already under investigations is not added twice."""
    if "advice" not in payload:
        return payload
    have = {(_coded_text(i) or "").strip().lower()
            for i in _items(payload, "investigations_advised")}  # fmt: skip
    added: list[dict[str, Any]] = []
    for item in _items(payload, "advice"):
        line = _coded_text(item) or ""
        parts = [p.strip(" .:;-") for p in _TEST_SPLIT.split(line)]
        parts = [p for p in parts if p]
        if _INTAKE.search(line):
            continue  # "Take calcium and vitamin D": something to take, not a test to do
        known = [_a_test(p) for p in parts]
        hits = [i for i, k in enumerate(known) if k]
        if len(hits) < 2 and not (hits and _TEST_CUE.search(line)):
            continue
        for i in range(hits[0], len(parts)):
            name = known[i] or parts[i]
            if len(hits) < 2 and not known[i]:
                continue  # one known test: only that one
            if len(name.split()) > 4 or name.lower() in have:
                continue
            have.add(name.lower())
            conf = _conf(item, 0.5)
            added.append({"text": name, "evidence": _ev(item),
                          "confidence": conf if known[i] else min(conf, 0.5)})  # fmt: skip
    if not added:
        return payload
    return {**payload, "investigations_advised": [*_items(payload, "investigations_advised"),
                                                  *added]}  # fmt: skip


def _med_facts(payload, default: float) -> list[FactDraft]:
    out = []
    for item in _items(payload, "medications"):
        if isinstance(item, dict) and str(item.get("drug_name") or "").strip():
            item = tidy_medication(reslot_name(item))
            if not item["drug_name"]:
                continue
            med = parse_medication(item)
            out.append(FactDraft("medication", str(item["drug_name"]).strip(),
                                 _conf(item, default), _ev(item), medication=med))  # fmt: skip
    return out


# ------------------------------------------------------------------ per doc type


def _facts_prescription(p, d):
    from cdi_adapter.extract.doctor import doctor_drafts  # EX-S2
    from cdi_adapter.extract.patient import patient_drafts  # EX-S1
    from cdi_adapter.extract.preparation import preparation_drafts  # EX-S4

    return (patient_drafts(p, d) + doctor_drafts(p, d)
            + _coded_facts(p, "diagnoses", "condition", d) + _med_facts(p, d)
            + _vital_facts(p, d) + _allergy_facts(p, d)
            + _coded_facts(p, "advice", "advice", d) + _order_facts(p, d)
            + preparation_drafts(p, d))  # fmt: skip


def _facts_lab(p, d):
    return _lab_facts(p, "tests", d)


def _facts_vitals(p, d):
    return _vital_facts(p, d) + _allergy_facts(p, d)


def _facts_opd_note(p, d):
    out = (_coded_facts(p, "chief_complaints", "symptom", d)
           + _coded_facts(p, "symptoms", "symptom", d) + _coded_facts(p, "findings", "finding", d)
           + _coded_facts(p, "diagnoses", "condition", d) + _vital_facts(p, d) + _med_facts(p, d)
           + _allergy_facts(p, d) + _coded_facts(p, "advice", "advice", d)
           + _order_facts(p, d))  # fmt: skip
    if isinstance(p.get("referral_to"), str) and p["referral_to"].strip():
        out.append(FactDraft("referral", p["referral_to"].strip(), d))
    return out


def _facts_discharge(p, d):
    return (_coded_facts(p, "diagnoses", "condition", d) + _procedure_facts(p, "procedures", d)
            + _lab_facts(p, "investigations", d) + _vital_facts(p, d) + _med_facts(p, d)
            + _allergy_facts(p, d) + _coded_facts(p, "advice", "advice", d))  # fmt: skip


def _facts_radiology(p, d):
    out = _coded_facts(p, "findings_list", "finding", d)
    out += _coded_facts(p, "impression_concepts", "condition", d)
    out += _coded_facts(p, "diagnoses", "condition", d)
    study = p.get("study") or p.get("modality")
    if isinstance(study, str) and study.strip():
        texts = (_coded_text(i) for i in _items(p, "impression_concepts"))
        impression = "; ".join(t for t in texts if t)
        out.append(FactDraft("diagnostic_report", study.strip(), d, value_kind="text" if impression
                             else None, value_text=impression or None))  # fmt: skip
    if p.get("procedure"):
        out += _procedure_facts({"p": [p["procedure"]]}, "p", d)
    out += _coded_facts(p, "advice", "advice", d)
    return out


HANDLERS = {
    "prescription": _facts_prescription,
    "lab_report": _facts_lab,
    "vitals_sheet": _facts_vitals,
    "opd_note": _facts_opd_note,
    "referral": _facts_opd_note,
    "discharge_summary": _facts_discharge,
    "operative_note": _facts_discharge,
    "radiology": _facts_radiology,
}

ENCOUNTER_DATE_KEYS = ("encounter_date", "reported_at", "study_date", "discharge_date",
                       "admission_date", "recorded_at")  # fmt: skip


def facts_from_payload(doc_type: str, payload: dict[str, Any]) -> list[FactDraft]:
    default = _conf(payload, 0.5)
    from cdi_adapter.extract.preparation import with_preparation  # EX-S4

    tidy = tests_in_advice(split_advice(tidy_lists(payload)))
    return HANDLERS[doc_type](with_preparation(tidy), default)
