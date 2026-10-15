"""Sign-in through the organisation's identity system (backlog ENT-S2): OpenID Connect.

**Configuration only.** Microsoft Entra ID, Google, Keycloak, Okta - any provider that speaks
OpenID Connect - is named by its issuer address and a client id; nothing here knows one
provider from another. Switching provider is a change of ``CDI_OIDC_*`` settings::

    CDI_AUTH_PROVIDER=oidc
    CDI_OIDC_ISSUER=https://login.microsoftonline.com/<tenant>/v2.0
    CDI_OIDC_CLIENT_ID=<application id>
    CDI_OIDC_CLIENT_SECRET=<secret>            # or CDI_OIDC_CLIENT_SECRET_FILE; empty = PKCE only
    CDI_OIDC_ROLE_CLAIM=roles                  # where the provider says what the person may do
    CDI_OIDC_ROLE_MAP={"CDI.Uploader":"uploader","CDI.Reviewer":"reviewer","CDI.Admin":"admin"}

What happens (the authorisation-code flow with PKCE):

1. ``/login/oidc`` sends the browser to the provider with a one-time ``state``, a ``nonce``
   and a PKCE challenge; the three are kept in a short-lived HttpOnly cookie.
2. The provider sends the browser back to ``/login/callback`` with a code.
3. The code is exchanged for an ID token **server to server**. The token is trusted only
   after its signature (the provider's published keys), issuer, audience, expiry and nonce
   are checked.
4. The role comes from the token's claim. A person with no role named for this product is
   refused - signing in at the provider is not, by itself, permission to see patient data.
5. The person is found or made in ``web_user`` by the provider's own id for them (``sub``),
   with **no password** - none is asked for, none is stored - and gets the same server-side
   session as before, so every route's role rule (``auth.POLICY``) applies unchanged.

Nothing of the token is logged or stored: not the token, not the code, not the secret.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx
from sqlalchemy import text

from cdi_adapter.config import Settings, get_settings
from cdi_adapter.logging import get_logger

log = get_logger(__name__)

FLOW_COOKIE = "cdi_oidc"
FLOW_SECONDS = 600
# what the story calls the roles -> the role names the route policy already uses
ROLE_NAMES = {"uploader": "intake", "intake": "intake", "reviewer": "reviewer", "admin": "admin"}
_RANK = ("intake", "reviewer", "admin")  # the last one a person holds is the one they get
ALGORITHMS = ("RS256", "RS384", "RS512", "ES256", "ES384", "PS256")  # never "none", never HS*
_CACHE_SECONDS = 3600


class SignInRefused(Exception):
    """The sign-in did not go through. ``reason`` is for the audit trail; the person is told
    only that it failed."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Identity:
    """Who the provider says this is, after every check passed."""

    issuer: str
    subject: str
    username: str
    display_name: str
    role: str


def enabled(settings: Settings | None = None) -> bool:
    return (settings or get_settings()).auth_provider == "oidc"


def client_secret(settings: Settings) -> str:
    if settings.oidc_client_secret_file:
        return Path(settings.oidc_client_secret_file).read_text(encoding="utf-8").strip()
    return settings.oidc_client_secret


# ------------------------------------------------------------------ the provider's documents

_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_cache_lock = threading.Lock()


def _get_json(url: str, http: httpx.Client | None) -> dict[str, Any]:
    with _cache_lock:
        hit = _cache.get(url)
        if hit and hit[0] > time.monotonic():
            return hit[1]
    if not url.startswith("https://") and not re.match(r"http://(localhost|127\.0\.0\.1)[:/]", url):
        raise SignInRefused("the identity provider's address is not https")
    client = http or httpx.Client(timeout=10.0)
    try:
        answer = client.get(url)
        answer.raise_for_status()
        doc = dict(answer.json())
    except (httpx.HTTPError, ValueError) as exc:
        raise SignInRefused(f"the identity provider did not answer: {type(exc).__name__}") from exc
    finally:
        if http is None:
            client.close()
    with _cache_lock:
        _cache[url] = (time.monotonic() + _CACHE_SECONDS, doc)
    return doc


def forget() -> None:
    """Drop the cached provider documents (tests; a provider that rotated its keys)."""
    with _cache_lock:
        _cache.clear()


def discover(settings: Settings, http: httpx.Client | None = None) -> dict[str, Any]:
    """The provider's own description of itself. Its ``issuer`` must be the one configured."""
    issuer = settings.oidc_issuer.rstrip("/")
    if not issuer or not settings.oidc_client_id:
        raise SignInRefused("single sign-on is switched on but CDI_OIDC_ISSUER / "
                            "CDI_OIDC_CLIENT_ID are not set")  # fmt: skip
    doc = _get_json(f"{issuer}/.well-known/openid-configuration", http)
    if str(doc.get("issuer", "")).rstrip("/") != issuer:
        raise SignInRefused("the identity provider describes itself under another issuer")
    for key in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
        if not doc.get(key):
            raise SignInRefused(f"the identity provider's description has no {key}")
    return doc


# ------------------------------------------------------------------ step 1: to the provider


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def begin(settings: Settings, redirect_uri: str, next_path: str = "",
          http: httpx.Client | None = None) -> tuple[str, str]:  # fmt: skip
    """``(address to send the browser to, value of the flow cookie)``."""
    doc = discover(settings, http)
    state, nonce, verifier = (secrets.token_urlsafe(24), secrets.token_urlsafe(24),
                              secrets.token_urlsafe(48))  # fmt: skip
    query = urlencode({
        "response_type": "code", "client_id": settings.oidc_client_id,
        "redirect_uri": redirect_uri, "scope": settings.oidc_scopes, "state": state,
        "nonce": nonce, "code_challenge": _b64(hashlib.sha256(verifier.encode()).digest()),
        "code_challenge_method": "S256",
    })  # fmt: skip
    flow = json.dumps({"state": state, "nonce": nonce, "verifier": verifier,
                       "redirect_uri": redirect_uri, "next": next_path})  # fmt: skip
    sep = "&" if "?" in doc["authorization_endpoint"] else "?"
    return f"{doc['authorization_endpoint']}{sep}{query}", _b64(flow.encode())


def read_flow(cookie: str | None) -> dict[str, str]:
    try:
        raw = base64.urlsafe_b64decode((cookie or "") + "=" * (-len(cookie or "") % 4))
        flow = json.loads(raw)
    except (ValueError, TypeError):
        flow = None
    if not isinstance(flow, dict) or not all(flow.get(k) for k in ("state", "nonce", "verifier")):
        raise SignInRefused("the sign-in was not started here, or took too long")
    return {k: str(v) for k, v in flow.items()}


# ------------------------------------------------------------------ steps 3 and 4: the token


def _claims(id_token: str, settings: Settings, doc: dict[str, Any], nonce: str,
            http: httpx.Client | None) -> dict[str, Any]:  # fmt: skip
    import jwt  # PyJWT (MIT)

    try:
        header = jwt.get_unverified_header(id_token)
        if header.get("alg") not in ALGORITHMS:
            raise SignInRefused(f"the ID token is signed with {header.get('alg')!r}, not allowed")
        keys = jwt.PyJWKSet.from_dict(_get_json(doc["jwks_uri"], http)).keys
        named = [k for k in keys if k.key_id == header.get("kid")] or (
            keys if len(keys) == 1 and not header.get("kid") else [])  # fmt: skip
        if not named:
            raise SignInRefused("the ID token is signed with a key the provider does not publish")
        claims: dict[str, Any] = jwt.decode(
            id_token, named[0].key, algorithms=[header["alg"]], audience=settings.oidc_client_id,
            issuer=str(doc["issuer"]), leeway=60,
            options={"require": ["exp", "iat", "iss", "aud", "sub"]})  # fmt: skip
    except jwt.PyJWTError as exc:
        raise SignInRefused(f"the ID token was not accepted: {type(exc).__name__}") from exc
    if not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
        raise SignInRefused("the ID token answers another sign-in (nonce)")
    return claims


def role_of(claims: dict[str, Any], settings: Settings) -> str | None:
    """The product role the provider gives this person, or None when it gives none. The claim
    (``CDI_OIDC_ROLE_CLAIM``) may hold one value or a list; ``CDI_OIDC_ROLE_MAP`` says which
    of the provider's values is which role. Without a map the role names themselves are read
    (``uploader`` / ``reviewer`` / ``admin``). Holding several, the widest counts."""
    held = claims.get(settings.oidc_role_claim)
    values = [str(v) for v in (held if isinstance(held, list) else [held]) if v is not None]
    try:
        mapping = json.loads(settings.oidc_role_map) if settings.oidc_role_map.strip() else {}
    except ValueError:
        raise SignInRefused("CDI_OIDC_ROLE_MAP is not valid JSON") from None
    if not isinstance(mapping, dict):
        raise SignInRefused("CDI_OIDC_ROLE_MAP must be an object of provider value -> role")
    roles = set()
    for value in values:
        named = mapping.get(value) if mapping else value
        role = ROLE_NAMES.get(str(named).strip().lower()) if named else None
        if role:
            roles.add(role)
    return max(roles, key=_RANK.index) if roles else None


_NOT_IN_NAME = re.compile(r"[^a-z0-9._-]+")


def username_of(claims: dict[str, Any], settings: Settings) -> str:
    """A user name that fits ``web_user`` (3-64 of a-z 0-9 . _ -), from the configured claim:
    ``asha.rao@clinic.example`` -> ``asha.rao``. Falls back to the subject."""
    said = str(claims.get(settings.oidc_username_claim) or claims.get("email") or "").lower()
    name = _NOT_IN_NAME.sub("-", said.split("@", 1)[0]).strip("-.")[:60]
    if len(name) < 3:
        name = ("u-" + _NOT_IN_NAME.sub("", str(claims.get("sub", "")).lower()))[:60]
    return name if len(name) >= 3 else "user"


def finish(settings: Settings, *, code: str | None, state: str | None, flow: dict[str, str],
           http: httpx.Client | None = None) -> Identity:  # fmt: skip
    """Exchange the code and check the answer. Raises :class:`SignInRefused` with the reason."""
    if not code or not state or not secrets.compare_digest(state, flow["state"]):
        raise SignInRefused("the answer does not belong to the sign-in that was started (state)")
    doc = discover(settings, http)
    form = {"grant_type": "authorization_code", "code": code,
            "redirect_uri": flow.get("redirect_uri", ""), "client_id": settings.oidc_client_id,
            "code_verifier": flow["verifier"]}  # fmt: skip
    secret = client_secret(settings)
    if secret:
        form["client_secret"] = secret
    client = http or httpx.Client(timeout=10.0)
    try:
        answer = client.post(doc["token_endpoint"], data=form,
                             headers={"Accept": "application/json"})  # fmt: skip
        body = answer.json() if answer.content else {}
    except (httpx.HTTPError, ValueError) as exc:
        raise SignInRefused(f"the token request failed: {type(exc).__name__}") from exc
    finally:
        if http is None:
            client.close()
    if answer.status_code != 200 or not isinstance(body, dict) or not body.get("id_token"):
        why = body.get("error") if isinstance(body, dict) else None
        said = why or answer.status_code
        raise SignInRefused(f"the identity provider refused the code ({said})")
    claims = _claims(str(body["id_token"]), settings, doc, flow["nonce"], http)
    role = role_of(claims, settings)
    if role is None:
        raise SignInRefused("no role for this product in the person's sign-in")
    name = str(claims.get("name") or claims.get(settings.oidc_username_claim) or "").strip()
    return Identity(issuer=str(doc["issuer"]), subject=str(claims["sub"]),
                    username=username_of(claims, settings),
                    display_name=name[:120] or "Signed-in user", role=role)  # fmt: skip


# ------------------------------------------------------------------ step 5: the account


def upsert_user(conn: Any, who: Identity) -> dict[str, Any]:
    """The ``web_user`` row of this person - found by the provider's id for them, never by a
    name - made on the first sign-in, with **no password**. The role and the display name
    follow the provider each time; a disabled account stays disabled."""
    key = {"p": who.issuer, "s": who.subject}
    row = conn.execute(
        text("SELECT id, username, display_name, role, active FROM web_user "
             "WHERE provider = :p AND subject = :s"), key,
    ).mappings().first()  # fmt: skip
    if row is not None:
        if row["role"] != who.role or row["display_name"] != who.display_name:
            conn.execute(text("UPDATE web_user SET role = :r, display_name = :n WHERE id = :i"),
                         {"r": who.role, "n": who.display_name, "i": row["id"]})  # fmt: skip
        return {**dict(row), "role": who.role, "display_name": who.display_name}
    username = who.username
    for n in range(1, 50):  # the name is someone else's already (a local account, a namesake)
        taken = conn.execute(text("SELECT 1 FROM web_user WHERE username = :u"),
                             {"u": username}).first()  # fmt: skip
        if not taken:
            break
        username = f"{who.username[:58]}-{n + 1}"
    new = conn.execute(
        text("INSERT INTO web_user (username, display_name, role, password_hash, provider, "
             "subject) VALUES (:u, :n, :r, NULL, :p, :s) RETURNING id"),
        {"u": username, "n": who.display_name, "r": who.role, **key},
    ).scalar_one()  # fmt: skip
    log.info("sso_account_created", username=username, role=who.role)
    return {"id": new, "username": username, "display_name": who.display_name,
            "role": who.role, "active": True}  # fmt: skip
