"""SW-S1 "Put every engine and component behind a small written interface".

* AC1  the PDF renderer the setting names produces the pages, and its name and version are
       saved with the result;
* AC2  a second object store adapter passes the contract test with no change to the ingest code;
* AC3  a setting that names a component whose library is not installed stops the service at
       start, and the message names the setting;
* AC4  a result shows the name and version of every engine and model that produced it.

The second renderer and the second store used here are written IN THIS FILE, to show that
adding one needs no change anywhere else. They are not shipped: building real second
implementations is SW-S6.
"""

from __future__ import annotations

import io
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image

from cdi_adapter import components, result, storage
from cdi_adapter.config import Settings, get_settings
from cdi_adapter.ingest import pages, pdf
from cdi_adapter.ingest import service as ingest
from tests import contracts
from tests.helpers import blank_pdf

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "cdi_adapter"


@contextmanager
def setting(monkeypatch, **env: str) -> Iterator[None]:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    storage.get_store.cache_clear()
    try:
        yield
    finally:
        for key in env:
            monkeypatch.delenv(key, raising=False)
        get_settings.cache_clear()
        storage.get_store.cache_clear()


# ------------------------------------------------ a second renderer, a second store


class GreyRenderer:
    """A second PDF renderer: it asks the first how many pages and how big, and draws each as
    a plain grey picture. Enough to be told apart from pypdfium2 in a result."""

    name = "grey-test-renderer"
    version = "0.1-test"

    def page_count(self, raw: bytes) -> int:
        return pdf.PdfiumRenderer().page_count(raw)

    def page_sizes(self, raw: bytes) -> list[tuple[float, float]]:
        return pdf.PdfiumRenderer().page_sizes(raw)

    def render(self, raw: bytes, dpi: int) -> list[bytes]:
        out = []
        for w, h in self.page_sizes(raw):
            buf = io.BytesIO()
            size = (round(w * dpi / 72), round(h * dpi / 72))
            Image.new("RGB", size, (128, 128, 128)).save(buf, format="PNG")
            out.append(buf.getvalue())
        return out


class MemoryStore:
    """A second object store: a dict. It knows nothing of S3 and offers no client."""

    name = "memory-test-store"
    version = "0.1-test"

    def __init__(self, settings: Settings | None = None) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}
        self.ready = False

    def ensure_bucket(self) -> None:
        self.ready = True

    def put_bytes(self, key: str, data: bytes, content_type: str) -> None:
        self.objects[key] = (bytes(data), content_type)

    def get_bytes(self, key: str) -> bytes:
        return self.objects[key][0]

    def delete(self, key: str) -> None:
        self.objects.pop(key, None)

    def exists(self, key: str) -> bool:
        return key in self.objects

    def healthy(self) -> bool:
        return self.ready

    def listing(self) -> Iterator[tuple[str, int]]:
        for key, (data, _) in sorted(self.objects.items()):
            yield key, len(data)

    def content_type(self, key: str) -> str | None:
        return self.objects[key][1]


@pytest.fixture
def grey():
    pdf.register("grey-test-renderer", GreyRenderer)
    yield
    pdf.unregister("grey-test-renderer")


@pytest.fixture
def memory():
    made: list[MemoryStore] = []

    def factory(settings: Settings) -> MemoryStore:
        made.append(MemoryStore(settings))
        return made[-1]

    storage.register("memory-test-store", factory)
    yield made
    storage.unregister("memory-test-store")


# ------------------------------------------------ AC1: the renderer the setting names


def test_ac1_the_setting_names_the_renderer_that_makes_the_pages(monkeypatch, grey):
    raw = blank_pdf(2, size=(200, 280))
    assert pdf.renderer().name == "pypdfium2"  # the default
    white = pages.render_pages(raw, "application/pdf", dpi=72)
    with setting(monkeypatch, CDI_PDF_RENDERER="grey-test-renderer"):
        assert pdf.renderer().name == "grey-test-renderer"
        made = pages.render_pages(raw, "application/pdf", dpi=72)
    assert len(made) == len(white) == 2
    with Image.open(io.BytesIO(made[0])) as im:
        assert im.getpixel((10, 10)) == (128, 128, 128), "drawn by the renderer the setting names"
    with Image.open(io.BytesIO(white[0])) as im:
        assert im.getpixel((10, 10)) == (255, 255, 255)


def test_ac1_its_name_and_version_are_what_ingest_saves_with_the_document(monkeypatch, grey):
    assert pdf.describe("application/pdf") == ("pypdfium2", pdf.renderer().version)
    assert pdf.renderer().version and pdf.renderer().version[0].isdigit()
    with setting(monkeypatch, CDI_PDF_RENDERER="grey-test-renderer"):
        assert pdf.describe("application/pdf") == ("grey-test-renderer", "0.1-test")
    source = Path(ingest.__file__).read_text(encoding="utf-8")
    assert source.count("maker, maker_version = pages.pdf.describe(mime)") == 2
    assert (
        source.count("model_name=maker") == 2 and source.count("model_version=maker_version") == 2
    )


def test_ac1_a_picture_is_not_said_to_be_made_by_the_pdf_renderer():
    import PIL

    assert pdf.describe("image/png") == ("pillow", PIL.__version__)


def test_ac1_the_saved_name_and_version_reach_the_result(grey):
    engines = result._engines(_Runs([("ingest", "grey-test-renderer", "0.1-test")]), "doc-1")
    assert engines == [
        {
            "stage": "ingest",
            "name": "grey-test-renderer",
            "version": "0.1-test",
            "role": "page maker (PDF renderer, or Pillow for a picture)",
        }
    ]


def test_ac1_every_renderer_there_is_passes_the_renderer_contract(grey):
    assert pdf.registered() == ["grey-test-renderer", "pypdfium2"]
    for name in pdf.registered():
        contracts.pdf_renderer_contract(pdf.renderer(name))


def test_ac1_a_name_that_is_not_a_renderer_is_refused_by_name(monkeypatch):
    with setting(monkeypatch, CDI_PDF_RENDERER="mupdf"):
        with pytest.raises(ValueError, match="CDI_PDF_RENDERER='mupdf' is not a registered"):
            pdf.renderer()


def test_ac1_no_pdf_library_is_named_outside_the_renderers_own_module():
    for path in SRC.rglob("*.py"):
        if path.name == "pdf.py" and path.parent.name == "ingest":
            continue
        text = path.read_text(encoding="utf-8")
        assert "import pypdfium2" not in text and "import fitz" not in text, path


# ------------------------------------------------------------------ AC2: a second object store


def test_ac2_a_second_store_passes_the_contract():
    contracts.object_store_contract(MemoryStore())


def test_ac2_naming_it_in_the_setting_is_all_it_takes(monkeypatch, memory):
    assert storage.registered() == ["fs", "memory-test-store", "s3"]  # fs: SW-S6
    with setting(monkeypatch, CDI_OBJECT_STORE="memory-test-store"):
        store = storage.get_store()
        assert store.name == "memory-test-store" and storage.get_store() is store
        contracts.object_store_contract(store)
    assert storage.needs("s3") == (("boto3",), "boto3")


def test_ac2_the_ingest_code_keeps_a_scan_in_it_without_knowing_which_store_it_is(memory):
    """The same lines of ingest that write to S3 write to the second store: what they call
    is the interface."""
    store = MemoryStore()
    sha = "ab" * 32
    key = storage.original_key(sha, "pdf")
    raw = blank_pdf(1)
    store.put_bytes(key, raw, "application/pdf")  # ingest step 3, word for word
    for n, page in enumerate(pages.render_pages(raw, "application/pdf", dpi=72), start=1):
        store.put_bytes(storage.page_key(sha, n), page, "image/png")
    assert sorted(k for k, _ in store.listing()) == [key, storage.page_key(sha, 1)]
    assert store.get_bytes(key) == raw


def test_ac2_ingest_and_the_other_callers_ask_only_what_the_interface_offers():
    """No change to the ingest code was needed - because it never names the store's library."""
    offered = {
        "ensure_bucket",
        "put_bytes",
        "get_bytes",
        "delete",
        "exists",
        "healthy",
        "listing",
        "content_type",
        "name",
        "version",
        "bucket",
    }
    import re

    for rel in (
        "ingest/service.py",
        "ingest/pages.py",
        "ingest/watcher.py",
        "result.py",
        "webapp/jobstore.py",
        "webapp/app.py",
        "dr/backup.py",
        "dr/counts.py",
        "listener/service.py",
    ):
        path = SRC / rel
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        assert "boto3" not in text and "botocore" not in text, rel
        asked = set(re.findall(r"\bstore\.([a-z_]+)", text)) | set(
            re.findall(r"get_store\(\)\.([a-z_]+)", text)
        )
        assert asked <= offered, (rel, sorted(asked - offered))


def test_ac2_the_s3_library_is_named_in_the_storage_module_and_minio_tooling_only():
    named = sorted(
        p.relative_to(SRC).as_posix()
        for p in SRC.rglob("*.py")
        if "import boto3" in p.read_text(encoding="utf-8")
    )
    assert named == ["storage.py"]


def test_ac2_the_backup_copies_from_any_store_through_the_interface(tmp_path):
    from cdi_adapter.dr import backup, counts

    store = MemoryStore()
    store.put_bytes("documents/aa/x/original.pdf", b"%PDF-1.4 one", "application/pdf")
    store.put_bytes("documents/aa/x/pages/0001.png", b"png-bytes", "image/png")
    assert backup.backup_objects(store, tmp_path) == {"objects": 2, "added": 2}
    assert backup.backup_objects(store, tmp_path) == {"objects": 2, "added": 0}
    assert backup.read_manifest(tmp_path)["documents/aa/x/pages/0001.png"]["content_type"] == (
        "image/png"
    )
    assert counts.object_count(store) == 2


def test_ac2_the_s3_adapter_implements_the_same_interface():
    for method in (
        "ensure_bucket",
        "put_bytes",
        "get_bytes",
        "delete",
        "exists",
        "healthy",
        "listing",
        "content_type",
    ):
        assert callable(getattr(storage.ObjectStore, method)), method
        assert method in storage.Store.__dict__, method
    assert storage.ObjectStore.name == "s3"


@pytest.mark.xfail(
    strict=True,
    reason="The S3 adapter itself has not been run through the "
    "contract here: that needs a real MinIO (CDI_TEST_S3_ENDPOINT), and a "
    "stand-in is not used in its place. tests/test_sw_s1_acceptance_integration.py "
    "runs it where one is up.",
)
def test_ac2_the_s3_adapter_was_run_through_the_contract_on_a_real_minio():
    import os

    assert os.environ.get("CDI_TEST_S3_ENDPOINT")


# ------------------------------------------------ AC3: a missing library stops the start


def _missing(monkeypatch, *libraries: str) -> None:
    real = components.installed
    monkeypatch.setattr(components, "installed", lambda lib: lib not in libraries and real(lib))


@pytest.mark.parametrize(
    ("env", "value", "library"),
    [
        ("CDI_PDF_RENDERER", "pypdfium2", "pypdfium2"),
        ("CDI_OBJECT_STORE", "s3", "boto3"),
        ("CDI_JOB_EXECUTION", "queue", "celery"),
        ("CDI_AUTH_PROVIDER", "oidc", "jwt"),
    ],
)
def test_ac3_a_missing_library_stops_the_start_and_the_message_names_the_setting(
    monkeypatch, env, value, library
):
    _missing(monkeypatch, library)
    with setting(monkeypatch, **{env: value}):
        with pytest.raises(SystemExit) as stopped:
            components.check(process="webapp")
    said = str(stopped.value)
    assert said.startswith("refusing to start: ")
    assert f"{env}={value!r} needs the library {library!r}, which is not installed" in said


def test_ac3_a_name_nobody_registered_stops_the_start_too(monkeypatch):
    with setting(monkeypatch, CDI_OBJECT_STORE="azure-blob"):
        with pytest.raises(SystemExit) as stopped:
            components.check()
    assert "CDI_OBJECT_STORE='azure-blob' is not a known object store (available: fs, s3)" in str(
        stopped.value
    )


def test_ac3_every_wrong_setting_is_named_not_only_the_first(monkeypatch):
    _missing(monkeypatch, "boto3")
    with setting(monkeypatch, CDI_PDF_RENDERER="mupdf"):
        found = components.problems()
    assert len(found) == 2 and "CDI_PDF_RENDERER" in found[0] and "CDI_OBJECT_STORE" in found[1]


def test_ac3_with_everything_in_place_the_service_starts():
    assert components.problems() == []
    components.check()  # no SystemExit


def test_ac3_a_library_another_process_loads_is_not_asked_of_the_web_app(monkeypatch):
    """The vision-language model's library lives in the model server; the web app only calls
    it. Asking the web app for 'vllm' would stop a service that does not need it."""
    _missing(monkeypatch, "vllm", "transformers", "torch")
    with setting(monkeypatch, CDI_MLSERVE_BACKEND="vllm"):
        assert components.problems(process="webapp") == []
        assert (
            "CDI_MLSERVE_BACKEND='vllm' needs the library 'vllm'"
            in components.problems(process="mlserve")[0]
        )


def test_ac3_the_web_app_runs_the_check_before_it_serves():
    source = (SRC / "webapp" / "app.py").read_text(encoding="utf-8")
    main = source.split("def main() -> None:")[1]
    assert main.index('components.check(s, "webapp")') < main.index("uvicorn.run(")


def test_ac3_the_real_start_stops_with_the_settings_name(tmp_path):
    """The web app's own entry point, in a process of its own, with a wrong setting."""
    done = subprocess.run(
        [sys.executable, "-c", "from cdi_adapter.webapp.app import main; main()"],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=tmp_path,
        env={
            **__import__("os").environ,
            "CDI_PDF_RENDERER": "mupdf",
            "CDI_METRICS_PORT": "0",
            "PYTHONPATH": str(ROOT / "src"),
        },
    )
    assert done.returncode != 0
    assert "refusing to start: CDI_PDF_RENDERER='mupdf' is not a known pdf renderer" in (
        done.stderr + done.stdout
    )


# ------------------------------------------------------------------ AC4: every engine in the result


class _Runs:
    """A stand-in database holding what the stages recorded."""

    def __init__(
        self,
        runs: list[tuple[str, str | None, str | None]],
        readers: list[tuple[str, str | None]] | None = None,
        stack: dict[str, Any] | None = None,
    ) -> None:
        self.runs, self.readers, self.stack = runs, readers or [], stack

    def execute(self, statement: Any, params: Any = None) -> Any:
        sql = str(statement)
        if "FROM pipeline_run" in sql:
            rows: list[Any] = [
                {"stage": s, "model_name": n, "model_version": v} for s, n, v in self.runs
            ]
        elif "FROM ocr_observation" in sql:
            rows = [{"engine": e, "engine_version": v} for e, v in self.readers]
        else:
            rows = [{"model_stack": self.stack}] if self.stack else []
        return SimpleNamespace(
            mappings=lambda: SimpleNamespace(
                all=lambda: rows, first=lambda: rows[0] if rows else None
            )
        )


FULL = _Runs(
    [
        ("validate", "rules", None),
        ("terminology", "in-snomed-loinc-icd10", "lab-master.2026-10-12.1"),
        ("extract", "Qwen/Qwen2.5-VL-7B-Instruct", "cc594898"),
        ("classify", "heuristic", None),
        ("ocr", "rapidocr-onnxruntime", None),
        ("ingest", "pypdfium2", "5.14.0"),
    ],
    readers=[
        ("rapidocr", "rapidocr-onnxruntime 1.4.4"),
        ("trocr", "microsoft/trocr-base eaacaf45"),
        ("qwen-line", "Qwen/Qwen2.5-VL-7B-Instruct cc594898"),
    ],
    stack={
        "schema": "prescription.v3",
        "prompt_version": "be0d7d0de044",
        "lab_master": "lab-master.2026-10-12.1",
        "preparation_phrases": "prep.2026-10-12.1",
        "context_rules": "ctx.2026-10-12.1",
        "extractor": "Qwen/Qwen2.5-VL-7B-Instruct",
    },
)


def test_ac4_the_result_names_every_engine_and_model_with_its_version():
    engines = result._engines(FULL, "doc-1")
    by = {(e["stage"], e["name"]): e["version"] for e in engines}
    assert by[("ingest", "pypdfium2")] == "5.14.0"  # the PDF renderer
    assert by[("ocr", "rapidocr")] == "1.4.4"  # the printed-text reader
    assert by[("ocr", "trocr")] == "eaacaf45"  # the handwriting reader
    assert by[("ocr", "qwen-line")] == "cc594898"
    assert by[("extract", "Qwen/Qwen2.5-VL-7B-Instruct")] == "cc594898"  # the model
    assert by[("terminology", "in-snomed-loinc-icd10")] == "lab-master.2026-10-12.1"
    assert ("classify", "heuristic") in by and ("validate", "rules") in by
    assert by[("extract", "prompt_version")] == "be0d7d0de044"  # which wording asked
    assert by[("extract", "lab_master")] == "lab-master.2026-10-12.1"
    assert [e["stage"] for e in engines] == sorted(
        (e["stage"] for e in engines), key=list(result.STAGE_ROLES).index
    ), "in pipeline order"
    assert all(set(e) == {"stage", "role", "name", "version"} and e["role"] for e in engines)


def test_ac4_it_is_what_was_recorded_when_it_ran_not_todays_settings(monkeypatch):
    """A result made under an older model still says that model."""
    older = _Runs([("extract", "Qwen/Qwen2.5-VL-7B-Instruct", "an-older-revision")])
    with setting(monkeypatch, CDI_VLM_MODEL_ID="some/other-model"):
        assert result._engines(older, "doc-1")[0]["version"] == "an-older-revision"
    assert "get_settings" not in __import__("inspect").getsource(result._engines)


def test_ac4_a_stage_run_twice_is_named_once_with_its_newest_run():
    twice = _Runs([("extract", "Qwen/new", "r2"), ("extract", "Qwen/old", "r1")])  # newest first
    assert [(e["name"], e["version"]) for e in result._engines(twice, "d")] == [("Qwen/new", "r2")]


def test_ac4_the_engines_are_part_of_the_fixed_result_format():
    assert "engines" not in result.ABSENT and result.ABSENT == ()
    import json

    schema = json.loads(result.SCHEMA_PATH.read_text(encoding="utf-8"))
    item = schema["properties"]["engines"]["items"]
    assert item["required"] == ["stage", "role", "name", "version"]
    assert item["additionalProperties"] is False
    doc = {
        "schema_version": "result.v1",
        "document_id": "0a1b2c3d-0000-4000-8000-000000000001",
        "received_at": None,
        "source": "upload_screen",
        "page_count": 1,
        "status": "complete",
        "review": {"needs_check_count": 0, "items": []},
        "absent": [],
        "engines": result._engines(FULL, "doc-1"),
    }
    assert result.problems(doc) == []
    doc["engines"][0]["licence"] = "MIT"
    assert result.problems(doc), "and nothing else may be slipped into an engine entry"


def test_ac4_a_result_is_built_with_its_engines():
    source = __import__("inspect").getsource(result.build)
    assert '"engines": _engines(conn, doc["id"])' in source


def test_ac4_every_stage_records_what_ran():
    """Where each name and version comes from: every stage opens its run with them."""
    for rel, needle in (
        ("ingest/service.py", "model_name=maker"),
        ("ocr/service.py", 'stage="ocr", model_name=model'),
        ("extract/service.py", "model_name=vlm.model, model_version=vlm.version"),
        ("terminology/service.py", "model_version=lab_master.version()"),
        ("validate/service.py", 'stage="validate", model_name="rules"'),
    ):
        assert needle in (SRC / rel).read_text(encoding="utf-8"), rel


# ------------------------------------------------ the list of swap points (FR1, FR4)


def test_fr1_the_ten_swap_points_are_listed_each_with_one_setting():
    listed = {p.key: p.setting for p in components.POINTS}
    assert listed == {
        "pdf_renderer": "CDI_PDF_RENDERER",
        "object_store": "CDI_OBJECT_STORE",
        "printed_text_reader": "CDI_OCR_ENGINE",
        "handwriting_reader": "CDI_TWO_READERS",
        "vlm": "CDI_MLSERVE_BACKEND",
        "job_queue": "CDI_JOB_EXECUTION",
        "terminology": "CDI_TERMINOLOGY_PACKAGE",
        "drive_connector": "CDI_LISTENER_CONNECTOR",
        "sign_in": "CDI_AUTH_PROVIDER",
        "metrics_sink": "(fixed)",
    }
    fields = {f"CDI_{name.upper()}" for name in Settings.model_fields}
    assert {s for s in listed.values() if s != "(fixed)"} <= fields, "each is a real setting"


def test_fr4_the_health_view_shows_the_choice_made_and_the_choices_there_are():
    shown = {c["swap_point"]: c for c in components.describe()}
    assert shown["pdf_renderer"]["current"] == "pypdfium2" and shown["pdf_renderer"]["version"]
    assert shown["object_store"]["available"] == ["fs", "s3"]  # fs: SW-S6
    assert shown["drive_connector"]["available"] == ["gdrive", "local", "onedrive", "sharepoint"]
    assert shown["vlm"]["available"] == ["hf", "stub", "vllm"]
    for c in shown.values():
        assert c["interface"] and c["contract_test"] and c["problem"] is None
        assert c["current"] in c["available"] or c["custom_allowed"]


def test_fr4_it_is_served_to_signed_in_staff_and_holds_no_address_or_secret():
    from cdi_adapter.webapp import app as web
    from cdi_adapter.webapp import auth

    assert auth.POLICY[("GET", "/api/components")] == {"intake", "reviewer", "admin"}
    body = web.components_view()
    assert len(body["swap_points"]) == 10 and body["problems"] == []
    said = str(body)
    s = get_settings()
    for hidden in (s.s3_endpoint_url, s.s3_secret_key, s.database_url, s.redis_url):
        assert hidden not in said


def test_fr3_each_interface_has_its_contract_test_and_its_page():
    doc = (ROOT / "docs" / "interfaces.md").read_text(encoding="utf-8")
    for point in components.POINTS:
        assert f"## {point.title}" in doc, point.title
        assert point.setting in doc
        target = point.contract.split("::")[0]
        assert (ROOT / target).is_file(), target
    assert callable(contracts.object_store_contract) and callable(contracts.pdf_renderer_contract)


@pytest.mark.xfail(
    strict=True,
    reason="FR3: only the PDF renderer and the object store have a "
    "SHARED contract test that takes any implementation (the story's 'first "
    "two'). The other eight are covered by their own stories' tests, which "
    "test the one implementation there is - not yet a contract a second one "
    "could be run through.",
)
def test_fr3_every_swap_point_has_a_shared_contract_any_implementation_can_run():
    assert all(p.contract.startswith("tests/contracts.py::") for p in components.POINTS)


def test_ac2_a_key_that_is_gone_is_told_apart_from_a_store_that_is_down_without_naming_s3():
    from botocore.exceptions import ClientError

    gone = ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
    denied = ClientError({"Error": {"Code": "AccessDenied"}}, "GetObject")
    assert storage.missing(gone) and not storage.missing(denied)
    assert storage.missing(KeyError("k")) and storage.missing(FileNotFoundError("k"))
    assert not storage.missing(ConnectionError("the store is down"))
    jobstore = (SRC / "webapp" / "jobstore.py").read_text(encoding="utf-8")
    assert "storage.missing(exc)" in jobstore and "botocore" not in jobstore
