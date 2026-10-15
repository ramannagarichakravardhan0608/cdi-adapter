"""The swap points (backlog SW-S1): every part that may be replaced, in one list.

A swap point is a part of the product behind a small written interface, chosen by ONE setting.
Replacing it is: write an adapter that passes the interface's contract test, register it under
a name, set the setting to that name. No other code changes.

==========================  =============================  ===================================
Swap point                  Setting                        Interface
==========================  =============================  ===================================
PDF renderer                ``CDI_PDF_RENDERER``           ``ingest.pdf.PdfRenderer``
Object store                ``CDI_OBJECT_STORE``           ``storage.Store``
Printed-text reader         ``CDI_OCR_ENGINE``             ``ocr.rapid`` (lines with boxes)
Handwriting line reader     ``CDI_TWO_READERS``            ``recognition.engines``
Vision-language model       ``CDI_MLSERVE_BACKEND``        ``mlserve.backends.Backend``
Job queue                   ``CDI_JOB_EXECUTION``          ``webapp.jobs.JobStore``
Terminology provider        ``CDI_TERMINOLOGY_PACKAGE``    ``terminology`` (lists and codes)
Drive connector             ``CDI_LISTENER_CONNECTOR``     ``listener.connectors.Connector``
Sign-in provider            ``CDI_AUTH_PROVIDER``          ``webapp.auth`` / ``webapp.oidc``
Metrics sink                (fixed: Prometheus)            ``metrics`` (``/metrics``)
==========================  =============================  ===================================

Two things this module does with the list:

* :func:`describe` - for each swap point: the setting, the choice made, its version, and the
  choices there are (the health view, ``GET /api/components``);
* :func:`check` - at start: a setting that names something unknown, or something whose library
  is not installed, STOPS the service with a message that names the setting. A wrong setting
  is found when the service starts, not when the first prescription arrives.

``docs/interfaces.md`` has one section per interface: inputs, outputs, errors, contract test.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from cdi_adapter.config import Settings, get_settings


@dataclass(frozen=True)
class Choice:
    """One implementation that can be named in a setting."""

    name: str
    libraries: tuple[str, ...] = ()  # what must be importable where this choice runs
    package: str | None = None  # the installed package whose version is the choice's version
    note: str = ""


@dataclass(frozen=True)
class SwapPoint:
    key: str
    title: str
    setting: str
    interface: str
    contract: str  # the test any implementation must pass
    choices: Callable[[], dict[str, Choice]]
    current: Callable[[Settings], str]
    # the processes in which the chosen library is loaded; elsewhere it is only named
    runs_in: Callable[[Settings], tuple[str, ...]] = field(default=lambda s: ("webapp",))
    custom: bool = False  # a class of your own may be named as package.module:Class
    detail: Callable[[Settings], str | None] = field(default=lambda s: None)
    # SW-S6: what else must hold for the choice to work (a folder named, a licence spelled right)
    extra: Callable[[Settings], str | None] = field(default=lambda s: None)


def installed(library: str) -> bool:
    try:
        return importlib.util.find_spec(library) is not None
    except (ImportError, ValueError):
        return False


def version_of(package: str | None) -> str | None:
    if not package:
        return None
    try:
        return importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        return None


def _pdf() -> dict[str, Choice]:
    from cdi_adapter.ingest import pdf

    return {n: Choice(n, *pdf.needs(n)) for n in pdf.registered()}


def _stores() -> dict[str, Choice]:
    from cdi_adapter import storage

    return {n: Choice(n, *storage.needs(n)) for n in storage.registered()}


def _licence_names(s: Settings) -> str | None:
    named = {x.strip().lower() for x in s.licensed_code_systems.split(",") if x.strip()}
    wrong = sorted(named - {"snomed", "icd10"})
    if not wrong:
        return None
    return (f"CDI_LICENSED_CODE_SYSTEMS names {', '.join(wrong)}: the code systems are snomed "
            "and icd10 (a misspelt licence is not a licence)")


def _connectors() -> dict[str, Choice]:
    from cdi_adapter.listener import connectors

    libs = {"onedrive": ("msal",), "sharepoint": ("msal",), "gdrive": ("jwt",)}
    return {n: Choice(n, libs.get(n, ()), {"msal": "msal", "jwt": "PyJWT"}.get(
        (libs.get(n) or ("",))[0])) for n in connectors.registered()}  # fmt: skip


def _fixed(*choices: Choice) -> Callable[[], dict[str, Choice]]:
    return lambda: {c.name: c for c in choices}


def _ocr_in(s: Settings) -> tuple[str, ...]:
    # with an OCR host configured the reader's library is loaded there, not in the web app
    return ("ocrhost",) if s.ocr_host_url else ("webapp", "worker")


POINTS: tuple[SwapPoint, ...] = (
    SwapPoint(
        "pdf_renderer", "PDF renderer", "CDI_PDF_RENDERER", "cdi_adapter.ingest.pdf.PdfRenderer",
        "tests/contracts.py::pdf_renderer_contract", _pdf, lambda s: s.pdf_renderer,
        lambda s: ("webapp", "worker", "listener")),
    SwapPoint(
        "object_store", "Object store", "CDI_OBJECT_STORE", "cdi_adapter.storage.Store",
        "tests/contracts.py::object_store_contract", _stores, lambda s: s.object_store,
        lambda s: ("webapp", "worker", "listener"),
        detail=lambda s: (f"folder {s.file_store_root or '(not set)'}"
                          if s.object_store.strip().lower() == "fs" else f"bucket {s.s3_bucket}"),
        extra=lambda s: ("CDI_OBJECT_STORE='fs' needs CDI_FILE_STORE_ROOT (a folder on an "
                         "encrypted volume)" if s.object_store.strip().lower() == "fs"
                         and not s.file_store_root.strip() else None)),
    SwapPoint(
        "printed_text_reader", "Printed-text reader", "CDI_OCR_ENGINE",
        "cdi_adapter.ocr.rapid (lines with text, box and confidence)",
        "tests/test_rd_s1_acceptance_unit.py",
        _fixed(Choice("rapidocr", ("rapidocr_onnxruntime",), "rapidocr-onnxruntime"),
               Choice("vlm", note="the vision-language model reads the page")),
        lambda s: s.ocr_engine, _ocr_in),
    SwapPoint(
        "handwriting_reader", "Handwriting line reader", "CDI_TWO_READERS",
        "cdi_adapter.recognition.engines (one line crop in, one reading out)",
        "tests/test_rd_s2_acceptance_unit.py",
        _fixed(Choice("trocr", ("transformers", "torch"), "transformers"),
               Choice("none", note="only the vision-language model reads handwriting")),
        lambda s: "trocr" if s.two_readers else "none", lambda s: ("ocrhost",),
        detail=lambda s: s.trocr_model_id if s.two_readers else None),
    SwapPoint(
        "vlm", "Vision-language model", "CDI_MLSERVE_BACKEND",
        "cdi_adapter.mlserve.backends.Backend", "tests/test_ml_unit.py",
        _fixed(Choice("stub", note="a keyword responder, no model: tests and development"),
               Choice("hf", ("transformers", "torch"), "transformers"),
               Choice("vllm", ("vllm",), "vllm")),
        lambda s: s.mlserve_backend, lambda s: ("mlserve",),
        detail=lambda s: None if s.mlserve_backend == "stub" else s.vlm_model_id),
    SwapPoint(
        "job_queue", "Job queue", "CDI_JOB_EXECUTION", "cdi_adapter.webapp.jobs.JobStore",
        "tests/test_apptier_unit.py",
        _fixed(Choice("inprocess", note="jobs run in the web app's own threads"),
               Choice("queue", ("celery", "redis"), "celery")),
        lambda s: s.job_execution, lambda s: ("webapp", "worker"),
        # SW-S6: the queue's server - Redis 7.2 or older, or Valkey (CDI_QUEUE_SERVER)
        detail=lambda s: f"server {s.queue_server}" if s.job_execution == "queue" else None),
    SwapPoint(
        "terminology", "Terminology provider", "CDI_TERMINOLOGY_PACKAGE",
        "cdi_adapter.terminology (names, aliases and codes of tests and diagnoses)",
        "tests/test_terminology_unit.py",
        lambda: {"in-snomed-loinc-icd10": Choice("in-snomed-loinc-icd10")},
        lambda s: s.terminology_package, custom=True,
        # SW-S6: the internal canonical vocabulary always; SNOMED CT / ICD-10 only if licensed
        detail=lambda s: "internal canonical vocabulary; licensed: "
        + (s.licensed_code_systems.strip() or "none (no SNOMED CT, no ICD-10)"),
        extra=lambda s: _licence_names(s)),
    SwapPoint(
        "drive_connector", "Drive connector", "CDI_LISTENER_CONNECTOR",
        "cdi_adapter.listener.connectors.Connector", "tests/test_ls_s1_acceptance_unit.py",
        _connectors, lambda s: s.listener_connector, lambda s: ("listener",), custom=True),
    SwapPoint(
        "sign_in", "Sign-in provider", "CDI_AUTH_PROVIDER",
        "cdi_adapter.webapp.auth (local accounts) / cdi_adapter.webapp.oidc (OpenID Connect)",
        "tests/test_ent_s2_acceptance_unit.py",
        _fixed(Choice("local", note="accounts of this product, with a password"),
               Choice("oidc", ("jwt",), "PyJWT")),
        lambda s: s.auth_provider,
        detail=lambda s: s.oidc_issuer if s.auth_provider == "oidc" else None),
    SwapPoint(
        "metrics_sink", "Metrics sink", "(fixed)", "cdi_adapter.metrics (GET /metrics)",
        "tests/test_monitoring_unit.py",
        _fixed(Choice("prometheus", ("prometheus_client",), "prometheus-client")),
        lambda s: "prometheus", lambda s: ("webapp", "worker", "mlserve"),
        detail=lambda s: "traces also sent over OTLP" if s.otel_traces_endpoint else None),
)  # fmt: skip


def _is_custom(value: str) -> bool:
    return ":" in value or "." in value


def problem_of(point: SwapPoint, settings: Settings, process: str | None = None) -> str | None:
    """What is wrong with this swap point's setting, in words that name the setting; None when
    nothing is. ``process``: only a library that THIS process loads is asked for."""
    value = str(point.current(settings) or "").strip()
    choices = point.choices()
    said = f"{point.setting}={value!r}"
    choice = choices.get(value.lower()) or choices.get(value)
    if choice is None:
        if point.custom and (_is_custom(value) or point.key == "terminology"):
            return None  # a class or a package of the owner's own: checked where it is loaded
        return (f"{said} is not a known {point.title.lower()} "
                f"(available: {', '.join(sorted(choices))})")  # fmt: skip
    if process is not None and process not in point.runs_in(settings):
        return None
    for library in choice.libraries:
        if not installed(library):
            return (f"{said} needs the library {library!r}, which is not installed in this "
                    f"environment")  # fmt: skip
    return point.extra(settings)


def problems(settings: Settings | None = None, process: str | None = "webapp") -> list[str]:
    s = settings or get_settings()
    return [p for p in (problem_of(point, s, process) for point in POINTS) if p]


def check(settings: Settings | None = None, process: str = "webapp") -> None:
    """Stop the service at start when a setting names a component that cannot be used (AC3)."""
    found = problems(settings, process)
    if found:
        raise SystemExit("refusing to start: " + "; ".join(found))


def describe(settings: Settings | None = None) -> list[dict[str, Any]]:
    """Every swap point with its setting, the choice made, its version and the other choices."""
    s = settings or get_settings()
    out = []
    for point in POINTS:
        value = str(point.current(s) or "").strip()
        choices = point.choices()
        choice = choices.get(value.lower()) or choices.get(value)
        out.append({
            "swap_point": point.key, "title": point.title, "setting": point.setting,
            "current": value, "version": version_of(choice.package) if choice else None,
            "detail": point.detail(s), "available": sorted(choices),
            "custom_allowed": point.custom, "interface": point.interface,
            "contract_test": point.contract, "runs_in": list(point.runs_in(s)),
            "problem": problem_of(point, s),
        })  # fmt: skip
    return out
