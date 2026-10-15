"""Web-app authentication + role-based access (backlog E11-S1; ARCHITECTURE §12 "Web UI auth").

Staff sign in with a username + password. Accounts are local (``web_user``, managed with
``python -m cdi_adapter.webapp.users``); no identity provider exists yet, and an OIDC/SSO login can
replace ``verify_login`` later without touching the policy below.

* **Every route has an explicit policy** in ``POLICY``: public, or the roles allowed. A route with
  no entry is refused (fail closed), and a test checks every registered route is listed.
* **Sessions** are server-side: the cookie holds a random 256-bit token, ``web_session`` holds only
  its sha256. HttpOnly, SameSite=Strict, Secure (``CDI_SESSION_COOKIE_SECURE``); idle and absolute
  timeouts (``CDI_SESSION_IDLE_MINUTES`` / ``CDI_SESSION_MAX_HOURS``).
* **Passwords**: salted scrypt (stdlib), at least 12 characters; ``CDI_LOGIN_MAX_FAILURES`` wrong
  passwords lock the account for ``CDI_LOGIN_LOCKOUT_MINUTES``.
* **CSRF**: besides SameSite=Strict, every state-changing request must carry an ``Origin`` (or
  ``Referer``) of this same host.
* Unauthenticated: ``/api/*`` -> 401 JSON, pages -> 303 to ``/login``. Wrong role -> 403.
  Sign-in, failed sign-in and sign-out are written to ``audit_log``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from html import escape
from typing import Any
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy import text

from cdi_adapter import repo
from cdi_adapter.config import get_settings
from cdi_adapter.db import session_scope
from cdi_adapter.logging import get_logger

log = get_logger(__name__)

COOKIE = "cdi_session"
ROLES = ("intake", "reviewer", "admin")
PUBLIC = frozenset({"public"})
MIN_PASSWORD = 12

_I, _R, _A = "intake", "reviewer", "admin"
_COUNTER = frozenset({_I, _A})  # the billing/registration counter
_CLINICAL = frozenset({_R, _A})  # clinician reviewers
_ANY_STAFF = frozenset({_I, _R, _A})
_ADMIN = frozenset({_A})

# (method, route path) -> who may call it. §7.1 endpoint table + §4.5 routes + the login routes.
POLICY: dict[tuple[str, str], frozenset[str]] = {
    ("GET", "/healthz"): PUBLIC,  # monitors + the E1-S9 ingress: component booleans only
    ("GET", "/readyz"): PUBLIC,  # the E10-S9 balancer's health check: booleans + instance name
    ("GET", "/login"): PUBLIC,
    ("POST", "/login"): PUBLIC,
    # ENT-S2: single sign-on - to the identity provider, and back from it
    ("GET", "/login/oidc"): PUBLIC,
    ("GET", "/login/callback"): PUBLIC,
    ("POST", "/logout"): _ANY_STAFF,
    ("GET", "/api/me"): _ANY_STAFF,
    # SW-S1: which engine and component is in use, by name and version (no content, no secret)
    ("GET", "/api/components"): _ANY_STAFF,
    # counter: upload, follow the job, hand over the bundles, patient registry
    ("GET", "/"): _COUNTER,
    ("POST", "/api/jobs"): _COUNTER,
    ("GET", "/api/jobs/{job_id}"): _ANY_STAFF,
    ("GET", "/api/activity"): _ANY_STAFF,  # counts of uploads being read: no content
    ("GET", "/api/jobs/{job_id}/fhir"): _ANY_STAFF,
    ("GET", "/api/jobs/{job_id}/fhir/download"): _ANY_STAFF,
    ("GET", "/api/patients/{patient_id}/fhir"): _ANY_STAFF,
    ("GET", "/api/registry/lookup"): _ANY_STAFF,
    ("POST", "/api/registry"): _COUNTER,
    # UP-S2: retaking a held page is part of uploading it.
    ("POST", "/api/jobs/{job_id}/pages/{page_no}/replace"): _COUNTER,
    # clinical content and decisions: clinicians only (keep/drop/correct is a clinical act, §7.2)
    ("GET", "/api/jobs/{job_id}/facts"): _CLINICAL,
    ("POST", "/api/jobs/{job_id}/generate"): _CLINICAL,
    ("GET", "/api/documents/{document_id}/bundle"): _CLINICAL,
    ("GET", "/api/documents/{document_id}/evidence"): _CLINICAL,
    ("GET", "/api/documents/{document_id}/pages/{page_no}"): _CLINICAL,
    ("GET", "/api/documents/{document_id}/pages/{page_no}/upright"): _CLINICAL,  # the same page
    ("GET", "/api/documents/{document_id}/original"): _CLINICAL,
    # UP-S3: the result on screen and its JSON are a document's extracted content, so they follow
    # the rule for everything under /api/documents: clinicians only (the counter is not shown it)
    ("GET", "/result/{document_id}"): _CLINICAL,
    ("GET", "/api/documents/{document_id}/result.json"): _CLINICAL,
    ("GET", "/api/documents/{document_id}/result.json/download"): _CLINICAL,
    # UP-S3 AC5: "this result is wrong", and the backlog those reports are worked from
    ("POST", "/api/documents/{document_id}/result/report"): _CLINICAL,
    ("GET", "/api/evaluation/backlog"): _CLINICAL,
    ("GET", "/api/results/search"): _CLINICAL,  # LS-S8: it leads to results
    # LS-S9: the listener's health. It lists file names (the ignored files), and file names can
    # carry a patient's name, so it is for the staff who may see results.
    ("GET", "/api/listener/health"): _CLINICAL,
    # LS-S9 AC4: the alerts firing now, for the banner on every page. Fixed sentences from the
    # alert rules, no patient content - and whoever is at a screen is who an alert can reach.
    ("GET", "/api/alerts/active"): _ANY_STAFF,
    ("GET", "/listener"): _CLINICAL,
    ("GET", "/evaluation"): _CLINICAL,
    # UP-S1 AC2: the one result of a whole upload is the same content, so the same rule
    ("GET", "/result/job/{job_id}"): _CLINICAL,
    ("GET", "/api/jobs/{job_id}/result.json"): _CLINICAL,
    ("GET", "/api/jobs/{job_id}/result.json/download"): _CLINICAL,
    ("GET", "/review"): _CLINICAL,
    ("GET", "/api/review/tasks"): _CLINICAL,
    ("GET", "/api/review/facts/{fact_id}"): _CLINICAL,
    ("POST", "/api/facts/{fact_id}/review"): _CLINICAL,
    ("GET", "/reviewer"): _CLINICAL,
    ("GET", "/api/reviewer/workbasket"): _CLINICAL,
    ("GET", "/api/reviewer/worklist"): _CLINICAL,
    ("POST", "/api/reviewer/items/{item_id}/claim"): _CLINICAL,
    ("POST", "/api/reviewer/items/{item_id}/approve"): _CLINICAL,
    ("POST", "/api/reviewer/items/{item_id}/reject"): _CLINICAL,
    ("GET", "/api/reviewer/items/{item_id}/elements"): _CLINICAL,
    ("GET", "/api/reviewer/items/{item_id}/bundle"): _CLINICAL,
    ("GET", "/api/reviewer/items/{item_id}/bundle/download"): _CLINICAL,
    ("GET", "/api/reviewer/approved"): _CLINICAL,
    ("POST", "/api/reviewer/items/{item_id}/agent"): _CLINICAL,
    ("GET", "/api/reviewer/facts/{fact_id}/replacements"): _CLINICAL,
    ("POST", "/api/reviewer/facts/{fact_id}/replace"): _CLINICAL,
    ("GET", "/reviewer/c360"): _CLINICAL,
    ("GET", "/api/reviewer/c360/{mpi}"): _CLINICAL,
    # E5-S3: settling a conflict between two visits is a clinical decision
    ("GET", "/conflicts"): _CLINICAL,
    ("GET", "/api/conflicts"): _CLINICAL,
    ("GET", "/api/conflicts/{conflict_id}"): _CLINICAL,
    ("POST", "/api/conflicts/{conflict_id}/resolve"): _CLINICAL,
    # the Pipeline Monitor microsite (stages, OCR lines, facts): clinical content
    ("GET", "/monitor"): _CLINICAL,
    ("GET", "/api/monitor/items"): _CLINICAL,
    ("GET", "/api/monitor/documents/{document_id}"): _CLINICAL,
    # UP-S2 AC5: the weekly picture-quality report - counts only, for whoever runs the service
    ("GET", "/api/reports/quality"): _CLINICAL,
    # data inspector over every table: admin only
    ("GET", "/admin"): _ADMIN,
}
HOME = {_I: "/", _R: "/reviewer", _A: "/"}  # where each role lands after signing in
_UNSAFE = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# ENT-S2 (FR4): what a person LOOKED AT or TOOK AWAY. (method, route) -> (what, the kind of
# thing, the path parameter that names it). Written to audit_log after the answer went out,
# with who, their id, when, from which address and whether it was served. Uploads and every
# change (a correction, an approval, a merge) are written by the code that makes them.
# Never the content: no name, no value, no file name.
AUDITED: dict[tuple[str, str], tuple[str, str, str]] = {
    ("GET", "/result/{document_id}"): ("result viewed", "source_document", "document_id"),
    ("GET", "/api/documents/{document_id}/result.json"):
        ("result viewed", "source_document", "document_id"),
    ("GET", "/api/documents/{document_id}/result.json/download"):
        ("result downloaded", "source_document", "document_id"),
    ("GET", "/api/documents/{document_id}/bundle"):
        ("FHIR bundle viewed", "source_document", "document_id"),
    ("GET", "/api/documents/{document_id}/original"):
        ("original document downloaded", "source_document", "document_id"),
    ("GET", "/result/job/{job_id}"): ("result viewed", "web_job", "job_id"),
    ("GET", "/api/jobs/{job_id}/result.json"): ("result viewed", "web_job", "job_id"),
    ("GET", "/api/jobs/{job_id}/result.json/download"): ("result downloaded", "web_job", "job_id"),
    ("GET", "/api/jobs/{job_id}/facts"): ("extracted values viewed", "web_job", "job_id"),
    ("GET", "/api/jobs/{job_id}/fhir"): ("FHIR bundles viewed", "web_job", "job_id"),
    ("GET", "/api/jobs/{job_id}/fhir/download"): ("FHIR bundles downloaded", "web_job", "job_id"),
    ("GET", "/api/patients/{patient_id}/fhir"):
        ("patient FHIR record viewed", "patient_identity", "patient_id"),
    ("GET", "/api/reviewer/items/{item_id}/elements"): ("review item viewed", "rv_item", "item_id"),
    ("GET", "/api/reviewer/items/{item_id}/bundle"): ("FHIR bundle viewed", "rv_item", "item_id"),
    ("GET", "/api/reviewer/items/{item_id}/bundle/download"):
        ("FHIR bundle downloaded", "rv_item", "item_id"),
    ("GET", "/api/reviewer/c360/{mpi}"): ("patient record viewed", "patient_identity", "mpi"),
    ("GET", "/api/monitor/documents/{document_id}"):
        ("pipeline detail viewed", "source_document", "document_id"),
    ("GET", "/admin"): ("data inspector opened", "admin", ""),
}  # fmt: skip


def client_of(request: Request) -> str | None:
    """The address the request came from: the connection's own peer. What a proxy SAYS the
    address was (X-Forwarded-For) can be typed by anyone, so it is not used as the address."""
    return request.client.host if request.client else None


def audit_access(request: Request, status: int) -> None:
    """Write who viewed or downloaded what (see ``AUDITED``). Called after the response, and
    never raises: a full audit table must not turn a served result into an error - it is
    logged loudly instead."""
    route = request.scope.get("route")
    method = "GET" if request.method == "HEAD" else request.method
    what = AUDITED.get((method, getattr(route, "path", "")))
    user = current_user(request)
    if what is None or user is None:
        return
    event, entity, param = what
    try:
        with session_scope() as c:
            repo.write_audit(
                c, actor=user.username, action="read", entity=entity,
                entity_id=request.path_params.get(param) if param else None,
                detail={"event": event, "served": 200 <= status < 300, "status": status,
                        "role": user.role, "route": getattr(route, "path", None)},
                actor_id=user.id, source_addr=client_of(request))  # fmt: skip
    except Exception as exc:  # noqa: BLE001 - see above
        log.error("audit_not_written", what=event, entity=entity, user=user.username,
                  error=f"{type(exc).__name__}: {exc}")  # fmt: skip


@dataclass(frozen=True)
class User:
    id: str
    username: str
    display_name: str
    role: str


# ---------------------------------------------------------------- passwords


def hash_password(password: str) -> str:
    if len(password) < MIN_PASSWORD:
        raise ValueError(f"password must be at least {MIN_PASSWORD} characters")
    salt = secrets.token_bytes(16)
    n, r, p = 2**15, 8, 1
    dk = hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, maxmem=64 * 2**20, dklen=32)
    b64 = base64.b64encode
    return f"scrypt${n}${r}${p}${b64(salt).decode()}${b64(dk).decode()}"


def check_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt, dk = stored.split("$")
        if algo != "scrypt":
            return False
        want = base64.b64decode(dk)
        got = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r),
                             p=int(p), maxmem=64 * 2**20, dklen=len(want))  # fmt: skip
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got, want)


# a real hash to check against when the username does not exist: same cost, no timing oracle
_DUMMY_HASH = hash_password(secrets.token_urlsafe(24))


# ---------------------------------------------------------------- sessions


def _sha(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC)


def verify_login(conn: Any, username: str, password: str) -> tuple[User | None, str]:
    """(user, "") on success, else (None, reason). Counts failures and locks the account."""
    s = get_settings()
    row = conn.execute(text("SELECT * FROM web_user WHERE username = :u"),
                       {"u": username.strip().lower()}).mappings().first()  # fmt: skip
    if row is None or not row["password_hash"]:
        # unknown, or an account of the identity system: it has no password to try (ENT-S2)
        check_password(password, _DUMMY_HASH)
        return None, "unknown user"
    if not row["active"]:
        check_password(password, _DUMMY_HASH)
        return None, "disabled"
    if row["locked_until"] and row["locked_until"] > _now():
        return None, "locked"
    if not check_password(password, row["password_hash"]):
        fails = row["failed_logins"] + 1
        lock = _now() + timedelta(minutes=s.login_lockout_minutes)
        conn.execute(text("UPDATE web_user SET failed_logins = :f, locked_until = :l "
                          "WHERE id = :i"),
                     {"f": 0 if fails >= s.login_max_failures else fails,
                      "l": lock if fails >= s.login_max_failures else None,
                      "i": row["id"]})  # fmt: skip
        return None, "locked" if fails >= s.login_max_failures else "wrong password"
    conn.execute(text("UPDATE web_user SET failed_logins = 0, locked_until = NULL WHERE id = :i"),
                 {"i": row["id"]})  # fmt: skip
    return User(str(row["id"]), row["username"], row["display_name"], row["role"]), ""


def create_session(conn: Any, user: User, client: str | None) -> str:
    token = secrets.token_urlsafe(32)
    expires = _now() + timedelta(hours=get_settings().session_max_hours)
    conn.execute(text("INSERT INTO web_session (token_sha256, user_id, expires_at, client) "
                      "VALUES (:t, :u, :e, :c)"),
                 {"t": _sha(token), "u": user.id, "e": expires, "c": client})  # fmt: skip
    return token


def session_user(conn: Any, token: str | None) -> User | None:
    """The signed-in user for a cookie token, or None (unknown, expired, idle, disabled)."""
    if not token:
        return None
    s = get_settings()
    row = conn.execute(
        text("SELECT s.token_sha256, s.expires_at, s.last_seen_at, u.id, u.username, "
             "u.display_name, u.role, u.active FROM web_session s JOIN web_user u "
             "ON u.id = s.user_id WHERE s.token_sha256 = :t"), {"t": _sha(token)}
    ).mappings().first()  # fmt: skip
    now = _now()
    idle = timedelta(minutes=s.session_idle_minutes)
    if row is None:
        return None
    if not row["active"] or row["expires_at"] <= now or row["last_seen_at"] + idle <= now:
        conn.execute(text("DELETE FROM web_session WHERE token_sha256 = :t"), {"t": _sha(token)})
        return None
    if now - row["last_seen_at"] > timedelta(seconds=60):  # at most one write a minute
        conn.execute(text("UPDATE web_session SET last_seen_at = :n WHERE token_sha256 = :t"),
                     {"n": now, "t": _sha(token)})  # fmt: skip
    return User(str(row["id"]), row["username"], row["display_name"], row["role"])


def end_session(conn: Any, token: str | None) -> None:
    if token:
        conn.execute(text("DELETE FROM web_session WHERE token_sha256 = :t"), {"t": _sha(token)})


# ---------------------------------------------------------------- the gate (global dependency)


def _same_origin(request: Request) -> bool:
    source = request.headers.get("origin") or request.headers.get("referer")
    if not source:
        return False
    netloc = urlsplit(source).netloc.lower()
    if netloc == (request.headers.get("host") or "").lower():
        return True
    # A reverse proxy that rewrites Host (Colab's port proxy, RunPod's pod proxy) makes the two
    # disagree for requests that ARE same-origin to the browser. The operator names those hosts
    # in CDI_PUBLIC_ORIGINS; anything not on that list is still refused (backlog E11-S1).
    allowed = {h.strip().lower() for h in get_settings().public_origins.split(",") if h.strip()}
    return netloc in allowed


def _deny(request: Request, status: int, detail: str) -> HTTPException:
    if status == 401 and not request.url.path.startswith("/api/") and request.method == "GET":
        target = quote(request.url.path + (f"?{request.url.query}" if request.url.query else ""))
        return HTTPException(303, detail, headers={"Location": f"/login?next={target}"})
    return HTTPException(status, detail)


def authorize(request: Request) -> None:
    """Runs before every route (``FastAPI(dependencies=[Depends(authorize)])``)."""
    route = request.scope.get("route")
    method = "GET" if request.method == "HEAD" else request.method
    allowed = POLICY.get((method, getattr(route, "path", "")))
    if allowed is None:
        log.warning("route_without_policy", method=method, path=getattr(route, "path", None))
        raise HTTPException(403, "no access policy for this route")
    if method in _UNSAFE and not _same_origin(request):
        raise HTTPException(403, "cross-origin request refused")
    if allowed is PUBLIC:
        return
    token = request.cookies.get(COOKIE)
    if not token:  # refused without a database round trip
        raise _deny(request, 401, "sign in required")
    with session_scope() as c:
        user = session_user(c, token)
    if user is None:
        raise _deny(request, 401, "sign in required")
    if user.role not in allowed:
        raise _deny(request, 403, f"role {user.role!r} may not use this")
    request.state.user = user


def current_user(request: Request) -> User | None:
    user: User | None = getattr(request.state, "user", None)
    return user


def actor(request: Request) -> User:
    """The signed-in user a clinical decision is recorded under (backlog E7-S1): the reviewer
    of a fact, a review item or a job's edits comes from the session, never from the client."""
    user = current_user(request)
    if user is None:  # authorize() ran; this is only reachable if a route lost its policy
        raise HTTPException(401, "sign in required")
    return user


# ---------------------------------------------------------------- login / logout routes

router = APIRouter()


def _safe_next(target: str | None) -> str | None:
    """Only same-site paths: never an absolute URL (open redirect)."""
    if not target or not target.startswith("/") or target.startswith("//") or "\\" in target:
        return None
    return target


def login_page(error: str = "", next_path: str = "") -> str:
    from cdi_adapter.webapp import oidc
    from cdi_adapter.webapp.theme import CSS, LOGO_SVG

    err = f'<p class="pill bad" role=alert>{escape(error)}</p>' if error else ""
    head = (
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        '<meta name=viewport content="width=device-width,initial-scale=1">'
        f"<title>Sign in · CareFlow</title><style>{CSS}</style></head><body>"
        f'<header class="top">{LOGO_SVG}<b>CareFlow Polyclinic</b></header><main>'
        '<section class=card style="max-width:380px;margin:40px auto">'
        '<h1 style="margin-top:0">Sign in</h1>'
        "<p class=muted>Intelligent OCR driven Agentic EMR generation</p>"
    )  # fmt: skip
    if oidc.enabled():  # ENT-S2: no password is asked for here - the organisation's sign-in
        target = "/login/oidc" + (f"?next={quote(next_path)}" if next_path else "")
        return (head + err + f'<a class=btn role=button href="{escape(target)}">'
                "Sign in with your organisation account</a>"
                "<p class=muted style=\"margin-top:14px\">You will be taken to your "
                "organisation's sign-in page and brought back here.</p>"
                "</section></main></body></html>")  # fmt: skip
    return (
        head + f'{err}<form method=post action="/login">'
        f'<input type=hidden name=next value="{escape(next_path)}">'
        '<label>Username<br><input name=username autocomplete=username required autofocus '
        'style="width:100%"></label><br><br>'
        '<label>Password<br><input name=password type=password autocomplete=current-password '
        'required style="width:100%"></label><br><br>'
        '<button type=submit>Sign in</button></form></section></main></body></html>'
    )  # fmt: skip


@router.get("/login", response_class=HTMLResponse)
def login_form(next: str | None = None) -> str:  # noqa: A002 - the query name
    return login_page(next_path=_safe_next(next) or "")


@router.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...),
          next: str = Form("")) -> Response:  # noqa: A002  # fmt: skip
    from cdi_adapter.webapp import oidc

    client = client_of(request)
    if oidc.enabled():  # ENT-S2: with single sign-on there is no password door at all
        raise HTTPException(404, "password sign-in is switched off; use /login/oidc")
    with session_scope() as c:
        user, why = verify_login(c, username, password)
        if user is None:
            repo.write_audit(c, actor=username.strip().lower()[:64] or "?", action="create",
                             entity="web_session", detail={"outcome": "refused", "reason": why,
                                                           "client": client})  # fmt: skip
            msg = ("Account locked after repeated failures; try again later or ask an admin."
                   if why == "locked" else "Wrong username or password.")  # fmt: skip
            return HTMLResponse(login_page(msg, _safe_next(next) or ""), status_code=401)
        token = create_session(c, user, client)
        repo.write_audit(c, actor=user.username, action="create", entity="web_session",
                         detail={"outcome": "signed_in", "role": user.role, "client": client},
                         actor_id=user.id, source_addr=client)  # fmt: skip
    resp = RedirectResponse(_safe_next(next) or HOME[user.role], status_code=303)
    _set_session_cookie(resp, token)
    return resp


def _set_session_cookie(resp: Response, token: str) -> None:
    s = get_settings()
    resp.set_cookie(COOKIE, token, httponly=True, samesite="strict",
                    secure=s.session_cookie_secure, path="/",
                    max_age=s.session_max_hours * 3600)  # fmt: skip


# ---------------------------------------------------------------- single sign-on (ENT-S2)


def _redirect_uri(request: Request) -> str:
    s = get_settings()
    return s.oidc_redirect_uri or str(request.base_url).rstrip("/") + "/login/callback"


def _sso_refused(reason: str, client: str | None, next_path: str = "") -> Response:
    """The person is told only that it did not work; the reason goes to the audit trail."""
    with session_scope() as c:
        repo.write_audit(c, actor="?", action="create", entity="web_session",
                         detail={"outcome": "refused", "reason": reason, "via": "oidc",
                                 "client": client}, source_addr=client)  # fmt: skip
    log.warning("sso_refused", reason=reason)
    msg = ("Your organisation account has no role for this product. Ask an administrator."
           if "no role" in reason else "Sign-in did not complete. Please try again.")  # fmt: skip
    resp = HTMLResponse(login_page(msg, next_path), status_code=403 if "no role" in reason
                        else 401)  # fmt: skip
    resp.delete_cookie("cdi_oidc", path="/login")
    return resp


@router.get("/login/oidc")
def login_oidc(request: Request, next: str | None = None) -> Response:  # noqa: A002
    """Step 1: to the identity provider, with a one-time state, nonce and PKCE challenge."""
    from cdi_adapter.webapp import oidc

    s = get_settings()
    if not oidc.enabled(s):
        raise HTTPException(404, "single sign-on is not switched on")
    try:
        target, flow = oidc.begin(s, _redirect_uri(request), _safe_next(next) or "")
    except oidc.SignInRefused as exc:
        return _sso_refused(exc.reason, client_of(request))
    resp = RedirectResponse(target, status_code=303)
    # Lax, not Strict: the provider brings the browser back with a top-level navigation, and
    # this cookie must come with it. It holds no session - only this one sign-in's check values.
    resp.set_cookie(oidc.FLOW_COOKIE, flow, httponly=True, samesite="lax", path="/login",
                    secure=s.session_cookie_secure, max_age=oidc.FLOW_SECONDS)  # fmt: skip
    return resp


@router.get("/login/callback")
def login_callback(request: Request, code: str | None = None, state: str | None = None,
                   error: str | None = None) -> Response:  # fmt: skip
    """Steps 2-5: the provider's answer is checked, the person found or made, a session begun."""
    from cdi_adapter.webapp import oidc

    s = get_settings()
    if not oidc.enabled(s):
        raise HTTPException(404, "single sign-on is not switched on")
    client = client_of(request)
    try:
        if error:
            raise oidc.SignInRefused(f"the identity provider answered: {error[:80]}")
        flow = oidc.read_flow(request.cookies.get(oidc.FLOW_COOKIE))
        who = oidc.finish(s, code=code, state=state, flow=flow)
    except oidc.SignInRefused as exc:
        return _sso_refused(exc.reason, client)
    with session_scope() as c:
        row = oidc.upsert_user(c, who)
        if not row["active"]:
            repo.write_audit(c, actor=row["username"], action="create", entity="web_session",
                             detail={"outcome": "refused", "reason": "disabled", "via": "oidc",
                                     "client": client},
                             actor_id=row["id"], source_addr=client)  # fmt: skip
            return HTMLResponse(login_page("This account is disabled. Ask an administrator."),
                                status_code=403)  # fmt: skip
        user = User(str(row["id"]), row["username"], row["display_name"], row["role"])
        token = create_session(c, user, client)
        repo.write_audit(c, actor=user.username, action="create", entity="web_session",
                         detail={"outcome": "signed_in", "role": user.role, "via": "oidc",
                                 "client": client},
                         actor_id=user.id, source_addr=client)  # fmt: skip
    # A page, not a redirect: the session cookie is SameSite=Strict, and a redirect chain that
    # began at the provider's site would not carry it. This page is ours, so its own move to
    # the home page does.
    home = escape(_safe_next(flow.get("next")) or HOME[user.role], quote=True)
    resp = HTMLResponse(
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        f'<meta http-equiv=refresh content="0;url={home}"><title>Signed in</title></head>'
        f'<body><p>Signed in. <a href="{home}">Continue</a></p></body></html>')  # fmt: skip
    _set_session_cookie(resp, token)
    resp.delete_cookie(oidc.FLOW_COOKIE, path="/login")
    return resp


@router.post("/logout")
def logout(request: Request) -> Response:
    user = current_user(request)
    with session_scope() as c:
        end_session(c, request.cookies.get(COOKIE))
        if user:
            repo.write_audit(c, actor=user.username, action="delete", entity="web_session",
                             detail={"outcome": "signed_out"}, actor_id=user.id,
                             source_addr=client_of(request))  # fmt: skip
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(COOKIE, path="/")
    return resp


@router.get("/api/me")
def me(request: Request) -> JSONResponse:
    user = current_user(request)
    assert user is not None  # authorize() ran
    return JSONResponse({"username": user.username, "display_name": user.display_name,
                         "role": user.role})  # fmt: skip
