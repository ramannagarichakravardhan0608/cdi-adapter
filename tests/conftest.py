"""Shared fixtures. Integration tests need a native PostgreSQL 16 (no Docker, ARCHITECTURE §3.3).

``CDI_TEST_ADMIN_URL`` points at a maintenance DB on that server (default: the local ``postgres``
DB as user ``cdi``); a throwaway ``cdi_test`` database is created per session. When no server is
reachable, integration tests are **skipped, not passed** — a block that needs them is not Done.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import OperationalError

ROOT = Path(__file__).resolve().parents[1]
ADMIN_URL = os.environ.get(
    "CDI_TEST_ADMIN_URL", "postgresql+psycopg://cdi:cdi@127.0.0.1:5432/postgres"
)
TEST_DB = "cdi_test"
# CI integration stage (backlog E1-S2): missing infra must turn the build red, not skip green.
REQUIRE_INFRA = os.environ.get("CDI_TEST_REQUIRE_INFRA") == "1"


def _no_infra(reason: str) -> None:
    if REQUIRE_INFRA:
        pytest.fail(f"CDI_TEST_REQUIRE_INFRA=1 but {reason}", pytrace=False)
    pytest.skip(reason)


def _admin_engine() -> Engine:
    return create_engine(
        ADMIN_URL, isolation_level="AUTOCOMMIT", connect_args={"connect_timeout": 3}
    )


# The developer's own .env may point the file listener at a real folder (on the owner's PC: a
# OneDrive folder, with the folder itself as the inbox). No test may ever read that, let alone
# write into it: these three are set in the environment, which outranks .env, before any test
# builds its settings. A test that wants other values passes them or sets them itself.
os.environ["CDI_LISTENER_CONNECTOR"] = "local"
os.environ["CDI_LISTENER_INBOX"] = "inbox"
os.environ["CDI_LISTENER_LOCAL_ROOT"] = str(
    Path(tempfile.gettempdir()) / "cdi-listener-tests-never-the-real-folder"
)
# LS-S2: the same for the listener's sign-in files - never the real token cache, never an alert
# file under a real /workspace, never a real sign-in from the developer's .env.
os.environ["CDI_GRAPH_TOKEN_CACHE"] = str(
    Path(tempfile.gettempdir()) / "cdi-listener-tests-never-the-real-folder" / "token_cache.json"
)
os.environ["CDI_LISTENER_ALERT_FILE"] = str(
    Path(tempfile.gettempdir()) / "cdi-listener-tests-never-the-real-folder" / "alert.json"
)
for _name in ("CDI_GRAPH_CLIENT_ID", "CDI_GRAPH_TENANT_ID", "CDI_GRAPH_CLIENT_SECRET",
              "CDI_GRAPH_CLIENT_SECRET_FILE", "CDI_GRAPH_CLIENT_CERT_FILE",
              "CDI_GDRIVE_SERVICE_ACCOUNT_FILE"):  # fmt: skip
    os.environ[_name] = ""
# ENT-S2: the tests sign in with local test accounts whatever the developer's .env says, and
# the per-person upload limit - which counts in the database on every upload - is off, so a
# suite that drives the upload route with no database does not wait for one. The limit and
# single sign-on are switched on, by name, in the tests that are about them.
os.environ["CDI_AUTH_PROVIDER"] = "local"
os.environ["CDI_UPLOAD_MAX_PER_MINUTE"] = "0"
# SW-S6: the product's default is NO SNOMED CT / ICD-10 (no licence). The suites written before
# SW-S6 check the coded output, so they run as a customer who holds both licences; the tests of
# the default (tests/test_sw_s6_acceptance_unit.py) switch it off by name.
os.environ.setdefault("CDI_LICENSED_CODE_SYSTEMS", "snomed,icd10")

# Suites that drive the job runner with placeholder bytes (``b"x"``) to test *orchestration* -
# ordering, retries, batching, tracing, leases. They are not pictures, so with the picture check
# on (the default since 2026-10-08, IM-S1 AC2) every one of them would be held as "unreadable"
# before the code under test ran. These suites run with the check off; what the check itself
# does, and that it is on by default, is tested where pictures are real (test_recognition_unit,
# test_upload_quality_unit, the *_acceptance_unit suites).
PLACEHOLDER_UPLOADS = {"test_jobs_unit", "test_batch_unit", "test_tracing_unit",
                       "test_epic2_unit", "test_pool_split_unit", "test_metrics_unit",
                       "test_apptier_unit", "test_monitoring_unit",
                       "test_out_s3_acceptance_unit"}  # fmt: skip


@pytest.fixture(autouse=True)
def _placeholder_uploads_skip_the_picture_check(request, monkeypatch):
    if request.module.__name__.rsplit(".", 1)[-1] not in PLACEHOLDER_UPLOADS:
        yield
        return
    from cdi_adapter.config import get_settings

    monkeypatch.setenv("CDI_QUALITY_CHECK", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(scope="session")
def pg_url() -> Iterator[str]:
    """A clean ``cdi_test`` database per session.

    The database is created once and its schema is reset at session start, rather than
    DROP/CREATE DATABASE every session: on the Windows dev box a DROP DATABASE takes ~1 min
    (each Postgres backend is a new, security-scanned process). Isolation is the same — every
    session starts from an empty ``public`` schema.
    """
    try:
        admin = _admin_engine()
        with admin.connect() as c:
            exists = c.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :d"), {"d": TEST_DB}
            ).first()
            if not exists:
                c.execute(text(f"CREATE DATABASE {TEST_DB}"))
    except OperationalError as exc:  # no server listening / wrong host / bad password
        reason = str(exc).splitlines()[0][:160]
        _no_infra(f"PostgreSQL not reachable at {ADMIN_URL}: {type(exc).__name__}: {reason}")
    except Exception as exc:  # noqa: BLE001 - the server answered but refused the statement
        # e.g. InsufficientPrivilege: the role may log in but has no CREATEDB. Saying "not
        # reachable" for this sends you hunting the wrong problem (seen on the Colab host).
        reason = str(exc).splitlines()[0][:160]
        _no_infra(
            f"PostgreSQL at {ADMIN_URL} refused CREATE DATABASE {TEST_DB}: "
            f"{type(exc).__name__}: {reason} "
            f"(the test role needs CREATEDB: ALTER ROLE <role> CREATEDB)"
        )
    admin.dispose()
    url = make_url(ADMIN_URL).set(database=TEST_DB).render_as_string(hide_password=False)
    eng = create_engine(url, isolation_level="AUTOCOMMIT")
    with eng.connect() as c:
        c.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        c.execute(text("CREATE SCHEMA public"))
        c.execute(text("DROP SEQUENCE IF EXISTS patient_mpi_seq"))
    eng.dispose()
    yield url


@pytest.fixture(scope="session")
def migrated_db(pg_url) -> str:
    """The throwaway test DB at Alembic head."""
    from alembic import command

    command.upgrade(alembic_cfg(pg_url), "head")
    return pg_url


# A REAL MinIO (ARCHITECTURE §3.3) — never a stand-in. Unset => MinIO integration tests skip and
# the backlog block stays Blocked. e.g. CDI_TEST_S3_ENDPOINT=http://127.0.0.1:9000
S3_ENDPOINT = os.environ.get("CDI_TEST_S3_ENDPOINT")


@pytest.fixture(scope="session")
def minio_endpoint() -> str:
    if not S3_ENDPOINT:
        _no_infra("real MinIO required: set CDI_TEST_S3_ENDPOINT (no substitute servers)")
    return S3_ENDPOINT


@pytest.fixture
def app_env(migrated_db, minio_endpoint, monkeypatch):
    """Point every CDI_* setting at the test Postgres + real MinIO for one test."""
    from cdi_adapter import db, storage
    from cdi_adapter.config import get_settings

    monkeypatch.setenv("CDI_DATABASE_URL", migrated_db)
    monkeypatch.setenv("CDI_S3_ENDPOINT_URL", minio_endpoint)
    monkeypatch.setenv("CDI_S3_BUCKET", "cdi-test")
    for k in ("CDI_S3_ACCESS_KEY", "CDI_S3_SECRET_KEY"):
        if os.environ.get(f"{k}_TEST"):
            monkeypatch.setenv(k, os.environ[f"{k}_TEST"])
    get_settings.cache_clear()
    storage.get_store.cache_clear()
    db.reset_engine()
    storage.get_store().ensure_bucket()
    yield
    db.reset_engine()
    get_settings.cache_clear()
    storage.get_store.cache_clear()


@pytest.fixture
def seeded_scans(app_env):
    """A FHIR bundle embeds the original scan (ARCHITECTURE §5 S9: DocumentReference
    ``attachment.data``, <= 12 MB; the ABDM profiles make it 1..1), so a test that builds one reads
    a REAL MinIO: skipped with the reason when CDI_TEST_S3_ENDPOINT is unset. The synthetic test
    documents (``tests.test_extract_integration._doc_with_ocr``) all point at the key "unused"."""
    from cdi_adapter.storage import get_store
    from tests.helpers import make_pdf

    get_store().put_bytes("unused", make_pdf([["Synthetic test scan"]]), "application/pdf")


@pytest.fixture
def db_env(migrated_db, monkeypatch):
    """Point CDI_DATABASE_URL at the test Postgres for code paths that touch no object store."""
    from cdi_adapter import db
    from cdi_adapter.config import get_settings

    monkeypatch.setenv("CDI_DATABASE_URL", migrated_db)
    get_settings.cache_clear()
    db.reset_engine()
    yield migrated_db
    db.reset_engine()
    get_settings.cache_clear()


def alembic_cfg(url: str):
    from alembic.config import Config

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "db" / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg
