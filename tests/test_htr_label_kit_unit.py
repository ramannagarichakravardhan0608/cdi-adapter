"""The labelling kit (RD-S2 AC4): real handwritten lines, with what a person says they read.

The pages here are stand-ins: lines are handed to the kit already found and labelled, so the
tests cover what the kit does with them - which lines it cuts, which it leaves out as being
about a person, what a typed sheet becomes, and that nothing of it may live in the repository.
One test runs the real line finder on a typeset page. No real handwriting is used.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from cdi_adapter.recognition import htr_bench, htr_labels, regions

ROOT = Path(__file__).resolve().parents[1]


def _region(label: str, tag: str) -> regions.Region:
    png = b"\x89PNG-" + tag.encode()
    crop = regions.Crop(png=png, sha256=hashlib.sha256(png).hexdigest(), bbox=(0, 0, 400, 30),
                        padded_bbox=(0, 0, 400, 30), pad_px=4, scale=1.0, source_size=(400, 30),
                        size=(400, 30))  # fmt: skip
    return regions.Region(bbox=(0, 0, 400, 30), label=label, reading_order=0, crop=crop)


PAGE = [(_region("printed", "header"), "City Care Clinic"),
        (_region("handwritten", "name"), "Name: Ravi Kumar"),
        (_region("handwritten", "rx1"), "T4b Paracetmol"),
        (_region("mixed", "rx2"), "Cap Omez 20 mg"),
        (_region("handwritten", "phone"), "98765 43210")]  # fmt: skip


def _cut(kit: Path, page: list[Any] = PAGE, files: int = 1) -> htr_labels.CutSummary:
    return htr_labels.cut([Path(f"scan-{n}.jpg") for n in range(files)], kit,
                          find_lines=lambda png: page, pages_of=lambda path: [b"page"])  # fmt: skip


def _type(kit: Path, texts: dict[int, str], skip: tuple[int, ...] = ()) -> list[dict[str, str]]:
    rows = htr_labels.read_labels(kit)
    for n, row in enumerate(rows):
        row["text"], row["skip"] = texts.get(n, ""), "yes" if n in skip else ""
    htr_labels.write_labels(kit, rows)
    return rows


# ------------------------------------------------------------------ 1. cut


def test_only_handwritten_lines_are_cut_and_lines_about_a_person_are_left_out(tmp_path) -> None:
    done = _cut(tmp_path)
    assert (done.lines_found, done.printed_left_out, done.identity_left_out, done.written) == (
        5, 1, 2, 2)  # fmt: skip
    rows = htr_labels.read_labels(tmp_path)
    assert len(rows) == 2 and all(r["text"] == "" and r["skip"] == "" for r in rows)
    crops = sorted(p.name for p in (tmp_path / "crops").iterdir())
    assert crops == sorted(Path(r["image"]).name for r in rows)
    assert (tmp_path / rows[0]["image"]).read_bytes() == PAGE[2][0].crop.png  # type: ignore[union-attr]


def test_a_crop_is_named_by_its_pixels_and_nothing_says_where_it_came_from(tmp_path) -> None:
    _cut(tmp_path)
    rows = htr_labels.read_labels(tmp_path)
    assert rows[0]["id"] == PAGE[2][0].crop.sha256[:16]  # type: ignore[union-attr]
    everything = "".join(p.read_text(encoding="utf-8", errors="ignore")
                         for p in tmp_path.iterdir() if p.is_file())  # fmt: skip
    assert "scan-0" not in everything  # a file name can carry a patient's name
    assert "T4b Paracetmol" not in everything  # nor is the print reader's guess kept


def test_cutting_again_adds_new_lines_and_keeps_what_was_typed(tmp_path) -> None:
    _cut(tmp_path)
    _type(tmp_path, {0: "Tab Paracetamol 500 mg"})
    more = [*PAGE, (_region("handwritten", "rx3"), "")]
    done = _cut(tmp_path, more)
    assert (done.already_in_kit, done.written) == (2, 1)
    rows = htr_labels.read_labels(tmp_path)
    assert len(rows) == 3 and rows[0]["text"] == "Tab Paracetamol 500 mg"


def test_a_file_that_cannot_be_opened_does_not_stop_the_rest(tmp_path) -> None:
    def pages_of(path: Path) -> list[bytes]:
        if path.name == "bad.pdf":
            raise OSError("not a document")
        return [b"page"]

    done = htr_labels.cut([Path("bad.pdf"), Path("good.jpg")], tmp_path,
                          find_lines=lambda png: PAGE, pages_of=pages_of)  # fmt: skip
    assert done.unreadable_files == 1 and done.written == 2


def test_the_real_line_finder_leaves_a_printed_page_alone(tmp_path) -> None:
    pytest.importorskip("rapidocr_onnxruntime")
    from cdi_adapter.ingest import pages
    from tests.helpers import make_pdf

    png = pages.render_pages(make_pdf([["City Care Clinic", "Complete Blood Count",
                                        "Haemoglobin 13.5 g/dL"]], dpi=150), "application/pdf",
                             dpi=150)[0]  # fmt: skip
    done = htr_labels.cut([Path("typeset.png")], tmp_path, pages_of=lambda path: [png])
    assert done.lines_found == 3 and done.printed_left_out == 3 and done.written == 0


# ------------------------------------------------------------------ what counts as a person


@pytest.mark.parametrize("line", [
    "Name: Ravi Kumar", "Mr Suresh", "Mrs. Lakshmi Devi", "Age 45", "45/M", "52 yrs / F",
    "Ph 98765 43210", "Mob: 9876543210", "Aadhaar 1234 5678 9012", "UHID 20261009",
    "ravi.k@example.com", "S/o Ramesh", "Address: 12 MG Road", "Reg No. 4471", "Pt c/o fever",
])  # fmt: skip
def test_a_line_about_a_person_is_recognised(line: str) -> None:
    assert htr_labels.identity_reason(line) is not None


@pytest.mark.parametrize("line", [
    "Tab Paracetamol 500 mg 1-0-1 x 5 days", "Cap Omez 20 mg bd", "Syp Ascoril 5 ml tds",
    "Inj Ceftriaxone 1 g iv", "CBC, LFT, RFT", "BP 120/80", "review after 1 week",
    "Tab Regestrone 5 mg", "Tab Telma 40", "",
])  # fmt: skip
def test_a_clinical_line_is_not(line: str) -> None:
    assert htr_labels.identity_reason(line) is None


# ------------------------------------------------------------------ 2. the page


def test_the_page_shows_each_crop_with_a_box_and_saves_the_sheet(tmp_path) -> None:
    _cut(tmp_path)
    page = (tmp_path / "label.html").read_text(encoding="utf-8")
    rows = htr_labels.read_labels(tmp_path)
    assert all(row["image"] in page for row in rows)
    assert "Type exactly what is written on each line" in page and "nothing corrected" in page
    assert "skip: it has a name, phone or ID on it, or cannot be read" in page
    assert "a.download='labels.csv'" in page and "'id,image,text,skip,note'" in page
    assert "http://" not in page and "https://" not in page  # nothing is sent anywhere
    assert "innerHTML" not in page


# ------------------------------------------------------------------ 3. build


def test_only_typed_unskipped_lines_with_no_person_on_them_are_kept(tmp_path) -> None:
    kit, out = tmp_path / "kit", tmp_path / "lines"
    page = [*PAGE, (_region("handwritten", "rx3"), ""), (_region("handwritten", "rx4"), ""),
            (_region("handwritten", "rx5"), "")]  # fmt: skip
    _cut(kit, page)
    rows = _type(kit, {0: "  Tab  Paracetamol 500 mg ", 1: "Cap Omez 20 mg", 2: "Mr Ravi Kumar",
                       3: "unreadable"}, skip=(3,))  # fmt: skip
    done = htr_labels.build(kit, out, screen=lambda text: None)
    assert (done.lines, done.kept, done.identity_left_out, done.skipped, done.not_typed) == (
        5, 2, 1, 1, 1)  # fmt: skip
    kept = [json.loads(line) for line in (out / "lines.jsonl").read_text("utf-8").splitlines()]
    assert [k["text"] for k in kept] == ["Tab Paracetamol 500 mg", "Cap Omez 20 mg"]
    assert sorted(p.name for p in (out / "crops").iterdir()) == sorted(
        Path(r["image"]).name for r in rows[:2])  # fmt: skip
    assert "Ravi" not in (out / "lines.jsonl").read_text("utf-8")  # the person's line is not there
    assert done.still_needed == 498 and done.enough is False


def test_the_second_screen_can_leave_a_line_out_too(tmp_path) -> None:
    kit, out = tmp_path / "kit", tmp_path / "lines"
    _cut(kit)
    _type(kit, {0: "Tab Paracetamol 500 mg", 1: "Lakshmi to review"})
    done = htr_labels.build(kit, out, screen=lambda t: "a name" if "Lakshmi" in t else None)
    assert done.kept == 1 and done.identity_left_out == 1


def test_the_benchmark_reads_what_build_writes(tmp_path) -> None:
    kit, out = tmp_path / "kit", tmp_path / "lines"
    _cut(kit)
    _type(kit, {0: "Tab Paracetamol 500 mg", 1: "Cap Omez 20 mg"})
    htr_labels.build(kit, out, screen=lambda text: None)
    spec = importlib.util.spec_from_file_location("bench_htr", ROOT / "scripts" / "bench_htr.py")
    assert spec and spec.loader
    bench = importlib.util.module_from_spec(spec)
    sys.modules["bench_htr"] = bench
    spec.loader.exec_module(bench)
    lines = bench.load_manifest(out / "lines.jsonl")
    assert [ln.text for ln in lines] == ["Tab Paracetamol 500 mg", "Cap Omez 20 mg"]
    assert lines[0].png == PAGE[2][0].crop.png and isinstance(lines[0], htr_bench.Line)  # type: ignore[union-attr]


def test_it_says_when_there_are_enough_lines() -> None:
    assert htr_labels.TARGET == (500, 1000) and htr_bench.Rule().min_lines == 500


# ------------------------------------------------------------------ never in the repository


def test_real_handwriting_is_refused_a_place_in_the_repository(tmp_path) -> None:
    with pytest.raises(ValueError, match="outside the repository"):
        htr_labels.cut([], ROOT / "docs" / "kit")
    with pytest.raises(ValueError, match="outside the repository"):
        htr_labels.build(tmp_path, ROOT / "bench" / "lines")
    assert not (ROOT / "docs" / "kit").exists() and not (ROOT / "bench" / "lines").exists()


def test_the_script_cuts_builds_and_refuses(tmp_path, monkeypatch, capsys) -> None:
    path = ROOT / "scripts" / "htr_label_kit.py"
    spec = importlib.util.spec_from_file_location("htr_label_kit", path)
    assert spec and spec.loader
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    real, real_build = htr_labels.cut, htr_labels.build

    def build(kit: Path, out: Path) -> htr_labels.BuildSummary:
        return real_build(kit, out, screen=lambda text: None)

    monkeypatch.setattr(htr_labels, "build", build)
    monkeypatch.setattr(htr_labels, "cut", lambda files, kit: real(
        files, kit, find_lines=lambda png: PAGE, pages_of=lambda path: [b"page"]))  # fmt: skip
    kit, out = tmp_path / "kit", tmp_path / "lines"
    assert script.main(["cut", "--out", str(kit), "a.jpg"]) == 0
    assert "2 new handwritten lines" in capsys.readouterr().out
    _type(kit, {0: "Tab Paracetamol 500 mg"})
    assert script.main(["build", "--kit", str(kit), "--out", str(out)]) == 0
    said = capsys.readouterr().out
    assert "1 lines ready" in said and "499 more needed" in said
    assert script.main(["build", "--kit", str(kit), "--out", str(ROOT / "docs" / "x")]) == 2
    assert "refused" in capsys.readouterr().err
