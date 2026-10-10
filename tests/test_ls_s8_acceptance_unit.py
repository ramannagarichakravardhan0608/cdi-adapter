"""LS-S8 acceptance: the same database rows and JSON as an upload, and where each result came
from.

One group of tests per acceptance criterion. What a unit test can reach: the listener loop on a
real folder, the ledger that links a dropped file to its document, the function that builds a
result's ``source``, the contract's JSON schema, and the search's own logic. **What it cannot:
the database rows and the JSON of a real result** - ``result.build`` reads PostgreSQL. No
automated test covers that yet; it was checked by hand on a running instance on 2026-10-09
(``docs/test-LS-S8.md``).
"""

from __future__ import annotations

import hashlib
import inspect
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from cdi_adapter import result
from cdi_adapter.config import Settings
from cdi_adapter.listener import connectors as C
from cdi_adapter.listener import ledger as L
from cdi_adapter.listener import service as S
from cdi_adapter.webapp import app as web
from cdi_adapter.webapp import auth, jobs
from cdi_adapter.webapp.page import upload_page
from tests.helpers import blank_pdf
from tests.test_listener_connectors_unit import Drive

ROOT = Path(__file__).resolve().parents[1]
WHEN = datetime(2026, 10, 9, 9, 30, tzinfo=UTC)
SCHEMA = json.loads((ROOT / "schemas" / "contract" / "result.v1.json").read_text("utf-8"))
DOC = "0a1b2c3d-0000-4000-8000-00000000000"


class Pipeline:
    """Stands where the pipeline is: one stored document per file's bytes, as ingest keeps
    one document per sha256 however often the bytes arrive."""

    def __init__(self) -> None:
        self.documents: dict[str, str] = {}
        self.jobs = 0

    def document_for(self, raw: bytes) -> str:
        key = hashlib.sha256(raw).hexdigest()
        return self.documents.setdefault(key, f"{DOC}{len(self.documents) + 1}")

    def reader(self) -> S.Reader:
        def read(name: str, raw: bytes, mime: str) -> S.Outcome:
            self.jobs += 1
            job = SimpleNamespace(
                id=f"job-{self.jobs}",
                state="review",
                docs=[
                    SimpleNamespace(
                        status="done", document_id=self.document_for(raw), error=None, doc_type="rx"
                    )
                ],
            )
            return S.outcome_of(job)  # the real mapping from a finished job

        return S.Reader(read=read)


@pytest.fixture
def folder(tmp_path) -> Path:
    root = tmp_path / "Clinic" / "Prescriptions"
    root.mkdir(parents=True)
    return root


def _listener(folder: Path, pipeline: Pipeline, **more: Any) -> S.Listener:
    settings = Settings(listener_connector="local", listener_local_root=str(folder),
                        listener_inbox="")  # fmt: skip
    connector = C.get_connector(settings)
    connector.ensure_folders()
    return S.Listener(connector, pipeline.reader(), now=lambda: WHEN, **more)


def _run(listener: S.Listener) -> dict[str, Any]:
    listener.poll()
    return listener.poll()


def _rows(listener: S.Listener) -> list[L.Entry]:
    return sorted(listener.ledger._rows.values(), key=lambda r: r.original_name or "")  # type: ignore[attr-defined]


def _as_arrival(row: L.Entry) -> dict[str, Any]:
    return {"source_id": row.source_id, "folder": row.folder, "original_name": row.original_name,
            "name": row.name}  # fmt: skip


# ------------------------------------------------------------------ AC1
# GIVEN a file is read from the folder WHEN the result is saved
# THEN the rows and the JSON have the same shape as an uploaded prescription


def test_ac1_the_source_block_has_the_same_keys_for_an_upload_and_a_dropped_file() -> None:
    uploaded = result.source_of("upload", "rx.png", [])
    dropped = result.source_of("listener", "rx.png", [
        {"source_id": "local:/srv/clinic/in", "folder": "/srv/clinic/in",
         "original_name": "rx.png", "name": "rx.png"}])  # fmt: skip
    assert set(uploaded) == set(dropped) == {"channel", "drive", "folder", "file_name"}
    assert uploaded == {"channel": "upload", "drive": None, "folder": None, "file_name": "rx.png"}


def _result_with(source: dict[str, Any], top: str) -> dict[str, Any]:
    """A minimal result document around a source block: what build() makes, without facts."""
    return {"schema_version": "result.v1", "document_id": f"{DOC}1",
            "received_at": "2026-10-09T09:30:00+00:00", "source": top, "page_count": 1,
            "status": "needs_check", "review": {"needs_check": 0, "accepted": 0},
            "absent": list(result.ABSENT), "document": {"source": source}}  # fmt: skip


def test_ac1_both_validate_against_the_one_contract_schema() -> None:
    validator = Draft202012Validator(SCHEMA)
    uploaded = _result_with(result.source_of("upload", "rx.png", []), "upload_screen")
    dropped = _result_with(result.source_of("listener", "rx.png", [
        {"source_id": "onedrive:d1:f1", "folder": "/Prescriptions/Incoming",
         "original_name": "rx.png", "name": "rx.png"}]), "listener")  # fmt: skip
    for document in (uploaded, dropped):
        errors = [e.message for e in validator.iter_errors(document)
                  if "source" in list(e.absolute_path) or not e.absolute_path]  # fmt: skip
        assert errors == [], errors
    assert set(uploaded) == set(dropped)  # the same top-level shape


def test_ac1_the_result_is_built_by_one_function_whatever_the_channel() -> None:
    """Nothing in the builder is different for a dropped file except what the source says."""
    source = inspect.getsource(result.build)
    assert "source_of(" in source and source.count("listener") == 0
    branches = [
        ln for ln in inspect.getsource(result).splitlines() if 'channel == "listener"' in ln
    ]
    assert len(branches) == 1  # the one place: whose arrival is the source


def test_ac1_the_rows_come_through_the_same_pipeline_as_an_upload() -> None:
    """The listener writes no clinical rows of its own: it submits a job, like the screen."""
    for name in ("service.py", "ledger.py", "recovery.py"):
        code = (ROOT / "src" / "cdi_adapter" / "listener" / name).read_text(encoding="utf-8")
        for table in ("clinical_fact", "source_document SET", "INSERT INTO source_document",
                      "document_page", "rv_item"):  # fmt: skip
            assert table not in code, (name, table)
    runner = inspect.getsource(jobs)
    assert 'source = {} if job.channel == "upload" else {"source_channel": job.channel}' in runner


def test_ac1_a_json_file_is_written_for_each_result() -> None:
    """Was an expected failure until the file writer (OUT-S2) was built, 2026-10-09: when a
    document is done the runner writes its result as a JSON file - for a dropped file exactly
    as for an upload. The writer itself is tested in tests/test_result_file_unit.py."""
    runner = inspect.getsource(jobs)
    assert "s.result_file(str(d.document_id))" in runner
    assert (
        "channel"
        not in runner[runner.index("s.result_file(") - 200 : runner.index("s.result_file(")]
    )
    assert callable(result.write_file) and result.file_name("d") == "d.result.v1.json"


# ------------------------------------------------------------------ AC2
# GIVEN the result is opened WHEN its source is read
# THEN it shows channel 'listener', the drive, the folder and the original file name


def test_ac2_a_dropped_files_result_says_listener_drive_folder_and_file_name(folder) -> None:
    pipeline = Pipeline()
    (folder / "rx-monday.pdf").write_bytes(blank_pdf(1))
    listener = _listener(folder, pipeline)
    assert _run(listener)["succeeded"] == ["rx-monday.pdf"]
    (row,) = _rows(listener)
    source = result.source_of("listener", "rx-monday.pdf", [_as_arrival(row)])
    assert source == {
        "channel": "listener",
        "drive": "local",
        "folder": folder.resolve().as_posix(),
        "file_name": "rx-monday.pdf",
    }


def test_ac2_the_dropped_file_is_linked_to_the_document_it_became(folder) -> None:
    pipeline, raw = Pipeline(), blank_pdf(1)
    (folder / "rx.pdf").write_bytes(raw)
    listener = _listener(folder, pipeline)
    _run(listener)
    (row,) = _rows(listener)
    assert row.document_id == pipeline.document_for(raw) and row.job_id == "job-1"
    assert row.state == "succeeded"


def test_ac2_the_original_name_is_kept_when_the_file_is_renamed_on_the_way(folder) -> None:
    """A second rx.pdf is kept in ``success`` as rx.1.pdf; its result still says rx.pdf."""
    listener = _listener(folder, Pipeline())
    for n in (1, 2):
        (folder / "rx.pdf").write_bytes(blank_pdf(1, size=(200 + n, 280)))
        _run(listener)
    rows = sorted(_rows(listener), key=lambda r: r.name)
    assert [r.name for r in rows] == ["rx.1.pdf", "rx.pdf"]  # where they are now
    assert [r.original_name for r in rows] == ["rx.pdf", "rx.pdf"]  # what they were dropped as
    for row in rows:
        assert result.source_of("listener", "x", [_as_arrival(row)])["file_name"] == "rx.pdf"


@pytest.mark.parametrize(("kind", "drive", "folder_says"), [
    ("local", "local", None), ("onedrive", "onedrive", "/Clinic/Incoming"),
    ("sharepoint", "sharepoint", "/Clinic/Incoming"), ("gdrive", "gdrive", "folder root-folder"),
])  # fmt: skip
def test_ac2_every_kind_of_drive_says_which_drive_and_which_folder(
    kind: str, drive: str, folder_says: str | None, tmp_path
) -> None:
    made = Drive(kind, tmp_path, listener_root="Clinic/Incoming")
    if kind in ("onedrive", "sharepoint"):
        made.put("Clinic/Incoming/")  # the watched folder has to be there on the drive
    made.connector.ensure_folders()
    label = made.connector.folder_label
    assert label == (folder_says or (tmp_path / "watch").resolve().as_posix())
    arrival = {"source_id": made.connector.source_id, "folder": label,
               "original_name": "rx.pdf", "name": "rx.pdf"}  # fmt: skip
    source = result.source_of("listener", "rx.pdf", [arrival])
    assert (source["channel"], source["drive"], source["folder"]) == ("listener", drive, label)


def test_ac2_a_drive_class_of_ones_own_with_no_label_still_gives_a_folder() -> None:
    source = result.source_of("listener", "rx.pdf", [
        {"source_id": "shelf:north/3", "folder": None, "original_name": "rx.pdf",
         "name": "rx.pdf"}])  # fmt: skip
    assert (source["drive"], source["folder"]) == ("shelf", "north/3")


def test_ac2_the_schema_has_a_place_for_each_of_the_four() -> None:
    fields = SCHEMA["properties"]["document"]["properties"]["source"]["properties"]
    assert set(fields) >= {"channel", "drive", "folder", "file_name"}


def test_ac2_the_runner_records_the_channel_on_the_stored_document() -> None:
    from cdi_adapter.webapp import jobstore

    assert jobstore.channel_of("listener") == "listener"
    assert S.outcome_of(SimpleNamespace(id="j", state="review", docs=[SimpleNamespace(
        status="done", document_id="doc-9")])).document_id == "doc-9"  # fmt: skip


def test_ac2_the_result_file_is_written_again_once_the_file_is_linked(folder) -> None:
    """Found on a running pipeline, 2026-10-09: the pipeline writes the result file when the
    document is finished, which is before the listener has linked the dropped file to it - so
    that file said channel 'listener' with no drive and no folder. The listener now tells the
    reader as soon as the link is made, and the reader writes the file again."""
    pipeline, told = Pipeline(), []
    base = pipeline.reader()
    raw = blank_pdf(1)
    (folder / "rx.pdf").write_bytes(raw)
    listener = _listener(folder, pipeline)
    listener.reader = S.Reader(read=base.read, linked=told.append)
    _run(listener)
    assert told == [pipeline.document_for(raw)]  # after the link, with the document it points at
    (folder / "rx-again.pdf").write_bytes(raw)  # the same bytes again: the result gains an arrival
    _run(listener)
    assert told == [pipeline.document_for(raw)] * 2
    real = inspect.getsource(S.pipeline_reader)
    assert "result.refresh_files([document_id])" in real and "linked=linked" in real


def test_ac2_a_refresh_that_fails_does_not_fail_the_file(folder) -> None:
    def broken(document_id: str) -> None:
        raise OSError("document store unreachable")

    pipeline = Pipeline()
    (folder / "rx.pdf").write_bytes(blank_pdf(1))
    listener = _listener(folder, pipeline)
    listener.reader = S.Reader(read=pipeline.reader().read, linked=broken)
    assert _run(listener)["succeeded"] == ["rx.pdf"]


# ------------------------------------------------------------------ AC3
# GIVEN the same file is uploaded and dropped WHEN both are processed THEN there is one result


def test_ac3_the_same_file_dropped_twice_is_one_document(folder) -> None:
    pipeline, raw = Pipeline(), blank_pdf(1)
    listener = _listener(folder, pipeline)
    for name in ("rx.pdf", "rx-again.pdf"):
        (folder / name).write_bytes(raw)
        _run(listener)
    first, second = sorted(_rows(listener), key=lambda r: r.state, reverse=True)
    assert (first.state, second.state) == ("succeeded", "duplicate")
    assert first.document_id == second.document_id and pipeline.jobs == 1  # one result


def test_ac3_uploaded_first_then_dropped_is_the_uploads_one_document(folder) -> None:
    pipeline, raw = Pipeline(), blank_pdf(1)
    uploaded = pipeline.document_for(raw)  # the screen got there first
    (folder / "rx.pdf").write_bytes(raw)
    listener = _listener(folder, pipeline)
    _run(listener)
    (row,) = _rows(listener)
    assert row.document_id == uploaded and len(pipeline.documents) == 1  # still one document


def test_ac3_that_one_result_says_it_was_uploaded_and_also_dropped(folder) -> None:
    pipeline, raw = Pipeline(), blank_pdf(1)
    pipeline.document_for(raw)
    (folder / "rx.pdf").write_bytes(raw)
    listener = _listener(folder, pipeline)
    _run(listener)
    source = result.source_of(
        "upload", "rx-from-desk.pdf", [_as_arrival(r) for r in _rows(listener)]
    )
    assert (source["channel"], source["drive"], source["file_name"]) == (
        "upload",
        None,
        "rx-from-desk.pdf",
    )
    assert source["also_received"] == [{"channel": "listener", "drive": "local",
                                        "folder": folder.resolve().as_posix(),
                                        "file_name": "rx.pdf"}]  # fmt: skip


def test_ac3_dropped_twice_the_first_is_the_source_and_the_second_is_listed() -> None:
    arrivals = [{"source_id": "local:/in", "folder": "/in", "original_name": n, "name": n}
                for n in ("rx.pdf", "rx-again.pdf")]  # fmt: skip
    source = result.source_of("listener", "rx.pdf", arrivals)
    assert source["file_name"] == "rx.pdf"
    assert [a["file_name"] for a in source["also_received"]] == ["rx-again.pdf"]


def test_ac3_one_document_per_set_of_bytes_is_the_ingest_rule() -> None:
    """What "one result" rests on, in the pipeline itself: a document is looked up by the
    sha256 of its bytes before a new one is made, under a lock on that hash."""
    ingest = (ROOT / "src" / "cdi_adapter" / "ingest" / "service.py").read_text(encoding="utf-8")
    assert "_one_ingest_per_sha(sha)" in ingest and "ingest_deduplicated" in ingest


# ------------------------------------------------------------------ AC4
# GIVEN a person searches for a file name WHEN the search runs
# THEN the result of that file is found


class _Conn:
    """A connection that answers the search's two queries from lists, and applies the search's
    own pattern the way PostgreSQL's ILIKE would."""

    def __init__(self, documents: list[dict[str, Any]], files: list[dict[str, Any]]) -> None:
        self.documents, self.files, self.patterns = documents, files, []  # type: ignore[var-annotated]

    @staticmethod
    def _matches(pattern: str, value: str | None) -> bool:
        import re

        if value is None:
            return False
        parts = re.split(r"(\\.|%|_)", pattern)
        regex = "".join(".*" if p == "%" else "." if p == "_" else re.escape(p[1:]) if p[:1] == "\\"
                        else re.escape(p) for p in parts)  # fmt: skip
        return re.fullmatch(regex, value, re.I | re.S) is not None

    def execute(self, statement: Any, params: dict[str, Any]) -> Any:
        self.patterns.append(params["q"])
        q = params["q"]
        if "FROM source_document" in str(statement):
            rows = []
            for d in self.documents:
                linked = [f for f in self.files if f.get("document_id") == d["id"]] or [{}]
                for f in linked:
                    if any(self._matches(q, v) for v in (d["filename"], f.get("original_name"),
                                                         f.get("name"))):  # fmt: skip
                        rows.append(
                            {
                                **d,
                                "original_name": f.get("original_name"),
                                "name": f.get("name"),
                                "source_id": f.get("source_id"),
                                "folder": f.get("folder"),
                                "state": f.get("state"),
                            }
                        )
        else:
            rows = [
                f
                for f in self.files
                if f.get("document_id") is None
                and f["state"] != "seen"
                and any(self._matches(q, v) for v in (f["original_name"], f["name"]))
            ]
        return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: rows))


def _doc(n: int, filename: str, channel: str) -> dict[str, Any]:
    return {"id": f"{DOC}{n}", "filename": filename, "source_channel": channel,
            "status": "validated", "created_at": WHEN, "page_count": 1}  # fmt: skip


def _file(name: str, document: int | None, state: str = "succeeded", original: str | None = None,
          kind: str | None = None) -> dict[str, Any]:  # fmt: skip
    return {
        "document_id": f"{DOC}{document}" if document else None,
        "name": name,
        "original_name": original or name,
        "source_id": "local:/srv/clinic/in",
        "folder": "/srv/clinic/in",
        "state": state,
        "kind": kind,
        "first_seen_at": WHEN,
    }


LIBRARY = _Conn(
    [_doc(1, "rx-anjali-monday.pdf", "listener"), _doc(2, "scan_0042.png", "upload"),
     _doc(3, "rx.pdf", "listener")],
    [_file("rx-anjali-monday.pdf", 1), _file("rx.1.pdf", 3, original="rx.pdf"),
     _file("blurry_100%.png", None, "refused", kind="rescan"),
     _file("waiting.pdf", None, "seen")])  # fmt: skip


def test_ac4_a_dropped_file_is_found_by_its_name() -> None:
    (hit,) = result.find_by_file_name(LIBRARY, "rx-anjali-monday.pdf")  # type: ignore[arg-type]
    assert hit["document_id"] == f"{DOC}1" and hit["result_url"] == f"/result/{DOC}1"
    assert (hit["channel"], hit["drive"], hit["folder"]) == ("listener", "local", "/srv/clinic/in")


def test_ac4_part_of_the_name_in_any_letter_case_is_enough() -> None:
    assert [h["file_name"] for h in result.find_by_file_name(LIBRARY, "ANJALI")] == [  # type: ignore[arg-type]
        "rx-anjali-monday.pdf"]  # fmt: skip


def test_ac4_an_uploaded_file_is_found_the_same_way() -> None:
    (hit,) = result.find_by_file_name(LIBRARY, "scan_0042")  # type: ignore[arg-type]
    assert hit["channel"] == "upload" and hit["drive"] is None and hit["result_url"]


def test_ac4_a_file_is_found_by_the_name_it_was_dropped_under_and_the_one_it_was_kept_under() -> (
    None
):
    by_original = result.find_by_file_name(LIBRARY, "rx.pdf")  # type: ignore[arg-type]
    by_kept = result.find_by_file_name(LIBRARY, "rx.1.pdf")  # type: ignore[arg-type]
    assert f"{DOC}3" in [h["document_id"] for h in by_original]
    assert [h["document_id"] for h in by_kept] == [f"{DOC}3"] and by_kept[0]["dropped_as"] == [
        "rx.pdf"
    ]


def test_ac4_a_dropped_file_with_no_result_is_found_too_and_says_what_became_of_it() -> None:
    (hit,) = result.find_by_file_name(LIBRARY, "blurry")  # type: ignore[arg-type]
    assert hit["document_id"] is None and hit["result_url"] is None
    assert hit["status"] == "no result: refused (rescan)" and hit["drive"] == "local"


def test_ac4_a_file_still_waiting_in_the_folder_is_not_listed_as_a_result() -> None:
    assert result.find_by_file_name(LIBRARY, "waiting") == []  # type: ignore[arg-type]


def test_ac4_the_characters_that_mean_something_to_the_search_are_taken_literally() -> None:
    assert result._like("100%_x\\y") == "%100\\%\\_x\\\\y%"
    assert [h["file_name"] for h in result.find_by_file_name(LIBRARY, "100%")] == [  # type: ignore[arg-type]
        "blurry_100%.png"]  # fmt: skip
    assert result.find_by_file_name(LIBRARY, "rx_anjali") == []  # "_" is not "any character"


def test_ac4_the_search_route_wants_two_characters_and_is_for_staff_who_may_see_results() -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as refused:
        web.find_results(file=" a ")
    assert refused.value.status_code == 422 and "two characters" in refused.value.detail
    assert auth.POLICY[("GET", "/api/results/search")] == auth.POLICY[
        ("GET", "/api/documents/{document_id}/result.json")]  # fmt: skip


def test_ac4_the_upload_screen_has_the_search_box() -> None:
    html = upload_page()
    assert "Find a result by file name" in html and "id=findq" in html
    assert "/api/results/search?file=' + encodeURIComponent(q)" in html
    assert "No file with that in its name." in html and "watched folder" in html


# ------------------------------------------------------------------ kept unchanged, and the policy


def test_the_original_file_is_kept_unchanged_as_evidence(folder) -> None:
    raw = blank_pdf(1)
    (folder / "rx.pdf").write_bytes(raw)
    _run(_listener(folder, Pipeline()))
    assert (folder / "success" / "rx.pdf").read_bytes() == raw  # in the folder, byte for byte
    store = (ROOT / "src" / "cdi_adapter" / "ingest" / "service.py").read_text(encoding="utf-8")
    assert "store.put_bytes(" in store  # and the pipeline keeps the original bytes it was sent


def test_people_are_told_not_to_put_patient_names_in_file_names() -> None:
    guide = (ROOT / "docs" / "LISTENER.md").read_text(encoding="utf-8")
    assert "Do not put a patient's name in a file name" in guide


def test_the_table_links_a_file_to_its_document() -> None:
    migration = (ROOT / "db" / "alembic" / "versions" / "0033_listener_file_document.py").read_text(
        encoding="utf-8")  # fmt: skip
    for column in ("document_id", "original_name", "folder"):
        assert f"ADD COLUMN {column}" in migration, column
    assert "REFERENCES source_document (id)" in migration and 'down_revision = "0032"' in migration
