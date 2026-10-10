"""RD-S2 acceptance: TrOCR and Qwen each read every handwritten line on their own.

One group of tests per acceptance criterion. The two readers here are stand-ins that record
exactly what they were handed: the TrOCR checkpoint (several hundred megabytes) is not
downloaded for a unit test, and Qwen needs the GPU host. So these tests prove the plumbing -
the same crop to both, nothing passed between them, both readings saved, one reading sent to
review, the benchmark's arithmetic and what its report says. They prove nothing about how well
either reader reads handwriting: that is the benchmark, and it has not been run on real lines.
"""

from __future__ import annotations

import hashlib
import importlib.util
import inspect
import json
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from cdi_adapter.config import Settings, get_settings
from cdi_adapter.ocr import service as ocr
from cdi_adapter.ocr import vlm_ocr
from cdi_adapter.ocrhost.app import create_app
from cdi_adapter.recognition import engines, htr_bench, pipeline, regions
from cdi_adapter.recognition.engines import QWEN, TROCR, EngineDown, Reading
from cdi_adapter.resolution import cascade, facts

ROOT = Path(__file__).resolve().parents[1]
CHECKPOINT = "microsoft/trocr-base-handwritten"


def _crop(tag: str) -> regions.Crop:
    png = f"crop-{tag}".encode()
    return regions.Crop(png=png, sha256=hashlib.sha256(png).hexdigest(), bbox=(0, 60, 400, 90),
                        padded_bbox=(0, 55, 400, 95), pad_px=5, scale=1.0, source_size=(400, 40),
                        size=(400, 40))  # fmt: skip


def _line(label: str, tag: str, top: int = 60) -> regions.Region:
    return regions.Region(bbox=(0, top, 400, top + 30), label=label, reading_order=0,
                          crop=_crop(tag))  # fmt: skip


class Reader:
    """A reader that records what it was handed and answers with a fixed text."""

    def __init__(self, name: str, text: str = "Tab Telma 40", *, fail: Exception | None = None,
                 delay: float = 0.0) -> None:  # fmt: skip
        self.name, self.text, self.fail, self.delay = name, text, fail, delay
        self.given: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def read(self, *args: Any, **kwargs: Any) -> Reading:
        self.given.append((args, kwargs))
        if self.delay:
            time.sleep(self.delay)
        if self.fail:
            raise self.fail
        return Reading(self.name, self.text, 0.91 if self.name == TROCR else 0.55,
                       CHECKPOINT if self.name == TROCR else "qwen-test", 0.01,
                       None if self.name == TROCR else "p" * 64)  # fmt: skip


class Gateway:
    """Stands where the model gateway is: keeps every prompt Qwen was sent."""

    model, backend = "Qwen/Qwen2.5-VL-7B-Instruct", "test"

    def __init__(self, answer: str = "Tab Telma 40") -> None:
        self.answer, self.prompts, self.images = answer, [], []

    def generate(self, prompt: str, image_png: bytes | None = None, **kw: Any) -> str:
        self.prompts.append(prompt)
        self.images.append(image_png)
        return self.answer


def _read(found: list[regions.Region], *readers: Any, timeout: float = 5.0) -> Any:
    return pipeline.recognize_page(found, readers, max_lines=60, timeout=timeout)


# ------------------------------------------------------------------ AC1
# GIVEN a handwritten line WHEN it is read THEN two readings (TrOCR and Qwen) are saved, each
# from the same crop


def test_ac1_both_readers_are_given_the_same_crop() -> None:
    line = _line("handwritten", "a")
    trocr, qwen = Reader(TROCR), Reader(QWEN)
    _read([line], trocr, qwen)
    assert trocr.given == [((line.crop.png,), {})] and qwen.given == [((line.crop.png,), {})]


def test_ac1_two_readings_are_saved_each_with_that_crops_fingerprint() -> None:
    line = _line("handwritten", "a")
    page = _read([line], Reader(TROCR, "Tab Telma 4O"), Reader(QWEN, "Tab Telma 40"))
    rows = pipeline.reading_rows("doc-1", "run-1", "page-1", page.lines, first_order=7)
    assert [r["engine"] for r in rows] == [TROCR, QWEN]  # two rows for the one line
    assert {r["crop_hash"] for r in rows} == {line.crop.sha256}  # the same crop
    assert [r["text"] for r in rows] == ["Tab Telma 4O", "Tab Telma 40"]  # each as it read it
    assert [r["ocr_conf"] for r in rows] == [0.91, 0.55]  # each with its confidence
    assert [r["engine_version"] for r in rows] == [CHECKPOINT, "qwen-test"]
    assert rows[0]["prompt_hash"] is None and rows[1]["prompt_hash"]  # only Qwen has a prompt
    assert all(r["review_reason"] is None and r["region_kind"] == "handwritten" for r in rows)
    assert [r["reading_order"] for r in rows] == [7, 8]
    assert all(r["bbox"] == [0, 60, 400, 90] and r["pipeline_run_id"] == "run-1" for r in rows)
    assert page.counts() == {"lines": 1, "both": 1, "one_reading": 0, "no_reading": 0,
                             "over_cap": 0}  # fmt: skip


def test_ac1_a_reading_is_never_changed_to_match_the_other() -> None:
    """They disagree on a digit: both are saved as read. Comparing them is a later step."""
    page = _read([_line("handwritten", "a")], Reader(TROCR, "Telma 4O"), Reader(QWEN, "Telma 40"))
    assert {n: r.text for n, r in page.lines[0].readings.items()} == {
        TROCR: "Telma 4O", QWEN: "Telma 40"}  # fmt: skip


def test_ac1_only_handwritten_lines_are_sent() -> None:
    printed, mixed = _line("printed", "p", 0), _line("mixed", "m", 120)
    trocr, qwen = Reader(TROCR), Reader(QWEN)
    page = _read([printed, _line("handwritten", "h"), mixed], trocr, qwen)
    assert [ln.label for ln in page.lines] == ["handwritten", "mixed"]
    assert len(trocr.given) == 2 and len(qwen.given) == 2  # the printed line went to neither


def test_ac1_the_pipeline_saves_them_through_the_evidence_table() -> None:
    stage = inspect.getsource(ocr.ocr_document)
    assert "evidence.recognize_page(" in stage and "evidence.reading_rows(" in stage
    assert "engines.TrOCREngine(settings.trocr_model_id" in stage
    assert "engines.QwenLineEngine(line_client)" in stage
    assert "repo.insert_ocr_blocks(conn, line_rows)" in stage  # only ever added (RD-S1)


def test_ac1_the_print_readers_line_gives_way_only_to_a_line_that_was_read() -> None:
    from cdi_adapter.ocr import rapid

    found = [_line("handwritten", "a"), _line("handwritten", "b", 200)]
    prints = [rapid.OcrLine(text="x", bbox=[0, 60, 400, 90], conf=0.4),
              rapid.OcrLine(text="y", bbox=[0, 200, 400, 230], conf=0.4)]  # fmt: skip
    down = EngineDown("down")
    page = pipeline.recognize_page(found[:1], [Reader(TROCR), Reader(QWEN)], max_lines=60,
                                   timeout=5)  # fmt: skip
    none = pipeline.recognize_page(found[1:], [Reader(TROCR, fail=down), Reader(QWEN, fail=down)],
                                   max_lines=60, timeout=5)  # fmt: skip
    none.lines[0].region = 1
    page.lines += none.lines
    reads = ocr.replaced_by(prints, found, page)
    assert reads.replaced == {0}  # the line neither reader read keeps its print reading
    assert reads.failed == 1 and len(reads.crops) == 2


def test_ac1_the_checkpoint_is_in_the_settings_and_reading_twice_is_off_until_set() -> None:
    """ISSUE, recorded as a fact: two readers are off by default. The checkpoint is unmeasured
    on prescription handwriting and its licence is a review item (ENT-S1)."""
    shipped = Settings(_env_file=None)
    assert shipped.trocr_model_id == CHECKPOINT
    assert shipped.two_readers is False
    stage = inspect.getsource(ocr.ocr_document)
    assert "twice = by_line and settings.two_readers" in stage


def test_ac1_trocr_on_the_host_is_sent_the_crop_and_nothing_else(monkeypatch) -> None:
    sent: list[dict[str, Any]] = []

    def post(url: str, **kw: Any) -> Any:
        sent.append({"url": url, **kw})
        return httpx.Response(200, request=httpx.Request("POST", url), json={
            "engine": "trocr", "engine_version": CHECKPOINT, "text": " Tab  Telma 40 ",
            "confidence": 0.8731, "seconds": 0.4})  # fmt: skip

    monkeypatch.setattr(httpx, "post", post)
    reading = engines.TrOCREngine(CHECKPOINT, url="http://127.0.0.1:8079/").read(b"crop-bytes")
    assert sent[0]["url"] == "http://127.0.0.1:8079/ocr/trocr"
    assert sent[0]["content"] == b"crop-bytes"
    assert set(sent[0]) == {"url", "content", "timeout", "headers"}  # no text travels with it
    assert (reading.engine, reading.text, reading.confidence, reading.engine_version) == (
        TROCR, "Tab Telma 40", 0.8731, CHECKPOINT)  # fmt: skip


def test_ac1_the_host_has_the_trocr_route_and_names_the_checkpoint(monkeypatch) -> None:
    seen: list[tuple[bytes, str]] = []

    def read(crop: bytes, model: str) -> tuple[str, float]:
        seen.append((crop, model))
        return "Tab Telma 40", 0.87

    monkeypatch.setattr(engines, "trocr_read", read)
    host = TestClient(create_app())
    body = host.post("/ocr/trocr", content=b"crop-bytes").json()
    assert body["engine"] == "trocr" and body["engine_version"] == CHECKPOINT
    assert body["text"] == "Tab Telma 40" and body["confidence"] == 0.87 and body["seconds"] >= 0
    assert seen == [(b"crop-bytes", CHECKPOINT)]
    assert host.post("/ocr/trocr", content=b"").status_code == 400
    assert host.get("/healthz").json()["trocr"]["model"] == CHECKPOINT


def test_ac1_trocr_reads_on_the_cpu() -> None:
    source = inspect.getsource(engines.load_trocr) + inspect.getsource(engines.trocr_read)
    assert 'model.to("cpu")' in source and "cuda" not in source
    assert "VisionEncoderDecoderModel.from_pretrained(model_id)" in source


# ------------------------------------------------------------------ AC2
# GIVEN the Qwen prompt is inspected WHEN it is checked THEN it contains no text from the other
# reader


def test_ac2_qwens_prompt_holds_nothing_from_the_other_reader() -> None:
    gateway = Gateway()
    trocr = Reader(TROCR, "ZZ-TROCR-READ-THIS-ZZ")
    page = _read([_line("handwritten", "a"), _line("handwritten", "b", 200)], trocr,
                 engines.QwenLineEngine(gateway))  # fmt: skip
    assert page.counts()["both"] == 2 and len(gateway.prompts) == 2
    for prompt in gateway.prompts:
        assert prompt == engines.QWEN_PROMPT  # the fixed sentence, whole, and nothing more
        assert "ZZ-TROCR-READ-THIS-ZZ" not in prompt and "trocr" not in prompt.lower()


def test_ac2_the_prompt_is_one_fixed_sentence_with_nowhere_to_put_a_reading() -> None:
    assert engines.QWEN_PROMPT is vlm_ocr.LINE_PROMPT
    assert "{" not in engines.QWEN_PROMPT and "%" not in engines.QWEN_PROMPT
    source = inspect.getsource(engines.QwenLineEngine)
    assert "self.client.generate(QWEN_PROMPT, crop_png" in source
    assert ".format(" not in source and 'f"' not in source.split("except")[0]


def test_ac2_a_reader_is_handed_the_crop_and_nothing_else() -> None:
    for engine in (engines.TrOCREngine, engines.QwenLineEngine):
        assert list(inspect.signature(engine.read).parameters) == ["self", "crop_png"]
    assert list(inspect.signature(engines.LineEngine.read).parameters) == ["self", "crop_png"]
    source = inspect.getsource(pipeline.recognize_page)
    assert "pool.submit(reader.read, r.crop.png)" in source  # submitted before any has answered


def test_ac2_qwen_is_given_the_picture_of_the_line_only() -> None:
    gateway = Gateway()
    line = _line("handwritten", "a")
    _read([line], Reader(TROCR), engines.QwenLineEngine(gateway))
    assert gateway.images == [line.crop.png]


def test_ac2_the_prompts_fingerprint_is_kept_with_the_reading() -> None:
    reading = engines.QwenLineEngine(Gateway()).read(b"crop")
    assert reading.prompt_hash == hashlib.sha256(engines.QWEN_PROMPT.encode()).hexdigest()
    assert reading.engine == QWEN and reading.engine_version == Gateway.model


# ------------------------------------------------------------------ AC3
# GIVEN the TrOCR host is down WHEN a line is read THEN the line has one reading and is sent to
# review


def test_ac3_with_the_trocr_host_down_the_line_has_qwens_reading_and_a_review_reason(
    monkeypatch,
) -> None:
    def refused(url: str, **kw: Any) -> Any:
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "post", refused)
    trocr = engines.TrOCREngine(CHECKPOINT, url="http://127.0.0.1:8079")
    page = _read([_line("handwritten", "a")], trocr, engines.QwenLineEngine(Gateway()))
    line = page.lines[0]
    assert list(line.readings) == [QWEN] and list(line.missing) == [TROCR]
    assert line.review_reason is not None
    assert line.review_reason.startswith("one reading: trocr is down")
    rows = pipeline.reading_rows("doc-1", "run-1", "page-1", page.lines, first_order=0)
    assert len(rows) == 1 and rows[0]["engine"] == QWEN
    assert rows[0]["review_reason"] == line.review_reason  # saved with the reading
    assert page.counts()["one_reading"] == 1


def test_ac3_a_value_resting_on_that_reading_goes_to_review(monkeypatch) -> None:
    why = "one reading: trocr is down (the TrOCR host did not answer (ConnectError))"
    fact = {"fact_type": "note", "value_text": "Tab Telma 40", "code_status": "unbound"}

    def decision(review_reason: str | None) -> Any:
        monkeypatch.setattr(facts, "_observations", lambda conn, prov: [
            {"engine": "vlm", "region_kind": "handwritten", "ocr_conf": 0.55,
             "review_reason": review_reason}])  # fmt: skip
        signals, levels, cohort = facts.evidence_for(None, fact, [])  # type: ignore[arg-type]
        return signals, cascade.resolve(signals, levels, field_="note", cohort=cohort)

    signals, held = decision(why)
    assert [(s.source, s.relation, s.reason) for s in signals if s.source == "single-reading"] == [
        ("single-reading", "contradicts", why)]  # fmt: skip
    assert held.outcome == "review"
    assert held.escalation_cause == "tier-1 contradiction (single-reading)"
    signals, _ = decision(None)
    assert not [s for s in signals if s.source == "single-reading"]  # only when one is missing


def test_ac3_with_the_host_down_the_print_reader_reads_the_page_itself(monkeypatch) -> None:
    """Found on the instance: the print reader and TrOCR share the OCR host, so with the host
    down the whole page failed before any line was reached. It now reads in-process instead."""
    from cdi_adapter.ocr import rapid

    def refused(url: str, **kw: Any) -> Any:
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "post", refused)
    monkeypatch.setattr(rapid, "read_here", lambda png, min_conf=None: ["read in-process"])
    monkeypatch.setenv("CDI_OCR_HOST_URL", "http://127.0.0.1:8079")
    get_settings.cache_clear()
    try:
        assert rapid.run_rapidocr(b"page") == ["read in-process"]
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()


def test_ac3_a_slow_reader_does_not_hold_the_page() -> None:
    started = time.perf_counter()
    page = _read([_line("handwritten", "a")], Reader(TROCR, delay=3.0), Reader(QWEN), timeout=0.3)
    assert time.perf_counter() - started < 2.0  # not the 3 s the slow reader took
    line = page.lines[0]
    assert list(line.readings) == [QWEN]
    assert line.review_reason == "one reading: trocr did not answer in 0.3 s"


def test_ac3_it_is_the_same_whichever_reader_is_down() -> None:
    page = _read([_line("handwritten", "a")], Reader(TROCR),
                 Reader(QWEN, fail=EngineDown("the gateway is restarting")))  # fmt: skip
    assert list(page.lines[0].readings) == [TROCR]
    assert page.lines[0].review_reason == "one reading: vlm is down (the gateway is restarting)"


def test_ac3_a_reader_that_breaks_or_reads_nothing_counts_as_missing() -> None:
    page = _read([_line("handwritten", "a"), _line("handwritten", "b", 200)],
                 Reader(TROCR, fail=ValueError("bad image")), Reader(QWEN, ""))  # fmt: skip
    for line in page.lines:
        assert line.readings == {} and line.review_reason is not None
        assert line.review_reason.startswith("no reading: trocr failed (ValueError); vlm read")
    assert page.counts()["no_reading"] == 2
    assert pipeline.reading_rows("d", "r", "p", page.lines, first_order=0) == []


def test_ac3_a_broken_engine_is_reported_as_down_not_raised() -> None:
    class Broken:
        model = "m"

        def generate(self, *a: Any, **kw: Any) -> str:
            raise TimeoutError("gateway timeout")

    with pytest.raises(EngineDown, match="Qwen could not read the line"):
        engines.QwenLineEngine(Broken()).read(b"crop")  # type: ignore[arg-type]


def test_ac3_the_reason_has_a_column_and_only_new_rows_get_it() -> None:
    migration = (ROOT / "db" / "alembic" / "versions" / "0035_observation_review_reason.py"
                 ).read_text(encoding="utf-8")  # fmt: skip
    assert "ALTER TABLE ocr_observation ADD COLUMN review_reason text" in migration
    assert "UPDATE" not in migration.split('"""')[2]  # no earlier row is touched
    from cdi_adapter import repo

    assert "review_reason" in repo.OBSERVATION_EXTRA
    assert ":review_reason" in inspect.getsource(repo.insert_ocr_blocks)


# ------------------------------------------------------------------ AC4
# GIVEN English handwritten lines are benchmarked WHEN the report is read THEN it names the
# checkpoint used and gives error rates for TrOCR alone, Qwen alone and both


def _reads() -> list[htr_bench.Read]:
    truth = [
        "Tab Telma 40 mg 1-0-1",
        "Cap Omez 20 mg 1-0-0",
        "Syp Ascoril 5 ml",
        "review in a week",
    ]
    trocr = [
        "Tab Telma 4O mg 1-0-1",
        "Cap Omez 20 mg 1-0-0",
        "Syp Ascoril 5 ml",
        "review in a weak",
    ]
    qwen = ["Tab Telma 40 mg 1-0-1", "Cap Omez 20 mg 1-0-0", "Syp Ascoril 5 ml", "review in a weak"]
    return [htr_bench.Read(f"l{i}", t, a, b, 4.5, 0.4)
            for i, (t, a, b) in enumerate(zip(truth, trocr, qwen, strict=True))]  # fmt: skip


def _report(data: str = "synthetic", reads: list[htr_bench.Read] | None = None) -> dict[str, Any]:
    return htr_bench.report(reads or _reads(), checkpoint=CHECKPOINT, qwen_model="qwen-test",
                            data=data, source="four lines made for the test")  # fmt: skip


def test_ac4_the_report_names_the_checkpoint_and_gives_the_three_error_rates() -> None:
    rep = _report()
    text = htr_bench.render(rep)
    assert f"**TrOCR checkpoint:** `{CHECKPOINT}`" in text
    assert "| | A: Qwen alone | B: TrOCR alone | C: both |" in text
    for name in "ABC":
        assert rep["setups"][name]["cer"] is not None and rep["setups"][name]["wer"] is not None
    cer_row = next(line for line in text.splitlines() if line.startswith("| CER |"))
    assert cer_row.count("0.") == 3  # one figure for each of the three


def test_ac4_the_figures_are_right() -> None:
    s = _report()["setups"]
    chars = sum(len(r.truth) for r in _reads())
    words = sum(len(r.truth.split()) for r in _reads())
    # A, Qwen alone: one wrong character ("weak" for "week"), so one wrong word
    assert s["A"]["cer"] == round(1 / chars, 4) and s["A"]["wer"] == round(1 / words, 4)
    assert s["A"]["accepted_wrong"] == 1 and s["A"]["critical_value_accuracy"] == 1.0
    # B, TrOCR alone: it also read 4O for 40 - a wrong number
    assert s["B"]["cer"] == round(2 / chars, 4) and s["B"]["accepted_wrong"] == 2
    assert s["B"]["critical_value_accuracy"] == round(2 / 3, 4)
    # C, both: they disagree on line 0 (caught), agree and are right on 1 and 2, and agree on
    # the same mistake on line 3 - which is accepted, and wrong
    assert s["C"]["accepted"] == 3 and s["C"]["sent_to_review"] == 1
    assert s["C"]["accepted_wrong"] == 1 and s["C"]["wrong_caught_by_disagreement"] == 1
    assert s["C"]["coverage"] == 0.75 and s["C"]["accepted_precision"] == round(2 / 3, 4)
    assert s["B"]["seconds_per_line"] == 4.5 and s["C"]["seconds_per_line"] == 4.9


def test_ac4_the_measures_themselves() -> None:
    assert htr_bench.edits("kitten", "sitting") == 3 and htr_bench.edits("", "abc") == 3
    assert htr_bench.cer([("abcd", "abcd")]) == 0 and htr_bench.cer([("", "")]) is None
    assert htr_bench.same("Tab  Telma 40", "tab telma 40") and not htr_bench.same("", "")
    assert not htr_bench.same("Telma 40", "Telma 4O")
    assert htr_bench.critical_values("Tab X 500 mg 1-0-1 x 5 days") == {"500": 1, "1-0-1": 1,
                                                                        "5": 1}  # fmt: skip
    assert htr_bench.critical_accuracy([("no number here", "anything")]) is None
    assert htr_bench.wilson_lower(0, 0) is None
    assert 0.90 < htr_bench.wilson_lower(480, 500) < 0.96  # type: ignore[operator]


def test_ac4_a_run_on_synthetic_lines_says_so_and_decides_nothing() -> None:
    rep = _report("synthetic")
    assert rep["verdict"]["decision"] is None
    text = htr_bench.render(rep)
    assert "NOT real handwriting" in text and "remains unmeasured" in text
    assert "**Decision:** none made" in text


def test_ac4_the_decision_rule_is_fixed_before_the_run_and_marked_an_assumption() -> None:
    rule = htr_bench.Rule()
    assert (rule.min_precision_gain, rule.max_seconds_per_line, rule.min_lines) == (0.02, 1.0, 500)
    assert "ASSUMPTION" in _report()["rule_is"]
    assert _report("real")["verdict"]["decision"] is None  # four lines decide nothing
    many = [htr_bench.Read(f"l{i}", "Tab A 5 mg", "Tab A 5 mg",
                           "Tab A 5 mg" if i % 10 else "Tab A 6 mg", 0.5, 0.4)
            for i in range(600)]  # fmt: skip
    kept = _report("real", many)["verdict"]
    assert kept["decision"] == "keep TrOCR" and kept["precision_gain_lower_bound"] > 0.02
    slow = [htr_bench.Read(r.id, r.truth, r.trocr, r.qwen, 4.5, 0.4) for r in many]
    assert _report("real", slow)["verdict"]["decision"].startswith("remove TrOCR (plan B")


def test_ac4_a_reader_that_was_not_run_is_shown_as_not_run() -> None:
    reads = [htr_bench.Read("l0", "Tab A 5 mg", None, "Tab A 5 mg", 0.0, 0.4)]
    rep = _report(reads=reads)
    assert rep["setups"]["B"] is None and rep["setups"]["C"] is None
    assert "| CER | 0.0000 | not run | not run |" in htr_bench.render(rep)


def test_ac4_the_synthetic_lines_are_typeset_fictional_and_repeatable() -> None:
    a, b = htr_bench.synthetic_lines(5, seed=3), htr_bench.synthetic_lines(5, seed=3)
    assert [x.text for x in a] == [x.text for x in b] and [x.png for x in a] == [x.png for x in b]
    assert all(x.png.startswith(b"\x89PNG") and htr_bench.critical_values(x.text) for x in a)


def _script() -> Any:
    spec = importlib.util.spec_from_file_location("bench_htr", ROOT / "scripts" / "bench_htr.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["bench_htr"] = module
    spec.loader.exec_module(module)
    return module


def test_ac4_the_harness_runs_and_writes_the_report(tmp_path, monkeypatch, capsys) -> None:
    script = _script()
    monkeypatch.setattr(engines, "TrOCREngine", lambda *a, **kw: Reader(TROCR, "Tab X 5 mg"))
    monkeypatch.setattr(engines, "QwenLineEngine", lambda client: Reader(QWEN, "Tab X 5 mg"))
    monkeypatch.setattr("cdi_adapter.ml.client.get_client", lambda: Gateway())
    assert script.main(["--synthetic", "6", "--out", str(tmp_path)]) == 0
    text = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert CHECKPOINT in text and "6 (synthetic)" in text and "NOT real handwriting" in text
    saved = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert saved["checkpoint"] == CHECKPOINT and len(saved["readings"]) == 6
    assert set(saved["setups"]) == {"A", "B", "C"}


def test_ac4_real_lines_are_refused_until_they_are_declared_de_identified(tmp_path, capsys) -> None:
    script = _script()
    manifest = tmp_path / "lines.jsonl"
    manifest.write_text("", encoding="utf-8")
    assert script.main(["--manifest", str(manifest), "--out", str(tmp_path / "out")]) == 2
    assert "refused" in capsys.readouterr().err and not (tmp_path / "out").exists()


@pytest.mark.xfail(strict=True, reason="RD-S2 AC4: the harness is built and its report gives the "
                   "three error rates, and the kit that makes the lines is built "
                   "(scripts/htr_label_kit.py) - but nobody has yet typed what 500 real "
                   "handwritten lines say, so it has not been run on English handwriting. "
                   "Accuracy on real handwriting is unmeasured; no decision is made.")  # fmt: skip
def test_ac4_the_benchmark_has_been_run_on_real_handwriting() -> None:
    # the report a person reads, not report.json: that one holds every line's text
    report = ROOT / "docs" / "bench" / "htr" / "report.md"
    assert report.exists() and "(real)" in report.read_text(encoding="utf-8")


# ------------------------------------------------------------------ what is said about trust


def test_the_code_says_plainly_that_no_reader_is_trusted_yet() -> None:
    doc = engines.__doc__ or ""
    assert "Not yet trusted" in doc and "IAM" in doc and "non-commercial" in doc
    assert "ENT-S1" in doc and "CDI_TWO_READERS" in doc
    assert get_settings().two_readers is False
