"""The quality gate (ENT-S3): limits, claims, the drop against the last accepted run, history.

Three checks, all from a scorecard and a thresholds file written BEFORE the run:

* **limits** (AC2) - a score below its ``min`` (or an error rate above its ``max``) fails the
  change. A score that could not be measured (nothing in that slice) fails too: an unmeasured
  number is not a passed one.
* **claims** (AC3) - "99% of accepted values are right" for a group and slice. Reported with
  the sample size and the 95% lower bound; it passes only when the LOWER BOUND meets the target.
* **the drop** - against a baseline scorecard (the last accepted run): any limited score that
  fell by more than ``allowed_drop`` fails the change, even while still above its limit.

The thresholds are a decision of the product owner and a clinician (NFR3). The file records
who agreed and when; ``require_agreed`` (a release) refuses thresholds nobody signed.
"""

from __future__ import annotations

import json
from html import escape
from pathlib import Path
from typing import Any

from cdi_adapter.accuracy import stats

THRESHOLDS_VERSION = "accuracy-thresholds.v1"
CONTEXT = "context"


def value_of(card: dict[str, Any], group: str, metric: str, slice_name: str = "overall") -> Any:
    """One number of a scorecard. ``group`` is a field group, ``all`` (the four together) or
    ``context`` (``prep_to_test.error_rate``, ``test_to_reason.error_rate``, ...)."""
    sl = (card.get("slices") or {}).get(slice_name)
    if sl is None:
        return None
    if group == CONTEXT:
        node: Any = sl["context"]
        for part in metric.split("."):
            node = node.get(part) if isinstance(node, dict) else None
        return node
    if group == "all":
        return sl.get(metric, sl["all_groups"].get(metric))
    return (sl["groups"].get(group) or {}).get(metric)


def agreed(thresholds: dict[str, Any]) -> bool:
    a = thresholds.get("agreed") or {}
    return bool(a.get("owner") and a.get("clinician") and a.get("date"))


def evaluate(
    card: dict[str, Any],
    thresholds: dict[str, Any],
    *,
    baseline: dict[str, Any] | None = None,
    require_agreed: bool = False,
) -> dict[str, Any]:
    """The verdict: every limit, every claim, every drop - and ``passed``."""
    failures: list[str] = []
    if require_agreed and not agreed(thresholds):
        failures.append("the thresholds are not agreed: no owner, clinician and date recorded")
    if card.get("cases_without_a_result"):
        failures.append(
            f"{len(card['cases_without_a_result'])} case(s) have no result: "
            + ", ".join(card["cases_without_a_result"][:5])
        )
    limits = []
    for lim in thresholds.get("limits") or []:
        group, metric = lim["group"], lim["metric"]
        sl = lim.get("slice", "overall")
        got = value_of(card, group, metric, sl)
        row = {**lim, "slice": sl, "value": got, "passed": True}
        name = f"{group}.{metric} [{sl}]"
        if got is None:
            row["passed"] = False
            failures.append(f"{name}: not measured (nothing to measure it on)")
        elif "min" in lim and got < lim["min"]:
            row["passed"] = False
            failures.append(f"{name}: {got:.4f} is below its limit {lim['min']}")
        elif "max" in lim and got > lim["max"]:
            row["passed"] = False
            failures.append(f"{name}: {got:.4f} is above its limit {lim['max']}")
        if baseline is not None and got is not None:
            before = value_of(baseline, group, metric, sl)
            allowed = float(thresholds.get("allowed_drop", 0.0))
            worse = None if before is None else (got - before if "max" in lim else before - got)
            row["baseline"], row["drop"] = before, worse
            if worse is not None and worse > allowed + 1e-12:
                row["passed"] = False
                failures.append(
                    f"{name}: fell from {before:.4f} to {got:.4f} (allowed drop {allowed})"
                )
        limits.append(row)
    claims = []
    for claim in thresholds.get("claims") or []:
        sl = claim.get("slice", "overall")
        group = claim["group"]
        n = value_of(card, group, "accepted", sl) or 0
        k = value_of(card, group, "accepted_correct", sl) or 0
        proof = stats.proves(int(k), int(n), float(claim["target"]))
        coverage = value_of(card, group, "coverage", sl)
        row = {**claim, "slice": sl, **proof, "coverage": coverage}
        claims.append(row)
        if not proof["proven"]:
            low = proof["lower_bound_95"]
            said = "nothing was accepted" if low is None else f"lower bound {low:.4f}"
            failures.append(
                f"claim {group} precision >= {claim['target']} [{sl}]: NOT proven - "
                f"{k} right of {n} accepted, {said}"
            )
    return {
        "passed": not failures,
        "failures": failures,
        "limits": limits,
        "claims": claims,
        "thresholds_agreed": agreed(thresholds),
        "compared_with_a_baseline": baseline is not None,
    }


# ------------------------------------------------------------------ the history (FR4)


def headline(card: dict[str, Any]) -> dict[str, Any]:
    """The few numbers of a run that go into the history."""
    out: dict[str, Any] = {"cases": card.get("cases")}
    for group in ("patient", "doctor", "tests", "preparation"):
        out[group] = {m: value_of(card, group, m) for m in ("precision_accepted", "coverage")}
    out["strict"] = value_of(card, "all", "strict_whole_prescription_rate")
    out["prep_to_test_error"] = value_of(card, CONTEXT, "prep_to_test.error_rate")
    out["test_to_reason_error"] = value_of(card, CONTEXT, "test_to_reason.error_rate")
    return out


def append_history(
    path: Path, card: dict[str, Any], verdict: dict[str, Any], *, at: str, change: str = ""
) -> dict[str, Any]:
    """One line per run in ``history.jsonl``: when, which change, the headline, the verdict."""
    row = {
        "at": at,
        "change": change,
        "set": card.get("set"),
        "passed": verdict["passed"],
        "failures": len(verdict["failures"]),
        **headline(card),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")
    return row


def read_history(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


_COLOURS = {"patient": "#1f77b4", "doctor": "#2ca02c", "tests": "#d62728", "preparation": "#9467bd"}


def chart_html(history: list[dict[str, Any]]) -> str:
    """The history as one page: precision of accepted values and coverage per group, run by
    run. Plain SVG - no script, nothing loaded from elsewhere."""
    w, h, pad = 760, 240, 40

    def line(metric: str, group: str) -> str:
        raw = [(i, (row.get(group) or {}).get(metric)) for i, row in enumerate(history)]
        pts = [(i, float(v)) for i, v in raw if v is not None]
        if not pts:
            return ""
        step = (w - 2 * pad) / max(len(history) - 1, 1)
        path = " ".join(f"{pad + i * step:.1f},{h - pad - v * (h - 2 * pad):.1f}" for i, v in pts)
        dots = "".join(
            f'<circle cx="{p.split(",")[0]}" cy="{p.split(",")[1]}" r="3" '
            f'fill="{_COLOURS[group]}"/>'
            for p in path.split()
        )
        return (
            f'<polyline fill="none" stroke="{_COLOURS[group]}" stroke-width="2" '
            f'points="{path}"/>{dots}'
        )

    def chart(title: str, metric: str) -> str:
        grid = "".join(
            f'<line x1="{pad}" x2="{w - pad}" y1="{h - pad - t * (h - 2 * pad):.0f}" '
            f'y2="{h - pad - t * (h - 2 * pad):.0f}" stroke="#ddd"/>'
            f'<text x="4" y="{h - pad - t * (h - 2 * pad) + 4:.0f}" font-size="11">'
            f"{t:.0%}</text>"
            for t in (0, 0.25, 0.5, 0.75, 1)
        )
        lines = "".join(line(metric, g) for g in _COLOURS)
        return (
            f"<h2>{escape(title)}</h2><svg viewBox='0 0 {w} {h}' width='{w}' "
            f"role='img' aria-label='{escape(title)}'>{grid}{lines}</svg>"
        )

    legend = " ".join(f'<span style="color:{c}">&#9632; {g}</span>' for g, c in _COLOURS.items())
    rows = "".join(
        f"<tr><td>{escape(str(r.get('at')))}</td><td>{escape(str(r.get('change') or ''))}</td>"
        f"<td>{'PASS' if r.get('passed') else 'FAIL'}</td><td>{r.get('cases')}</td>"
        + "".join(f"<td>{_pct((r.get(g) or {}).get('precision_accepted'))}</td>" for g in _COLOURS)
        + f"<td>{_pct(r.get('strict'))}</td></tr>"
        for r in history
    )
    return (
        "<!doctype html><html lang=en><head><meta charset=utf-8><title>Accuracy history"
        "</title><style>body{font:14px sans-serif;margin:24px}td,th{padding:4px 10px;"
        "border-bottom:1px solid #ddd;text-align:left}</style></head><body>"
        f"<h1>Accuracy history</h1><p>{len(history)} run(s). {legend}</p>"
        + chart("Precision of accepted values", "precision_accepted")
        + chart("Coverage (accepted by itself)", "coverage")
        + "<h2>Runs</h2><table><tr><th>When</th><th>Change</th><th>Gate</th><th>Cases</th>"
        + "".join(f"<th>{g}</th>" for g in _COLOURS)
        + "<th>All four right</th></tr>"
        + rows
        + "</table></body></html>"
    )


def _pct(value: Any) -> str:
    return "-" if value is None else f"{value:.1%}"


# ------------------------------------------------------------------ the report


def as_markdown(card: dict[str, Any], verdict: dict[str, Any]) -> str:
    out = [
        f"# Accuracy scorecard - {card.get('set') or 'answer-key set'}",
        "",
        f"Cases: {card['cases']}. Gate: **{'PASS' if verdict['passed'] else 'FAIL'}**. "
        f"Thresholds agreed by the owner and a clinician: "
        f"{'yes' if verdict['thresholds_agreed'] else 'NO'}.",
        "",
    ]
    for name, sl in card["slices"].items():
        out += [
            f"## {name} ({sl['cases']} cases)",
            "",
            "| Group | Accepted | Right | Precision of accepted | 95% lower bound | Coverage "
            "| Field accuracy | Invented | Blank kept |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for group, m in sl["groups"].items():
            out.append(
                f"| {group} | {m['accepted']} | {m['accepted_correct']} | "
                f"{_pct(m['precision_accepted'])} | {_pct(m['precision_accepted_lower_95'])} "
                f"| {_pct(m['coverage'])} | {_pct(m['field_accuracy'])} | "
                f"{_pct(m['invented_rate'])} | {_pct(m['blank_kept'])} |"
            )
        c = sl["context"]
        out += [
            "",
            f"Test names: list F1 {_pct(sl['groups']['tests']['f1'])}, standard-name "
            f"accuracy {_pct(sl['standard_name_accuracy'])}. Preparation: list F1 "
            f"{_pct(sl['groups']['preparation']['f1'])}, amount exact "
            f"{_pct(sl['preparation_amount_exact'])}.",
            "",
            "**Context errors (counted apart from reading errors):**",
            "",
            "| Link | In the key | Given | Right | Wrong | Missing | Error rate |",
            "|---|---|---|---|---|---|---|",
        ]
        for kind, label in (
            ("prep_to_test", "preparation -> test"),
            ("test_to_reason", "test -> reason"),
        ):
            k = c[kind]
            out.append(
                f"| {label} | {k['in_key']} | {k['given']} | {k['right']} | {k['wrong']} | "
                f"{k['missing']} | {_pct(k['error_rate'])} |"
            )
        out += [
            "",
            f"Values put in the wrong field: {c['misplaced_values']}. All four groups "
            f"right in the same prescription: {_pct(sl['strict_whole_prescription_rate'])}.",
            "",
        ]
    if verdict["claims"]:
        out += [
            "## Claims",
            "",
            "| Claim | Slice | Accepted (n) | Right | Observed | "
            "95% lower bound | Target | Proven? |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for c in verdict["claims"]:
            out.append(
                f"| {c['group']} precision of accepted | {c['slice']} | {c['n']} | "
                f"{c['correct']} | {_pct(c['observed'])} | {_pct(c['lower_bound_95'])} | "
                f"{_pct(c['target'])} | {'YES' if c['proven'] else 'NOT YET'} |"
            )
        out.append("")
    out += ["## Gate", ""] + [f"- FAIL: {f}" for f in verdict["failures"] or []]
    if not verdict["failures"]:
        out.append("- every limit held and every claim is proven")
    return "\n".join(out) + "\n"
