"""Signing in to the drive without ever handling a password (backlog LS-S2).

    python -m cdi_adapter.listener login     # the one-time sign-in

**No password is typed into, stored by or logged by this system.** A person signs in on
Microsoft's own page; a service identity proves itself with a key. What is kept here is a
*token*, which can be taken away without anyone changing a password.

Three ways in, chosen by settings:

* **OneDrive, a person, once (device code)** - ``CDI_GRAPH_AUTH=device``. The login command
  prints a web address and a short code; the person opens the address on any device, types the
  code and signs in there. The renewable token is kept in the MSAL token cache file
  (``CDI_GRAPH_TOKEN_CACHE``, on the persistent disk) and renewed quietly, so a restart does not
  ask again.
* **OneDrive / SharePoint, a service identity (app only)** - ``CDI_GRAPH_AUTH=app``, with a
  client secret or a certificate, once an administrator has approved it. Nothing is cached on
  disk: a fresh token is asked for with the key whenever one is needed.
* **Google Drive, a service account** - ``CDI_GDRIVE_SERVICE_ACCOUNT_FILE``, the JSON key of an
  account the folder has been shared with. Google's OAuth for a person is not built.

**Only the permission needed.** A person's sign-in asks for ``Files.ReadWrite`` (their own
OneDrive) or ``Sites.Selected`` (one SharePoint site), and every token is checked when it
arrives: one that carries anything broader (``Files.ReadWrite.All``, ``Sites.ReadWrite.All``,
...) is refused, by name, so an over-broad app registration cannot be used by accident.

**When a sign-in has expired** the token source raises :class:`SignInExpired`; the listener
stops on it (``service.py``), writes the reason to its log and raises an alert
(:func:`raise_alert`) instead of trying again for ever.

**Nothing secret is written to a note or a log**: :func:`scrub` takes tokens, keys and
passwords out of any text before it is filed, and :func:`scrub_event` does the same for every
line the listener logs.
"""

from __future__ import annotations

import base64
import json
import os
import re
import time
from collections.abc import Callable, MutableMapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from cdi_adapter.config import Settings
from cdi_adapter.listener.connectors import (
    ListenerError,
    SignInExpired,
    SignInRequired,
    TokenSource,
    _static_token,
)
from cdi_adapter.logging import get_logger

log = get_logger(__name__)

LOGIN = "python -m cdi_adapter.listener login"
# What a sign-in may carry, and nothing broader (LS-S2 AC4).
PERMITTED = frozenset({"Files.ReadWrite", "Sites.Selected"})
# Not permissions on any file: they only say "this is a sign-in that can be renewed".
SIGN_IN_BASICS = frozenset({"openid", "profile", "offline_access", "email"})
GRAPH_APP_SCOPE = "https://graph.microsoft.com/.default"
GOOGLE_SCOPE = "https://www.googleapis.com/auth/drive"
_GOOGLE_GRANT = "urn:ietf:params:oauth:grant-type:jwt-bearer"
_EARLY = 120.0  # renew a token this many seconds before it runs out


# ------------------------------------------------------------------ nothing secret in a note

_REMOVED = "[removed]"
_SECRET_NAMES = (r"access_token|refresh_token|id_token|client_secret|client_assertion|assertion"
                 r"|device_code|password|passwd|pwd|secret|private_key|api_key|apikey|tempauth"
                 r"|sig|token")  # fmt: skip
_PATTERNS = (
    (
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(-----END [A-Z ]*PRIVATE KEY-----|\Z)", re.S
        ),
        _REMOVED,
    ),  # fmt: skip
    (re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{8,}"), r"\1 " + _REMOVED),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}(\.[A-Za-z0-9_-]*)?"), _REMOVED),
    (re.compile(r"\bya29\.[A-Za-z0-9._-]{8,}"), _REMOVED),
    (
        re.compile(
            rf"(?i)([\"']?\b(?:{_SECRET_NAMES})\b[\"']?\s*[=:]\s*)"
            r"(\"[^\"]*\"|'[^']*'|[^\s&,;\"'}]+)"
        ),
        r"\1" + _REMOVED,
    ),  # fmt: skip
)


def scrub(text: str, secrets: tuple[str, ...] = ()) -> str:
    """``text`` with every token, key and password taken out. ``secrets`` are values known to
    be secret (a client secret from settings), removed wherever they appear."""
    for value in secrets:
        if value and len(value) >= 6:
            text = text.replace(value, _REMOVED)
    for pattern, put in _PATTERNS:
        text = pattern.sub(put, text)
    return text


def scrub_event(_logger: Any, _method: str, event: MutableMapping[str, Any]) -> Any:
    """A structlog step: scrub every piece of text in a log line before it is written."""
    for key, value in list(event.items()):
        event[key] = _scrub_value(key, value)
    return event


_SECRET_KEY = re.compile(rf"(?i)^(?:{_SECRET_NAMES})$")


def _scrub_value(key: Any, value: Any) -> Any:
    if isinstance(value, str):
        return _REMOVED if _SECRET_KEY.match(str(key)) else scrub(value)
    if isinstance(value, dict):
        return {k: _scrub_value(k, v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_scrub_value(key, v) for v in value]
    return value


# ------------------------------------------------------------------ only the permission needed


def granted(result: dict[str, Any]) -> set[str]:
    """The permissions a token carries: what Microsoft says it granted, and what is written in
    the token itself (``scp`` for a person, ``roles`` for a service identity)."""
    found: set[str] = set()
    scope = result.get("scope")
    found.update(scope.split() if isinstance(scope, str) else scope or [])
    try:
        body = str(result.get("access_token", "")).split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except (IndexError, ValueError):
        claims = {}  # not every token can be read (a personal account's cannot)
    found.update(str(claims.get("scp", "")).split())
    found.update(claims.get("roles") or [])
    # "https://graph.microsoft.com/Files.ReadWrite" and "Files.ReadWrite" are the same thing
    return {p.rsplit("/", 1)[-1] for p in found if p} - {".default"}


def broader_than_needed(permissions: set[str]) -> list[str]:
    return sorted(permissions - PERMITTED - SIGN_IN_BASICS)


def _only_what_is_needed(result: dict[str, Any]) -> set[str]:
    permissions = granted(result)
    extra = broader_than_needed(permissions)
    if extra:
        raise ListenerError(
            f"the sign-in carries more permission than the listener may have: {', '.join(extra)}."
            f" Only {' or '.join(sorted(PERMITTED))} is allowed. Remove the extra permission "
            "from the Entra app registration and sign in again.")  # fmt: skip
    return permissions - SIGN_IN_BASICS


# ------------------------------------------------------------------ Microsoft: the token cache


def _load_cache(path: Path) -> Any:
    import msal

    cache = msal.SerializableTokenCache()
    if path.is_file():
        cache.deserialize(path.read_text(encoding="utf-8"))
    return cache


def _save_cache(cache: Any, path: Path) -> None:
    """Write the token cache where it survives a restart, readable by its owner only."""
    if not cache.has_state_changed:
        return
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        out.write(cache.serialize())
    for attempt in range(6):
        try:
            os.replace(tmp, path)
            break
        except PermissionError:  # Windows: a scanner or sync client has the file open a moment
            if attempt == 5:
                raise
            time.sleep(0.1 * (attempt + 1))
    cache.has_state_changed = False


def _secret(settings: Settings) -> str:
    if settings.graph_client_secret:
        return settings.graph_client_secret
    if settings.graph_client_secret_file:
        return Path(settings.graph_client_secret_file).read_text(encoding="utf-8").strip()
    return ""


class GraphSignIn:
    """The Microsoft sign-in as a token source: call it for a bearer token.

    ``http_client`` is MSAL's own hook for the HTTPS client (the tests hand in a stand-in for
    Microsoft's sign-in service); leave it out and MSAL talks to Microsoft."""

    def __init__(self, settings: Settings, *, name: str = "onedrive",
                 http_client: Any = None) -> None:  # fmt: skip
        if not settings.graph_client_id or not settings.graph_tenant_id:
            raise SignInRequired("no sign-in for Microsoft Graph: set CDI_GRAPH_CLIENT_ID and "
                                 "CDI_GRAPH_TENANT_ID (the Entra app registration), then run "
                                 f"`{LOGIN}`")  # fmt: skip
        self.mode = settings.graph_auth.strip().lower()
        if self.mode not in ("device", "app"):
            raise ListenerError(f"CDI_GRAPH_AUTH={settings.graph_auth!r}: use device or app")
        self.cache_path = Path(settings.graph_token_cache)
        self.scopes = ["Sites.Selected" if name == "sharepoint" else "Files.ReadWrite"]
        self._s, self._http, self._app, self._cache = settings, http_client, None, None
        self.permissions: set[str] = set()

    # ---------------------------------------------------------------- MSAL

    def _client(self) -> Any:
        if self._app is not None:
            return self._app
        try:
            import msal
        except ImportError as exc:  # pragma: no cover - the listener extra is not installed
            raise ListenerError("signing in to Microsoft needs MSAL: pip install "
                                "'cdi-adapter[listener]'") from exc  # fmt: skip
        s = self._s
        common = {"authority": f"{s.graph_authority_host.rstrip('/')}/{s.graph_tenant_id}",
                  "http_client": self._http}  # fmt: skip
        if self._http is not None:
            common["instance_discovery"] = False
        if self.mode == "device":
            self._cache = _load_cache(self.cache_path)
            self._app = msal.PublicClientApplication(s.graph_client_id, token_cache=self._cache,
                                                     **common)  # fmt: skip
            return self._app
        credential: Any = _secret(s)
        if s.graph_client_cert_file:
            if not s.graph_client_cert_thumbprint:
                raise ListenerError("CDI_GRAPH_CLIENT_CERT_FILE needs "
                                    "CDI_GRAPH_CLIENT_CERT_THUMBPRINT too")  # fmt: skip
            key = Path(s.graph_client_cert_file).read_text(encoding="utf-8")
            credential = {"private_key": key, "thumbprint": s.graph_client_cert_thumbprint}
        if not credential:
            raise SignInRequired("no sign-in for Microsoft Graph: CDI_GRAPH_AUTH=app needs a "
                                 "certificate (CDI_GRAPH_CLIENT_CERT_FILE) or a secret "
                                 "(CDI_GRAPH_CLIENT_SECRET_FILE)")  # fmt: skip
        self._app = msal.ConfidentialClientApplication(
            s.graph_client_id, client_credential=credential, **common
        )
        return self._app

    def _keep(self, result: dict[str, Any]) -> str:
        self.permissions = _only_what_is_needed(result)
        if self._cache is not None:
            _save_cache(self._cache, self.cache_path)
        return str(result["access_token"])

    # ---------------------------------------------------------------- a token

    def __call__(self) -> str:
        app = self._client()
        if self.mode == "app":
            result = app.acquire_token_for_client(scopes=[GRAPH_APP_SCOPE])
            if "access_token" not in result:
                raise SignInExpired(
                    "Microsoft refused the service sign-in: "
                    f"{_why(result)}. The certificate or secret may have "
                    "expired, or the administrator's approval was withdrawn."
                )
            return self._keep(result)
        accounts = app.get_accounts()
        if not accounts:
            raise SignInRequired(f"nobody has signed in to Microsoft yet: run `{LOGIN}`")
        result = app.acquire_token_silent_with_error(self.scopes, account=accounts[0])
        if not result or "access_token" not in result:
            raise SignInExpired("the Microsoft sign-in has expired"
                                f"{' (' + _why(result) + ')' if result else ''}: "
                                f"run `{LOGIN}` again")  # fmt: skip
        return self._keep(result)

    def login(self, say: Callable[[str], None] = print) -> dict[str, Any]:
        """The one-time sign-in. A person: show the address and the code, wait for them to sign
        in on Microsoft's page, keep the token. A service identity: prove the key works."""
        app = self._client()
        if self.mode == "app":
            self()
        else:
            flow = app.initiate_device_flow(scopes=self.scopes)
            if "user_code" not in flow:
                raise ListenerError(
                    f"Microsoft would not start the sign-in: {_why(flow)}. Is "
                    "'Allow public client flows' switched on for the app?"
                )
            say(f"Open {flow['verification_uri']} and type the code {flow['user_code']}")
            say("Sign in there, on Microsoft's page. No password is typed here.")
            result = app.acquire_token_by_device_flow(flow)
            if "access_token" not in result:
                raise SignInRequired(f"the sign-in was not completed: {_why(result)}")
            self._keep(result)
        return self.describe()

    def describe(self) -> dict[str, Any]:
        """Who is signed in and with what, for the check and login commands. No token."""
        out: dict[str, Any] = {"how": "device code" if self.mode == "device" else "app only",
                               "permissions": sorted(self.permissions)}  # fmt: skip
        if self.mode == "device":
            accounts = self._client().get_accounts()
            out["as"] = accounts[0].get("username") if accounts else None
            out["token_cache"] = str(self.cache_path)
        else:
            out["as"] = f"app {self._s.graph_client_id}"
            out["key"] = "certificate" if self._s.graph_client_cert_file else "secret"
        return out


def _why(result: dict[str, Any] | None) -> str:
    """Microsoft's own reason, shortened - and never the token it came with."""
    if not result:
        return "no answer"
    said = str(result.get("error_description") or result.get("error") or "no reason given")
    return scrub(said.splitlines()[0])[:200]


# ------------------------------------------------------------------ Google: a service account


class GoogleServiceAccount:
    """A Google service account as a token source. The key file signs a short request; Google
    answers with a one-hour token, asked for again shortly before it runs out. The folder must
    be shared with the account's address (``client_email``) - it can see nothing else."""

    def __init__(self, key_file: str | Path, *, transport: httpx.BaseTransport | None = None,
                 now: Callable[[], float] = time.time) -> None:  # fmt: skip
        self.key_file = Path(key_file)
        if not self.key_file.is_file():
            raise SignInRequired("no sign-in for Google Drive: the service account key "
                                 f"{self.key_file} is not there")  # fmt: skip
        try:
            key = json.loads(self.key_file.read_text(encoding="utf-8"))
            self.email, self._key = str(key["client_email"]), str(key["private_key"])
        except (ValueError, KeyError) as exc:
            raise SignInRequired(f"{self.key_file} is not a service account key "
                                 f"({type(exc).__name__})") from None  # fmt: skip
        self._key_id = str(key.get("private_key_id", ""))
        self._token_uri = str(key.get("token_uri") or "https://oauth2.googleapis.com/token")
        self._transport, self._now = transport, now
        self._token, self._until = "", 0.0

    def __call__(self) -> str:
        if self._token and self._now() < self._until - _EARLY:
            return self._token
        import jwt

        at = int(self._now())
        claims = {"iss": self.email, "scope": GOOGLE_SCOPE, "aud": self._token_uri, "iat": at,
                  "exp": at + 3600}  # fmt: skip
        try:
            signed = jwt.encode(
                claims,
                self._key,
                algorithm="RS256",
                headers={"kid": self._key_id} if self._key_id else None,
            )
        except Exception as exc:  # noqa: BLE001 - a damaged key; never echo it
            raise SignInRequired(f"the key in {self.key_file} cannot be used "
                                 f"({type(exc).__name__})") from None  # fmt: skip
        with httpx.Client(transport=self._transport, timeout=60.0) as client:
            answer = client.post(
                self._token_uri, data={"grant_type": _GOOGLE_GRANT, "assertion": signed}
            )
        if answer.status_code in (400, 401, 403):
            raise SignInExpired("Google refused the service account "
                                f"{self.email}: {_google_why(answer)}. The key may have been "
                                "deleted or the account disabled.")  # fmt: skip
        if answer.status_code != 200:
            raise ListenerError(f"Google's sign-in service answered {answer.status_code}")
        body = answer.json()
        self._token = str(body["access_token"])
        self._until = self._now() + float(body.get("expires_in", 3600))
        return self._token

    def login(self, say: Callable[[str], None] = print) -> dict[str, Any]:
        self()  # prove the key works
        return self.describe()

    def describe(self) -> dict[str, Any]:
        return {"how": "service account", "as": self.email, "key": str(self.key_file),
                "permissions": ["drive (only what is shared with this account)"]}  # fmt: skip


def _google_why(answer: httpx.Response) -> str:
    try:
        body = answer.json()
        said = str(body.get("error_description") or body.get("error") or answer.status_code)
    except ValueError:
        said = str(answer.status_code)
    return scrub(said)[:200]


# ------------------------------------------------------------------ chosen by settings


def graph_token_source(settings: Settings, name: str = "onedrive") -> TokenSource:
    if settings.graph_access_token:  # a token pasted in for one test run
        return _static_token(settings.graph_access_token, "Microsoft Graph",
                             "CDI_GRAPH_ACCESS_TOKEN")  # fmt: skip
    if not settings.graph_client_id:
        return _refuse("no sign-in for Microsoft Graph: set CDI_GRAPH_CLIENT_ID and "
                       f"CDI_GRAPH_TENANT_ID, then run `{LOGIN}` (for a test run only, a bearer "
                       "token in CDI_GRAPH_ACCESS_TOKEN)")  # fmt: skip
    return GraphSignIn(settings, name=name)


def gdrive_token_source(settings: Settings) -> TokenSource:
    if settings.gdrive_access_token:
        return _static_token(settings.gdrive_access_token, "Google Drive",
                             "CDI_GDRIVE_ACCESS_TOKEN")  # fmt: skip
    if not settings.gdrive_service_account_file:
        return _refuse(
            "no sign-in for Google Drive: put the service account key file in "
            "CDI_GDRIVE_SERVICE_ACCOUNT_FILE and share the folder with that account "
            "(for a test run only, a bearer token in CDI_GDRIVE_ACCESS_TOKEN)"
        )
    return GoogleServiceAccount(settings.gdrive_service_account_file)


def _refuse(why: str) -> TokenSource:
    def token() -> str:
        raise SignInRequired(why)

    return token


def sign_in_for(settings: Settings) -> GraphSignIn | GoogleServiceAccount | None:
    """The sign-in the settings call for, or ``None`` when the drive needs none (this
    computer, a drive class of your own) or a pasted test token is in use."""
    kind = settings.listener_connector.strip().lower()
    if kind in ("onedrive", "sharepoint") and not settings.graph_access_token:
        return GraphSignIn(settings, name=kind)
    if kind == "gdrive" and not settings.gdrive_access_token:
        if not settings.gdrive_service_account_file:
            raise SignInRequired("no sign-in for Google Drive: put the service account key file "
                                 "in CDI_GDRIVE_SERVICE_ACCOUNT_FILE")  # fmt: skip
        return GoogleServiceAccount(settings.gdrive_service_account_file)
    return None


def known_secrets(settings: Settings) -> tuple[str, ...]:
    """The secret values in settings, so a note can be scrubbed of them by value."""
    return tuple(v for v in (settings.graph_client_secret, settings.graph_access_token,
                             settings.gdrive_access_token) if v)  # fmt: skip


# ------------------------------------------------------------------ the alert


def raise_alert(settings: Settings, alert: str, reason: str) -> Path | None:
    """Say, where an operator will see it, that the listener has stopped and why.

    Two things happen: a ``critical`` line in the listener's log (shipped to Loki with every
    other line), and an alert file on the persistent disk that stays until the listener next
    starts cleanly. Paging a person from it (Alertmanager) waits for ENT-S4."""
    reason = scrub(reason, known_secrets(settings))
    log.critical("listener_alert", alert=alert, reason=reason, action=f"run `{LOGIN}`")
    if not settings.listener_alert_file:
        return None
    path = Path(settings.listener_alert_file)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"alert": alert, "reason": reason,
                                    "at": datetime.now(tz=UTC).isoformat(timespec="seconds"),
                                    "drive": settings.listener_connector,
                                    "action": f"run `{LOGIN}`, then start the listener"},
                                   indent=2) + "\n", encoding="utf-8")  # fmt: skip
    except OSError as exc:
        log.error("listener_alert_file_not_written", error=type(exc).__name__)
        return None
    return path


def clear_alert(settings: Settings) -> None:
    if settings.listener_alert_file:
        Path(settings.listener_alert_file).unlink(missing_ok=True)
