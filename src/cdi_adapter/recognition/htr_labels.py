"""The labelling kit: real handwritten lines, with what a person says they read (RD-S2 AC4).

The handwriting benchmark (``htr_bench.py``) needs 500-1,000 real English handwritten lines, each
with its true text, and none exist. This makes them, in two steps with a person in between:

1. :func:`cut` - from prescriptions the owner chooses, cut every handwritten line (IM-S3) into
   its own picture and write a kit: ``crops/``, an empty ``labels.csv`` and a ``label.html``
   page to type into. Printed lines are left out. So is any line the print reader could read as
   carrying a person (:func:`identity_reason`).
2. a person types what each line says - or ticks *skip* for a line with a name, a phone number
   or an ID on it, or one nobody can read;
3. :func:`build` - turn the typed sheet into the benchmark's manifest. A line is kept only if
   it was typed, was not skipped, and its typed text carries no identity either.

**De-identified by leaving out, not by blacking out.** A line that names or numbers a person is
dropped whole, twice over (before and after typing). Nothing about where a line came from is
kept: a crop is named by the fingerprint of its pixels, never by its file - file names carry
patients' names. The kit holds real patients' handwriting, so it is refused a place inside the
repository.
"""

from __future__ import annotations

import csv
import html
import json
import mimetypes
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cdi_adapter.recognition import regions

FIELDS = ("id", "image", "text", "skip", "note")
TARGET = (500, 1000)  # the story's range of lines for a run that may decide something
REPO = Path(__file__).resolve().parents[3]

# What marks a line as being about a person rather than about a medicine. Deliberately wide:
# a clinical line wrongly left out costs one line; an identity line let through is a leak.
_LABELS = re.compile(
    r"\b(name|patient|pt|age|sex|gender|dob|d\.o\.b|mob|mobile|phone|ph|tel|contact|address|addr"
    r"|s/o|d/o|w/o|c/o|uhid|mrd|mrn|opd|ipd|reg|regn|registration|abha|aadhaar|aadhar|id|email"
    r"|mr|mrs|ms|miss|master|smt|shri|sri|kumari|baby)\b\.?\s*(no\b\.?)?\s*[:\-.#]?",
    re.I,
)
_LONG_NUMBER = re.compile(r"(?<!\d)(?:\d[\s-]?){8,}(?!\d)")  # a phone, an Aadhaar, an ID
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_AGE_SEX = re.compile(r"\b\d{1,3}\s*(y|yr|yrs|years?)\b\s*/?\s*[mf]\b|\b\d{1,3}\s*/\s*[mf]\b", re.I)


def identity_reason(text: str | None) -> str | None:
    """Why a line is left out as being about a person, or ``None`` if nothing on it says so."""
    line = " ".join(str(text or "").split())
    if not line:
        return None
    if _EMAIL.search(line):
        return "an email address"
    if _LONG_NUMBER.search(line):
        return "a long number (a phone or an ID)"
    if _AGE_SEX.search(line):
        return "an age and sex"
    found = _LABELS.search(line)
    if found:
        return f"the word {found.group(1)!r}"
    return None


def model_reason(text: str) -> str | None:
    """The project's own de-identifier (``deid/engine.py``) as a second screen on typed text,
    where it can be loaded. ``None`` when it finds nothing - or is not installed here."""
    try:
        from cdi_adapter.deid import engine

        spans = [s for s in engine.detect(text) if not engine.clinical_false_positive(text, s)]
    except Exception:  # noqa: BLE001 - the pattern screen above has already run
        return None
    return f"the de-identifier marked it ({spans[0].entity})" if spans else None


def inside_repository(path: Path) -> bool:
    try:
        path.resolve().relative_to(REPO)
    except ValueError:
        return False
    return True


# ---------------------------------------------------------------- 1. cut


@dataclass
class CutSummary:
    pages: int = 0
    lines_found: int = 0
    printed_left_out: int = 0
    identity_left_out: int = 0
    already_in_kit: int = 0
    written: int = 0
    unreadable_files: int = 0


def _find_lines(png: bytes) -> list[tuple[regions.Region, str]]:
    """Every line of a page with its crop, and the print reader's reading of it (used only to
    screen the line; it is not kept)."""
    from cdi_adapter.ocr import rapid
    from cdi_adapter.ocr.service import region_index

    printed = rapid.run_rapidocr(png)
    found = regions.detect_regions(png, ocr=printed, crops=True)
    on = region_index(printed, found)
    return [(r, " ".join(ln.text for ln, where in zip(printed, on, strict=True) if where == i))
            for i, r in enumerate(found)]  # fmt: skip


def _pages(path: Path) -> list[bytes]:
    from cdi_adapter.ingest import pages

    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return pages.render_pages(path.read_bytes(), mime)


def read_labels(kit: Path) -> list[dict[str, str]]:
    sheet = kit / "labels.csv"
    if not sheet.exists():
        return []
    with sheet.open(encoding="utf-8-sig", newline="") as f:
        return [{k: (row.get(k) or "").strip() for k in FIELDS} for row in csv.DictReader(f)]


def write_labels(kit: Path, rows: Iterable[dict[str, str]]) -> None:
    with (kit / "labels.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def cut(files: Sequence[Path], kit: Path, *,
        find_lines: Callable[[bytes], list[tuple[regions.Region, str]]] = _find_lines,
        pages_of: Callable[[Path], list[bytes]] = _pages) -> CutSummary:  # fmt: skip
    """Cut the handwritten lines of ``files`` into ``kit``. Running it again with more files
    adds their lines and keeps everything already typed."""
    if inside_repository(kit):
        raise ValueError("the kit holds real patients' handwriting: keep it outside the repository")
    (kit / "crops").mkdir(parents=True, exist_ok=True)
    rows = read_labels(kit)
    have = {row["id"] for row in rows}
    out = CutSummary()
    for path in files:
        try:
            pngs = pages_of(path)
        except Exception:  # noqa: BLE001 - one bad file must not stop the rest
            out.unreadable_files += 1
            continue
        for png in pngs:
            out.pages += 1
            for region, seen in find_lines(png):
                out.lines_found += 1
                if "handwriting" not in region.readers or region.crop is None:
                    out.printed_left_out += 1
                elif identity_reason(seen):
                    out.identity_left_out += 1
                elif (name := region.crop.sha256[:16]) in have:
                    out.already_in_kit += 1
                else:
                    (kit / "crops" / f"{name}.png").write_bytes(region.crop.png)
                    rows.append({"id": name, "image": f"crops/{name}.png", "text": "",
                                 "skip": "", "note": ""})  # fmt: skip
                    have.add(name)
                    out.written += 1
    write_labels(kit, rows)
    (kit / "label.html").write_text(label_page(rows), encoding="utf-8", newline="\n")
    return out


# ---------------------------------------------------------------- 2. the page a person types in

_PAGE_SCRIPT = """
const rows=JSON.parse(document.getElementById('rows').textContent),KEY='htr-labels';
let saved={};try{saved=JSON.parse(localStorage.getItem(KEY)||'{}')}catch(e){}
const list=document.getElementById('list');
function keep(){try{localStorage.setItem(KEY,JSON.stringify(saved))}catch(e){};count()}
function count(){const done=rows.filter(r=>(saved[r.id]||{}).text||(saved[r.id]||{}).skip).length;
document.getElementById('done').textContent=done+' of '+rows.length+' lines done'}
rows.forEach(r=>{const s=saved[r.id]=saved[r.id]||{text:r.text,skip:r.skip==='yes',note:r.note};
const box=document.createElement('div');box.className='line';
const im=document.createElement('img');im.src=r.image;im.alt='line '+r.id;im.loading='lazy';
const t=document.createElement('input');t.type='text';t.value=s.text||'';
t.placeholder='type exactly what is written on this line';t.autocomplete='off';t.spellcheck=false;
t.oninput=()=>{s.text=t.value;keep()};
const lab=document.createElement('label');const c=document.createElement('input');c.type='checkbox';
c.checked=!!s.skip;c.onchange=()=>{s.skip=c.checked;keep()};
lab.append(c,' skip: it has a name, phone or ID on it, or cannot be read');
box.append(im,t,lab);list.append(box)});
function cell(v){v=String(v==null?'':v);return /[",\\n]/.test(v)?'"'+v.replace(/"/g,'""')+'"':v}
document.getElementById('save').onclick=()=>{
const out=['id,image,text,skip,note'].concat(rows.map(r=>{const s=saved[r.id]||{};
return [r.id,r.image,s.text||'',s.skip?'yes':'',s.note||''].map(cell).join(',')}))
.join('\\n')+'\\n';
const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([out],{type:'text/csv'}));
a.download='labels.csv';a.click()};
count();
"""


def label_page(rows: Sequence[dict[str, str]]) -> str:
    """One page, no server: each crop with a box to type in. What is typed stays in the browser
    until *Save labels.csv* is pressed; the saved file replaces the kit's ``labels.csv``."""
    data = json.dumps([dict(r) for r in rows]).replace("</", "<\\/")
    return (
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        '<meta name=viewport content="width=device-width,initial-scale=1">'
        "<title>Type what each line says</title><style>"
        "body{font:16px/1.5 system-ui,sans-serif;margin:0 auto;max-width:900px;padding:16px}"
        ".line{border:1px solid #dbe4f5;border-radius:10px;padding:12px;margin:12px 0}"
        ".line img{max-width:100%;display:block;margin-bottom:8px;background:#fff}"
        ".line input[type=text]{width:100%;font:inherit;padding:8px;box-sizing:border-box}"
        ".line label{display:block;margin-top:6px;color:#5b6b88;font-size:14px}"
        "header{position:sticky;top:0;background:#fff;padding:8px 0;"
        "border-bottom:1px solid #dbe4f5}"
        "button{font:inherit;padding:8px 14px}</style></head><body><header>"
        "<b>Type exactly what is written on each line</b> &mdash; same spelling, same numbers, "
        "nothing corrected. Tick <i>skip</i> for a line with a person's name, phone or ID on it, "
        "or one you cannot read. <span id=done></span> "
        "<button id=save>Save labels.csv</button></header>"
        f"<p>{html.escape(str(len(rows)))} lines. Put the saved file in this folder, in place of "
        "the labels.csv that is here.</p><div id=list></div>"
        f'<script id=rows type="application/json">{data}</script>'
        f"<script>{_PAGE_SCRIPT}</script></body></html>"
    )


# ---------------------------------------------------------------- 3. build


@dataclass
class BuildSummary:
    lines: int = 0
    kept: int = 0
    not_typed: int = 0
    skipped: int = 0
    identity_left_out: int = 0
    crop_missing: int = 0
    still_needed: int = 0
    enough: bool = False
    model_screen: bool = False


def _yes(value: str) -> bool:
    return value.strip().lower() in ("yes", "y", "1", "true", "x", "skip")


def build(kit: Path, out: Path, *, screen: Callable[[str], str | None] = model_reason
          ) -> BuildSummary:  # fmt: skip
    """Write the benchmark's manifest from the typed sheet: ``out/lines.jsonl`` and a copy of
    each kept crop. Only what passes is copied, so ``out`` holds no line about a person."""
    if inside_repository(out):
        raise ValueError(
            "the lines are real patients' handwriting: keep them outside the repository"
        )
    summary = BuildSummary()
    summary.model_screen = True
    if screen is model_reason:  # is the project's de-identifier there to be the second screen?
        try:
            from cdi_adapter.deid import engine

            engine.detect("Tab Paracetamol 500 mg")  # it must actually run, not just load
        except Exception:  # noqa: BLE001 - recorded: the summary says which screens ran
            summary.model_screen = False
    kept: list[dict[str, Any]] = []
    for row in read_labels(kit):
        summary.lines += 1
        typed = " ".join(row["text"].split())
        crop = kit / row["image"]
        if _yes(row["skip"]):
            summary.skipped += 1
        elif not typed:
            summary.not_typed += 1
        elif identity_reason(typed) or screen(typed):
            summary.identity_left_out += 1
        elif not crop.is_file():
            summary.crop_missing += 1
        else:
            kept.append({"id": row["id"], "image": row["image"], "text": typed, "png": crop})
    (out / "crops").mkdir(parents=True, exist_ok=True)
    with open(out / "lines.jsonl", "w", encoding="utf-8", newline="\n") as f:
        for item in kept:
            (out / item["image"]).write_bytes(item.pop("png").read_bytes())
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    summary.kept = len(kept)
    summary.still_needed = max(0, TARGET[0] - len(kept))
    summary.enough = len(kept) >= TARGET[0]
    return summary
