"""The scorer (ENT-S3): a result (result.v1, OUT-S2) against its answer key, value by value.

Deterministic - exact match after one fixed normalisation (``keys.norm``); no model grades
anything. Every value becomes one **record** with an outcome:

=============  ==================================================================
``correct``    on the page, and read right
``wrong``      on the page, read, but not right
``missed``     on the page, and the system gave nothing
``invented``   NOT on the page (the key says null / does not list it), yet given
``blank``      not on the page, and rightly left blank
=============  ==================================================================

and whether the system **accepted** it by itself (``status: accepted``) or held it for a
person. From the records, per group (patient, doctor, tests, preparation) and per slice:

* **precision of accepted values** - of what the system accepted by itself, how much is right.
  The headline. Shown with its sample size and 95% lower bound (``stats``);
* **coverage** - how much of what it produced it accepted by itself;
* field accuracy / recall, list precision and F1, the invented (hallucination) rate, how often
  an absent field was rightly left blank, digit-exactness, and the calibration gap.

**Context is counted apart from reading** (AC4). A fasting note read perfectly but attached
to the wrong test is not a reading error and must not hide inside reading accuracy. So links
are judged only between items that were themselves matched: preparation -> test, test ->
reason, and a value put in the wrong field (the doctor's name given as the patient's).
The context error rate is the Accuracy Plan's: wrong links / links in the key - with link
precision and recall beside it, so a link that is simply missing shows as well.
"""

from __future__ import annotations

from typing import Any

from cdi_adapter.accuracy import stats
from cdi_adapter.accuracy.keys import (
    DIGIT_FIELDS,
    DOCTOR_FIELDS,
    GROUPS,
    PATIENT_FIELDS,
    norm,
    number,
    same,
)

OUTCOMES = ("correct", "wrong", "missed", "invented", "blank")
BANDS = ((0.0, 0.5), (0.5, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 0.95), (0.95, 1.0001))


def _given(v: Any) -> tuple[Any, bool, float | None]:
    """``(value, accepted, confidence)`` of a result value object; ``(None, False, None)``
    when there is none."""
    if not isinstance(v, dict) or v.get("value") in (None, ""):
        return None, False, None
    conf = v.get("confidence")
    return (
        v["value"],
        v.get("status") == "accepted",
        conf if isinstance(conf, int | float) else None,
    )


def _record(
    case: str,
    group: str,
    field: str,
    key: Any,
    got: Any,
    accepted: bool,
    confidence: float | None,
    outcome: str,
) -> dict[str, Any]:
    return {
        "case": case,
        "group": group,
        "field": field,
        "key": key,
        "got": got,
        "accepted": accepted and got is not None,
        "confidence": confidence,
        "outcome": outcome,
    }


def _single(case: str, group: str, field: str, key: Any, v: Any) -> dict[str, Any]:
    got, accepted, conf = _given(v)
    if key is None:
        outcome = "blank" if got is None else "invented"
    elif got is None:
        outcome = "missed"
    else:
        outcome = "correct" if same(field, key, got) else "wrong"
    return _record(case, group, field, key, got, accepted, conf, outcome)


def _match(keys: list[str], gots: list[str], extra: Any = None) -> dict[int, int]:
    """Pair key items with given items: equal names first, then ``extra(ki, gi)`` (the same
    standard code, the same type and amount). ``{key index: given index}``; each used once."""
    pairs: dict[int, int] = {}
    free = set(range(len(gots)))
    for ki, name in enumerate(keys):
        hit = next((gi for gi in sorted(free) if gots[gi] == name and name), None)
        if hit is not None:
            pairs[ki] = hit
            free.discard(hit)
    if extra is not None:
        for ki in range(len(keys)):
            if ki in pairs:
                continue
            hit = next((gi for gi in sorted(free) if extra(ki, gi)), None)
            if hit is not None:
                pairs[ki] = hit
                free.discard(hit)
    return pairs


def _prep_value(p: dict[str, Any]) -> tuple[str, float | None, str]:
    return norm(p.get("type")), number(p.get("amount")), norm(p.get("unit"))


def score_case(case: str, answer: dict[str, Any], result: dict[str, Any] | None) -> dict[str, Any]:
    """One prescription: its records, its link counts, and whether all of it is right."""
    result = result or {}
    records = []
    for group, fields in (("patient", PATIENT_FIELDS), ("doctor", DOCTOR_FIELDS)):
        given = result.get(group)
        block: dict[str, Any] = given if isinstance(given, dict) else {}
        for field in fields:
            records.append(
                _single(case, group, field, (answer.get(group) or {}).get(field), block.get(field))
            )

    # ---- tests: the ordered tests (a lab RESULT written on the page is not an order)
    key_tests = answer.get("tests") or []
    got_tests = [t for t in result.get("lab_tests") or [] if isinstance(t, dict) and "match" in t]
    key_codes = [norm(t.get("standard_code")) for t in key_tests]
    got_codes = [norm((t.get("standard") or {}).get("code")) for t in got_tests]
    test_pairs = _match(
        [norm(t.get("as_written")) for t in key_tests],
        [norm(t.get("as_written")) for t in got_tests],
        lambda ki, gi: bool(key_codes[ki]) and key_codes[ki] == got_codes[gi],
    )
    named = named_right = 0
    for ki, t in enumerate(key_tests):
        if ki not in test_pairs:
            records.append(
                _record(case, "tests", "name", t.get("as_written"), None, False, None, "missed")
            )
            continue
        got, accepted, conf = _given(got_tests[test_pairs[ki]].get("value"))
        got = got if got is not None else got_tests[test_pairs[ki]].get("as_written")
        records.append(
            _record(case, "tests", "name", t.get("as_written"), got, accepted, conf, "correct")
        )
        if key_codes[ki]:  # read right is one thing; NAMED as the right standard test another
            named += 1
            named_right += key_codes[ki] == got_codes[test_pairs[ki]]
    for gi, t in enumerate(got_tests):
        if gi not in test_pairs.values():
            got, accepted, conf = _given(t.get("value"))
            records.append(
                _record(
                    case,
                    "tests",
                    "name",
                    None,
                    got or t.get("as_written"),
                    accepted,
                    conf,
                    "invented",
                )
            )

    # ---- preparation: the note's words, then its type / amount / unit
    key_preps = answer.get("preparation") or []
    got_preps = [p for p in result.get("preparation") or [] if isinstance(p, dict)]
    prep_pairs = _match(
        [norm(p.get("text")) for p in key_preps],
        [norm(p.get("text") or p.get("value")) for p in got_preps],
        lambda ki, gi: (
            _prep_value(key_preps[ki])[0] != ""
            and _prep_value(key_preps[ki]) == _prep_value(got_preps[gi])
        ),
    )
    amounts = amounts_right = 0
    for ki, p in enumerate(key_preps):
        if ki not in prep_pairs:
            records.append(
                _record(case, "preparation", "item", p.get("text"), None, False, None, "missed")
            )
            continue
        g = got_preps[prep_pairs[ki]]
        right = all(
            k in ("", None) or k == v for k, v in zip(_prep_value(p), _prep_value(g), strict=True)
        )
        conf = g.get("confidence")
        records.append(
            _record(
                case,
                "preparation",
                "item",
                p.get("text"),
                g.get("text"),
                g.get("status") == "accepted",
                conf if isinstance(conf, int | float) else None,
                "correct" if right else "wrong",
            )
        )
        if number(p.get("amount")) is not None:
            amounts += 1
            amounts_right += number(p.get("amount")) == number(g.get("amount"))
    for gi, g in enumerate(got_preps):
        if gi not in prep_pairs.values():
            conf = g.get("confidence")
            records.append(
                _record(
                    case,
                    "preparation",
                    "item",
                    None,
                    g.get("text"),
                    g.get("status") == "accepted",
                    conf if isinstance(conf, int | float) else None,
                    "invented",
                )
            )

    links = _links(
        answer, key_tests, got_tests, test_pairs, key_preps, got_preps, prep_pairs, result
    )
    reading_ok = all(r["outcome"] in ("correct", "blank") for r in records)
    return {
        "case": case,
        "records": records,
        "links": links,
        "standard_name": {"n": named, "correct": named_right},
        "amounts": {"n": amounts, "correct": amounts_right},
        "no_result": not result,
        "all_correct": reading_ok
        and not any(
            links[k]["wrong"] or links[k]["missing"] for k in ("prep_to_test", "test_to_reason")
        )
        and not links["misplaced"],
    }


def _links(
    answer: dict[str, Any],
    key_tests: list[Any],
    got_tests: list[Any],
    test_pairs: dict[int, int],
    key_preps: list[Any],
    got_preps: list[Any],
    prep_pairs: dict[int, int],
    result: dict[str, Any],
) -> dict[str, Any]:
    """AC4: the links, judged only between items that were themselves read (matched) - so a
    link error is never a reading error counted twice, nor hidden inside one."""
    got_to_key = {gi: ki for ki, gi in test_pairs.items()}
    name_to_key = {norm(got_tests[gi].get("as_written")): ki for gi, ki in got_to_key.items()}
    key_names = {norm(t.get("as_written")): ki for ki, t in enumerate(key_tests)}

    def targets(applies: Any, names: dict[str, int], everything: set[int]) -> set[int]:
        if applies == "all":
            return set(everything)
        if isinstance(applies, list):
            return {names[norm(a)] for a in applies if norm(a) in names}
        return set()  # unclear: linked to nothing

    matched_tests = set(test_pairs)
    key_pt, got_pt = set(), set()
    for ki, gi in prep_pairs.items():
        key_pt |= {
            (ki, t)
            for t in targets(key_preps[ki].get("applies_to"), key_names, set(range(len(key_tests))))
            if t in matched_tests
        }
        got_pt |= {
            (ki, t) for t in targets(got_preps[gi].get("applies_to"), name_to_key, matched_tests)
        }
    key_tr, got_tr = set(), set()
    for ki, gi in test_pairs.items():
        key_tr |= {(ki, norm(r)) for r in key_tests[ki].get("reasons") or [] if norm(r)}
        got_tr |= {
            (ki, norm(c.get("text")))
            for c in got_tests[gi].get("context") or []
            if isinstance(c, dict) and norm(c.get("text"))
        }
    # a value in the wrong field: the doctor's name given as the patient's, or the other way
    misplaced = []
    names = {
        "patient": (answer.get("patient") or {}).get("name"),
        "doctor": (answer.get("doctor") or {}).get("name"),
    }
    for group, other in (("patient", "doctor"), ("doctor", "patient")):
        block = result.get(group)
        got, _, _ = _given(block.get("name") if isinstance(block, dict) else None)
        if (
            got
            and names[other]
            and same("name", names[other], got)
            and not (names[group] and same("name", names[group], got))
        ):
            misplaced.append(f"{other}'s name given as the {group}'s")

    def count(key: set[Any], got: set[Any]) -> dict[str, int]:
        return {
            "in_key": len(key),
            "given": len(got),
            "right": len(key & got),
            "wrong": len(got - key),
            "missing": len(key - got),
        }

    return {
        "prep_to_test": count(key_pt, got_pt),
        "test_to_reason": count(key_tr, got_tr),
        "misplaced": misplaced,
    }


# ------------------------------------------------------------------ from records to a scorecard


def _ratio(k: int, n: int) -> float | None:
    return k / n if n else None


def metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    """The numbers of one group in one slice."""
    n = {o: sum(1 for r in records if r["outcome"] == o) for o in OUTCOMES}
    produced = n["correct"] + n["wrong"] + n["invented"]
    in_key = n["correct"] + n["wrong"] + n["missed"]
    accepted = [r for r in records if r["accepted"]]
    accepted_right = sum(1 for r in accepted if r["outcome"] == "correct")
    precision = _ratio(n["correct"], produced)
    recall = _ratio(n["correct"], in_key)
    digit = [r for r in records if r["field"] in DIGIT_FIELDS and r["key"] is not None]
    gaps, weighted = [], 0.0
    scored = [r for r in records if r["confidence"] is not None and r["got"] is not None]
    for low, high in BANDS:
        band = [r for r in scored if low <= r["confidence"] < high]
        if band:
            right = sum(1 for r in band if r["outcome"] == "correct") / len(band)
            stated = sum(r["confidence"] for r in band) / len(band)
            gaps.append(
                {
                    "band": f"{low:.2f}-{min(high, 1.0):.2f}",
                    "n": len(band),
                    "stated": round(stated, 4),
                    "right": round(right, 4),
                }
            )
            weighted += abs(stated - right) * len(band)
    return {
        "values_in_key": in_key,
        "produced": produced,
        **n,
        "accepted": len(accepted),
        "accepted_correct": accepted_right,
        "precision_accepted": _ratio(accepted_right, len(accepted)),
        "precision_accepted_lower_95": stats.wilson_lower(accepted_right, len(accepted)),
        "coverage": _ratio(len(accepted), produced),
        "field_accuracy": recall,
        "recall": recall,
        "list_precision": precision,
        "f1": (
            2 * precision * recall / (precision + recall)
            if precision and recall
            else (0.0 if in_key or produced else None)
        ),
        "invented_rate": _ratio(n["invented"], produced),
        # for a list (tests, preparation) nothing is "left blank": the invented rate says it
        "blank_kept": None
        if records and all(r["group"] in ("tests", "preparation") for r in records)
        else _ratio(n["blank"], n["blank"] + n["invented"]),
        "digits_exact": _ratio(sum(1 for r in digit if r["outcome"] == "correct"), len(digit)),
        "calibration_gap": round(weighted / len(scored), 4) if scored else None,
        "calibration_bands": gaps,
    }


def _context(cases: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for kind in ("prep_to_test", "test_to_reason"):
        total = {
            k: sum(c["links"][kind][k] for c in cases)
            for k in ("in_key", "given", "right", "wrong", "missing")
        }
        out[kind] = {
            **total,
            "error_rate": _ratio(total["wrong"], total["in_key"]),
            "link_precision": _ratio(total["right"], total["given"]),
            "link_recall": _ratio(total["right"], total["in_key"]),
        }
    out["misplaced_values"] = sum(len(c["links"]["misplaced"]) for c in cases)
    return out


def scorecard(
    answers: dict[str, dict[str, Any]],
    results: dict[str, dict[str, Any] | None],
    *,
    set_name: str = "",
) -> dict[str, Any]:
    """Every case scored, then gathered: overall and per slice (printed / handwritten, known /
    unknown doctor, suite), each with the four groups; the context counts; the strict
    whole-prescription rate; and the records themselves, so a person can read what the
    scorer decided (a grader can be wrong too)."""
    scored = {c: score_case(c, answers[c], results.get(c)) for c in sorted(answers)}
    slices: dict[str, list[str]] = {"overall": list(scored)}
    for case, answer in answers.items():
        for key in ("script", "doctor", "suite"):
            value = (answer.get("slices") or {}).get(key)
            if value:
                slices.setdefault(f"{key}={value}", []).append(case)
    out_slices = {}
    for name, members in sorted(slices.items()):
        cases = [scored[c] for c in sorted(members)]
        records = [r for c in cases for r in c["records"]]
        named = {k: sum(c["standard_name"][k] for c in cases) for k in ("n", "correct")}
        amounts = {k: sum(c["amounts"][k] for c in cases) for k in ("n", "correct")}
        out_slices[name] = {
            "cases": len(cases),
            "groups": {g: metrics([r for r in records if r["group"] == g]) for g in GROUPS},
            "all_groups": metrics(records),
            "standard_name_accuracy": _ratio(named["correct"], named["n"]),
            "preparation_amount_exact": _ratio(amounts["correct"], amounts["n"]),
            "context": _context(cases),
            "strict_whole_prescription_rate": _ratio(
                sum(c["all_correct"] for c in cases), len(cases)
            ),
        }
    return {
        "schema_version": "scorecard.v1",
        "set": set_name,
        "cases": len(scored),
        "cases_without_a_result": sorted(c for c, s in scored.items() if s["no_result"]),
        "slices": out_slices,
        "records": [r for c in scored.values() for r in c["records"]],
        "links": {c: s["links"] for c, s in scored.items()},
    }


def transcript(card: dict[str, Any], limit: int = 40) -> list[str]:
    """Plain lines of what the scorer decided, errors first - to be READ, so a wrong grader is
    caught (TR8)."""
    order = {"wrong": 0, "invented": 1, "missed": 2, "correct": 3, "blank": 4}
    rows = sorted(card["records"], key=lambda r: (order[r["outcome"]], r["case"], r["group"]))
    return [
        f"{r['outcome']:9} {'accepted' if r['accepted'] else 'held    '} {r['case']} "
        f"{r['group']}.{r['field']}: key={r['key']!r} got={r['got']!r}"
        for r in rows[:limit]
    ]
