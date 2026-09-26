"""Medicine catalogue: import, search and identification (owner request 2026-09-26).

The Tata 1mg catalogue is the master list medicines are identified against. It is a layer AFTER
extraction and S5 terminology: the extracted text is never changed; each medication fact gets a
``medication_catalogue_match`` row holding the product it was identified as, or UNKNOWN_MEDICATION.

* ``import_csv(path)`` loads a catalogue file (one product per row) into ``med_catalogue`` with its
  brand key (``normalize.split_name``) and records the load in ``med_catalogue_import`` (sha256).
  The file is a snapshot supplied under the owner's agreement with the source (docs/medicine-
  catalogue.md); nothing is fetched from the web at run time.
* ``identify(conn, text, strengths)``: exact brand key -> ``pg_trgm`` candidates re-scored with
  ``normalize.score`` -> accepted only when the best brand clears ``catalogue_min_score`` AND leads
  every other brand by ``catalogue_min_margin``; otherwise UNKNOWN_MEDICATION with the closest
  products as candidates. Among a brand's products, a stated strength picks the pack.
* ``match_document_medications(conn, document_id)`` runs it for every current medication fact.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, text

from cdi_adapter import repo
from cdi_adapter.catalogue.normalize import (
    MIN_KEY_LETTERS,
    best_brand,
    dosage_form,
    fold,
    ingredients,
    letters,
    query_keys,
    score,
    split_name,
    strength_agrees,
)
from cdi_adapter.config import get_settings
from cdi_adapter.logging import get_logger

log = get_logger(__name__)

UNKNOWN = "UNKNOWN_MEDICATION"
CANDIDATES_KEPT = 5
_TRGM_THRESHOLD = 0.2  # candidate recall only; acceptance is normalize.score + the margin rule
_TRGM_LIMIT = 40
_BATCH = 2000

# header names of a 1mg-style catalogue export (and the widely used community snapshot of it),
# lower-cased; the first header present wins
COLUMNS: dict[str, tuple[str, ...]] = {
    "source_id": ("id", "sku_id", "product_id", "sku"),
    "name": ("name", "product_name", "medicine_name", "sku_name"),
    "manufacturer": ("manufacturer_name", "manufacturer", "marketer", "company"),
    "dosage_form": ("type", "dosage_form", "form", "product_form"),
    "pack_size_label": ("pack_size_label", "pack_size", "packaging", "pack"),
    "composition": ("composition", "salt_composition", "short_composition1", "salt"),
    "composition2": ("short_composition2",),
    "price": ("price(₹)", "price", "mrp"),
    "discontinued": ("is_discontinued", "discontinued"),
}


@dataclass
class Identification:
    status: str  # matched | unknown
    display_name: str
    catalogue_id: int | None = None
    score: float | None = None
    method: str | None = None  # exact | fuzzy | generic | generic_fuzzy
    strength_match: bool | None = None
    product: dict[str, Any] | None = None
    candidates: list[dict[str, Any]] = field(default_factory=list)
    query: str = ""

    def as_json(self) -> dict[str, Any]:
        return json.loads(json.dumps(asdict(self), default=str))


# ------------------------------------------------------------------------------ import


def _pick(row: dict[str, str], key: str) -> str:
    for h in COLUMNS[key]:
        v = row.get(h)
        if v not in (None, ""):
            return v.strip()
    return ""


def _price(v: str) -> float | None:
    try:
        return float(v.replace(",", "").replace("₹", "").strip()) if v else None
    except ValueError:
        return None


def import_csv(conn: Connection, path: Path, *, source: str = "tata1mg",
               actor: str = "system") -> dict[str, Any]:  # fmt: skip
    """Load (or reload) a catalogue snapshot: the source's rows are replaced by the file's in one
    transaction; existing matches keep their display name and lose only a removed product's id."""
    raw = path.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
    reader.fieldnames = [(h or "").strip().lower() for h in (reader.fieldnames or [])]
    if not any(h in reader.fieldnames for h in COLUMNS["name"]):
        raise ValueError(f"{path.name}: no product-name column (one of {COLUMNS['name']})")
    import_id = conn.execute(
        text("INSERT INTO med_catalogue_import (source, file_name, sha256, rows_loaded, "
             "imported_by) VALUES (:s, :f, :h, 0, :a) RETURNING id"),
        {"s": source, "f": path.name, "h": sha, "a": actor},
    ).scalar_one()  # fmt: skip
    conn.execute(text("UPDATE medication_catalogue_match SET catalogue_id = NULL WHERE "
                      "catalogue_id IN (SELECT id FROM med_catalogue WHERE source = :s)"),
                 {"s": source})  # fmt: skip
    conn.execute(text("DELETE FROM med_catalogue WHERE source = :s"), {"s": source})
    batch: list[dict[str, Any]] = []
    loaded, seen = 0, set()
    for n, row in enumerate(reader, start=1):
        name = _pick(row, "name")
        if not name:
            continue
        sid = _pick(row, "source_id") or f"row-{n}"
        if sid in seen:
            continue
        seen.add(sid)
        brand, strengths = split_name(name)
        if not brand:
            continue
        comp = " + ".join(c for c in (_pick(row, "composition"), _pick(row, "composition2")) if c)
        batch.append({
            "source": source, "sid": sid, "imp": import_id, "name": name, "brand": brand,
            "strengths": list(strengths), "mfr": _pick(row, "manufacturer") or None,
            "form": _pick(row, "dosage_form") or None,
            "pack": _pick(row, "pack_size_label") or None, "comp": comp or None,
            "price": _price(_pick(row, "price")),
            "disc": _pick(row, "discontinued").lower() in ("true", "1", "yes", "y"),
        })  # fmt: skip
        if len(batch) >= _BATCH:
            loaded += _insert(conn, batch)
            batch = []
    loaded += _insert(conn, batch)
    conn.execute(text("UPDATE med_catalogue_import SET rows_loaded = :n WHERE id = :i"),
                 {"n": loaded, "i": import_id})  # fmt: skip
    log.info("catalogue_imported", source=source, file=path.name, sha256=sha, rows=loaded)
    return {"import_id": import_id, "source": source, "rows": loaded, "sha256": sha}


def _insert(conn: Connection, rows: list[dict[str, Any]]) -> int:
    if rows:
        conn.execute(
            text("INSERT INTO med_catalogue (source, source_id, import_id, name, brand_norm, "
                 "strengths, manufacturer, dosage_form, pack_size_label, composition, price, "
                 "discontinued) VALUES (:source, :sid, :imp, :name, :brand, "
                 "CAST(:strengths AS numeric[]), :mfr, :form, :pack, :comp, :price, :disc)"),
            [{**r, "strengths": "{" + ",".join(str(s) for s in r["strengths"]) + "}"}
             for r in rows],
        )  # fmt: skip
    return len(rows)


def catalogue_size(conn: Connection) -> int:
    return conn.execute(text("SELECT count(*) FROM med_catalogue")).scalar_one()


def latest_import(conn: Connection) -> dict[str, Any] | None:
    row = conn.execute(text("SELECT * FROM med_catalogue_import ORDER BY id DESC LIMIT 1")
                       ).mappings().first()  # fmt: skip
    return dict(row) if row else None


# ------------------------------------------------------------------------------ identify


def _product(r: Any) -> dict[str, Any]:
    return {"id": r["id"], "name": r["name"], "manufacturer": r["manufacturer"],
            "dosage_form": r["dosage_form"], "pack_size_label": r["pack_size_label"],
            "composition": r["composition"], "price": r["price"],
            "discontinued": r["discontinued"], "source": r["source"],
            "source_id": r["source_id"],
            "strengths": [float(s) for s in r["strengths"]]}  # fmt: skip


def _form_agrees(form: str | None, product: dict[str, Any]) -> bool:
    """False only when the line states a form and the product states a different one."""
    pf = dosage_form(product["name"])
    return form is None or pf is None or pf == form


def _pick_pack(
    rows: list[dict[str, Any]], strengths: tuple[float, ...], form: str | None = None
) -> tuple[dict[str, Any], bool | None]:
    """The brand's product for this line: the written dosage form (Cap. / Tab.), then exactly the
    stated strengths (Glycomet-GP 1, not 1/850), then any product sharing one, then a product
    still on the market, then the catalogue's first."""

    def key(p: dict[str, Any]) -> tuple[int, int, int, int]:
        agrees = strength_agrees(strengths, p["strengths"])
        rank = 0 if agrees and set(strengths) == set(p["strengths"]) else 1 if agrees else 2
        return (0 if _form_agrees(form, p) else 1, rank, 1 if p["discontinued"] else 0, p["id"])

    best = sorted(rows, key=key)[0]
    return best, strength_agrees(strengths, best["strengths"])


_generic_cache: tuple[int | None, dict[str, str]] = (None, {})


def _generics(conn: Connection) -> dict[str, str]:
    """generic key -> generic name, from every product's composition; rebuilt per import."""
    global _generic_cache
    imp = latest_import(conn)
    iid = imp["id"] if imp else None
    if iid is None or _generic_cache[0] != iid:
        found: dict[str, str] = {}
        for comp in conn.execute(text("SELECT DISTINCT composition FROM med_catalogue "
                                      "WHERE composition IS NOT NULL")).scalars():  # fmt: skip
            for key, name in ingredients(comp):
                found.setdefault(key, name)
        _generic_cache = (iid, found)
    return _generic_cache[1]


def _generic(key: str, name: str, sc: float, method: str,
             candidates: list[dict[str, Any]] | None = None) -> Identification:  # fmt: skip
    """A line written by its generic (salt) name: identified as that generic, never as one
    manufacturer's product ("Amlodipine" is not "S Amlodipine Tablet")."""
    return Identification("matched", f"{name} (generic)", None, sc, method, None,
                          {"generic": name, "composition": name}, candidates or [],
                          key)  # fmt: skip


def identify(conn: Connection, drug_text: str | None, strengths: tuple[float, ...] = (),
             form: str | None = None) -> Identification:  # fmt: skip
    """The catalogue's identification of a written medicine name: tried as written, then without
    a leading prescription marker ("Ad. Sompraz"); the first match wins. Failing that, a generic
    written in brackets beside an unknown brand ("Nofza (Tofacitinib)") - only when it is exactly
    a catalogue generic. Else the as-written UNKNOWN_MEDICATION with its closest candidates."""
    form = form or dosage_form(drug_text)
    first: Identification | None = None
    for brand, written in query_keys(drug_text):
        ident = _identify_key(conn, brand, strengths or written, form)
        if ident.status == "matched":
            return ident
        first = first or ident
    generics = _generics(conn)
    for inner in re.findall(r"\(([^()]*)\)", drug_text or ""):
        key = split_name(inner)[0]
        if letters(key) >= MIN_KEY_LETTERS and key in generics:
            return _generic(key, generics[key], 1.0, "generic", first.candidates if first else [])
    return first or Identification("unknown", UNKNOWN)


def _identify_key(conn: Connection, brand: str, strengths: tuple[float, ...],
                  form: str | None = None) -> Identification:  # fmt: skip
    s = get_settings()
    if letters(brand) < MIN_KEY_LETTERS:  # "T", "CAP": not a medicine name
        return Identification("unknown", UNKNOWN, query=brand)
    generics = _generics(conn)
    if brand in generics:
        return _generic(brand, generics[brand], 1.0, "generic")
    exact = [_product(r) for r in conn.execute(
        text("SELECT * FROM med_catalogue WHERE brand_norm = :b ORDER BY id"), {"b": brand}
    ).mappings()]  # fmt: skip
    if exact:
        p, agrees = _pick_pack(exact, strengths, form)
        return Identification("matched", p["name"], p["id"], 1.0, "exact", agrees, p, query=brand)

    conn.execute(text("SELECT set_config('pg_trgm.similarity_threshold', :t, true)"),
                 {"t": str(_TRGM_THRESHOLD)})  # fmt: skip
    rows = [_product(r) | {"brand": r["brand_norm"]} for r in conn.execute(
        text("SELECT * FROM med_catalogue WHERE brand_norm % :b "
             "ORDER BY similarity(brand_norm, :b) DESC, id LIMIT :n"),
        {"b": brand, "n": _TRGM_LIMIT},
    ).mappings()]  # fmt: skip
    scored = sorted(((p, score(brand, p["brand"])) for p in rows), key=lambda x: -x[1])
    # a misread generic name ("Metfornin") competes with the brands under the same rules
    lead = fold(brand)[:1]
    gen_scored = [(g, score(brand, g)) for g in generics
                  if abs(len(g) - len(brand)) <= 2 and fold(g)[:1] == lead]  # fmt: skip
    # a product named after its generic ("Metformin 500mg Tablet") is that generic here
    pool = [
        (
            {
                "id": p["id"],
                "name": p["name"],
                "brand": p["brand"],
                "manufacturer": p["manufacturer"],
            },
            sc,
        )
        for p, sc in scored
        if p["brand"] not in generics
    ]
    pool += [({"id": None, "name": f"{generics[g]} (generic)", "brand": "generic:" + g,
               "manufacturer": None}, sc) for g, sc in gen_scored if sc > 0]  # fmt: skip
    pool.sort(key=lambda x: -x[1])
    # what the reviewer is shown: products of the written form first ("Cap." -> capsules)
    shown = sorted(pool, key=lambda x: (not _form_agrees(form, x[0]), -x[1]))
    candidates: list[dict[str, Any]] = []
    for c, sc in shown:
        if len(candidates) < CANDIDATES_KEPT and c["brand"] not in {x["brand"] for x in candidates}:
            candidates.append(c | {"score": sc})
    top, top_score, runner = best_brand((c["brand"], sc) for c, sc in pool)
    reliable = top_score >= s.catalogue_min_score and top_score - runner >= s.catalogue_min_margin
    if top.startswith("generic:") and reliable:
        g = top.removeprefix("generic:")
        return _generic(g, generics[g], top_score, "generic_fuzzy", candidates)
    if top and reliable:
        products = [_product(r) for r in conn.execute(
            text("SELECT * FROM med_catalogue WHERE brand_norm = :b ORDER BY id"), {"b": top}
        ).mappings()]  # fmt: skip
        p, agrees = _pick_pack(products, strengths, form)
        if not _form_agrees(form, p):  # "Cap. Sonpraz": Sompraz is only a tablet / injection
            return Identification("unknown", UNKNOWN, score=top_score, method="fuzzy",
                                  candidates=candidates, query=brand)  # fmt: skip
        if agrees is False and s.catalogue_strength_must_agree:
            return Identification("unknown", UNKNOWN, score=top_score, method="fuzzy",
                                  strength_match=False, candidates=candidates,
                                  query=brand)  # fmt: skip
        return Identification("matched", p["name"], p["id"], top_score, "fuzzy", agrees, p,
                              candidates, brand)  # fmt: skip
    return Identification("unknown", UNKNOWN, score=top_score or None, method="fuzzy",
                          candidates=candidates, query=brand)  # fmt: skip


# ------------------------------------------------------------------------------ pipeline


def save_identification(conn: Connection, fact_id: UUID | str, ident: Identification,
                        import_id: int | None) -> None:  # fmt: skip
    conn.execute(
        text("INSERT INTO medication_catalogue_match (fact_id, status, catalogue_id, "
             "display_name, score, method, strength_match, query, candidates, import_id) "
             "VALUES (:f, :st, :c, :n, :sc, :m, :sm, :q, CAST(:cand AS jsonb), :imp) "
             "ON CONFLICT (fact_id) DO UPDATE SET status = EXCLUDED.status, "
             "catalogue_id = EXCLUDED.catalogue_id, display_name = EXCLUDED.display_name, "
             "score = EXCLUDED.score, method = EXCLUDED.method, "
             "strength_match = EXCLUDED.strength_match, query = EXCLUDED.query, "
             "candidates = EXCLUDED.candidates, import_id = EXCLUDED.import_id, "
             "matched_at = now()"),
        {"f": str(fact_id), "st": ident.status, "c": ident.catalogue_id, "n": ident.display_name,
         "sc": ident.score, "m": ident.method, "sm": ident.strength_match, "q": ident.query,
         "cand": json.dumps(ident.candidates, default=str), "imp": import_id},
    )  # fmt: skip


def match_document_medications(conn: Connection, document_id: UUID | str) -> dict[str, int]:
    """Identify every current medication fact of a document against the catalogue. A no-op when
    the layer is off or the catalogue is empty (nothing recorded, nothing changed)."""
    if not get_settings().catalogue_enabled or catalogue_size(conn) == 0:
        return {}
    imp = latest_import(conn)
    counts = {"catalogue_matched": 0, "catalogue_unknown": 0}
    for f in repo.get_facts_for_document(conn, document_id):
        if f["fact_type"] != "medication" or not f.get("is_current"):
            continue
        strengths = (float(f["strength_num"]),) if f.get("strength_num") is not None else ()
        # the form as written on the line ("Cap. Sonpraz"), which drug_text may have dropped
        form = dosage_form(f.get("local_text")) or dosage_form(f.get("form"))
        ident = identify(conn, f.get("drug_text") or f.get("local_text"), strengths, form)
        save_identification(conn, f["id"], ident, imp["id"] if imp else None)
        counts["catalogue_matched" if ident.status == "matched" else "catalogue_unknown"] += 1
    return counts


def matches_for_facts(conn: Connection, fact_ids: list[str]) -> dict[str, dict[str, Any]]:
    """fact id -> its catalogue identification, with the product's catalogue details."""
    if not fact_ids:
        return {}
    rows = conn.execute(
        text("SELECT m.*, c.name AS product_name, c.manufacturer, c.dosage_form, "
             "c.pack_size_label, c.composition, c.price, c.discontinued "
             "FROM medication_catalogue_match m LEFT JOIN med_catalogue c ON c.id = m.catalogue_id "
             "WHERE CAST(m.fact_id AS text) = ANY(:ids)"), {"ids": fact_ids}
    ).mappings()  # fmt: skip
    return {str(r["fact_id"]): json.loads(json.dumps(dict(r), default=str)) for r in rows}
