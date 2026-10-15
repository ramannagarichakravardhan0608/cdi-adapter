"""ENT-S2 "Sign-in, roles and an audit trail" - the four acceptance criteria.

* AC1  a person who is not signed in is sent to sign-in and cannot call the API;
* AC2  an uploader who tries the review console is refused;
* AC3  after a result is downloaded the audit table shows who, what, which document and when;
* AC4  the identity provider is switched by configuration, and sign-in works with no code change.

The web app is the real one. Two things stand in: the database (``Db`` - the few tables
sign-in and the audit trail touch) and the identity provider (``Provider`` - a made-up one
that signs real tokens with its own key, so every check of the token is really made).
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from cdi_adapter.config import get_settings
from cdi_adapter.webapp import app as web
from cdi_adapter.webapp import auth, oidc
from tests.helpers import browser

ROOT = Path(__file__).resolve().parents[1]
DOC = "0a1b2c3d-0000-4000-8000-000000000001"


# ------------------------------------------------------------------ the stand-in database


class Result:
    def __init__(self, rows: list[Any]) -> None:
        self.rows = rows

    def mappings(self) -> Result:
        return self

    def first(self) -> Any:
        return self.rows[0] if self.rows else None

    def scalar_one(self) -> Any:
        return self.rows[0]


class Db:
    """``web_user``, ``web_session``, ``audit_log`` and the upload count - nothing else."""

    def __init__(self) -> None:
        self.users: list[dict[str, Any]] = []
        self.sessions: dict[str, dict[str, Any]] = {}
        self.audit: list[dict[str, Any]] = []
        self.uploads_last_minute = 0

    def add(self, username: str, role: str, **more: Any) -> dict[str, Any]:
        user = {
            "id": f"user-{len(self.users) + 1}",
            "username": username,
            "role": role,
            "display_name": username.title(),
            "active": True,
            "password_hash": None,
            "provider": None,
            "subject": None,
            "failed_logins": 0,
            "locked_until": None,
            **more,
        }
        self.users.append(user)
        return user

    def execute(self, statement: Any, params: Any = None) -> Result:
        sql, p = " ".join(str(statement).split()), dict(params or {})
        if "FROM web_user WHERE provider" in sql:
            return Result(
                [u for u in self.users if (u["provider"], u["subject"]) == (p["p"], p["s"])]
            )
        if sql.startswith("SELECT 1 FROM web_user WHERE username"):
            return Result([1 for u in self.users if u["username"] == p["u"]])
        if sql.startswith("SELECT * FROM web_user WHERE username"):
            return Result([u for u in self.users if u["username"] == p["u"]])
        if sql.startswith("INSERT INTO web_user"):
            assert "NULL, :p, :s" in sql, "an account of the identity system has no password"
            return Result(
                [
                    self.add(p["u"], p["r"], display_name=p["n"], provider=p["p"], subject=p["s"])[
                        "id"
                    ]
                ]
            )
        if sql.startswith("UPDATE web_user SET role"):
            next(u for u in self.users if u["id"] == p["i"]).update(
                role=p["r"], display_name=p["n"]
            )
        if sql.startswith("INSERT INTO web_session"):
            self.sessions[p["t"]] = {
                "user_id": p["u"],
                "expires_at": p["e"],
                "client": p["c"],
                "last_seen_at": datetime.now(UTC),
            }
        if "FROM web_session s JOIN web_user u" in sql:
            s = self.sessions.get(p["t"])
            if s is None:
                return Result([])
            u = next(u for u in self.users if u["id"] == s["user_id"])
            return Result([{**u, **s, "token_sha256": p["t"]}])
        if sql.startswith("UPDATE web_session"):
            self.sessions[p["t"]]["last_seen_at"] = p["n"]
        if sql.startswith("DELETE FROM web_session"):
            self.sessions.pop(p["t"], None)
        if sql.startswith("INSERT INTO audit_log"):
            cols = re.search(r"\((.*?)\) VALUES", sql).group(1).split(", ")  # type: ignore[union-attr]
            row = {c: p.get(c) for c in cols}
            row["detail"] = json.loads(row["detail"] or "{}")
            row["at"] = datetime.now(UTC)  # the table's own default: now()
            self.audit.append(row)
            return Result([len(self.audit)])
        if "FROM web_job WHERE created_by" in sql:
            return Result([self.uploads_last_minute])
        return Result([])


@pytest.fixture
def db(monkeypatch) -> Db:
    database = Db()

    @contextmanager
    def scope():
        yield database

    monkeypatch.setattr(auth, "session_scope", scope)
    monkeypatch.setattr(web, "session_scope", scope)
    return database


def _signed_in(db: Db, role: str, username: str = "asha.rao") -> Any:
    """A browser holding a live session of a person with ``role``."""
    user = db.add(username, role)
    with auth.session_scope() as c:
        token = auth.create_session(c, auth.User(user["id"], username, username, role), "test")
    client = browser(web.app)
    client.cookies.set(auth.COOKIE, token)
    return client


# ------------------------------------------------------------------ the stand-in provider


class Provider:
    """A made-up OpenID Connect provider: its description, its keys, its token endpoint."""

    def __init__(self, issuer: str, client_id: str = "cdi-app") -> None:
        self.issuer, self.client_id = issuer, client_id
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.kid = "key-" + hashlib.sha256(issuer.encode()).hexdigest()[:6]
        self.claims: dict[str, Any] = {}
        self.sign_with = self.key
        self.asked: dict[str, str] = {}
        self.token_forms: list[dict[str, str]] = []

    def jwks(self) -> dict[str, Any]:
        public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()))
        return {"keys": [{**public, "kid": self.kid, "use": "sig", "alg": "RS256"}]}

    def token(self) -> str:
        now = int(time.time())
        claims = {
            "iss": self.issuer,
            "aud": self.client_id,
            "iat": now,
            "exp": now + 300,
            "nonce": self.asked.get("nonce"),
            **self.claims,
        }
        return jwt.encode(claims, self.sign_with, algorithm="RS256", headers={"kid": self.kid})

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if not str(request.url).startswith(self.issuer):
            return httpx.Response(404)
        if path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(
                200,
                json={
                    "issuer": self.issuer,
                    "authorization_endpoint": f"{self.issuer}/authorize",
                    "token_endpoint": f"{self.issuer}/token",
                    "jwks_uri": f"{self.issuer}/keys",
                },
            )
        if path.endswith("/keys"):
            return httpx.Response(200, json=self.jwks())
        if path.endswith("/token"):
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            self.token_forms.append(form)
            made = base64.urlsafe_b64encode(
                hashlib.sha256(form.get("code_verifier", "").encode()).digest()
            ).rstrip(b"=")
            if made.decode() != self.asked.get("code_challenge") or form.get("code") != "good":
                return httpx.Response(400, json={"error": "invalid_grant"})
            return httpx.Response(200, json={"id_token": self.token(), "token_type": "Bearer"})
        return httpx.Response(404)


@pytest.fixture
def sso(monkeypatch):
    """Switch single sign-on on for a provider - by SETTINGS only - and answer as that provider."""
    providers: dict[str, Provider] = {}
    real_client = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        for p in providers.values():
            if str(request.url).startswith(p.issuer):
                return p.handle(request)
        return httpx.Response(404)

    monkeypatch.setattr(
        oidc.httpx, "Client", lambda **k: real_client(transport=httpx.MockTransport(handler))
    )

    def use(issuer: str, **settings: str) -> Provider:
        provider = providers.setdefault(issuer, Provider(issuer))
        env = {
            "CDI_AUTH_PROVIDER": "oidc",
            "CDI_OIDC_ISSUER": issuer,
            "CDI_OIDC_CLIENT_ID": provider.client_id,
            "CDI_OIDC_CLIENT_SECRET": "",
            "CDI_OIDC_CLIENT_SECRET_FILE": "",
            "CDI_OIDC_ROLE_CLAIM": "roles",
            "CDI_OIDC_ROLE_MAP": "",
            "CDI_OIDC_USERNAME_CLAIM": "preferred_username",
            "CDI_OIDC_REDIRECT_URI": "",
            **settings,
        }
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        get_settings.cache_clear()
        oidc.forget()
        return provider

    yield use
    monkeypatch.undo()
    get_settings.cache_clear()
    oidc.forget()


def _sign_in(
    provider: Provider, client: Any = None, code: str = "good", tamper_state: bool = False
) -> tuple[Any, Any]:
    """Walk the whole sign-in as a browser would. Returns ``(browser, the last answer)``."""
    client = client or browser(web.app)
    first = client.get("/login/oidc", follow_redirects=False)
    assert first.status_code == 303 and first.headers["location"].startswith(provider.issuer)
    provider.asked = {
        k: v[0] for k, v in parse_qs(urlsplit(first.headers["location"]).query).items()
    }
    state = "forged" if tamper_state else provider.asked["state"]
    return client, client.get(f"/login/callback?code={code}&state={state}", follow_redirects=False)


# ------------------------------------------------------------------ AC1: not signed in


def test_ac1_the_upload_screen_sends_a_person_who_is_not_signed_in_to_sign_in():
    r = browser(web.app).get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login?next=/"


@pytest.mark.parametrize("path", ["/reviewer", "/review", "/admin", f"/result/{DOC}", "/monitor"])
def test_ac1_every_page_does_the_same(path):
    r = browser(web.app).get(path, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/login?next=")


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/me"),
        ("POST", "/api/jobs"),
        ("GET", f"/api/documents/{DOC}/result.json"),
        ("GET", f"/api/documents/{DOC}/result.json/download"),
        ("GET", "/api/reviewer/workbasket"),
        ("GET", f"/api/documents/{DOC}/original"),
        ("POST", "/api/registry"),
    ],
)
def test_ac1_the_api_cannot_be_called_without_a_sign_in(method, path):
    r = browser(web.app).request(method, path, follow_redirects=False)
    assert r.status_code == 401 and r.json() == {"detail": "sign in required"}


def test_ac1_no_route_is_left_without_a_rule_and_only_sign_in_and_health_are_open():
    open_ones = sorted(path for (_, path), who in auth.POLICY.items() if who is auth.PUBLIC)
    assert open_ones == [
        "/healthz",
        "/login",
        "/login",
        "/login/callback",
        "/login/oidc",
        "/readyz",
    ]
    assert web.app.docs_url is None and web.app.openapi_url is None


def test_ac1_a_made_up_or_expired_session_is_not_a_sign_in(db):
    client = browser(web.app)
    client.cookies.set(auth.COOKIE, "made-up")
    assert client.get("/api/me").status_code == 401
    live = _signed_in(db, "reviewer")
    assert live.get("/api/me").status_code == 200
    for s in db.sessions.values():
        s["expires_at"] = datetime(2020, 1, 1, tzinfo=UTC)
    assert live.get("/api/me").status_code == 401 and db.sessions == {}


def test_ac1_sign_out_ends_the_session(db):
    client = _signed_in(db, "intake")
    assert client.post("/logout", follow_redirects=False).headers["location"] == "/login"
    assert db.sessions == {} and client.get("/api/me").status_code == 401
    assert db.audit[-1]["detail"]["outcome"] == "signed_out"


# ------------------------------------------------------------------ AC2: roles


def test_ac2_an_uploader_is_refused_the_review_console(db):
    client = _signed_in(db, "intake")
    r = client.get("/reviewer", follow_redirects=False)
    assert r.status_code == 403 and "may not use this" in r.json()["detail"]


@pytest.mark.parametrize(
    "path",
    [
        "/review",
        "/api/reviewer/workbasket",
        "/api/review/tasks",
        f"/api/documents/{DOC}/result.json",
        "/admin",
        "/monitor",
    ],
)
def test_ac2_and_everything_behind_it(db, path):
    assert _signed_in(db, "intake").get(path, follow_redirects=False).status_code == 403


def test_ac2_the_uploader_can_still_upload_and_the_reviewer_can_still_review():
    P = auth.POLICY
    assert "intake" in P[("GET", "/")] and "intake" in P[("POST", "/api/jobs")]
    assert P[("GET", "/reviewer")] == {"reviewer", "admin"} == P[("GET", "/review")]
    assert P[("GET", "/admin")] == {"admin"}
    assert "reviewer" not in P[("POST", "/api/jobs")], "a reviewer does not upload"


def test_ac2_the_three_roles_of_the_story_are_the_three_roles_of_the_policy():
    assert oidc.ROLE_NAMES == {
        "uploader": "intake",
        "intake": "intake",
        "reviewer": "reviewer",
        "admin": "admin",
    }
    assert set(auth.ROLES) == set(oidc.ROLE_NAMES.values())


# ------------------------------------------------------------------ AC3: the audit trail


@pytest.fixture
def served(monkeypatch):
    monkeypatch.setattr(web.result, "as_json", lambda c, d: json.dumps({"document_id": str(d)}))


def test_ac3_a_download_is_written_with_who_what_which_document_and_when(db, served):
    client = _signed_in(db, "reviewer", "asha.rao")
    before = datetime.now(UTC)
    r = client.get(f"/api/documents/{DOC}/result.json/download")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    row = db.audit[-1]
    assert row["actor"] == "asha.rao" and row["actor_id"] == db.users[0]["id"]  # who
    assert row["detail"]["event"] == "result downloaded" and row["action"] == "read"  # what
    assert (row["entity"], row["entity_id"]) == ("source_document", DOC)  # which document
    assert before <= row["at"] <= datetime.now(UTC)  # when
    assert row["source_addr"] == "testclient"  # from where
    assert row["detail"]["served"] is True and row["detail"]["role"] == "reviewer"


def test_ac3_a_view_is_written_too_and_told_apart_from_a_download(db, served):
    client = _signed_in(db, "reviewer")
    client.get(f"/api/documents/{DOC}/result.json")
    client.get(f"/api/documents/{DOC}/result.json/download")
    assert [a["detail"]["event"] for a in db.audit] == ["result viewed", "result downloaded"]


def test_ac3_a_refused_or_failed_request_is_not_written_as_served(db, monkeypatch):
    def missing(c: Any, d: Any) -> str:
        raise LookupError("no document")

    monkeypatch.setattr(web.result, "as_json", missing)
    r = _signed_in(db, "reviewer").get(f"/api/documents/{DOC}/result.json/download")
    assert r.status_code == 404
    assert db.audit[-1]["detail"] == {**db.audit[-1]["detail"], "served": False, "status": 404}
    # a person with no right to it never reaches the document: nothing to say they read it
    db.audit.clear()
    assert (
        _signed_in(db, "intake", "desk").get(f"/api/documents/{DOC}/result.json").status_code == 403
    )
    assert db.audit == []


def test_ac3_no_patient_detail_goes_into_the_audit_row(db, served):
    _signed_in(db, "reviewer").get(f"/api/documents/{DOC}/result.json/download")
    assert set(db.audit[-1]["detail"]) == {"event", "served", "status", "role", "route"}
    assert db.audit[-1]["detail"]["route"] == "/api/documents/{document_id}/result.json/download"


def test_ac3_a_full_audit_table_does_not_turn_a_served_result_into_an_error(
    db, served, monkeypatch
):
    logged: list[str] = []
    monkeypatch.setattr(
        auth.repo, "write_audit", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk full"))
    )
    monkeypatch.setattr(auth.log, "error", lambda event, **k: logged.append(event))
    assert _signed_in(db, "reviewer").get(f"/api/documents/{DOC}/result.json").status_code == 200
    assert logged == ["audit_not_written"], "but it is said loudly"


def test_ac3_everything_that_shows_or_hands_over_content_is_audited():
    audited = {path for _, path in auth.AUDITED}
    for path in (
        "/api/documents/{document_id}/result.json/download",
        "/result/{document_id}",
        "/api/jobs/{job_id}/result.json/download",
        "/api/jobs/{job_id}/fhir/download",
        "/api/documents/{document_id}/original",
        "/api/documents/{document_id}/bundle",
        "/api/reviewer/items/{item_id}/bundle/download",
        "/admin",
    ):
        assert path in audited, path
    assert set(auth.AUDITED) <= set(auth.POLICY), "only routes that exist"
    assert all(auth.POLICY[k] is not auth.PUBLIC for k in auth.AUDITED)


def test_ac3_uploads_and_changes_are_written_by_the_code_that_makes_them():
    src = ROOT / "src/cdi_adapter/webapp"
    assert 'entity="web_job"' in (src / "jobstore.py").read_text(encoding="utf-8")  # an upload
    assert 'entity="clinical_fact"' in (src / "review.py").read_text(encoding="utf-8")  # a change
    assert "write_audit" in (src / "conflicts.py").read_text(encoding="utf-8")


def test_ac3_the_table_has_a_column_for_the_user_id_and_the_address():
    migration = (ROOT / "db/alembic/versions/0040_sso_and_audit_who.py").read_text("utf-8")
    assert "ALTER TABLE audit_log ADD COLUMN actor_id text" in migration
    assert "ALTER TABLE audit_log ADD COLUMN source_addr text" in migration
    up = migration.split("def upgrade")[1].split("def downgrade")[0]
    assert "DROP TABLE" not in up and "DELETE" not in up, "nothing recorded is removed"


def test_ac3_sign_in_and_refused_sign_in_are_in_the_trail(db, sso):
    provider = sso("https://idp.example/tenant-a")
    provider.claims = {
        "sub": "a-1",
        "preferred_username": "asha.rao@clinic.example",
        "name": "Asha Rao",
        "roles": ["reviewer"],
    }
    _sign_in(provider)
    row = db.audit[-1]
    assert (row["actor"], row["detail"]["outcome"], row["detail"]["via"]) == (
        "asha.rao",
        "signed_in",
        "oidc",
    )
    assert row["actor_id"] == db.users[0]["id"] and row["source_addr"] == "testclient"
    _sign_in(provider, tamper_state=True)
    assert (
        db.audit[-1]["detail"]["outcome"] == "refused"
        and "state" in db.audit[-1]["detail"]["reason"]
    )


# ------------------------------------------------------------------ AC4: the provider is settings


def test_ac4_sign_in_through_the_configured_provider_works(db, sso):
    provider = sso("https://idp.example/tenant-a")
    provider.claims = {
        "sub": "a-1",
        "preferred_username": "asha.rao@clinic.example",
        "name": "Asha Rao",
        "roles": ["reviewer"],
    }
    client, answer = _sign_in(provider)
    assert answer.status_code == 200 and 'url=/reviewer"' in answer.text
    me = client.get("/api/me").json()
    assert me == {"username": "asha.rao", "display_name": "Asha Rao", "role": "reviewer"}
    # and the role rule of every route applies to this session as to any other
    assert client.get("/admin", follow_redirects=False).status_code == 403


def test_ac4_another_provider_is_only_other_settings(db, sso):
    """A second provider - another address, other keys, the role in another claim under other
    names - and the same code signs a person in through it."""
    first = sso("https://idp.example/tenant-a")
    first.claims = {"sub": "a-1", "preferred_username": "asha.rao", "roles": ["uploader"]}
    one, _ = _sign_in(first)
    assert one.get("/api/me").json()["role"] == "intake"

    second = sso(
        "https://accounts.other.example/realms/clinic",
        CDI_OIDC_ROLE_CLAIM="groups",
        CDI_OIDC_USERNAME_CLAIM="email",
        CDI_OIDC_ROLE_MAP='{"/cdi/admins": "admin", "/cdi/reviewers": "reviewer"}',
    )
    second.claims = {
        "sub": "b-9",
        "email": "ravi.k@other.example",
        "name": "Ravi K",
        "groups": ["/staff", "/cdi/reviewers", "/cdi/admins"],
    }
    two, answer = _sign_in(second)
    assert answer.status_code == 200
    assert two.get("/api/me").json() == {
        "username": "ravi.k",
        "display_name": "Ravi K",
        "role": "admin",
    }
    assert {u["provider"] for u in db.users} == {first.issuer, second.issuer}


def test_ac4_nothing_in_the_code_names_a_provider():
    code = (ROOT / "src/cdi_adapter/webapp/oidc.py").read_text(encoding="utf-8")
    body = code.split('"""', 2)[2]  # the module's own explanation gives examples; the code not
    for name in ("microsoft", "entra", "google", "okta", "keycloak", "login."):
        assert name not in body.lower(), name
    fields = get_settings().__class__.model_fields
    assert {
        "auth_provider",
        "oidc_issuer",
        "oidc_client_id",
        "oidc_client_secret_file",
        "oidc_role_claim",
        "oidc_role_map",
        "oidc_scopes",
    } <= set(fields)


def test_ac4_no_password_is_asked_for_or_stored(db, sso):
    provider = sso("https://idp.example/tenant-a")
    provider.claims = {"sub": "a-1", "preferred_username": "asha.rao", "roles": ["reviewer"]}
    page = browser(web.app).get("/login").text
    assert "type=password" not in page and "Sign in with your organisation account" in page
    _sign_in(provider)
    assert db.users[0]["password_hash"] is None and db.users[0]["subject"] == "a-1"
    refused = browser(web.app).post("/login", data={"username": "asha.rao", "password": "x" * 12})
    assert refused.status_code == 404, "there is no password door while single sign-on is on"


def test_ac4_the_person_is_known_by_the_providers_id_not_by_a_name(db, sso):
    provider = sso("https://idp.example/tenant-a")
    db.add("asha.rao", "admin", password_hash="scrypt$...")  # a local account of that name
    provider.claims = {"sub": "a-1", "preferred_username": "asha.rao", "roles": ["uploader"]}
    client, _ = _sign_in(provider)
    assert client.get("/api/me").json() == {
        "username": "asha.rao-2",
        "display_name": "asha.rao",
        "role": "intake",
    }
    provider.claims["roles"] = ["reviewer"]  # the provider changes the role: it follows
    again, _ = _sign_in(provider)
    assert again.get("/api/me").json()["role"] == "reviewer" and len(db.users) == 2


@pytest.mark.parametrize(
    ("change", "why"),
    [
        ({"roles": []}, "no role"),
        ({"roles": ["some-other-app"]}, "no role"),
        ({"aud": "another-app"}, "not accepted"),
        ({"iss": "https://evil.example"}, "not accepted"),
        ({"exp": 1}, "not accepted"),
        ({"nonce": "replayed"}, "nonce"),
    ],
)
def test_ac4_a_token_that_does_not_check_out_signs_nobody_in(db, sso, change, why):
    provider = sso("https://idp.example/tenant-a")
    provider.claims = {
        "sub": "a-1",
        "preferred_username": "asha.rao",
        "roles": ["reviewer"],
        **change,
    }
    client, answer = _sign_in(provider)
    assert answer.status_code in (401, 403) and db.sessions == {}
    assert why in db.audit[-1]["detail"]["reason"]
    assert client.get("/api/me").status_code == 401


def test_ac4_a_token_signed_by_someone_else_is_refused(db, sso):
    provider = sso("https://idp.example/tenant-a")
    provider.claims = {"sub": "a-1", "preferred_username": "asha.rao", "roles": ["admin"]}
    provider.sign_with = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    _, answer = _sign_in(provider)
    assert answer.status_code == 401 and db.sessions == {} and db.users == []


def test_ac4_an_answer_that_was_not_asked_for_is_refused(db, sso):
    provider = sso("https://idp.example/tenant-a")
    provider.claims = {"sub": "a-1", "preferred_username": "asha.rao", "roles": ["admin"]}
    _, forged = _sign_in(provider, tamper_state=True)
    assert forged.status_code == 401 and db.sessions == {}
    cold = browser(web.app).get("/login/callback?code=good&state=x", follow_redirects=False)
    assert cold.status_code == 401, "no sign-in was started in this browser"
    _, bad_code = _sign_in(provider, code="stolen")
    assert bad_code.status_code == 401


def test_ac4_the_code_is_exchanged_with_the_proof_only_this_browser_holds(db, sso):
    provider = sso("https://idp.example/tenant-a", CDI_OIDC_CLIENT_SECRET="s3cret-for-the-test")
    provider.claims = {"sub": "a-1", "preferred_username": "asha.rao", "roles": ["reviewer"]}
    _sign_in(provider)
    form = provider.token_forms[-1]
    assert form["grant_type"] == "authorization_code" and form["client_secret"]
    assert len(form["code_verifier"]) >= 43 and provider.asked["code_challenge_method"] == "S256"
    assert form["redirect_uri"] == "https://testserver/login/callback"


def test_ac4_with_single_sign_on_off_the_two_addresses_do_not_exist(db):
    assert not oidc.enabled()
    assert browser(web.app).get("/login/oidc", follow_redirects=False).status_code == 404
    assert browser(web.app).get("/login/callback?code=x&state=y").status_code == 404
    assert "type=password" in browser(web.app).get("/login").text


# ------------------------------------------------------------------ FR7 and FR9


def test_fr7_too_many_uploads_in_a_minute_are_refused_before_a_file_is_read(db, monkeypatch):
    monkeypatch.setenv("CDI_UPLOAD_MAX_PER_MINUTE", "5")
    get_settings.cache_clear()
    try:
        db.uploads_last_minute = 4
        web._within_upload_rate("desk")  # the fifth is still let through
        db.uploads_last_minute = 5
        client = _signed_in(db, "intake", "desk")
        r = client.post("/api/jobs", files=[("files", ("rx.png", b"\x89PNG", "image/png"))])
        assert r.status_code == 429 and r.headers["retry-after"] == "60"
        assert r.json()["detail"] == web.TOO_MANY_UPLOADS
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()


def test_fr7_the_limit_can_be_switched_off_and_never_blocks_when_it_cannot_count(monkeypatch):
    monkeypatch.setattr(
        web,
        "uploads_last_minute",
        lambda u: (_ for _ in ()).throw(ConnectionError("database away")),
    )
    web._within_upload_rate("desk")
    assert get_settings().__class__.model_fields["upload_max_per_minute"].default == 30


def test_fr9_the_web_app_refuses_to_start_on_development_credentials():
    Settings = get_settings().__class__
    dev = Settings(
        refuse_default_secrets=True,
        s3_access_key="minioadmin",
        s3_secret_key="minioadmin",
        database_url="postgresql+psycopg://cdi:cdi@127.0.0.1:5432/cdi",
    )
    assert web.default_secrets(dev) == ["s3_access_key", "s3_secret_key", "database_url"]
    with pytest.raises(SystemExit, match="refusing to start"):
        web.refuse_default_secrets(dev)
    real = Settings(
        refuse_default_secrets=True,
        s3_access_key="k" * 20,
        s3_secret_key="s" * 32,
        database_url="postgresql+psycopg://cdi:" + "q" * 24 + "@db:5432/cdi",
        redis_url="redis://:" + "r" * 24 + "@redis:6379/0",
    )
    web.refuse_default_secrets(real)


def test_fr9_single_sign_on_always_means_real_secrets():
    Settings = get_settings().__class__
    with pytest.raises(SystemExit):
        web.refuse_default_secrets(Settings(auth_provider="oidc", s3_secret_key="minioadmin"))
    web.refuse_default_secrets(
        Settings(auth_provider="local", refuse_default_secrets=False, s3_secret_key="minioadmin")
    )  # a developer's machine


@pytest.mark.xfail(
    strict=True,
    reason="FR9 asks for the default credentials to be REMOVED "
    "from config.py. They are still there (minioadmin, cdi:cdi) so a developer's "
    "machine starts; the app refuses them only when told to, or with SSO on.",
)
def test_fr9_no_default_credential_is_left_in_the_code():
    code = (ROOT / "src/cdi_adapter/config.py").read_text(encoding="utf-8")
    assert "minioadmin" not in code and "cdi:cdi@" not in code


@pytest.mark.xfail(
    strict=True,
    reason="Not proven against a real identity provider or a real "
    "database: no Entra ID / OIDC tenant is configured, and migration 0040 has "
    "not been applied anywhere.",
)
def test_proven_against_a_real_identity_provider():
    assert (ROOT / "docs/evidence/ENT-S2/real-provider-sign-in.md").is_file()
