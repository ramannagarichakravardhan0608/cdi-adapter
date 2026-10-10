"""RD-S3 acceptance: if the readers disagree, or the picture does not support the answer, send
it to a person.

One group of tests per acceptance criterion, then the functional and non-functional
requirements. The two readers' answers are given as text (what TrOCR and Qwen read is RD-S2's
business). The picture check is the real one where it matters - RapidOCR on a synthetic line
picture drawn for the test - and a stand-in elsewhere. No real handwriting is used: these tests
prove the rules, not how often real lines pass them.
"""

from __future__ import annotations

import inspect
import io
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image, ImageDraw, ImageFont

from cdi_adapter import metrics
from cdi_adapter.config import Settings
from cdi_adapter.ocr import service as ocr
from cdi_adapter.recognition import adjudicate, disagreement, engines, grounding, hierarchy
from cdi_adapter.recognition import pipeline as P
from cdi_adapter.recognition.engines import QWEN, TROCR, Reading
from cdi_adapter.resolution import facts

ROOT = Path(__file__).resolve().parents[1]


def _picture(text: str) -> bytes:
    """A line crop with ``text`` on it, printed - what the picture check reads."""
    font = ImageFont.load_default(size=40)
    im = Image.new("RGB", (int(font.getlength(text)) + 60, 80), "white")
    ImageDraw.Draw(im).text((30, 18), text, fill="black", font=font)
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def _shows(text: str) -> Any:
    """A stand-in picture check that reads ``text`` in every picture."""
    return lambda png, value: grounding.ground(png, value, read=lambda _: text)


def _assess(trocr: str | None, qwen: str | None, **kw: Any) -> hierarchy.Assessment:
    readings = {k: v for k, v in ((TROCR, trocr), (QWEN, qwen)) if v is not None}
    kw.setdefault("ground", _shows(qwen or trocr or ""))
    return hierarchy.assess_fact(readings, crop_png=b"crop", **kw)


rapidocr = pytest.importorskip("rapidocr_onnxruntime")


# ------------------------------------------------------------------ AC1
# GIVEN TrOCR reads 'Telma 4O' and Qwen 'Telma 40' WHEN they are compared THEN they are treated
# as the same and the value is accepted as Telma 40


def test_ac1_telma_4O_and_telma_40_are_the_same() -> None:
    c = disagreement.compare_texts("Telma 4O", "Telma 40")
    assert (c.state, c.value, c.numbers) == ("AGREE", "Telma 40", ("40",))


def test_ac1_the_value_is_accepted_as_telma_40() -> None:
    a = _assess("Telma 4O", "Telma 40")
    assert (a.decision, a.value, a.reasons) == ("accept", "Telma 40", [])
    assert [s["check"] for s in a.steps] == ["readers", "picture"]


def test_ac1_with_the_real_picture_check_on_a_picture_of_telma_40() -> None:
    a = hierarchy.assess_fact({TROCR: "Telma 4O", QWEN: "Telma 40"}, crop_png=_picture("Telma 40"))
    picture = next(s for s in a.steps if s["check"] == "picture")
    assert picture["status"] == "supported" and picture["seen"] == ["40"]
    assert (a.decision, a.value) == ("accept", "Telma 40")


def test_ac1_only_letters_in_a_numbers_place_are_read_as_digits() -> None:
    assert disagreement.numbers("Tab 4O mg 1-O-1 x l0 days") == ("40", "1-0-1", "10")
    assert disagreement.numbers("Tab Olmesartan Idrozid") == ()  # in a word, a letter is a letter
    assert disagreement.compare_texts("Tab. Telma 40mg", "tab telma 40 mg").agrees


# ------------------------------------------------------------------ AC2
# GIVEN TrOCR reads 'Telma 40' and Qwen 'Telmikind 40' WHEN they are compared THEN the value
# goes to review


def test_ac2_telma_and_telmikind_go_to_review() -> None:
    c = disagreement.compare_texts("Telma 40", "Telmikind 40")
    assert c.state == "MATERIAL" and c.value is None and "telma" in c.reason
    a = _assess("Telma 40", "Telmikind 40")
    assert a.decision == "review" and a.reasons[0].startswith("different words")
    assert a.value == "Telmikind 40"  # shown first to the person: Qwen's, as read


def test_ac2_numbers_must_match_exactly() -> None:
    for a, b in (("Telma 40", "Telma 4"), ("Tab X 500 mg", "Tab X 50 mg"),
                 ("1-0-1", "1-1-1"), ("Telma 40", "Telma")):  # fmt: skip
        c = disagreement.compare_texts(a, b)
        assert c.state == "MATERIAL" and c.reason.startswith("the numbers differ"), (a, b)


def test_ac2_a_near_miss_is_labelled_a_slip_and_still_goes_to_review() -> None:
    c = disagreement.compare_texts("Pantoprazole 40", "Pantoprazo1e 40")
    assert c.state in ("PARTIAL", "MATERIAL")  # how alike, recorded ...
    assert _assess("Pantoprazole 40", "Pantoprazole 4O").decision == "accept"
    assert _assess("Pantoprazole 40", "Pantaprazole 40").decision == "review"  # ... never guessed
    assert disagreement.compare_texts("Pantoprazole 40", "Pantaprazole 40").state == "PARTIAL"


def test_ac2_one_reading_alone_is_never_accepted() -> None:
    a = _assess(None, "Telma 40")
    assert a.decision == "review" and "only one reader" in a.reasons[0]


# ------------------------------------------------------------------ AC3
# GIVEN both readers agree but the number is not in the picture WHEN the value is checked THEN
# it goes to review (picture check wins)


def test_ac3_the_picture_check_wins_over_two_readers_that_agree() -> None:
    a = _assess("Telma 40", "Telma 40", ground=_shows("Telma 4"))
    assert a.comparison is not None and a.comparison.agrees  # the readers agree ...
    assert a.decision == "review"  # ... and it still goes to a person
    assert a.reasons == ["40 is not in the picture (it reads 4)"]


def test_ac3_with_the_real_picture_check_on_a_picture_of_telma_4() -> None:
    a = hierarchy.assess_fact({TROCR: "Telma 40", QWEN: "Telma 40"}, crop_png=_picture("Telma 4"))
    picture = next(s for s in a.steps if s["check"] == "picture")
    assert picture["status"] == "contradicted" and a.decision == "review"


def test_ac3_the_picture_check_gives_the_line_a_margin_before_reading_it() -> None:
    """Found while testing: on a tight crop the print reader read "a40" for "40" - a number it
    then could not see. With a margin it reads the line as written."""
    assert grounding.PAD_PX >= 24
    assert "repad(crop_png, PAD_PX)" in inspect.getsource(grounding.read_with_rapidocr)


def test_ac3_a_number_the_picture_cannot_confirm_is_not_taken_as_yes() -> None:
    blank = _assess("Telma 40", "Telma 40", ground=_shows(""))
    assert blank.decision == "review" and "cannot confirm 40" in blank.reasons[0]

    def broken(png: bytes) -> str:
        raise RuntimeError("the reader broke")

    g = grounding.ground(b"x", "Telma 40", read=broken)
    assert g.status == "unreadable" and not g.passes


def test_ac3_a_value_with_no_number_needs_no_picture_check() -> None:
    g = grounding.ground(b"x", "Review after a week", read=lambda _: "")
    assert g.status == "not_applicable" and g.passes
    assert _assess("Review after a week", "review after a week").decision == "accept"


def test_ac3_every_number_must_be_there_as_often_as_it_is_written() -> None:
    assert grounding.ground(b"x", "1-0-1 x 5", read=lambda _: "1-0-1 x 5").status == "supported"
    assert grounding.ground(b"x", "5 x 5", read=lambda _: "x 5").status == "contradicted"


# ------------------------------------------------------------------ AC4
# GIVEN the second-opinion model fails WHEN a value is processed THEN nothing breaks and the
# value still goes through the normal checks


class _Model:
    model = "Qwen/Qwen2.5-VL-7B-Instruct"

    def __init__(self, answer: str | Exception) -> None:
        self.answer, self.prompts = answer, []

    def generate(self, prompt: str, image_png: bytes | None = None, **kw: Any) -> str:
        self.prompts.append(prompt)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def _opinion(model: _Model) -> Any:
    return lambda a, b: adjudicate.second_opinion(model, b"crop", a, b)  # type: ignore[arg-type]


def test_ac4_a_second_opinion_that_fails_breaks_nothing() -> None:
    a = _assess("Telma 40", "Telmikind 40",
                second_opinion=_opinion(_Model(TimeoutError("gateway timeout"))))  # fmt: skip
    assert a.decision == "review"  # the normal checks ran
    assert a.second_opinion is not None and a.second_opinion.answer is None
    assert "failed (TimeoutError)" in a.second_opinion.note
    assert [s["check"] for s in a.steps] == ["readers", "second opinion", "picture"]


def test_ac4_a_second_opinion_never_resolves_a_disagreement() -> None:
    model = _Model("A")
    a = _assess("Telma 40", "Telmikind 40", second_opinion=_opinion(model))
    assert a.second_opinion is not None and a.second_opinion.answer == "A"
    assert a.decision == "review"  # it picked one: still a person's decision
    assert "Telma 40" in model.prompts[0] and "Telmikind 40" in model.prompts[0]


def test_ac4_it_is_asked_only_where_the_readers_disagree() -> None:
    model = _Model("A")
    a = _assess("Telma 4O", "Telma 40", second_opinion=_opinion(model))
    assert model.prompts == [] and a.second_opinion is None and a.decision == "accept"


def test_ac4_an_answer_it_cannot_use_is_recorded_as_such() -> None:
    for said in ("", "Maybe the first one", "C"):
        op = adjudicate.second_opinion(_Model(said), b"x", "a", "b")  # type: ignore[arg-type]
        assert op.answer is None and "no usable answer" in op.note
    assert adjudicate.second_opinion(None, b"x", "a", "b").answer is None
    assert adjudicate.SecondOpinion(None, "x").advisory is True


# ------------------------------------------------------------------ AC5
# GIVEN the same crop is read twice with different padding WHEN the two answers differ THEN the
# value goes to review


def test_ac5_two_different_answers_from_the_same_crop_go_to_review() -> None:
    a = _assess("Telma 40", "Telma 40", again="Telma 46")
    assert a.decision == "review"
    assert a.reasons[0].startswith("Qwen read the line differently the second time")


def test_ac5_the_same_answer_twice_lets_it_through() -> None:
    a = _assess("Telma 40", "Telma 40", again="telma 40")
    assert a.decision == "accept"
    assert [s["check"] for s in a.steps] == ["readers", "self-consistency", "picture"]


def test_ac5_the_second_reading_is_of_the_same_line_with_more_paper_round_it() -> None:
    crop = _picture("Telma 40")
    padded = engines.repad(crop, 24)
    with Image.open(io.BytesIO(crop)) as a, Image.open(io.BytesIO(padded)) as b:
        assert b.size == (a.size[0] + 48, a.size[1] + 48)
        assert b.crop((24, 24, 24 + a.size[0], 24 + a.size[1])).tobytes() == a.convert(
            "RGB").tobytes()  # fmt: skip


def test_ac5_the_pipeline_reads_again_with_other_padding_when_asked() -> None:
    source = inspect.getsource(ocr._decide)
    assert "qwen.read(engines.repad(png)).text" in source
    assert "settings.self_consistency" in source
    assert Settings(_env_file=None).self_consistency is False  # one more model call a line


# ------------------------------------------------------------------ FR2: the word lists


def test_fr2_a_medicine_the_word_list_does_not_know_goes_to_review() -> None:
    def words(text: str) -> hierarchy.WordCheck:
        if "Telma" in text:
            return hierarchy.WordCheck("known", "in the medicine list as Telma 40")
        return hierarchy.WordCheck("unknown", "the medicine is not in the medicine list")

    assert _assess("Tab Telma 40", "Tab Telma 40", word_list=words).decision == "accept"
    unknown = _assess("Tab Telmo 40", "Tab Telmo 40", word_list=words)
    assert unknown.decision == "review"
    assert unknown.reasons == ["the medicine is not in the medicine list"]


def test_fr2_a_word_list_that_breaks_cannot_vouch_for_a_value() -> None:
    def broken(text: str) -> hierarchy.WordCheck:
        raise ConnectionError("database away")

    a = _assess("Tab Telma 40", "Tab Telma 40", word_list=broken)
    assert a.decision == "review" and "could not be checked" in a.reasons[0]


def test_fr2_the_real_word_lists_are_the_medicine_catalogue_and_the_test_names() -> None:
    source = inspect.getsource(hierarchy.catalogue_word_list)
    assert "identify(conn, text)" in source and 'bind_text("lab_order", text)' in source
    assert "dosage_form(text)" in source
    check = hierarchy.catalogue_word_list(None)
    assert check("Review after a week").status == "not_applicable"


# ------------------------------------------------------------------ FR5: how it was decided


def test_fr5_every_check_is_written_down_with_what_it_found() -> None:
    a = _assess("Telma 40", "Telma 40", ground=_shows("Telma 4"), again="Telma 40")
    record = a.as_json()
    assert record["decision"] == "review" and record["algorithm_version"] == "rd-s3.v1"
    assert [(s["check"], s["passed"]) for s in record["steps"]] == [
        ("readers", True), ("self-consistency", True), ("picture", False)]  # fmt: skip
    picture = record["steps"][2]
    assert (picture["expected"], picture["seen"], picture["reader"]) == (["40"], ["4"], "rapidocr")
    json.dumps(record)  # it can be stored as it is


def _line(trocr: str, qwen: str) -> P.LineReadings:
    crop = SimpleNamespace(png=b"crop", sha256="c" * 64)
    line = P.LineReadings(0, "handwritten", (0, 0, 10, 10), crop)  # type: ignore[arg-type]
    line.readings = {TROCR: Reading(TROCR, trocr, 0.9, "microsoft/trocr-base-handwritten", 1.0),
                     QWEN: Reading(QWEN, qwen, 0.55, "qwen", 0.5, "p" * 64)}  # fmt: skip
    return line


def test_fr5_the_decision_is_saved_beside_the_two_readings(monkeypatch) -> None:
    from cdi_adapter import agreement

    saved: list[dict[str, Any]] = []
    monkeypatch.setattr(agreement, "record_agreement", lambda conn, **kw: saved.append(kw))

    class Conn:
        def execute(self, *a: Any, **k: Any) -> Any:
            return SimpleNamespace(all=lambda: [(TROCR, "obs-t"), (QWEN, "obs-q")])

    page = P.PageReadings(lines=[_line("Telma 40", "Telmikind 40")])
    P.decide_lines(page, ground=_shows("Telma 40"))
    assert P.record_decisions(Conn(), "run-1", page.lines) == 1  # type: ignore[arg-type]
    row = saved[0]
    assert (row["observation_id_a"], row["observation_id_b"]) == ("obs-t", "obs-q")
    assert row["disagreement_state"] == "MATERIAL" and row["algorithm_version"] == "rd-s3.v1"
    assert (row["trocr_confidence"], row["qwen_confidence"]) == (0.9, 0.55)
    assert json.loads(row["reason"])["decision"] == "review"


def test_fr5_the_line_rows_carry_the_reason_and_an_agreed_line_carries_none() -> None:
    page = P.PageReadings(lines=[_line("Telma 4O", "Telma 40"), _line("Telma 40", "Telmikind 40")])
    P.decide_lines(page, ground=_shows("Telma 40"))
    rows = P.reading_rows("doc", "run", "page", page.lines, first_order=0)
    assert [r["review_reason"] for r in rows[:2]] == [None, None]  # agreed: both rows clear
    assert all(r["review_reason"].startswith("different words") for r in rows[2:])
    assert [r["text"] for r in rows] == ["Telma 4O", "Telma 40", "Telma 40", "Telmikind 40"]


def test_fr5_the_pipeline_decides_each_line_and_saves_the_decisions() -> None:
    stage = inspect.getsource(ocr.ocr_document)
    assert "_decide(both, readers, line_client, settings)" in stage
    assert "evidence.record_decisions(conn, run_id, both.lines)" in stage
    decide = inspect.getsource(ocr._decide)
    assert "hierarchy.catalogue_word_list(conn)" in decide
    assert '"the word lists could not be reached"' in decide  # fail closed, not open


# ------------------------------------------------------------------ resolution reads it


def _signals(monkeypatch: Any, obs: list[dict[str, Any]], states: list[str]) -> list[str]:
    monkeypatch.setattr(facts, "_observations", lambda conn, prov: obs)

    class Conn:
        def execute(self, *a: Any, **k: Any) -> Any:
            rows = [("t1", "q1", s) for s in states]
            return SimpleNamespace(all=lambda: rows)

    fact = {"fact_type": "medication", "value_text": "Telma 40", "code_status": "unbound"}
    signals, _, _ = facts.evidence_for(Conn(), fact, [])  # type: ignore[arg-type]
    return [s.source for s in signals]


PAIR = [{"id": "t1", "engine": "trocr", "region_kind": "handwritten", "ocr_conf": 0.9,
         "review_reason": None},
        {"id": "q1", "engine": "vlm", "region_kind": "handwritten", "ocr_conf": 0.55,
         "review_reason": None}]  # fmt: skip


def test_an_agreed_pair_is_support_not_an_uncompared_reading(monkeypatch) -> None:
    assert "readers-agree" in _signals(monkeypatch, PAIR, ["AGREE"])
    assert "uncompared-readings" not in _signals(monkeypatch, PAIR, ["AGREE"])


def test_without_an_agreement_the_two_readings_still_need_a_person(monkeypatch) -> None:
    assert "uncompared-readings" in _signals(monkeypatch, PAIR, [])
    assert "uncompared-readings" in _signals(monkeypatch, PAIR, ["MATERIAL"])


def test_a_line_the_checks_sent_to_a_person_is_a_contradiction(monkeypatch) -> None:
    obs = [{**o, "review_reason": "40 is not in the picture (it reads 4)"} for o in PAIR]
    sources = _signals(monkeypatch, obs, ["AGREE"])
    assert "line-check" in sources and "readers-agree" not in sources


# ------------------------------------------------------------------ FR7: drift


def test_fr7_the_disagreement_rate_is_watched() -> None:
    from prometheus_client import CollectorRegistry, generate_latest

    reg = CollectorRegistry()
    reg.register(metrics.DisagreementCollector(lambda: [("1h", 40, 10), ("7d", 0, 0)]))
    text = generate_latest(reg).decode()
    assert 'cdi_reader_disagreement_ratio{window="1h"} 0.25' in text
    assert 'cdi_reader_comparisons{window="7d"} 0.0' in text
    assert 'cdi_reader_disagreement_ratio{window="7d"}' not in text  # no lines, no ratio


def test_fr7_a_sudden_change_raises_an_alarm() -> None:
    import yaml

    rules = yaml.safe_load((ROOT / "infra" / "monitoring" / "rules" / "cdi-alerts.yml").read_text(
        encoding="utf-8"))["groups"][0]["rules"]  # fmt: skip
    rule = next(r for r in rules if r["alert"] == "CdiReaderDisagreementDrift")
    assert rule["labels"]["severity"] == "ticket" and rule["for"] == "15m"
    assert 'cdi_reader_disagreement_ratio{window="1h"}' in rule["expr"]
    assert "#cdireaderdisagreementdrift" in rule["annotations"]["runbook_url"]


# ------------------------------------------------------------------ non-functional


def test_nfr1_agreement_never_overrides_a_failed_picture_check() -> None:
    for again in (None, "Telma 40"):
        a = _assess("Telma 40", "Telma 40", ground=_shows("Telma 4"), again=again)
        assert a.decision == "review"
    source = inspect.getsource(hierarchy.assess_fact)
    assert "comparison.agrees and not out.reasons" in source


def test_nfr2_the_readings_are_never_edited() -> None:
    readings = {TROCR: "Telma 4O", QWEN: "telma 40"}
    before = dict(readings)
    a = hierarchy.assess_fact(readings, crop_png=b"x", ground=_shows("40"))
    assert readings == before and a.value == "telma 40"
    page = P.PageReadings(lines=[_line("Telma 4O", "Telma 40")])
    P.decide_lines(page, ground=_shows("40"))
    assert page.lines[0].readings[TROCR].text == "Telma 4O"  # still as TrOCR read it


@pytest.mark.xfail(strict=True, reason="RD-S3 NFR3: the thresholds are ASSUMPTIONS written down "
                   "beside them - how alike two words must be to count as a slip (0.75; it only "
                   "changes a label: both go to review) and the drift alarm's 2x + 10 points over "
                   "20 lines. ENT-S3 asks for them to come from measurement on real readings, "
                   "and there are none yet.")  # fmt: skip
def test_nfr3_the_thresholds_come_from_measurement() -> None:
    assert (ROOT / "docs" / "evidence" / "RD-S3" / "thresholds.md").exists()


def test_nothing_of_this_runs_unless_two_readers_are_on() -> None:
    shipped = Settings(_env_file=None)
    assert shipped.two_readers is False and shipped.second_opinion is False
    stage = inspect.getsource(ocr.ocr_document)
    assert "if readers:" in stage  # the readers exist only when two_readers is on
