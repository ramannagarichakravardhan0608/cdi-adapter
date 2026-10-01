"""Regression checks for patient safety, bounded intake, and sharing gates."""

from datetime import date
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from cdi_adapter.config import Settings
from cdi_adapter.extract.service import check_mismatch
from cdi_adapter.fhir import resources, validate_abdm
from cdi_adapter.ingest.pages import UnsupportedDocument, render_pages
from cdi_adapter.mpi.service import Candidate
from cdi_adapter.training.isotonic import fit
from cdi_adapter.webapp.limits import RequestSizeLimit
from cdi_adapter.webapp.review import ReviewError, _number, _signed
from tests.helpers import make_pdf
from tests.test_fhir_unit import _mini_bundle


def test_full_dob_and_abha_conflicts_are_not_fuzzy_matches():
    expected = {"name": "Synthetic Person", "dob": "1980-01-01", "abha_id": "11111111111111"}
    assert check_mismatch(expected, Candidate(name="Synthetic Person", dob=date(1980, 12, 31)))
    assert check_mismatch(expected, Candidate(name="Synthetic Person", abha="22222222222222"))
    assert not check_mismatch(expected, Candidate(name="Synthetic Person", birth_year=1980))


def test_estimated_dob_is_not_exported_as_a_full_date():
    p = {"birth_date": date(1978, 1, 1), "birth_date_precision": "year"}
    assert "birthDate" not in resources.patient(p, "urn:uuid:p")
    p["birth_date_precision"] = "day"
    assert resources.patient(p, "urn:uuid:p")["birthDate"] == "1978-01-01"


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", float("nan")])
def test_nonfinite_corrections_are_rejected(value):
    for parse in (_number, _signed):
        with pytest.raises(ReviewError, match="finite"):
            parse("value", value)


def test_signed_observations_are_allowed_but_negative_doses_are_not():
    assert _signed("value_num", -5) == -5
    with pytest.raises(ReviewError):
        _number("dose_num", -5)


def test_missing_validator_fails_closed(monkeypatch):
    monkeypatch.setattr(validate_abdm, "hapi_available", lambda: False)
    monkeypatch.setattr(
        validate_abdm, "get_settings", lambda: SimpleNamespace(fhir_require_hapi=True)
    )
    result = validate_abdm.validate(_mini_bundle(), "PrescriptionRecord")
    assert not result["valid"]
    assert result["issues"][-1]["code"] == "validator-unavailable"


def test_isotonic_preserves_pooled_training_predictions():
    curve = fit([0, 0.5, 1], [1, 0, 1])
    assert [curve.predict(x) for x in [0, 0.5, 1]] == [0.5, 0.5, 1]
    tied = fit([0, 0.5, 0.5, 1], [1, 0, 1, 1])
    assert tied.predict(0) == tied.predict(0.5) == pytest.approx(2 / 3)


def test_pdf_limits_are_checked_before_rendering(monkeypatch):
    from cdi_adapter.ingest import pages

    s = Settings(_env_file=None, document_max_pages=1, page_max_pixels=1_000_000)
    monkeypatch.setattr(pages, "get_settings", lambda: s)
    with pytest.raises(UnsupportedDocument, match="page limit"):
        render_pages(make_pdf([["one"], ["two"]]), "application/pdf")
    with pytest.raises(UnsupportedDocument, match="pixel budget"):
        render_pages(make_pdf([["one"]]), "application/pdf")


def test_body_limit_covers_content_length_and_chunked_requests(monkeypatch):
    from cdi_adapter.webapp import limits

    monkeypatch.setattr(limits, "get_settings", lambda: SimpleNamespace(request_max_bytes=1024))
    app = FastAPI()
    app.add_middleware(RequestSizeLimit)

    @app.post("/")
    async def length(request: Request):
        return {"bytes": len(await request.body())}

    client = TestClient(app)
    assert client.post("/", content=b"a" * 1024).json() == {"bytes": 1024}
    assert client.post("/", content=b"a" * 1025).status_code == 413
    assert client.post("/", content=iter([b"a" * 600, b"b" * 600])).status_code == 413


def test_replay_plan_stays_bounded_after_replays(monkeypatch):
    from cdi_adapter.orchestrator import workflow

    original = [
        {"id": i, "stage": stage, "status": "ok", "outputs": {}}
        for i, stage in enumerate(["ingest", "ocr", "classify", "extract", "validate"])
    ]
    repeated = original + [{**s, "id": s["id"] + 10} for s in original[1:]]
    monkeypatch.setattr(workflow, "steps", lambda *_: repeated)
    plan = workflow.replay_plan(None, "doc")
    assert [p["stage"] for p in plan] == ["ocr", "classify", "extract", "validate"]
    assert all(p["step_id"] > 10 for p in plan)
    monkeypatch.setattr(workflow, "steps", lambda *_: original + [{**original[1], "id": 99}])
    partial = workflow.replay_plan(None, "doc")
    assert [p["stage"] for p in partial] == ["ocr", "classify", "extract", "validate"]
    assert partial[0]["step_id"] == 99


def test_job_projection_only_reads_its_documents(monkeypatch):
    from cdi_adapter.webapp import app

    calls = []
    monkeypatch.setattr(app, "project_patient", lambda pid, **kw: calls.append((pid, kw)) or {})
    job = SimpleNamespace(
        id="j", patient_id="p", trace_parent=lambda: None, docs=[SimpleNamespace(document_id="d")]
    )
    app._fhir(job)
    assert calls == [("p", {"persist": False, "document_ids": ["d"]})]


def test_production_configuration_rejects_development_defaults(monkeypatch):
    from cdi_adapter.config import Settings, get_settings
    from cdi_adapter.preflight import configuration_issues

    issues = configuration_issues(Settings(_env_file=None))
    assert any("CDI_DATABASE_URL" in s for s in issues)
    assert any("real model" in s for s in issues)
    assert any("calibrator" in s for s in issues)
    monkeypatch.setenv("CDI_PRODUCTION", "true")
    get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="Production configuration rejected"):
            get_settings()
    finally:
        get_settings.cache_clear()


def test_preflight_requires_matching_loaded_model(monkeypatch):
    from cdi_adapter import preflight
    from cdi_adapter.config import get_settings

    # A preflight run in the synthetic environment must never claim production readiness.
    monkeypatch.setattr(preflight, "checks", lambda: {"configuration": [], "model": False})
    assert preflight.main() == 1
    get_settings.cache_clear()
