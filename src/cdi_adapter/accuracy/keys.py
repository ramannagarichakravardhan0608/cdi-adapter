"""The answer-key set (ENT-S3): its folder format, its checks, and labeller agreement.

A set is a folder::

    <set>/
      manifest.json               what the set is: name, suite, sealed or not, where it came from
      <case id>/
        answer.json               the settled answer key (answer.v1)
        answer.<labeller>.json    each labeller's own, independent key (kept, never edited)
        image.png | page-1.png    the picture - identity removed (may be absent for a
                                  synthetic case that has no picture at all)
        result.json               what the system produced (result.v1) - written by a run

``answer.json`` holds the four groups the product is judged on - patient, doctor, tests,
preparation - and the links between them. A field that is on the page is written as it should
be read; a field that is **absent or illegible is written null**, so "leave it blank" is a
tested answer too.

Three things make a key usable, and :func:`check_set` refuses a set that lacks any:

* **it is settled** - two labellers' keys agree, or their disagreements were adjudicated and
  the key says by whom (AC5: an unclear label cannot be a fair test);
* **its identity is removed** - the case records how (synthetic, or de-identified with which
  tool), who checked the picture and when (AC6);
* **it says which slices it belongs to** - printed or handwritten, known or unknown doctor,
  and the doctor/clinic group used to keep test and training data apart.
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any

ANSWER_VERSION = "answer.v1"
GROUPS = ("patient", "doctor", "tests", "preparation")
PATIENT_FIELDS = ("name", "age_text", "sex", "dob", "phone", "address")
DOCTOR_FIELDS = ("name", "registration_no", "qualification", "department")
DIGIT_FIELDS = ("phone", "registration_no")  # compared digit for digit
SCRIPTS = ("printed", "handwritten", "mixed")
DOCTORS = ("known", "unknown")
SUITES = ("capability", "regression", "adversarial")
IDENTITY_METHODS = ("synthetic", "deidentified")
IMAGE_NAMES = re.compile(r"(?i)^(image|page-\d+)\.(png|jpe?g|tiff?|pdf)$")
_SEX = {
    "m": "male",
    "male": "male",
    "f": "female",
    "female": "female",
    "o": "other",
    "other": "other",
}


# ------------------------------------------------------------------ one rule for "the same"


def norm(value: Any) -> str:
    """The normalisation every comparison uses, fixed in advance (Accuracy Plan): Unicode NFC,
    case folded, runs of spaces made one, punctuation at the ends dropped."""
    text = unicodedata.normalize("NFC", str(value if value is not None else ""))
    return " ".join(text.casefold().split()).strip(" .,;:-")


def digits(value: Any) -> str:
    return re.sub(r"\D", "", str(value if value is not None else ""))


def same(field: str, key: Any, got: Any) -> bool:
    """Is ``got`` the right reading of ``key`` for this field? Digits fields: every digit, in
    order (a country prefix on a phone is not a wrong reading). Sex: m / male are one."""
    if field in DIGIT_FIELDS:
        k, g = digits(key), digits(got)
        if field == "phone" and len(k) >= 10 and len(g) >= 10:
            return k[-10:] == g[-10:]
        return bool(k) and k == g
    if field == "sex":
        return _SEX.get(norm(key), norm(key)) == _SEX.get(norm(got), norm(got))
    return norm(key) == norm(got)


def number(value: Any) -> float | None:
    try:
        return None if value is None or value == "" else float(value)
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------ reading and checking


def problems_of(answer: Any) -> list[str]:
    """Everything wrong with one answer key's FORM; empty when it can be scored."""
    if not isinstance(answer, dict):
        return ["the answer is not an object"]
    found = []
    if answer.get("schema_version") != ANSWER_VERSION:
        found.append(f"schema_version must be {ANSWER_VERSION!r}")
    if not answer.get("case_id"):
        found.append("no case_id")
    slices = answer.get("slices") or {}
    if slices.get("script") not in SCRIPTS:
        found.append(f"slices.script must be one of {SCRIPTS}")
    if slices.get("doctor") not in DOCTORS:
        found.append(f"slices.doctor must be one of {DOCTORS}")
    if not slices.get("doctor_group"):
        found.append("slices.doctor_group is missing (it keeps test and training data apart)")
    for group, fields in (("patient", PATIENT_FIELDS), ("doctor", DOCTOR_FIELDS)):
        block = answer.get(group)
        if not isinstance(block, dict):
            found.append(f"{group} must be an object (a field not on the page is null)")
            continue
        for field in fields:
            if field not in block:
                found.append(f"{group}.{field} is not stated (write null when it is absent)")
        found += [f"{group}.{f} is not a field of the key" for f in block if f not in fields]
    for group in ("tests", "preparation"):
        if not isinstance(answer.get(group), list):
            found.append(f"{group} must be a list (empty when none is written)")
    for i, t in enumerate(answer.get("tests") or []):
        if not isinstance(t, dict) or not t.get("as_written"):
            found.append(f"tests[{i}] has no as_written")
        elif not isinstance(t.get("reasons", []), list):
            found.append(f"tests[{i}].reasons must be a list")
    for i, p in enumerate(answer.get("preparation") or []):
        if not isinstance(p, dict) or not p.get("text"):
            found.append(f"preparation[{i}] has no text")
            continue
        applies = p.get("applies_to")
        if not (applies in ("all", "unclear") or isinstance(applies, list)):
            found.append(f"preparation[{i}].applies_to must be 'all', 'unclear' or a list")
    return found


def settled(answer: dict[str, Any]) -> str | None:
    """None when the key may be used; else why not (AC5). A key is settled when two labellers
    made it independently and either agreed or had their disagreements adjudicated."""
    lab = answer.get("labelling") or {}
    who = [w for w in lab.get("labellers") or [] if w]
    if len(set(who)) < 2:
        return "fewer than two labellers"
    if lab.get("disagreements", 0) and not (
        lab.get("adjudicated_by") and lab.get("adjudicated_on")
    ):
        return f"{lab['disagreements']} disagreement(s) not adjudicated"
    if "disagreements" not in lab:
        return "the labellers' keys were never compared (no disagreement count)"
    return None


def identity_removed(answer: dict[str, Any]) -> str | None:
    """None when the case records that it shows no real identity; else what is missing (AC6).
    ``synthetic``: a made-up person and clinic. ``deidentified``: a real page with every
    identity removed - by which tool, and checked by a person who looked at the picture."""
    rec = answer.get("identity") or {}
    if rec.get("method") not in IDENTITY_METHODS:
        return f"identity.method must be one of {IDENTITY_METHODS}"
    if rec["method"] == "deidentified" and not rec.get("tool"):
        return "identity.tool is missing (which tool removed the identity)"
    if not (rec.get("checked_by") and rec.get("checked_on")):
        return "nobody is recorded as having looked at the picture (checked_by, checked_on)"
    if rec.get("readable_identity") is not False:
        return "identity.readable_identity is not recorded as false"
    return None


def load_set(folder: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """``(manifest, {case id: answer})`` of a set folder."""
    manifest_path = folder / "manifest.json"
    manifest = json.loads(manifest_path.read_text("utf-8")) if manifest_path.is_file() else {}
    cases = {}
    for path in sorted(folder.glob("*/answer.json")):
        answer = json.loads(path.read_text(encoding="utf-8"))
        cases[path.parent.name] = answer
    return manifest, cases


def check_set(folder: Path) -> dict[str, Any]:
    """Is this set fit to measure with? Every problem, by case; plus what the set is made of
    (how many of each slice, how many blank fields - a set with no absent fields does not
    test "leave it blank")."""
    manifest, cases = load_set(folder)
    problems: dict[str, list[str]] = {}
    counts: dict[str, int] = {}
    blank = present = 0
    groups: dict[str, set[str]] = {}
    for case_id, answer in cases.items():
        found = problems_of(answer)
        if answer.get("case_id") not in (None, case_id):
            found.append(f"case_id {answer.get('case_id')!r} is not the folder's name")
        for why in (settled(answer), identity_removed(answer)):
            if why:
                found.append(why)
        pictures = [p.name for p in (folder / case_id).iterdir() if IMAGE_NAMES.match(p.name)]
        if not pictures and (answer.get("identity") or {}).get("method") != "synthetic":
            found.append("no picture (image.png / page-N.png)")
        if found:
            problems[case_id] = found
        slices = answer.get("slices") or {}
        for key in ("script", "doctor", "suite"):
            label = f"{key}={slices.get(key)}"
            counts[label] = counts.get(label, 0) + 1
        groups.setdefault(str(slices.get("doctor_group")), set()).add(case_id)
        for group in ("patient", "doctor"):
            for value in (answer.get(group) or {}).values():
                blank, present = blank + (value is None), present + (value is not None)
    notes = []
    if cases and not blank:
        notes.append("no field is absent in any key: 'leave it blank' is not tested")
    if not manifest:
        notes.append("no manifest.json")
    return {
        "set": folder.name,
        "cases": len(cases),
        "problems": problems,
        "notes": notes,
        "slices": dict(sorted(counts.items())),
        "fields_present": present,
        "fields_blank": blank,
        "doctor_groups": {g: len(c) for g, c in sorted(groups.items())},
        "sealed": bool(manifest.get("sealed")),
        "suite": manifest.get("suite"),
        "usable": bool(cases) and not problems,
    }


def split_leaks(test_set: Path, training_groups: set[str]) -> list[str]:
    """Cases of the test set whose doctor/clinic group is also in the training data (NFR2:
    test and training data are split by doctor and clinic)."""
    _, cases = load_set(test_set)
    return sorted(
        c
        for c, a in cases.items()
        if str((a.get("slices") or {}).get("doctor_group")) in training_groups
    )


# ------------------------------------------------------------------ two labellers (AC5)


def _values(answer: dict[str, Any]) -> dict[str, Any]:
    """An answer key flattened to ``{what: value}`` so two keys can be compared item by item."""
    out: dict[str, Any] = {}
    for group, fields in (("patient", PATIENT_FIELDS), ("doctor", DOCTOR_FIELDS)):
        for field in fields:
            value = (answer.get(group) or {}).get(field)
            out[f"{group}.{field}"] = (
                None if value is None else (digits(value) if field in DIGIT_FIELDS else norm(value))
            )
    for t in answer.get("tests") or []:
        name = norm(t.get("as_written"))
        out[f"tests[{name}]"] = norm(t.get("standard_code") or "") or "listed"
        out[f"tests[{name}].reasons"] = sorted(norm(r) for r in t.get("reasons") or [])
    for p in answer.get("preparation") or []:
        text = norm(p.get("text"))
        applies = p.get("applies_to")
        out[f"preparation[{text}]"] = [
            norm(p.get("type")),
            number(p.get("amount")),
            norm(p.get("unit")),
        ]
        out[f"preparation[{text}].applies_to"] = (
            sorted(norm(a) for a in applies) if isinstance(applies, list) else applies
        )
    return out


def agreement(
    first: dict[str, dict[str, Any]], second: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Two labellers' keys for the same cases, compared item by item. Returns the agreement -
    overall and per group - and EVERY disagreement, each one to be settled before the key is
    used. An item only one of them wrote down is a disagreement too."""
    shared = sorted(set(first) & set(second))
    by_group: dict[str, list[int]] = {g: [0, 0] for g in GROUPS}
    disagreements = []
    for case in shared:
        a, b = _values(first[case]), _values(second[case])
        for item in sorted(set(a) | set(b)):
            group = item.split(".", 1)[0].split("[", 1)[0]
            agree = item in a and item in b and a[item] == b[item]
            by_group[group][0] += agree
            by_group[group][1] += 1
            if not agree:
                disagreements.append(
                    {
                        "case": case,
                        "item": item,
                        "first": a.get(item, "(not written)"),
                        "second": b.get(item, "(not written)"),
                    }
                )
    total = sum(n for _, n in by_group.values())
    agreed = sum(k for k, _ in by_group.values())
    return {
        "cases_compared": len(shared),
        "only_one_labeller": sorted(set(first) ^ set(second)),
        "items": total,
        "agreed": agreed,
        "agreement": agreed / total if total else None,
        "by_group": {
            g: {"items": n, "agreed": k, "agreement": k / n if n else None}
            for g, (k, n) in by_group.items()
        },
        "disagreements": disagreements,
        "all_settled": not disagreements,
    }


def labeller_keys(folder: Path, labeller: str) -> dict[str, dict[str, Any]]:
    """``{case id: that labeller's own key}`` - the files ``answer.<labeller>.json``."""
    return {
        p.parent.name: json.loads(p.read_text(encoding="utf-8"))
        for p in sorted(folder.glob(f"*/answer.{labeller}.json"))
    }
