"""SW-S6 "Replace any non-model component" - the parts that need no database or server.

* AC1  the filesystem store passes the SAME contract test as the S3 store, and is chosen by the
       one setting; the ingest code does not know which store it has (end to end, with the real
       ingest: ``test_sw_s6_acceptance_integration.py``);
* AC2  the queue's server is asked what it is: Redis 7.2 or Valkey as the setting says, never a
       Redis whose licence is not open-source (20 jobs on Valkey needs the server: last test);
* AC3  with no SNOMED CT / ICD-10 licence a diagnosis is the page's text plus the canonical
       term, with no external code - in the saved fact and in the FHIR it becomes;
* AC4  the swap drill on the golden set: every difference explained, or the drill fails.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from cdi_adapter import components, queueserver, storage, swapdrill
from cdi_adapter.config import Settings, get_settings
from cdi_adapter.fhir import resources as R
from cdi_adapter.terminology import canonical, seed
from cdi_adapter.terminology.service import bind_fact
from tests.contracts import object_store_contract

ROOT = Path(__file__).resolve().parents[1]
SNOMED_OR_ICD = (seed.SNOMED, seed.ICD10)


@pytest.fixture
def unlicensed(monkeypatch):
    """A customer with no SNOMED CT and no ICD-10 licence: the product's default."""
    monkeypatch.setenv("CDI_LICENSED_CODE_SYSTEMS", "")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def licensed(monkeypatch):
    monkeypatch.setenv("CDI_LICENSED_CODE_SYSTEMS", "snomed,icd10")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ------------------------------------------------------------------ AC1: the filesystem store


def test_ac1_the_filesystem_store_passes_the_object_store_contract(tmp_path):
    object_store_contract(storage.FileStore(root=tmp_path / "scans"))


def test_ac1_one_setting_chooses_the_store(tmp_path, monkeypatch):
    assert {"s3", "fs"} <= set(storage.registered())
    monkeypatch.setenv("CDI_OBJECT_STORE", "fs")
    monkeypatch.setenv("CDI_FILE_STORE_ROOT", str(tmp_path / "scans"))
    get_settings.cache_clear()
    storage.get_store.cache_clear()
    try:
        store = storage.get_store()
        assert isinstance(store, storage.FileStore) and store.root == (tmp_path / "scans").resolve()
        view = {c["swap_point"]: c for c in components.describe()}["object_store"]
        assert (view["current"], view["problem"]) == ("fs", None)
        assert view["detail"] == f"folder {tmp_path / 'scans'}"
    finally:
        get_settings.cache_clear()
        storage.get_store.cache_clear()


def test_ac1_the_filesystem_store_without_its_folder_is_refused_at_start():
    s = Settings(object_store="fs", file_store_root="")
    assert "CDI_OBJECT_STORE='fs' needs CDI_FILE_STORE_ROOT" in " ".join(components.problems(s))
    with pytest.raises(ValueError, match="needs CDI_FILE_STORE_ROOT"):
        storage.FileStore(s)


@pytest.mark.parametrize(
    "key",
    ["../escape.png", "/abs/key", "a/../../b", "a\\b", "c:/x", "", "a//b", ".content-types/x"],
)
def test_ac1_no_key_reaches_outside_the_folder(tmp_path, key):
    store = storage.FileStore(root=tmp_path / "scans")
    with pytest.raises(ValueError):
        store.put_bytes(key, b"x", "image/png")
    assert not (tmp_path / "escape.png").exists()


def test_ac1_a_write_is_whole_or_not_at_all_and_listing_shows_only_the_scans(tmp_path):
    store = storage.FileStore(root=tmp_path / "scans")
    key = storage.page_key("a" * 64, 1)
    store.put_bytes(key, b"first", "image/png")
    store.put_bytes(key, b"second, longer", "image/png")
    assert store.get_bytes(key) == b"second, longer"
    assert list(store.listing()) == [(key, len(b"second, longer"))]
    leftovers = [p.name for p in (tmp_path / "scans").rglob(".tmp-*")]
    assert leftovers == []
    assert storage.missing(FileNotFoundError(key))  # "not there" is told apart from "down"


def test_ac1_the_ingest_code_does_not_know_which_store_it_has():
    source = (ROOT / "src/cdi_adapter/ingest/service.py").read_text("utf-8")
    assert "get_store()" in source
    for adapter in ("FileStore", "boto3", "botocore", "file_store_root", "s3_endpoint"):
        assert adapter not in source


@pytest.mark.xfail(
    strict=True,
    reason="not run here: the S3-compatible side needs a real MinIO (CDI_TEST_S3_ENDPOINT); "
    "no substitute server is used",
)
def test_ac1_the_same_scan_saved_and_read_back_on_the_s3_store():
    import os

    assert os.environ.get("CDI_TEST_S3_ENDPOINT"), "no MinIO"
    object_store_contract(storage.ObjectStore(get_settings()))


# ------------------------------------------------------------------ AC2: Redis 7.2 or Valkey

VALKEY = {"server_name": "valkey", "valkey_version": "8.0.2", "redis_version": "7.2.4"}
REDIS_72 = {"redis_version": "7.2.5", "redis_mode": "standalone"}
REDIS_74 = {"redis_version": "7.4.1"}


def test_ac2_the_server_is_asked_what_it_is():
    assert queueserver.identify(VALKEY) == {"server": "valkey", "version": "8.0.2"}
    assert queueserver.identify(REDIS_72) == {"server": "redis", "version": "7.2.5"}


def test_ac2_valkey_or_redis_7_2_as_the_setting_says_is_accepted():
    assert queueserver.problem(VALKEY, "valkey") is None
    assert queueserver.problem(REDIS_72, "redis") is None


def test_ac2_the_wrong_server_or_a_redis_that_is_not_open_source_is_refused():
    assert queueserver.problem(REDIS_72, "valkey") == (
        "CDI_QUEUE_SERVER='valkey', but the queue's server is redis 7.2.5"
    )
    assert "is valkey 8.0.2" in str(queueserver.problem(VALKEY, "redis"))
    said = str(queueserver.problem(REDIS_74, "redis"))
    assert "Redis 7.4.1: Redis after 7.2 is not under an open-source licence" in said
    s = Settings(queue_server="redis", broker_url="redis://127.0.0.1:6379/0")
    with pytest.raises(SystemExit, match="refusing to start"):
        queueserver.check(s, info=REDIS_74)
    assert queueserver.check(Settings(queue_server="valkey"), info=VALKEY)["server"] == "valkey"


def test_ac2_a_worker_asks_before_it_takes_a_task():
    from celery import signals

    from cdi_adapter import worker

    receivers = [r[1]() for r in signals.worker_init.receivers]
    assert worker._check_queue_server in receivers
    assert queueserver.check(Settings(broker_url="amqp://guest@127.0.0.1//"))["server"] is None


def test_ac2_the_queue_drill_says_when_it_cannot_run():
    got = swapdrill.queue()
    assert got["status"].startswith("not run: no Redis and Valkey servers named")


@pytest.mark.xfail(
    strict=True,
    reason="not run here: needs a real Valkey and a real Redis 7.2 (CDI_TEST_VALKEY_URL, "
    "CDI_TEST_REDIS_URL); Windows Smart App Control blocks building either locally",
)
def test_ac2_twenty_jobs_on_valkey_all_finish_with_no_duplicate():
    import os

    url, redis_url = os.environ.get("CDI_TEST_VALKEY_URL"), os.environ.get("CDI_TEST_REDIS_URL")
    assert url and redis_url, "no servers"
    got = swapdrill.queue(redis_url, url, jobs=20)
    assert got["status"] == "matched", got


# ------------------------------------------------------------------ AC3: no licence, no code


def test_ac3_with_no_licence_a_diagnosis_is_its_text_plus_the_canonical_term(unlicensed):
    u = bind_fact({"fact_type": "condition", "local_text": "T2DM"})
    assert u["code_status"] == "bound"
    assert u["code_display"] == "Type 2 diabetes mellitus"  # the canonical term
    assert u["code_system"] == canonical.INTERNAL_SYSTEM
    assert u["code"] == "condition:type-2-diabetes-mellitus"  # the vocabulary's own id
    assert canonical.icd10_for(u["code"]) is None
    assert Settings.model_fields["licensed_code_systems"].default == ""  # the default


@pytest.mark.parametrize(
    ("fact_type", "text", "term"),
    [
        ("condition", "htn", "condition:essential-hypertension"),
        ("medication", "Tab Metformin 500 mg", "medicine:metformin"),
        ("allergy", "Penicillin", "allergen:penicillin"),
        ("symptom", "fever", "symptom:fever"),
    ],
)
def test_ac3_no_snomed_code_on_any_kind_of_term(unlicensed, fact_type, text, term):
    u = bind_fact({"fact_type": fact_type, "local_text": text})
    assert (u["code_system"], u["code"]) == (canonical.INTERNAL_SYSTEM, term)
    if fact_type == "medication":
        assert u["medication"]["rxlike_system"] == canonical.INTERNAL_SYSTEM


def test_ac3_lab_tests_and_vital_signs_keep_loinc_which_needs_no_licence(unlicensed):
    u = bind_fact({"fact_type": "lab_result", "local_text": "HbA1c"})
    assert u["code_system"] == seed.LOINC and u["code"] == "4548-4"


def test_ac3_the_fhir_condition_carries_no_snomed_and_no_icd(unlicensed):
    u = bind_fact({"fact_type": "condition", "local_text": "Type 2 diabetes mellitus"})
    fact = {
        "local_text": "Type 2 DM",
        **u,
        "icd10": canonical.icd10_for(u["code"]),
        "asserted_time": None,
    }
    res = R.condition(fact, "urn:uuid:c1", "urn:uuid:p1", None)
    systems = [c["system"] for c in res["code"].get("coding", [])]
    assert not set(systems) & set(SNOMED_OR_ICD)
    assert canonical.route_coding("oral") is None


def test_ac3_a_licensed_customer_gets_snomed_and_icd(licensed):
    u = bind_fact({"fact_type": "condition", "local_text": "T2DM"})
    assert (u["code_system"], u["code"]) == (seed.SNOMED, "44054006")
    assert canonical.icd10_for(u["code"]) == "E11"
    assert canonical.route_coding("oral") == {
        "system": seed.SNOMED,
        "code": "26643006",
        "display": "Oral route",
    }


def test_ac3_a_misspelt_licence_is_not_a_licence():
    s = Settings(licensed_code_systems="snomed-ct")
    assert any("CDI_LICENSED_CODE_SYSTEMS names snomed-ct" in p for p in components.problems(s))


def test_ac3_the_vocabulary_ships_with_no_snomed_or_icd_content():
    path = canonical.FILE
    rows = list(csv.DictReader(path.read_text("utf-8").splitlines()))
    assert tuple(rows[0]) == canonical.COLUMNS  # the story's columns (TR3)
    assert not any(r["snomed_code_optional"] or r["icd_code_optional"] for r in rows)
    snomed_codes = {c.code for c in seed.ALL_CONCEPTS if c.system == seed.SNOMED}
    icd_codes = {c.icd10 for c in seed.ALL_CONCEPTS if c.icd10}
    cells = {v for r in rows for v in r.values()}
    assert not cells & (snomed_codes | icd_codes)
    assert path.read_text("utf-8") == canonical.build()  # up to date with its source
    assert len(rows) > 100 and {"condition", "medicine", "lab"} <= {r["type"] for r in rows}


# ------------------------------------------------------------------ AC4: the swap drill


def test_ac4_terminology_differences_on_the_golden_set_are_all_explained():
    got = swapdrill.terminology()
    assert got["status"] == "differences explained"
    assert got["unexplained"] == [] and got["external_codes_without_licence"] == []
    assert {d["field"] for d in got["explained"]} == {"code_system", "code", "icd10"}
    assert got["default_terms"]["condition: Type 2 diabetes mellitus"] == (
        "Type 2 diabetes mellitus"
    )


def test_ac4_a_difference_nobody_explained_fails_the_drill(monkeypatch):
    monkeypatch.setattr(swapdrill, "EXPECTED", {})
    got = swapdrill.terminology()
    assert got["status"] == "differences UNEXPLAINED" and got["unexplained"]


def test_ac4_the_drill_report(monkeypatch, tmp_path):
    monkeypatch.setattr(storage.ObjectStore, "healthy", lambda self: False)  # MinIO not here
    code = swapdrill.main(["--out", str(tmp_path)])
    report = json.loads((tmp_path / "swap-drill.json").read_text("utf-8"))
    assert code == 0 and report["passed"] is True and report["complete"] is False
    by = {p["swap_point"]: p for p in report["swap_points"]}
    assert by["pdf_renderer"]["status"] == "matched"
    assert by["terminology"]["status"] == "differences explained"
    assert by["object_store"]["status"].startswith("not run: ConnectionError")
    assert by["object_store"]["alternative_ok"] is True  # the filesystem store: every byte back
    assert set(report["not_run"]) == {"object_store", "job_queue", "drive_connector", "sign_in"}
    md = (tmp_path / "swap-drill.md").read_text("utf-8")
    assert "**PASSED** - not complete:" in md and "## terminology: each difference" in md


@pytest.mark.xfail(
    strict=True,
    reason="not complete here: the drill needs MinIO (the default store), Redis 7.2 and Valkey, "
    "real drive accounts and a real identity provider",
)
def test_ac4_the_whole_drill_has_run_on_every_swap_point():
    found = sorted((ROOT / "docs" / "evidence" / "SW-S6").glob("swap-drill*.json"))
    assert found and json.loads(found[-1].read_text("utf-8"))["complete"]
