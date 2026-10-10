"""OUT-S2's writer: every result is also a JSON file (built to close LS-S8 AC1, 2026-10-09).

One JSON file per prescription, the same whether it was uploaded or dropped in the watched
folder: built from the database, validated against ``result.v1.json`` before a byte is written,
written all at once, and identical when written again.

``result.build`` reads PostgreSQL, so here it is replaced by a function that returns a fixed
result document - what is tested is everything *around* it: the validation, the file, the
store, the name, and the places the file is written from. The whole thing on a real database
has no automated test yet; it was checked by hand on a running instance on 2026-10-09
(``docs/test-LS-S8.md``).
"""

from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import ValidationError

from cdi_adapter import result
from cdi_adapter.config import Settings
from cdi_adapter.webapp import jobs

ROOT = Path(__file__).resolve().parents[1]
DOC = "0a1b2c3d-0000-4000-8000-000000000001"


def _document(channel: str = "listener", **more: Any) -> dict[str, Any]:
    arrivals = (
        [
            {
                "source_id": "local:/srv/clinic/in",
                "folder": "/srv/clinic/in",
                "original_name": "rx.pdf",
                "name": "rx.pdf",
            }
        ]
        if channel == "listener"
        else []
    )
    return {
        "schema_version": "result.v1",
        "document_id": DOC,
        "received_at": "2026-10-09T09:30:00+00:00",
        "source": "upload_screen" if channel == "upload" else channel,
        "page_count": 1,
        "status": "needs_check",
        "review": {"needs_check_count": 0, "items": []},
        "absent": list(result.ABSENT),
        "document": {"source": result.source_of(channel, "rx.pdf", arrivals)},
        **more,
    }


class Store:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}

    def put_bytes(self, key: str, data: bytes, content_type: str) -> None:
        self.objects[key] = (data, content_type)


@pytest.fixture
def built(monkeypatch) -> dict[str, Any]:
    """Stand in for the database: ``build`` returns whatever is in ``state["document"]``."""
    state: dict[str, Any] = {"document": _document(), "builds": 0}

    def build(conn: Any, document_id: Any) -> dict[str, Any]:
        state["builds"] += 1
        return json.loads(json.dumps(state["document"]))

    monkeypatch.setattr(result, "build", build)
    return state


def _settings(tmp_path: Path | None = None, prefix: str = "results") -> Settings:
    return Settings(result_output_dir=str(tmp_path) if tmp_path else "",
                    result_output_prefix=prefix)  # fmt: skip


# ------------------------------------------------------------------ the file


def test_a_json_file_is_written_for_the_result(built, tmp_path) -> None:
    out = result.write_file(None, DOC, settings=_settings(tmp_path), store=Store())  # type: ignore[arg-type]
    path = tmp_path / f"{DOC}.result.v1.json"
    assert out["file"] == str(path) and path.is_file()
    assert json.loads(path.read_text(encoding="utf-8")) == built["document"]
    assert out["bytes"] == path.stat().st_size
    assert out["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_it_is_also_put_in_the_document_store(built) -> None:
    store = Store()
    out = result.write_file(None, DOC, settings=_settings(), store=store)  # type: ignore[arg-type]
    assert out["key"] == f"results/{DOC}.result.v1.json" and out["file"] is None
    raw, content_type = store.objects[out["key"]]
    assert content_type == "application/json" and json.loads(raw) == built["document"]


def test_the_name_carries_the_document_and_the_format_version() -> None:
    assert result.file_name(DOC) == f"{DOC}.result.v1.json"
    assert result.SCHEMA_VERSION in result.FILE_SUFFIX


def test_the_folder_and_the_store_prefix_are_settings(built, tmp_path) -> None:
    fields = Settings.model_fields
    assert fields["result_output_prefix"].default == "results"
    assert fields["result_output_dir"].default == ""  # no folder unless one is named
    store = Store()
    out = result.write_file(None, DOC, settings=_settings(tmp_path / "out", "exports/json/"),  # type: ignore[arg-type]
                            store=store)  # fmt: skip
    assert out["key"] == f"exports/json/{DOC}.result.v1.json"
    assert (tmp_path / "out" / f"{DOC}.result.v1.json").is_file()  # the folder is made


def test_with_neither_set_nothing_is_written(built, tmp_path) -> None:
    store = Store()
    out = result.write_file(None, DOC, settings=_settings(None, ""), store=store)  # type: ignore[arg-type]
    assert out["file"] is None and out["key"] is None and store.objects == {}


# ------------------------------------------------------------------ the same for both channels


def test_an_upload_and_a_dropped_file_give_files_of_the_same_shape(built, tmp_path) -> None:
    def keys(node: Any, path: str = "") -> set[str]:
        if isinstance(node, dict):
            return {path + k for k in node} | set().union(
                *(keys(v, path + k + ".") for k, v in node.items()))  # fmt: skip
        return set()

    files = {}
    for channel in ("upload", "listener"):
        built["document"] = _document(channel)
        folder = tmp_path / channel
        result.write_file(None, DOC, settings=_settings(folder, ""))  # type: ignore[arg-type]
        files[channel] = json.loads((folder / result.file_name(DOC)).read_text(encoding="utf-8"))
    assert keys(files["upload"]) == keys(files["listener"])
    assert files["listener"]["document"]["source"]["channel"] == "listener"
    assert files["upload"]["document"]["source"]["drive"] is None


# ------------------------------------------------------------------ validated, whole, identical


def test_a_result_that_does_not_fit_the_format_is_not_written(built, tmp_path) -> None:
    built["document"] = _document()
    built["document"].pop("status")  # a required field is missing
    store = Store()
    with pytest.raises(ValidationError):
        result.write_file(None, DOC, settings=_settings(tmp_path), store=store)  # type: ignore[arg-type]
    assert list(tmp_path.iterdir()) == [] and store.objects == {}


def test_a_bad_rebuild_leaves_the_earlier_file_as_it_was(built, tmp_path) -> None:
    result.write_file(None, DOC, settings=_settings(tmp_path, ""))  # type: ignore[arg-type]
    path = tmp_path / result.file_name(DOC)
    before = path.read_bytes()
    built["document"] = {**_document(), "status": "something else"}  # not one of the fixed list
    with pytest.raises(ValidationError):
        result.write_file(None, DOC, settings=_settings(tmp_path, ""))  # type: ignore[arg-type]
    assert path.read_bytes() == before


def test_a_reader_never_sees_half_a_file(built, tmp_path, monkeypatch) -> None:
    """The bytes go to a temporary file first; the real name appears only by a rename."""
    seen: list[tuple[str, bool]] = []
    real = result.os.replace

    def watched(src: Any, dst: Any) -> None:
        seen.append((Path(src).name, Path(dst).exists()))
        assert json.loads(Path(src).read_text(encoding="utf-8"))  # whole before it is named
        real(src, dst)

    monkeypatch.setattr(result.os, "replace", watched)
    result.write_file(None, DOC, settings=_settings(tmp_path, ""))  # type: ignore[arg-type]
    ((temporary, existed),) = seen
    assert temporary.startswith(".") and temporary.endswith(".tmp") and existed is False
    assert [p.name for p in tmp_path.iterdir()] == [result.file_name(DOC)]  # no litter


def test_writing_it_again_gives_the_identical_file(built, tmp_path) -> None:
    store = Store()
    first = result.write_file(None, DOC, settings=_settings(tmp_path), store=store)  # type: ignore[arg-type]
    one = (tmp_path / result.file_name(DOC)).read_bytes()
    second = result.write_file(None, DOC, settings=_settings(tmp_path), store=store)  # type: ignore[arg-type]
    assert (tmp_path / result.file_name(DOC)).read_bytes() == one
    assert first["sha256"] == second["sha256"] and len(store.objects) == 1


def test_the_file_is_what_the_api_serves(built) -> None:
    store = Store()
    out = result.write_file(None, DOC, settings=_settings(), store=store)  # type: ignore[arg-type]
    assert store.objects[out["key"]][0].decode("utf-8") == result.as_json(None, DOC)  # type: ignore[arg-type]


def test_a_changed_row_changes_the_file(built, tmp_path) -> None:
    """The file is rebuilt from the database, so a reviewer's correction shows in it."""
    result.write_file(None, DOC, settings=_settings(tmp_path, ""))  # type: ignore[arg-type]
    built["document"] = _document(status="complete")
    result.write_file(None, DOC, settings=_settings(tmp_path, ""))  # type: ignore[arg-type]
    written = json.loads((tmp_path / result.file_name(DOC)).read_text(encoding="utf-8"))
    assert written["status"] == "complete"


# ------------------------------------------------------------------ when it is written


def test_the_pipeline_writes_it_when_a_document_is_done() -> None:
    runner = inspect.getsource(jobs)
    assert "s.result_file(str(d.document_id))" in runner
    done, wrote = runner.index('if d.status == "done":'), runner.index("s.result_file(")
    assert done < wrote  # only for a document that finished
    real = inspect.getsource(jobs.Stages.real)
    assert "result.refresh_files([document_id])" in real and "result_file=result_file" in real


def test_the_runner_writes_it_for_both_channels_and_survives_a_failure(monkeypatch) -> None:
    from cdi_adapter.config import get_settings

    monkeypatch.setenv("CDI_QUALITY_CHECK", "false")  # the "file" here is a placeholder byte
    get_settings.cache_clear()
    calls: list[str] = []

    def boom(document_id: str) -> None:
        calls.append(document_id)
        raise OSError("disk full")

    stages = jobs.Stages(
        ingest=lambda raw, name, mime, patient_id=None, source_channel="upload":
            __import__("types").SimpleNamespace(document_id=f"doc-{source_channel}"),
        ocr=lambda doc, force_engine=None: None,
        classify=lambda doc: {"doc_type": "prescription", "is_handwritten": False},
        extract=lambda doc, patient_id=None, expected_patient=None: {"patient_id": "p", "facts": 1},
        terminology=lambda doc: None, validate=lambda doc: None,
        counts=lambda doc: {"facts": 1, "accepted": 1, "in_review": 0},
        merge_identity=lambda patient: None, result_file=boom)  # fmt: skip
    for channel in ("upload", "listener"):
        runner = jobs.JobRunner(stages, max_workers=1)
        job = jobs.Job(id=f"job-{channel}", channel=channel, docs=[jobs.DocState(filename="rx")])
        runner._run(job, [("rx", b"x", None)])
        assert job.docs[0].status == "done"  # the document is not failed by its file
    assert calls == ["doc-upload", "doc-listener"]
    get_settings.cache_clear()


def test_it_is_written_again_after_a_correction_and_after_an_approval() -> None:
    review = (ROOT / "src" / "cdi_adapter" / "webapp" / "review.py").read_text(encoding="utf-8")
    reviewer = (ROOT / "src" / "cdi_adapter" / "webapp" / "reviewer.py").read_text(encoding="utf-8")
    assert "result.refresh_files(documents or [])" in review
    assert 'result.refresh_files([item.get("source_document_id")] if item else [])' in reviewer
    for source in (review, reviewer):  # after the transaction that saved the change, not inside it
        body = source[
            source.index("result.refresh_files(") - 400 : source.index("result.refresh_files(")
        ]
        assert "with session_scope()" in body


def test_a_file_that_cannot_be_written_never_raises_from_a_refresh(monkeypatch) -> None:
    def broken(conn: Any, document_id: Any, **kw: Any) -> dict[str, Any]:
        raise OSError("no space left on device")

    class _Scope:
        def __enter__(self) -> None:
            return None

        def __exit__(self, *a: Any) -> bool:
            return False

    monkeypatch.setattr(result, "write_file", broken)
    monkeypatch.setattr("cdi_adapter.db.session_scope", lambda: _Scope())
    assert result.refresh_files([DOC, DOC, None]) == []  # logged, not raised; one try per document
