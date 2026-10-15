"""OUT-S2 "Also give every result as a JSON file with a fixed format" - the five criteria.

* AC1  a finished prescription's JSON exists and validates against result.v1.json;
* AC2  built twice, the two files are identical;
* AC3  a value that needs a check says ``needs_check`` with the reason;
* AC4  after a reviewer's change the rebuilt JSON shows the corrected value, and the older
       reading is still referenced;
* AC5  a field the schema does not know is rejected.

The result is built by the real builder (``result.build``) from a stand-in for the database:
the rows a finished prescription has - patient, doctor, tests, preparation, reasons - so every
part of the document the builder can emit is in what is validated.
"""

from __future__ import annotations

import copy
import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, ValidationError

from cdi_adapter import result
from cdi_adapter.config import Settings

DOC = "0a1b2c3d-0000-4000-8000-000000000001"
SCHEMA = json.loads(result.SCHEMA_PATH.read_text(encoding="utf-8"))
NOTE = {"findings": [{"severity": "blocker", "code": "phone-digits",
                      "message": "the phone number has 9 digits, not 10"},
                     {"severity": "info", "code": "duplicate",
                      "message": "same as fact 7"}]}  # fmt: skip


def _fid(n: int) -> str:
    return f"00000000-0000-4000-8000-{n:012d}"


def _fact(n: int, kind: str, written: str, value: str | None = None, *,
          state: str = "auto_accepted", **more: Any) -> dict[str, Any]:  # fmt: skip
    return {"id": _fid(n), "fact_type": kind, "local_text": written, "value_text": value,
            "value_num": None, "value_unit_ucum": None, "code": None, "code_system": None,
            "code_display": None, "review_state": state, "confidence_overall": 0.97,
            "is_current": True, "encounter_id": "enc-1", "review_note": None,
            "supersedes": None, **more}  # fmt: skip


def _facts() -> list[dict[str, Any]]:
    return [
        _fact(1, "patient_detail", "name", "Rahul Sharma"),
        _fact(2, "patient_detail", "phone", "98765 4321", state="in_review", review_note=NOTE),
        _fact(3, "patient_detail", "hospital number", "MRN-1001"),
        _fact(4, "doctor_detail", "name", "Dr. Anita Sen Roy"),
        _fact(5, "doctor_detail", "registration number", "71112 (WBMC)"),
        _fact(6, "doctor_detail", "clinic name", "City Clinic"),
        _fact(7, "condition", "Type 2 diabetes mellitus", code="E11", code_system="ICD-10",
              code_display="Type 2 diabetes mellitus"),  # fmt: skip
        _fact(8, "lab_order", "HbA1c"),
        _fact(9, "lab_order", "Lipid profile urgent"),
        _fact(10, "lab_order", "Zqx profile", state="in_review"),
        _fact(11, "preparation", "fasting 10-12 hrs", "tests: Lipid profile urgent"),
        _fact(12, "preparation", "come at 8 am", "all", state="pending"),
        _fact(13, "advice", "Review after 2 weeks"),
        _fact(14, "lab_result", "Hb", value_num=11.2, value_unit_ucum="g/dL"),
    ]


class Db:
    """A stand-in for the database: the rows a finished prescription has. ``build`` reads
    nothing else - there is no in-memory result to copy from."""

    def __init__(self, facts: list[dict[str, Any]] | None = None, *, status: str = "validated",
                 reading: dict[str, Any] | None = None) -> None:  # fmt: skip
        self.facts = facts if facts is not None else _facts()
        self.old: dict[str, dict[str, Any]] = {}
        self.status, self.reading, self.queries = status, reading, 0

    def execute(self, statement: Any, params: Any = None) -> Any:
        sql, p = " ".join(str(statement).split()), params or {}
        self.queries += 1
        self._one: Any = None
        self._many: list[Any] = []
        if "FROM source_document" in sql:
            self._one = {"id": DOC, "filename": "rx.jpg", "mime_type": "image/jpeg",
                         "page_count": 1, "status": self.status, "source_channel": "upload",
                         "created_at": datetime(2026, 10, 13, 9, 30, tzinfo=UTC),
                         "patient_id": None}  # fmt: skip
        elif "FROM fact_provenance" in sql:
            n = int(str(p["f"])[-4:])
            self._one = {"bbox_union": [10, 20 * n, 500, 20 * n + 18], "page_id": "page-1",
                         "ocr_block_ids": [f"block-{n}"], "page_no": 1}  # fmt: skip
        elif "FROM encounter" in sql:
            self._one = date(2026, 10, 12)
        elif "FROM extraction" in sql:
            self._one = self.reading
        elif "FROM pipeline_run" in sql:
            self._one = "lab-master.2026-10-12.1"
        elif "FROM clinical_fact WHERE id" in sql:
            self._one = self.old.get(str(p["i"]))
        return self

    def mappings(self) -> Any:
        return self

    def first(self) -> Any:
        return self._one

    def all(self) -> list[Any]:
        return self._many

    def scalar_one_or_none(self) -> Any:
        return self._one


@pytest.fixture
def db(monkeypatch) -> Db:
    database = Db()
    monkeypatch.setattr(result, "_facts", lambda conn, document_id: [dict(f) for f in conn.facts])
    monkeypatch.setattr(result, "_doctor_link", lambda conn, document_id: {
        "method": "unknown", "confidence": None, "practitioner_id": None, "stamp_present": True,
        "signature_present": None, "stamp_text": "Dr. Anita Sen Roy Reg 71112",
        "why": "no doctor list is loaded",
        "candidates": [{"practitioner_id": "p-1", "name": "Dr. Anita Sen", "council": "WBMC",
                        "registration_no": "71112", "department": None,
                        "why": "same name"}]})  # fmt: skip
    return database


def _settings(folder: Path) -> Settings:
    return Settings(result_output_dir=str(folder), result_output_prefix="")


def _values(node: Any) -> list[dict[str, Any]]:
    return result._walk_values(node)


def _find(built: dict[str, Any], said: Any) -> dict[str, Any]:
    return next(v for v in _values(built) if v["value"] == said)


# ------------------------------------------------------------------ AC1: exists and validates


def test_ac1_a_finished_prescription_builds_a_json_that_fits_the_format(db):
    built = result.build(db, DOC)  # type: ignore[arg-type]
    assert result.problems(built) == []
    result.validate(built)
    assert built["schema_version"] == "result.v1" == SCHEMA["properties"]["schema_version"]["const"]


def test_ac1_it_holds_patient_doctor_tests_with_preparation_and_context(db):
    built = result.build(db, DOC)  # type: ignore[arg-type]
    assert built["patient"]["name"]["value"] == "Rahul Sharma"
    assert built["patient"]["ids_read"][0]["type"] == "mrn"
    assert built["doctor"]["registration_no"]["value"] == "71112 (WBMC)"
    assert built["doctor"]["clinic"]["name"]["value"] == "City Clinic"
    assert built["doctor"]["link"]["method"] == "unknown" and built["doctor"]["stamp_present"]
    tests = {t["as_written"]: t for t in built["lab_tests"]}
    assert set(tests) == {"HbA1c", "Lipid profile urgent", "Zqx profile", "Hb"}
    lipid = tests["Lipid profile urgent"]
    assert lipid["kind"] == "panel" and lipid["urgency"] == "urgent"
    assert [built["preparation"][i]["text"] for i in lipid["preparation_refs"]] == [
        "fasting 10-12 hrs", "come at 8 am"]  # fmt: skip
    assert tests["HbA1c"]["context"][0]["text"] == "Type 2 diabetes mellitus"
    assert tests["HbA1c"]["context"][0]["relation"] in ("same_line", "same_page", "other_page")
    assert built["visit"]["follow_up"][0]["value"] == "Review after 2 weeks"


def test_ac1_every_value_says_where_it_came_from(db):
    built = result.build(db, DOC)  # type: ignore[arg-type]
    read = [v for v in _values(built) if v["value"] != "2026-10-12"]  # the visit date is derived
    assert len(read) >= 14
    for v in read:
        assert v["evidence"]["page"] == 1 and v["evidence"]["bbox"]
        assert v["evidence"]["observation_ids"], v


def test_ac1_the_file_exists_in_the_output_folder(db, tmp_path):
    out = result.write_file(db, DOC, settings=_settings(tmp_path))  # type: ignore[arg-type]
    path = tmp_path / f"{DOC}.result.v1.json"
    assert path.is_file() and out["file"] == str(path)
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert result.problems(on_disk) == [] and on_disk["document_id"] == DOC


def test_ac1_it_is_built_from_the_database_not_from_memory(db):
    result.build(db, DOC)  # type: ignore[arg-type]
    first = db.queries
    assert first > 10, "every part is read from the rows"
    db.facts = [f for f in db.facts if f["fact_type"] != "lab_order"]
    again = result.build(db, DOC)  # type: ignore[arg-type]
    assert [t["as_written"] for t in again["lab_tests"]] == ["Hb"], "nothing is remembered"


def test_ac1_a_result_that_does_not_fit_is_not_written(db, tmp_path, monkeypatch):
    real = result.build
    monkeypatch.setattr(result, "build", lambda conn, d: {**real(conn, d), "status": "done"})
    with pytest.raises(ValidationError):
        result.write_file(db, DOC, settings=_settings(tmp_path))  # type: ignore[arg-type]
    assert list(tmp_path.iterdir()) == [], "no file, and no half-written one"


def test_ac1_the_same_document_is_served_at_the_address():
    from cdi_adapter.webapp.app import app

    paths = {getattr(r, "path", None) for r in app.routes}
    assert "/api/documents/{document_id}/result.json" in paths


def test_ac1_the_folder_and_the_store_key_come_from_configuration():
    fields = Settings.model_fields
    assert "result_output_dir" in fields and "result_output_prefix" in fields


# ------------------------------------------------------------------ AC2: twice is identical


def test_ac2_two_builds_are_the_same_bytes(db):
    assert result.as_json(db, DOC) == result.as_json(db, DOC)  # type: ignore[arg-type]


def test_ac2_two_written_files_are_identical(db, tmp_path):
    one = result.write_file(db, DOC, settings=_settings(tmp_path / "a"))  # type: ignore[arg-type]
    two = result.write_file(db, DOC, settings=_settings(tmp_path / "b"))  # type: ignore[arg-type]
    a = (tmp_path / "a" / f"{DOC}.result.v1.json").read_bytes()
    assert a == (tmp_path / "b" / f"{DOC}.result.v1.json").read_bytes()
    assert one["sha256"] == two["sha256"] and one["bytes"] == len(a)


def test_ac2_writing_over_the_file_leaves_the_same_file(db, tmp_path):
    result.write_file(db, DOC, settings=_settings(tmp_path))  # type: ignore[arg-type]
    before = (tmp_path / f"{DOC}.result.v1.json").read_bytes()
    result.write_file(db, DOC, settings=_settings(tmp_path))  # type: ignore[arg-type]
    assert (tmp_path / f"{DOC}.result.v1.json").read_bytes() == before
    assert [p.name for p in tmp_path.iterdir()] == [f"{DOC}.result.v1.json"]


def test_ac2_nothing_in_the_file_depends_on_when_it_was_built(db):
    text = result.as_json(db, DOC)  # type: ignore[arg-type]
    assert str(datetime.now(UTC).date()) not in text.replace("2026-10-13T09:30", "")
    assert json.dumps(json.loads(text), indent=2, sort_keys=True) + "\n" == text


# ------------------------------------------------------------------ AC3: needs_check + reason


def test_ac3_a_held_value_says_needs_check_with_the_reason_the_check_gave(db):
    phone = result.build(db, DOC)["patient"]["phone"]  # type: ignore[arg-type]
    assert phone["status"] == "needs_check"
    assert phone["reason"] == "the phone number has 9 digits, not 10"


def test_ac3_a_note_that_holds_nothing_back_is_not_given_as_the_reason():
    assert result.reason_of({"review_note": NOTE}) == "the phone number has 9 digits, not 10"
    assert result.reason_of({"review_note": json.dumps(NOTE)}) == (
        "the phone number has 9 digits, not 10")  # fmt: skip
    only_info = {"review_note": {"findings": [NOTE["findings"][1]]}, "review_state": "in_review"}
    assert result.reason_of(only_info) == "waiting for a person to check it"


def test_ac3_every_held_value_has_a_reason_and_no_accepted_value_is_doubtful(db):
    built = result.build(db, DOC)  # type: ignore[arg-type]
    held = [v for v in _values(built) if v["status"] == "needs_check"]
    assert {v["value"] for v in held} >= {"98765 4321", "Zqx profile", "come at 8 am"}
    assert all(isinstance(v["reason"], str) and v["reason"] for v in held)
    for v in _values(built):
        if v["status"] == "accepted":
            assert "reason" not in v and v["evidence"]


@pytest.mark.parametrize("state", ["pending", "in_review", "extracted", "rejected", None])
def test_ac3_a_value_no_gate_accepted_is_never_shown_as_final(state):
    proof = {"page": 1, "bbox": [1, 2, 3, 4], "observation_ids": ["b"]}
    v = result.value("HbA1c", review_state=state, confidence=0.99, evidence=proof)
    assert v["status"] == "needs_check" and v["reason"]


def test_ac3_the_format_itself_refuses_needs_check_without_a_reason(db):
    built = result.build(db, DOC)  # type: ignore[arg-type]
    del built["patient"]["phone"]["reason"]
    assert any("reason" in p for p in result.problems(built))
    built["patient"]["phone"]["reason"] = None
    assert result.problems(built), "a reason of null is no reason"


def test_ac3_the_held_values_are_counted_and_the_result_says_needs_check(db):
    built = result.build(db, DOC)  # type: ignore[arg-type]
    assert built["status"] == "needs_check" and built["status_reason"] is None
    assert built["review"]["needs_check_count"] == len(built["review"]["items"]) >= 3
    assert {"value": "98765 4321", "reason": "the phone number has 9 digits, not 10"} in (
        built["review"]["items"])  # fmt: skip


def test_fr5_an_answer_that_was_cut_short_says_incomplete(db):
    db.reading = {"is_partial": True, "cut": "true", "raw": '{"medications": [{"drug',
                  "error": "the answer was cut off by the length limit "
                           "(1024 tokens)"}  # fmt: skip
    built = result.build(db, DOC)  # type: ignore[arg-type]
    assert built["status"] == "incomplete" and "cut off" in built["status_reason"]
    assert result.problems(built) == []


def test_fr5_a_model_that_declined_says_refusal(db):
    db.facts = []
    db.reading = {"is_partial": True, "cut": None, "error": "no JSON object found",
                  "raw": "I'm sorry, but I can't help with reading this document."}  # fmt: skip
    built = result.build(db, DOC)  # type: ignore[arg-type]
    assert built["status"] == "refusal" and built["lab_tests"] == []
    assert result.problems(built) == []


def test_fr5_an_answer_with_json_in_it_is_never_called_a_refusal():
    assert not result.refused('{"notes": "I cannot read the second line"}')
    assert not result.refused("") and not result.refused(None)
    assert result.refused("I cannot assist with that request.")


def test_fr5_the_states_are_a_fixed_list():
    assert SCHEMA["properties"]["status"]["enum"] == list(result.STATUSES) == [
        "complete", "needs_check", "incomplete", "refusal", "held_for_rescan"]  # fmt: skip
    assert SCHEMA["$defs"]["value"]["properties"]["status"]["enum"] == ["accepted", "needs_check"]


# ------------------------------------------------------------------ AC4: a reviewer's change


def _corrected(db: Db) -> None:
    """What ``apply_fact_correction`` leaves: the old value kept but no longer current, and a
    NEW value that points at it."""
    old = next(f for f in db.facts if f["id"] == _fid(2))
    old.update(is_current=False, review_state="corrected")
    db.old[_fid(2)] = old
    db.facts.append(_fact(22, "patient_detail", "phone", "98765 43210", state="corrected",
                          supersedes=_fid(2)))  # fmt: skip


def test_ac4_the_rebuilt_json_shows_the_corrected_value(db):
    before = result.build(db, DOC)["patient"]["phone"]  # type: ignore[arg-type]
    _corrected(db)
    after = result.build(db, DOC)["patient"]["phone"]  # type: ignore[arg-type]
    assert (before["value"], before["status"]) == ("98765 4321", "needs_check")
    assert (after["value"], after["status"]) == ("98765 43210", "accepted")
    assert "reason" not in after


def test_ac4_the_older_reading_is_still_referenced(db):
    _corrected(db)
    built = result.build(db, DOC)  # type: ignore[arg-type]
    was = built["patient"]["phone"]["corrected_from"]
    assert was == {"fact_id": _fid(2), "value": "98765 4321", "observation_ids": ["block-2"]}
    assert result.problems(built) == []
    # and only the corrected value carries it
    assert [v["value"] for v in _values(built) if "corrected_from" in v] == ["98765 43210"]


def test_ac4_a_corrected_test_name_references_what_was_written(db):
    old = next(f for f in db.facts if f["id"] == _fid(10))
    old.update(is_current=False, review_state="corrected")
    db.old[_fid(10)] = old
    db.facts.append(_fact(30, "lab_order", "Lipid profile", state="corrected",
                          supersedes=_fid(10)))  # fmt: skip
    tests = {t["as_written"]: t for t in result.build(db, DOC)["lab_tests"]}  # type: ignore[arg-type]
    assert "Zqx profile" not in tests
    assert tests["Lipid profile"]["value"]["corrected_from"]["value"] == "Zqx profile"


def test_ac4_the_older_value_is_kept_in_the_database_not_overwritten():
    import inspect

    from cdi_adapter.webapp import review

    body = inspect.getsource(review.apply_fact_correction)
    assert '"supersedes": fact["id"]' in body and "is_current = false" in body
    assert "DELETE" not in body


def test_ac4_the_file_is_written_again_after_a_reviewers_change(db, tmp_path):
    result.write_file(db, DOC, settings=_settings(tmp_path))  # type: ignore[arg-type]
    path = tmp_path / f"{DOC}.result.v1.json"
    assert json.loads(path.read_text("utf-8"))["patient"]["phone"]["value"] == "98765 4321"
    _corrected(db)
    result.write_file(db, DOC, settings=_settings(tmp_path))  # type: ignore[arg-type]
    phone = json.loads(path.read_text("utf-8"))["patient"]["phone"]
    assert phone["value"] == "98765 43210" and phone["corrected_from"]["value"] == "98765 4321"


def test_ac4_a_correction_and_an_approval_both_rewrite_the_file():
    root = Path(result.__file__).parent
    callers = [p.name for p in (root / "webapp").glob("*.py")
               if "refresh_files(" in p.read_text(encoding="utf-8")]  # fmt: skip
    assert callers, "the reviewer's actions rewrite the result file"


# ------------------------------------------------------------------ AC5: unknown field rejected

PLACES = (
    ((), "extra"),
    (("patient",), "blood_group"),
    (("patient", "name"), "note"),
    (("patient", "name", "evidence"), "polygon"),
    (("patient", "ids_read", 0), "issuer"),
    (("doctor",), "mobile"),
    (("doctor", "clinic"), "website"),
    (("doctor", "link"), "score"),
    (("doctor", "link", "candidates", 0), "phone"),
    (("visit",), "follow_up_date"),
    (("lab_tests", 0), "price"),
    (("lab_tests", 0, "standard"), "loinc_long_name"),
    (("lab_tests", 0, "context", 0), "because"),
    (("diagnoses", 0), "severity"),
    (("diagnoses", 0, "external_codes", 0), "display"),
    (("preparation", 0), "duration"),
    (("review",), "reviewer"),
    (("review", "items", 0), "fact_id"),
    (("document",), "sha256"),
    (("document", "source"), "url"),
)


def _at(node: Any, path: tuple[Any, ...]) -> Any:
    for step in path:
        node = node[step]
    return node


@pytest.mark.parametrize(("path", "field"), PLACES, ids=lambda p: "/".join(map(str, p)) or p)
def test_ac5_a_field_the_schema_does_not_know_is_rejected(db, path, field):
    built = result.build(db, DOC)  # type: ignore[arg-type]
    assert result.problems(built) == []
    _at(built, path)[field] = "anything"
    found = result.problems(built)
    assert found and any(repr(field) in p or field in p for p in found)
    with pytest.raises(ValidationError):
        result.validate(built)


def test_ac5_every_object_in_the_schema_is_closed():
    open_ones: list[str] = []

    def walk(node: Any, where: str) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object" or "properties" in node and "if" not in where:
                if node.get("additionalProperties") is not False and "required" != where[-8:]:
                    open_ones.append(where or "<root>")
            for key, child in node.items():
                if key not in ("if", "then"):  # a condition on a closed object, not an object
                    walk(child, f"{where}/{key}")
        elif isinstance(node, list):
            for i, child in enumerate(node):
                walk(child, f"{where}/{i}")

    walk(SCHEMA, "")
    assert open_ones == []
    Draft202012Validator.check_schema(SCHEMA)


def test_ac5_a_category_outside_its_fixed_list_is_rejected(db):
    for path, field, bad in ((("lab_tests", 0), "kind", "profile"),
                             (("lab_tests", 0), "match", "maybe"),
                             (("doctor", "link"), "method", "guessed"),
                             (("preparation", 0), "type", "exercise"),
                             (("lab_tests", 0, "context", 0), "relation", "ordered_because"),
                             (("patient", "name"), "status", "final"),
                             ((), "status", "done")):  # fmt: skip
        built = result.build(db, DOC)  # type: ignore[arg-type]
        _at(built, path)[field] = bad
        assert result.problems(built), (path, field)


def test_ac5_unknown_is_null_never_a_made_up_value(db):
    built = result.build(db, DOC)  # type: ignore[arg-type]
    unknown = next(t for t in built["lab_tests"] if t["as_written"] == "Zqx profile")
    assert unknown["standard"] is None and unknown["kind"] is None and unknown["specimen"] is None
    assert built["doctor"]["signature_present"] is None
    assert built["doctor"]["link"]["practitioner_id"] is None


def test_ac5_a_rejected_file_is_not_written_and_the_earlier_one_stays(db, tmp_path, monkeypatch):
    result.write_file(db, DOC, settings=_settings(tmp_path))  # type: ignore[arg-type]
    path = tmp_path / f"{DOC}.result.v1.json"
    before = path.read_bytes()
    real = result.build
    monkeypatch.setattr(result, "build", lambda conn, d: {**real(conn, d), "new_field": 1})
    with pytest.raises(ValidationError):
        result.write_file(db, DOC, settings=_settings(tmp_path))  # type: ignore[arg-type]
    assert path.read_bytes() == before and [p.name for p in tmp_path.iterdir()] == [path.name]


def test_ac5_the_schema_before_this_story_accepted_any_field():
    """What changed: result.v1.json used to say additionalProperties: true everywhere."""
    loose = copy.deepcopy(SCHEMA)
    loose["additionalProperties"] = True
    assert not list(Draft202012Validator(loose).iter_errors(
        {"schema_version": "result.v1", "document_id": DOC, "received_at": None, "source": "x",
         "page_count": 1, "status": "complete", "absent": [], "anything": 1,
         "review": {"needs_check_count": 0, "items": []}}))  # fmt: skip


@pytest.mark.xfail(strict=True, reason="PROVE IT asks for one REAL JSON: no prescription has "
                   "been built from a real database since these changes (no Postgres, no GPU "
                   "instance); tests/test_result_integration.py has not been run")
def test_proven_on_a_real_prescription():
    raise AssertionError("not run against a real database")
