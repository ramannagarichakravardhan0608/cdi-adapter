"""Medication agent: diagnosis-aware medicine identification (backlog E12-S4 reviewer copilot,
E12-S5 trace; owner request 2026-09-26).

The catalogue layer (``catalogue/``) matches a medicine by its *spelling*. A badly misread
handwritten name ("Muronorm" for Thyronorm, "Shaz" for Saaz) is too far from any product to match
safely, so it stays ``UNKNOWN_MEDICATION``. This agent uses what the spelling cannot: the page
image, the written strength and form, and the prescription's own diagnoses.

For each page with medicine lines, after the document is filed for review:

1. **Shortlist** (catalogue tool, no model): per line not already identified exactly, the products
   of the written strength and form whose names are nearest the reading, plus the closest names
   (``catalogue.service.shortlist``).
2. **Choose** (model, page image): which shortlisted product is written on each line, or none
   (schema ``med_agent_choice.v1``).
3. **Guard** (no model, ADR-0003 rule 4, veto only): a choice outside the shortlist is dropped; a
   product whose name does not look like the line's own reading is vetoed (the model may not pick
   a product because it suits a diagnosis).
4. **Fit** (model, text): for each identified product, from what it *contains*, the ONE diagnosis on
   the prescription it treats, or none (schema ``med_agent_fit.v1``).
5. **Verify** (model, text): a yes/no second opinion on every medicine-diagnosis link from step 4
   (schema ``med_agent_verify.v1``); a link it does not confirm is dropped. The 7B links a sedative
   to asthma when asked "which diagnosis?", but answers "no" when asked about the pair directly
   (measured on the owner's test prescription, 2026-09-26).

The result is a **suggestion** per medication fact (``ai_event`` kind ``medication_agent`` ->
``ai_suggestion``): the product, the diagnosis it treats or "no matching diagnosis", the evidence
and a one-line rationale. It never changes a fact or a review state (ADR-0003 rule 3); the reviewer
applies a suggestion through the normal correction. Every step is written to ``agent_trace``.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, text

from cdi_adapter import repo
from cdi_adapter.catalogue import service as catalogue
from cdi_adapter.catalogue.normalize import nearness, split_name
from cdi_adapter.config import get_settings
from cdi_adapter.db import session_scope
from cdi_adapter.logging import get_logger
from cdi_adapter.ml.client import MLClient, get_client, is_partial

log = get_logger(__name__)

AGENT = "medication_agent.v1"
EVENT_KIND = "medication_agent"
CHOOSE_SCHEMA = "med_agent_choice.v1"
FIT_SCHEMA = "med_agent_fit.v1"
VERIFY_SCHEMA = "med_agent_verify.v1"
DIAGNOSIS_TYPES = ("condition", "symptom")
# a line whose catalogue identification is already exact needs no choice, only the fit check
SETTLED = ("exact", "generic")


@dataclass
class Line:
    """One medicine line of a page, as the agent works on it."""

    n: int
    fact_id: str
    read_as: str
    strengths: tuple[float, ...]
    form: str | None
    catalogue: dict[str, Any] | None
    options: list[dict[str, Any]] = field(default_factory=list)

    @property
    def settled(self) -> bool:
        c = self.catalogue or {}
        return c.get("status") == "matched" and c.get("method") in SETTLED


# ------------------------------------------------------------------------------ pure parts


def diagnoses_of(facts: list[dict[str, Any]]) -> list[str]:
    """The diagnoses (and complaints) a prescription states, one per item, in order: a line
    "Rheumatoid Arthritis | Hypothyroid" (or with commas) is two diagnoses."""
    out: list[str] = []
    for f in facts:
        if f["fact_type"] not in DIAGNOSIS_TYPES or not f.get("is_current", True):
            continue
        for part in re.split(r"\s*[|;,]\s*", f.get("local_text") or ""):
            part = part.strip(" .,-")
            if len(part) >= 3 and part.lower() not in (d.lower() for d in out):
                out.append(part)
    return out


def _strength_text(line: Line) -> str:
    return f", strength {', '.join(f'{s:g}' for s in line.strengths)}" if line.strengths else ""


def choose_prompt(lines: list[Line]) -> str:
    blocks = []
    for ln in lines:
        opts = "\n".join(
            f"   {i}) {o['name']} - {o.get('composition') or 'composition not listed'}"
            for i, o in enumerate(ln.options, start=1)
        )
        blocks.append(f'[{ln.n}] first read as "{ln.read_as}"{_strength_text(ln)}. '
                      f"Options:\n{opts}")  # fmt: skip
    return (
        "You are helping a clinician check a handwritten prescription. The page image is "
        "attached. A first reading of its medicine lines is below; it may be wrong.\n\n"
        "For each line, look at the handwriting of that line on the page and choose the "
        "catalogue product that is actually written there. Answer choice 0 when none of the "
        "options is what is written.\n\n" + "\n\n".join(blocks) + "\n\n"
        "For EACH line: choice (the option number, or 0), evidence (the medicine name exactly as "
        "you read it on the page), rationale (one short sentence), confidence (0 to 1)."
    )


def fit_prompt(diagnoses: list[str], items: list[tuple[int, str, str]]) -> str:
    """``items``: (line, product name, what it contains)."""
    return (
        "You are a clinical pharmacist checking a prescription. Its diagnoses, as the doctor "
        "wrote them (doctors abbreviate: read each abbreviation as the condition it stands "
        "for):\n" + "\n".join(f"- {d}" for d in diagnoses) + "\n\n"
        "For EACH medicine below (with what it contains), give the ONE diagnosis from that list "
        "it is a standard, recognised treatment for, copied exactly as listed, and why in a few "
        "words. Give null when it is not a standard treatment for any of them; do not stretch.\n\n"
        + "\n".join(f"[{n}] {name} - contains {contains}" for n, name, contains in items)
        + '\n\nExample answer (another prescription): {"lines": [{"line": 1, "treats": '
        '"Type 2 diabetes mellitus", "why": "metformin lowers blood glucose"}], "confidence": 0.8}'
    )


def verify_prompt(pairs: list[tuple[str, str]]) -> str:
    """``pairs``: (what the medicine contains, diagnosis)."""
    return (
        "You are a clinical pharmacist. Doctors abbreviate diagnoses; read each as the condition "
        "it stands for. For EACH numbered pair below, answer yes only if the medicine is a "
        "standard, recognised treatment (or a standard protective / supportive therapy) for that "
        "condition; otherwise no.\n\n"
        + "\n".join(f"[{i}] {m} for {d}" for i, (m, d) in enumerate(pairs, start=1))
        + '\n\nExample answer: {"pairs": [{"pair": 1, "answer": "yes"}], "confidence": 0.8}'
    )


def match_diagnosis(answer: Any, diagnoses: list[str]) -> str | None:
    """The prescription diagnosis a model's "treats" answer names, or None when it names none of
    them (a model may not introduce a diagnosis the prescription does not state)."""
    if not isinstance(answer, str) or answer.strip().lower() in ("", "null", "none", "n/a"):
        return None
    a = answer.strip().lower()
    best, score = None, 0.0
    for d in diagnoses:
        dl = d.lower()
        s = 1.0 if a == dl else 0.9 if (a in dl or dl in a) else nearness(a, dl)
        if s > score:
            best, score = d, s
    return best if score >= 0.75 else None


def guard(line: Line, choice: dict[str, Any] | None, min_grounding: float) -> dict[str, Any]:
    """ADR-0003 rule 4, veto only: the suggestion a line gets from the model's choice. A choice
    outside the shortlist, or 0, is no suggestion. A chosen product whose name does not look like
    the line's own reading is vetoed. The model's quoted evidence counts only when it is its own
    reading of the page, not the option's name copied back."""
    if not choice or not isinstance(choice.get("choice"), int):
        return {"status": "none", "reason": "no answer for this line"}
    k = choice["choice"]
    if k < 1 or k > len(line.options):
        return {"status": "none", "reason": "none of the catalogue options is written here"}
    product = line.options[k - 1]
    brand = product["brand"]
    evidence = (choice.get("evidence") or "").strip()
    readings = [split_name(line.read_as)[0]]
    ev_key = split_name(evidence)[0]
    if ev_key and nearness(ev_key, brand) < 0.99:  # an independent reading, not a copy
        readings.append(ev_key)
    grounding = max(nearness(r, brand) for r in readings)
    conf = choice.get("confidence")
    conf = float(conf) if isinstance(conf, int | float) else 0.5
    out = {"product": _public(product), "evidence": evidence or None,
           "rationale": (choice.get("rationale") or "").strip() or None,
           "grounding": round(grounding, 3)}  # fmt: skip
    if grounding < min_grounding:
        return out | {
            "status": "vetoed",
            "confidence": round(min(conf, grounding), 3),
            "reason": "the chosen product does not look like what is written",
        }
    return out | {"status": "suggested", "confidence": round(min(conf, 0.5 + grounding / 2), 3)}


def _public(p: dict[str, Any]) -> dict[str, Any]:
    return {k: p.get(k) for k in ("id", "name", "manufacturer", "composition")}


def _contains(product: dict[str, Any]) -> str:
    """What a product contains, for the fit questions: its composition, else its generic name."""
    comp = (product.get("composition") or "").strip()
    return comp or re.sub(r"\s*\(generic\)$", "", product.get("name") or "")


# ------------------------------------------------------------------------------ the run


class _Trace:
    """Appends the run's steps to ``agent_trace`` (E12-S5), in order."""

    def __init__(self, document_id: str, run_id: Any) -> None:
        self.document_id, self.run_id, self.seq = document_id, run_id, 0

    def __call__(self, step: str, tool: str, inp: Any, out: Any, *, fact_id: str | None = None,
                 confidence: float | None = None, started: float | None = None,
                 ai_event_id: Any = None) -> None:  # fmt: skip
        self.seq += 1
        ms = int((time.perf_counter() - started) * 1000) if started is not None else None
        with session_scope() as c:
            c.execute(
                text("INSERT INTO agent_trace (document_id, run_id, ai_event_id, fact_id, seq, "
                     "agent, step, tool, input, output, confidence, duration_ms) VALUES (:d, :r, "
                     ":e, :f, :s, :a, :st, :t, CAST(:i AS jsonb), CAST(:o AS jsonb), :c, :ms)"),
                {"d": self.document_id, "r": self.run_id, "e": ai_event_id, "f": fact_id,
                 "s": self.seq, "a": AGENT, "st": step, "t": tool,
                 "i": json.dumps(inp, default=str), "o": json.dumps(out, default=str),
                 "c": confidence, "ms": ms},
            )  # fmt: skip


def _lines_by_page(conn: Connection, document_id: str) -> tuple[list[str], dict[Any, list[Line]]]:
    facts = [f for f in repo.get_facts_for_document(conn, document_id) if f.get("is_current")]
    meds = [f for f in facts if f["fact_type"] == "medication"]
    matches = catalogue.matches_for_facts(conn, [str(f["id"]) for f in meds])
    placed = []
    for i, f in enumerate(meds):
        prov = repo.get_provenance(conn, f["id"])
        page_id = next((p["page_id"] for p in prov if p.get("page_id")), None)
        blocks = [str(b) for p in prov for b in (p.get("ocr_block_ids") or [])]
        order = conn.execute(
            text("SELECT min(reading_order) FROM ocr_block WHERE id = ANY(CAST(:b AS uuid[]))"),
            {"b": blocks},
        ).scalar() if blocks else None  # fmt: skip
        placed.append((order if order is not None else 10**6 + i, page_id, f))
    pages: dict[Any, list[Line]] = {}
    # lines are numbered in the order they are written on the page, as the model will see them
    for _, page_id, f in sorted(placed, key=lambda x: x[0]):
        group = pages.setdefault(page_id, [])
        group.append(Line(
            n=len(group) + 1, fact_id=str(f["id"]),
            read_as=f.get("drug_text") or f.get("local_text") or "",
            strengths=catalogue.fact_strengths(f), form=catalogue.fact_form(f),
            catalogue=matches.get(str(f["id"])),
        ))  # fmt: skip
    return diagnoses_of(facts), pages


def _page_image(conn: Connection, document_id: str, page_id: Any) -> bytes | None:
    from cdi_adapter.storage import get_store

    pages = repo.get_pages(conn, document_id)
    page = next((p for p in pages if p["id"] == page_id), pages[0] if pages else None)
    if page is None:
        return None
    try:
        return get_store().get_bytes(page["storage_key"])
    except Exception as exc:  # noqa: BLE001 - the agent still has the first reading to go on
        log.warning("agent_page_image_missing", document_id=document_id, error=str(exc))
        return None


def run_medication_agent(document_id: UUID | str, *, client: MLClient | None = None,
                         actor: str = "agent") -> dict[str, Any]:  # fmt: skip
    """Run the agent on one document; returns the run's metrics. Advisory only: it writes an
    ``ai_event`` with one ``ai_suggestion`` per medication fact and its ``agent_trace``, and never
    changes a fact."""
    s = get_settings()
    did = str(document_id)
    if not s.agent_enabled:
        return {"skipped": "disabled"}
    with session_scope() as c:
        if catalogue.catalogue_size(c) == 0:
            return {"skipped": "no catalogue"}
        diagnoses, pages = _lines_by_page(c, did)
    if not pages:
        return {"skipped": "no medicines"}
    vlm = client or get_client(did)
    with session_scope() as c:
        run_id = repo.start_run(c, document_id=did, stage="agent", model_name=vlm.model,
                                model_version=vlm.version)  # fmt: skip
    trace = _Trace(did, run_id)
    try:
        out = [r for page_id, lines in pages.items()
               for r in _run_page(did, page_id, lines, vlm, trace, s.agent_max_options,
                                  s.agent_min_grounding)]  # fmt: skip
        _fit(out, diagnoses, vlm, trace)
        with session_scope() as c:
            event = _record(c, did, out, vlm, diagnoses)
            m = {"lines": len(out), "diagnoses": len(diagnoses),
                 "suggested": sum(r["status"] == "suggested" for r in out),
                 "vetoed": sum(r["status"] == "vetoed" for r in out),
                 "no_matching_diagnosis": sum(r["fits_diagnosis"] is False
                                           for r in out)}  # fmt: skip
            repo.finish_run(c, run_id, metrics=m)
            repo.write_audit(
                c,
                actor=actor,
                action="create",
                entity="ai_event",
                entity_id=event,
                detail={"document_id": did, "agent": AGENT, **m},
            )
        trace("suggest", "ai_suggestion", {"lines": len(out)}, m, ai_event_id=event)
        log.info("medication_agent_done", document_id=did, **m)
        return m
    except Exception as exc:
        with session_scope() as c:
            repo.fail_run(c, run_id, error=f"{type(exc).__name__}: {exc}")
        log.error("medication_agent_failed", document_id=did, error=str(exc))
        raise


def _run_page(did: str, page_id: Any, lines: list[Line], vlm: MLClient, trace: _Trace,
              max_options: int, min_grounding: float) -> list[dict[str, Any]]:  # fmt: skip
    # 1. shortlist: catalogue products for each line not identified exactly (a tool, no model)
    open_lines = [ln for ln in lines if not ln.settled]
    for ln in open_lines:
        t0 = time.perf_counter()
        with session_scope() as c:
            ln.options = catalogue.shortlist(c, names=[ln.read_as], generics=[],
                                             strengths=ln.strengths, form=ln.form,
                                             limit=max_options)  # fmt: skip
        trace("shortlist", "catalogue.shortlist",
              {"name": ln.read_as, "strengths": ln.strengths, "form": ln.form},
              [o["name"] for o in ln.options], fact_id=ln.fact_id, started=t0)  # fmt: skip
    # 2. choose: which shortlisted product is written on each line (the page image)
    choices: dict[int, dict[str, Any]] = {}
    asked = [ln for ln in open_lines if ln.options]
    if asked:
        with session_scope() as c:
            image = _page_image(c, did, page_id)
        t0 = time.perf_counter()
        chosen = vlm.vlm_json(image, choose_prompt(asked), CHOOSE_SCHEMA,
                              max_tokens=120 + 110 * len(asked))  # fmt: skip
        trace("choose", f"vlm:{CHOOSE_SCHEMA}",
              {"image": image is not None, "lines": [ln.n for ln in asked]}, chosen,
              started=t0, confidence=_conf(chosen))  # fmt: skip
        choices = {r["line"]: r for r in chosen.get("lines", [])
                   if isinstance(r, dict) and isinstance(r.get("line"), int)}  # fmt: skip
    # 3. guard, per line
    out = []
    for ln in lines:
        verdict: dict[str, Any]
        if ln.settled:
            m = ln.catalogue or {}
            verdict = {"status": "confirmed", "confidence": 1.0,
                       "product": {"id": m.get("catalogue_id"),
                                   "name": m.get("product_name") or m.get("display_name"),
                                   "manufacturer": m.get("manufacturer"),
                                   "composition": m.get("composition")},
                       "reason": "the catalogue identified this line exactly"}  # fmt: skip
        elif not ln.options:
            verdict = {"status": "none", "reason": "no catalogue product fits this line"}
        else:
            verdict = guard(ln, choices.get(ln.n), min_grounding)
            trace("guard", "guard", {"line": ln.n, "choice": choices.get(ln.n)}, verdict,
                  fact_id=ln.fact_id, confidence=verdict.get("confidence"))  # fmt: skip
        out.append({"fact_id": ln.fact_id, "line": ln.n, "read_as": ln.read_as, "agent": AGENT,
                    "options": [o["name"] for o in ln.options], "treats": None, "why": None,
                    "fits_diagnosis": None, **verdict})  # fmt: skip
    return out


def _fit(out: list[dict[str, Any]], diagnoses: list[str], vlm: MLClient, trace: _Trace) -> None:
    """Steps 4 and 5: the diagnosis each identified product treats, then a yes/no second opinion
    on each link. Sets ``treats`` / ``why`` / ``fits_diagnosis`` (None: nothing to check)."""
    items = [(i, r["product"]["name"], _contains(r["product"]))
             for i, r in enumerate(out, start=1)
             if r["status"] in ("confirmed", "suggested") and r.get("product")]  # fmt: skip
    if not items or not diagnoses:
        return
    t0 = time.perf_counter()
    fit = vlm.vlm_json(None, fit_prompt(diagnoses, items), FIT_SCHEMA,
                       max_tokens=100 + 60 * len(items))  # fmt: skip
    trace("fit", f"vlm:{FIT_SCHEMA}", {"diagnoses": diagnoses, "items": items}, fit,
          started=t0, confidence=_conf(fit))  # fmt: skip
    by_n = {r.get("line"): r for r in fit.get("lines", []) if isinstance(r, dict)}
    claims: list[tuple[int, str]] = []
    for n, _name, _what in items:
        r = out[n - 1]
        r["fits_diagnosis"] = False  # checked; a confirmed link below makes it True
        answer = by_n.get(n) or {}
        dx = match_diagnosis(answer.get("treats"), diagnoses)
        if dx:
            claims.append((n, dx))
            r["why"] = (answer.get("why") or "").strip() or None
    if not claims:
        return
    pairs = [(_contains(out[n - 1]["product"]), dx) for n, dx in claims]
    t0 = time.perf_counter()
    ver = vlm.vlm_json(None, verify_prompt(pairs), VERIFY_SCHEMA, max_tokens=60 + 25 * len(pairs))
    trace("verify", f"vlm:{VERIFY_SCHEMA}", {"pairs": pairs}, ver, started=t0,
          confidence=_conf(ver))  # fmt: skip
    yes = {p.get("pair") for p in ver.get("pairs", []) if isinstance(p, dict)
           and str(p.get("answer", "")).strip().lower() == "yes"}  # fmt: skip
    for i, (n, dx) in enumerate(claims, start=1):
        r = out[n - 1]
        if i in yes:
            r["treats"], r["fits_diagnosis"] = dx, True
        else:
            r["why"] = None  # the second opinion did not confirm the link


def _conf(payload: dict[str, Any]) -> float | None:
    c = payload.get("confidence")
    return None if is_partial(payload) or not isinstance(c, int | float) else float(c)


def _record(conn: Connection, did: str, out: list[dict[str, Any]], vlm: MLClient,
            diagnoses: list[str]) -> Any:  # fmt: skip
    item = conn.execute(text("SELECT id FROM rv_item WHERE source_document_id = :d "
                             "ORDER BY created_at DESC LIMIT 1"), {"d": did}).scalar()  # fmt: skip
    event = conn.execute(
        text("INSERT INTO ai_event (rv_item_id, source_document_id, kind, model_stack) "
             "VALUES (:i, :d, :k, CAST(:m AS jsonb)) RETURNING id"),
        {"i": item, "d": did, "k": EVENT_KIND,
         "m": json.dumps({"agent": AGENT, "model": vlm.model, "model_version": vlm.version,
                          "diagnoses": diagnoses})},
    ).scalar_one()  # fmt: skip
    for r in out:
        conn.execute(
            text("INSERT INTO ai_suggestion (ai_event_id, fact_id, element_type, suggested, "
                 "confidence) VALUES (:e, :f, 'medication', CAST(:s AS jsonb), :c)"),
            {"e": event, "f": r["fact_id"], "s": json.dumps(r, default=str),
             "c": r.get("confidence")},
        )  # fmt: skip
    return event


# ------------------------------------------------------------------------------ reading back


def latest_for_document(conn: Connection, document_id: UUID | str) -> dict[str, Any]:
    """The agent's state and newest suggestions for a document: ``run`` (status / started /
    finished / metrics of the latest agent run, or None) and ``by_fact`` (fact id -> suggestion)."""
    run = conn.execute(
        text("SELECT status, started_at, finished_at, metrics, error FROM pipeline_run "
             "WHERE document_id = :d AND stage = 'agent' ORDER BY started_at DESC LIMIT 1"),
        {"d": str(document_id)},
    ).mappings().first()  # fmt: skip
    rows = conn.execute(
        text("SELECT s.fact_id, s.suggested, s.confidence FROM ai_suggestion s "
             "WHERE s.ai_event_id = (SELECT id FROM ai_event WHERE source_document_id = :d "
             "  AND kind = :k ORDER BY created_at DESC LIMIT 1)"),
        {"d": str(document_id), "k": EVENT_KIND},
    ).mappings()  # fmt: skip
    by_fact = {str(r["fact_id"]): r["suggested"] for r in rows if r["fact_id"]}
    return {"run": json.loads(json.dumps(dict(run), default=str)) if run else None,
            "by_fact": by_fact}  # fmt: skip
