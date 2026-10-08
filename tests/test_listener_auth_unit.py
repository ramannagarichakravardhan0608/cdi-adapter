"""LS-S2: signing in to the drive safely - no password handled by us.

How these run. **Microsoft**: the real MSAL library and the real ``GraphSignIn`` talk to an
in-memory stand-in for Microsoft's sign-in service (``FakeEntra``), handed to MSAL through its
own ``http_client`` hook. The device-code sign-in, the token cache file, quiet renewal and a
refused renewal are therefore MSAL's real behaviour; only the far end is a stand-in.
**Google**: the real ``GoogleServiceAccount`` signs a request with a key made in the test, and a
stand-in for Google's token service checks the signature. **No real Microsoft or Google account
is called**, and no Entra app registration exists to be reviewed.

One group per acceptance criterion, then the pieces behind them.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from cdi_adapter.config import Settings, get_settings
from cdi_adapter.listener import __main__ as cli
from cdi_adapter.listener import auth as A
from cdi_adapter.listener import connectors as C
from cdi_adapter.listener import service as S
from tests.test_listener_connectors_unit import FakeDrive, FakeGraph

ROOT = Path(__file__).resolve().parents[1]
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
TENANT, CLIENT = "tenant-1", "client-1"
HOST = "https://login.example.test"
PASSWORD = "Correct-Horse-Battery-9"  # a person's password: must never reach us
_PEM = "-----" + "BEGIN"  # in two pieces, so the repository's own secret scan stays quiet


def _b64(data: dict[str, Any]) -> str:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()


def _jwt(claims: dict[str, Any]) -> str:
    return f"{_b64({'alg': 'none', 'typ': 'JWT'})}.{_b64(claims)}.c2lnbmF0dXJl"


class _Answer:
    def __init__(self, status: int, body: dict[str, Any]) -> None:
        self.status_code, self.text, self.headers = status, json.dumps(body), {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeEntra:
    """Microsoft's sign-in service, as far as MSAL uses it. It never sees - and has no way to be
    given - a password: the person "signs in" on the far side, which here is taken as done."""

    def __init__(self, *, scope: str = "Files.ReadWrite", roles: list[str] | None = None,
                 lifetime: int = 3600) -> None:  # fmt: skip
        self.scope, self.roles, self.lifetime = scope, roles, lifetime
        self.calls: list[str] = []  # what was asked: "device_code", "device_token", ...
        self.sent: list[dict[str, Any]] = []  # every form MSAL posted
        self.refuse_refresh: str | None = None
        self.refuse_app: str | None = None
        self.issued = 0

    def _tokens(self, *, person: bool) -> dict[str, Any]:
        self.issued += 1
        claims: dict[str, Any] = {"aud": "https://graph.microsoft.com", "n": self.issued}
        claims.update({"scp": self.scope} if person else {"roles": self.roles or []})
        out: dict[str, Any] = {"token_type": "Bearer", "expires_in": self.lifetime,
                               "access_token": _jwt(claims)}  # fmt: skip
        if person:
            out.update(scope=self.scope + " openid profile offline_access",
                       refresh_token=f"refresh-{self.issued}-very-secret",
                       id_token=_jwt({"preferred_username": "clerk@clinic.example", "sub": "s1",
                                      "oid": "u1", "tid": TENANT, "aud": CLIENT}),
                       client_info=_b64({"uid": "u1", "utid": TENANT}))  # fmt: skip
        return out

    def get(self, url: str, **_: Any) -> _Answer:
        base = f"{HOST}/{TENANT}/oauth2/v2.0"
        return _Answer(200, {"issuer": f"{HOST}/{TENANT}/v2.0", "token_endpoint": f"{base}/token",
                             "authorization_endpoint": f"{base}/authorize",
                             "device_authorization_endpoint": f"{base}/devicecode"})  # fmt: skip

    def post(self, url: str, data: Any = None, **_: Any) -> _Answer:
        form = dict(data or {})
        self.sent.append(form)
        if url.endswith("/devicecode"):
            self.calls.append("device_code")
            return _Answer(200, {"device_code": "dev-code-very-secret", "user_code": "ABCD-1234",
                                 "verification_uri": "https://microsoft.com/devicelogin",
                                 "expires_in": 900, "interval": 0,
                                 "message": "open the page and type ABCD-1234"})  # fmt: skip
        grant = str(form.get("grant_type", ""))
        if grant.endswith("device_code"):
            self.calls.append("device_token")
            return _Answer(200, self._tokens(person=True))
        if grant == "refresh_token":
            self.calls.append("refresh")
            if self.refuse_refresh:
                return _Answer(400, {"error": "invalid_grant",
                                     "error_description": self.refuse_refresh})  # fmt: skip
            return _Answer(200, self._tokens(person=True))
        if grant == "client_credentials":
            self.calls.append("app")
            if self.refuse_app:
                return _Answer(401, {"error": "invalid_client",
                                     "error_description": self.refuse_app})  # fmt: skip
            return _Answer(200, self._tokens(person=False))
        return _Answer(400, {"error": "unsupported_grant_type"})


def _through(drive: Any) -> httpx.MockTransport:
    """The stand-in drive, reached with whatever token the sign-in produced. (The stand-in
    itself insists on the fixed test token, so the real one is checked here and swapped.)"""

    def handler(request: httpx.Request) -> httpx.Response:
        kind, _, token = request.headers["Authorization"].partition(" ")
        assert kind == "Bearer" and len(token) > 10
        request.headers["Authorization"] = "Bearer test-token"
        return drive(request)

    return httpx.MockTransport(handler)


def _settings(tmp: Path, **more: Any) -> Settings:
    base: dict[str, Any] = {"listener_connector": "onedrive", "graph_client_id": CLIENT,
                            "graph_tenant_id": TENANT, "graph_authority_host": HOST,
                            "graph_token_cache": str(tmp / "disk" / "graph_token_cache.json"),
                            "listener_alert_file": str(tmp / "logs" / "listener_alert.json"),
                            "graph_access_token": "", "gdrive_access_token": ""}  # fmt: skip
    return Settings(**{**base, **more})


def _signed_in(tmp: Path, entra: FakeEntra, said: list[str] | None = None,
               **more: Any) -> A.GraphSignIn:  # fmt: skip
    sign_in = A.GraphSignIn(_settings(tmp, **more), http_client=entra)
    sign_in.login((said if said is not None else []).append)
    return sign_in


# ------------------------------------------------------------------ AC1
# GIVEN a device-code sign-in was done once WHEN the pod restarts
# THEN the listener starts without asking for a code again (the token cache is on the disk)


def test_ac1_the_first_sign_in_shows_an_address_and_a_code(tmp_path) -> None:
    entra, said = FakeEntra(), []
    who = _signed_in(tmp_path, entra, said)
    assert "https://microsoft.com/devicelogin" in said[0] and "ABCD-1234" in said[0]
    assert "No password is typed here" in said[1]
    assert who.describe()["as"] == "clerk@clinic.example"
    assert entra.calls == ["device_code", "device_token"]


def test_ac1_the_token_cache_is_written_to_the_path_in_settings(tmp_path) -> None:
    _signed_in(tmp_path, FakeEntra())
    cache = tmp_path / "disk" / "graph_token_cache.json"
    assert cache.is_file() and "RefreshToken" in json.loads(cache.read_text(encoding="utf-8"))
    default = Settings.model_fields["graph_token_cache"].default
    assert default == "/workspace/.cdi/graph_token_cache.json"  # the pod's persistent disk


def test_ac1_after_a_restart_no_code_is_asked_for_again(tmp_path) -> None:
    _signed_in(tmp_path, FakeEntra())
    # the pod restarts: a new process, nothing in memory, only the file on the disk
    after = FakeEntra()
    token = A.GraphSignIn(_settings(tmp_path), http_client=after)()
    assert token.count(".") == 2
    assert "device_code" not in after.calls and "device_token" not in after.calls


def test_ac1_after_a_restart_the_listener_starts_and_reads_a_file(tmp_path) -> None:
    _signed_in(tmp_path, FakeEntra())
    after, graph, read = FakeEntra(), FakeGraph(), []
    graph.put("inbox/rx-001.png", PNG)
    settings = _settings(tmp_path)
    connector = C.GraphConnector.from_settings(
        settings, token=A.GraphSignIn(settings, http_client=after),
        transport=_through(graph))  # fmt: skip
    listener = S.Listener(
        connector, S.Reader(read=lambda n, r, m: read.append(n) or S.Outcome(True))
    )
    connector.ensure_folders()
    listener.poll()
    assert listener.poll()["succeeded"] == ["rx-001.png"] and read == ["rx-001.png"]
    assert "device_code" not in after.calls


def test_ac1_a_token_that_has_run_out_is_renewed_quietly(tmp_path) -> None:
    _signed_in(tmp_path, FakeEntra(lifetime=60))  # MSAL treats under five minutes as run out
    after = FakeEntra(lifetime=60)
    sign_in = A.GraphSignIn(_settings(tmp_path), http_client=after)
    first, second = sign_in(), sign_in()
    assert after.calls == ["refresh", "refresh"] and first != second
    # ... and the renewed sign-in is put back on the disk for the next restart
    assert "refresh-2-very-secret" in (tmp_path / "disk" / "graph_token_cache.json").read_text()


def test_ac1_with_no_cache_file_it_says_to_run_the_login_command(tmp_path) -> None:
    with pytest.raises(C.SignInRequired, match="listener login") as caught:
        A.GraphSignIn(_settings(tmp_path), http_client=FakeEntra())()
    assert not isinstance(caught.value, C.SignInExpired)  # missing, not expired


# ------------------------------------------------------------------ AC2
# GIVEN the sign-in has expired WHEN the listener polls
# THEN it stops, writes the reason to its log and raises an alert instead of retrying forever

EXPIRED = "AADSTS700082: The refresh token has expired due to inactivity."


def _expired_listener(tmp: Path) -> tuple[S.Listener, FakeEntra, FakeGraph, Settings]:
    _signed_in(tmp, FakeEntra(lifetime=60))
    entra, graph = FakeEntra(lifetime=60), FakeGraph()
    settings = _settings(tmp)
    connector = C.GraphConnector.from_settings(
        settings, token=A.GraphSignIn(settings, http_client=entra),
        transport=_through(graph))  # fmt: skip
    listener = S.Listener(connector, S.Reader(read=lambda n, r, m: S.Outcome(True)))
    connector.ensure_folders()
    graph.put("inbox/rx-001.png", PNG)
    listener.poll()
    return listener, entra, graph, settings


def test_ac2_an_expired_sign_in_ends_the_pass(tmp_path) -> None:
    listener, entra, graph, _ = _expired_listener(tmp_path)
    entra.refuse_refresh = EXPIRED
    with pytest.raises(C.SignInExpired, match="has expired"):
        listener.poll()
    assert graph.names("inbox") == ["rx-001.png"]  # nothing was moved to error


def test_ac2_it_is_not_tried_again_and_again(tmp_path) -> None:
    listener, entra, _, _ = _expired_listener(tmp_path)
    before = len(entra.calls)
    entra.refuse_refresh = EXPIRED
    with pytest.raises(C.SignInExpired):
        listener.run(interval=0, once=False, sleep=lambda s: None)  # the long-running loop
    assert len(entra.calls) - before == 1  # asked once, refused once, stopped


@pytest.mark.parametrize("where", ["move", "download"])
def test_ac2_a_sign_in_lost_half_way_through_a_file_stops_too(where: str, tmp_path) -> None:
    """Not swallowed as "this one file failed"."""
    graph, state = FakeGraph(), {"dead": False}
    graph.put("inbox/rx-001.png", PNG)

    def token() -> str:
        if state["dead"]:
            raise C.SignInExpired("the Microsoft sign-in has expired")
        return "test-token"

    connector = C.GraphConnector.from_settings(Settings(), token=token,
                                               transport=httpx.MockTransport(graph))  # fmt: skip
    real = getattr(connector, where)

    def dies(*args: Any, **kwargs: Any) -> Any:
        state["dead"] = True
        return real(*args, **kwargs)

    setattr(connector, where, dies)
    listener = S.Listener(connector, S.Reader(read=lambda n, r, m: S.Outcome(True)))
    connector.ensure_folders()
    listener.poll()
    with pytest.raises(C.SignInExpired):
        listener.poll()
    assert graph.names("error") == [] and graph.names("log") == []


def _run_cli(monkeypatch, capsys, tmp: Path, connector: Any, *argv: str) -> tuple[int, str, str]:
    settings = _settings(tmp)
    monkeypatch.setattr(cli.connectors, "get_connector", lambda s=None: connector)
    monkeypatch.setattr(S, "pipeline_reader",
                        lambda s=None: S.Reader(read=lambda n, r, m: S.Outcome(True)))  # fmt: skip
    monkeypatch.setattr("cdi_adapter.config.get_settings", lambda: settings)
    code = cli.main(list(argv))
    out, err = capsys.readouterr()
    return code, out, err


def test_ac2_the_run_command_stops_logs_the_reason_and_raises_an_alert(
    monkeypatch, capsys, tmp_path
) -> None:
    listener, entra, _, settings = _expired_listener(tmp_path)
    entra.refuse_refresh = EXPIRED
    code, out, err = _run_cli(monkeypatch, capsys, tmp_path, listener.connector,
                              "run", "--once", "--interval", "0")  # fmt: skip
    assert code == 2  # it stopped
    assert "LISTENER STOPPED" in err and "SIGN-IN EXPIRED" in err
    lines = [json.loads(ln) for ln in out.splitlines() if ln.startswith("{")]
    stopped = next(ln for ln in lines if ln["event"] == "listener_stopped")
    assert stopped["why"] == "sign_in_expired" and "AADSTS700082" in stopped["reason"]  # the log
    alert = next(ln for ln in lines if ln["event"] == "listener_alert")
    assert alert["level"] == "critical" and alert["alert"] == "ListenerSignInExpired"
    on_disk = json.loads(Path(settings.listener_alert_file).read_text(encoding="utf-8"))
    assert on_disk["alert"] == "ListenerSignInExpired" and "expired" in on_disk["reason"]
    assert "listener login" in on_disk["action"]


def test_ac2_a_clean_start_takes_the_old_alert_away(monkeypatch, capsys, tmp_path) -> None:
    listener, _, _, settings = _expired_listener(tmp_path)
    A.raise_alert(settings, "ListenerSignInExpired", "the Microsoft sign-in has expired")
    assert Path(settings.listener_alert_file).is_file()
    code, _, _ = _run_cli(monkeypatch, capsys, tmp_path, listener.connector,
                          "run", "--once", "--interval", "0")  # fmt: skip
    assert code == 0 and not Path(settings.listener_alert_file).exists()


def test_ac2_the_check_command_says_expired_not_just_missing(monkeypatch, capsys, tmp_path) -> None:
    _signed_in(tmp_path, FakeEntra(lifetime=60))
    entra = FakeEntra(lifetime=60)
    entra.refuse_refresh = EXPIRED
    settings = _settings(tmp_path)
    monkeypatch.setattr("cdi_adapter.config.get_settings", lambda: settings)
    monkeypatch.setattr(A, "sign_in_for", lambda s: A.GraphSignIn(s, http_client=entra))
    assert cli.main(["check"]) == 2
    assert capsys.readouterr().err.startswith("SIGN-IN EXPIRED:")


@pytest.mark.xfail(strict=True, reason="LS-S2 AC2: the alert is a critical log line and an alert "
                   "file. Paging a person from it (an Alertmanager rule and route) waits for "
                   "ENT-S4, which is not built.")  # fmt: skip
def test_ac2_the_alert_reaches_a_person() -> None:
    rules = (ROOT / "infra" / "monitoring" / "rules" / "cdi-alerts.yml").read_text("utf-8")
    assert "ListenerSignInExpired" in rules


# ------------------------------------------------------------------ AC3
# GIVEN a failure note and the listener log are searched WHEN for tokens, secrets and passwords
# THEN none are found

SECRETS = ["eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ4In0.c2lnbmF0dXJl", "ya29.a0AfH6SMBx-very-secret-token",
           "s3cr3t~client-secret-value", "refresh-9-very-secret", PASSWORD]  # fmt: skip


def _leaky(_n: str, _r: bytes, _m: str) -> S.Outcome:
    """A reader that fails with everything secret in its error message."""
    raise RuntimeError(
        f"GET https://x.sharepoint.com/download?tempauth={SECRETS[0]}&sig=abc123def456 failed; "
        f"Authorization: Bearer {SECRETS[1]}; client_secret={SECRETS[2]}; "
        f'"refresh_token": "{SECRETS[3]}"; password={PASSWORD}'
    )


def test_ac3_a_failure_note_holds_no_token_secret_or_password(tmp_path) -> None:
    root = tmp_path / "watch"
    (root / "inbox").mkdir(parents=True)
    (root / "inbox" / "rx-001.png").write_bytes(PNG)
    connector = C.get_connector(Settings(listener_connector="local", listener_local_root=str(root)))
    listener = S.Listener(connector, S.Reader(read=_leaky))
    connector.ensure_folders()
    listener.poll()
    report = listener.poll()
    note = next((root / "log").iterdir()).read_text(encoding="utf-8")
    assert "RuntimeError" in note and "[removed]" in note  # the reason is still there
    for secret in [*SECRETS, "abc123def456"]:
        assert secret not in note, secret
        assert secret not in json.dumps(report), secret


def test_ac3_the_listener_log_holds_no_token_secret_or_password(
    monkeypatch, capsys, tmp_path
) -> None:
    root = tmp_path / "watch"
    (root / "inbox").mkdir(parents=True)
    (root / "inbox" / "rx-001.png").write_bytes(PNG)
    connector = C.get_connector(Settings(listener_connector="local", listener_local_root=str(root)))
    monkeypatch.setattr(cli.connectors, "get_connector", lambda s=None: connector)
    monkeypatch.setattr(S, "pipeline_reader", lambda s=None: S.Reader(read=_leaky))
    monkeypatch.setattr(S.time, "sleep", lambda s: None)
    assert cli.main(["run", "--once", "--interval", "0"]) == 0
    out, err = capsys.readouterr()
    assert "listener_file_failed" in out
    for secret in [*SECRETS, "abc123def456"]:
        assert secret not in out and secret not in err, secret


def test_ac3_every_line_the_listener_logs_is_scrubbed(capsys) -> None:
    from cdi_adapter.logging import configure, get_logger

    cli._logging()
    try:
        get_logger("t").warning("anything", note=f"Bearer {SECRETS[1]}",
                                nested={"access_token": SECRETS[0], "ok": "kept"})  # fmt: skip
    finally:
        configure()
    out = capsys.readouterr().out
    assert SECRETS[0] not in out and SECRETS[1] not in out and "kept" in out


def test_ac3_signing_in_and_expiring_leave_nothing_secret_in_the_output(
    monkeypatch, capsys, tmp_path
) -> None:
    listener, entra, _, settings = _expired_listener(tmp_path)
    entra.refuse_refresh = EXPIRED
    _, out, err = _run_cli(monkeypatch, capsys, tmp_path, listener.connector,
                           "run", "--once", "--interval", "0")  # fmt: skip
    cache = Path(settings.graph_token_cache).read_text(encoding="utf-8")
    alert = Path(settings.listener_alert_file).read_text(encoding="utf-8")
    assert "clerk@clinic.example" in cache  # the sign-in is on the disk, where it belongs ...
    for text in (out, err, alert):  # ... and no token is anywhere else
        assert "very-secret" not in text and "eyJ" not in text


def test_ac3_no_password_is_ever_sent_asked_for_or_stored(tmp_path) -> None:
    entra, said = FakeEntra(lifetime=60), []
    sign_in = _signed_in(tmp_path, entra, said)
    sign_in()
    for form in entra.sent:
        assert not {"password", "username"} & set(form), form
        assert form.get("grant_type") != "password"
    assert "password" not in (tmp_path / "disk" / "graph_token_cache.json").read_text().lower()
    source = (ROOT / "src" / "cdi_adapter" / "listener" / "auth.py").read_text(encoding="utf-8")
    for asks in ("getpass", "input(", "acquire_token_by_username_password"):
        assert asks not in source, asks
    assert not [f for f in Settings.model_fields if "password" in f and "graph" in f]


def test_ac3_secret_settings_do_not_show_when_settings_are_printed(tmp_path) -> None:
    shown = repr(
        _settings(tmp_path, graph_client_secret=SECRETS[2], graph_access_token="tok-12345")
    )
    assert SECRETS[2] not in shown and "tok-12345" not in shown


@pytest.mark.parametrize(("text", "gone"), [
    ("Authorization: Bearer abcdefgh12345678", "abcdefgh12345678"),
    ("token eyJhbGciOiJub25lIn0.eyJzdWIiOiJ4In0.sig", "eyJhbGciOiJub25lIn0"),
    ('{"access_token": "opaque-value-1"}', "opaque-value-1"),
    ("client_secret=abc~DEF.123&x=1", "abc~DEF.123"),
    ("password: hunter2-hunter2", "hunter2-hunter2"),
    ("https://d/x?tempauth=v1.abc.def&keep=1", "v1.abc.def"),
    (f"{_PEM} PRIVATE KEY-----\nMIIEvQIBADANBg\n-----END PRIVATE KEY-----", "MIIEvQIBADANBg"),
    ("ya29.a0AfH6SMBxyz_123456", "a0AfH6SMBxyz_123456"),
])  # fmt: skip
def test_ac3_what_scrub_takes_out(text: str, gone: str) -> None:
    cleaned = A.scrub(text)
    assert gone not in cleaned and "[removed]" in cleaned


def test_ac3_scrub_leaves_ordinary_text_alone() -> None:
    plain = "rx-001.png: the picture is too dark (brightness 31, needs 60). Job 7f3a."
    assert A.scrub(plain) == plain
    assert A.scrub("the secret is s3cr3t~value here", ("s3cr3t~value",)).count("[removed]") == 1


def test_ac3_sign_in_material_is_kept_out_of_git() -> None:
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for line in ("/secrets/", ".cdi/", "*token_cache*.json", "*service_account*.json", ".env"):
        assert line in ignored.splitlines(), line
    for example in (".env.onedrive.example", ".env.gdrive.example"):
        text = (ROOT / example).read_text(encoding="utf-8")
        pairs = [ln.split("=", 1) for ln in text.splitlines() if "=" in ln and ln[0] != "#"]
        for key, value in pairs:
            if any(w in key for w in ("SECRET", "TOKEN", "PASSWORD")) and "FILE" not in key:
                assert value.strip() == "" or "CACHE" in key, key


# ------------------------------------------------------------------ AC4
# GIVEN the Entra app registration is reviewed WHEN its permissions are listed
# THEN they are only delegated Files.ReadWrite or Sites.Selected, nothing broader


def test_ac4_a_persons_sign_in_asks_for_files_readwrite_and_nothing_else(tmp_path) -> None:
    entra = FakeEntra()
    _signed_in(tmp_path, entra)
    asked = set(entra.sent[0]["scope"].split())
    assert asked - A.SIGN_IN_BASICS == {"Files.ReadWrite"}


def test_ac4_a_sharepoint_sign_in_asks_for_sites_selected(tmp_path) -> None:
    entra = FakeEntra(scope="Sites.Selected")
    _signed_in(tmp_path, entra, listener_connector="sharepoint", graph_site_id="site-9")
    sign_in = A.sign_in_for(_settings(tmp_path, listener_connector="sharepoint"))
    assert isinstance(sign_in, A.GraphSignIn) and sign_in.scopes == ["Sites.Selected"]


def test_ac4_the_permissions_the_sign_in_carries_are_listed(tmp_path) -> None:
    sign_in = _signed_in(tmp_path, FakeEntra())
    assert sign_in.describe()["permissions"] == ["Files.ReadWrite"]


@pytest.mark.parametrize("broad", ["Files.ReadWrite.All", "Sites.ReadWrite.All", "Mail.Read",
                                   "User.Read", "Files.Read.All"])  # fmt: skip
def test_ac4_a_sign_in_with_anything_broader_is_refused_and_not_kept(broad: str, tmp_path) -> None:
    entra = FakeEntra(scope=f"Files.ReadWrite {broad}")
    with pytest.raises(C.ListenerError, match="more permission") as caught:
        _signed_in(tmp_path, entra)
    assert broad in str(caught.value)
    assert not (tmp_path / "disk" / "graph_token_cache.json").exists()  # not stored


def test_ac4_a_service_identity_may_only_have_sites_selected(tmp_path) -> None:
    more = {"graph_auth": "app", "graph_client_secret": "s3cr3t~client-secret-value",
            "listener_connector": "sharepoint"}  # fmt: skip
    ok = A.GraphSignIn(_settings(tmp_path, **more), name="sharepoint",
                       http_client=FakeEntra(roles=["Sites.Selected"]))  # fmt: skip
    assert ok().count(".") == 2 and ok.describe()["permissions"] == ["Sites.Selected"]
    wide = A.GraphSignIn(
        _settings(tmp_path, **more),
        name="sharepoint",
        http_client=FakeEntra(roles=["Sites.Selected", "Files.ReadWrite.All"]),
    )
    with pytest.raises(C.ListenerError, match="Files.ReadWrite.All"):
        wide()


def test_ac4_the_allowed_list_is_exactly_the_two() -> None:
    assert frozenset({"Files.ReadWrite", "Sites.Selected"}) == A.PERMITTED
    assert A.broader_than_needed({"Files.ReadWrite", "openid", "offline_access"}) == []
    assert A.broader_than_needed({"https://graph.microsoft.com/Files.ReadWrite.All"}) == [
        "https://graph.microsoft.com/Files.ReadWrite.All"]  # fmt: skip


@pytest.mark.xfail(strict=True, reason="LS-S2 AC4: there is no Entra app registration yet, so "
                   "nobody has listed its permissions. The code refuses anything broader; the "
                   "review itself is a person's job in the Entra portal, recorded in "
                   "docs/LISTENER.md.")  # fmt: skip
def test_ac4_the_app_registration_has_been_reviewed() -> None:
    doc = (ROOT / "docs" / "LISTENER.md").read_text(encoding="utf-8")
    row = next(ln for ln in doc.splitlines() if ln.startswith("| Permissions listed"))
    assert row.split("|")[2].strip()


# ------------------------------------------------------------------ service sign-in (app only)


def test_app_only_sign_in_with_a_secret_keeps_nothing_on_disk(tmp_path) -> None:
    entra = FakeEntra(roles=["Sites.Selected"])
    secret_file = tmp_path / "secrets" / "graph_client_secret"
    secret_file.parent.mkdir()
    secret_file.write_text("s3cr3t~client-secret-value\n", encoding="utf-8")
    sign_in = A.GraphSignIn(_settings(tmp_path, graph_auth="app", listener_connector="sharepoint",
                                      graph_client_secret_file=str(secret_file)),
                            name="sharepoint", http_client=entra)  # fmt: skip
    who = sign_in.login([].append)
    assert entra.calls == ["app"] and entra.sent[0]["scope"] == A.GRAPH_APP_SCOPE
    assert who == {"how": "app only", "as": f"app {CLIENT}", "key": "secret",
                   "permissions": ["Sites.Selected"]}  # fmt: skip
    assert not (tmp_path / "disk").exists()


def test_app_only_sign_in_needs_a_key(tmp_path) -> None:
    sign_in = A.GraphSignIn(_settings(tmp_path, graph_auth="app"), http_client=FakeEntra())
    with pytest.raises(C.SignInRequired, match="CDI_GRAPH_CLIENT_CERT_FILE"):
        sign_in()


def test_a_refused_service_sign_in_stops_the_listener_like_an_expired_one(tmp_path) -> None:
    entra = FakeEntra(roles=["Sites.Selected"])
    entra.refuse_app = "AADSTS7000222: The provided client secret keys are expired."
    settings = _settings(tmp_path, graph_auth="app", graph_client_secret="s3cr3t~x-y-z")
    sign_in = A.GraphSignIn(settings, http_client=entra)
    with pytest.raises(C.SignInExpired, match="AADSTS7000222"):
        sign_in()


def test_a_wrong_setting_is_said_in_plain_words(tmp_path) -> None:
    with pytest.raises(C.ListenerError, match="device or app"):
        A.GraphSignIn(_settings(tmp_path, graph_auth="password"))
    with pytest.raises(C.SignInRequired, match="CDI_GRAPH_TENANT_ID"):
        A.GraphSignIn(_settings(tmp_path, graph_tenant_id=""))


# ------------------------------------------------------------------ Google: a service account


@pytest.fixture(scope="module")
def google_key(tmp_path_factory) -> dict[str, Any]:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode()  # fmt: skip
    path = tmp_path_factory.mktemp("secrets") / "gdrive_service_account.json"
    path.write_text(json.dumps({
        "type": "service_account", "client_email": "listener@proj.iam.gserviceaccount.com",
        "private_key": pem, "private_key_id": "key-1",
        "token_uri": "https://oauth2.example.test/token"}), encoding="utf-8")  # fmt: skip
    return {"path": path, "public": key.public_key(), "pem": pem}


class FakeGoogleSignIn:
    def __init__(self, public: Any) -> None:
        self.public, self.asked, self.refuse = public, [], False  # type: ignore[var-annotated]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        import jwt

        form = dict(httpx.QueryParams(request.content.decode()))
        claims = jwt.decode(form["assertion"], self.public, algorithms=["RS256"],
                            audience="https://oauth2.example.test/token",
                            options={"verify_iat": False, "verify_exp": False})  # fmt: skip
        self.asked.append({**claims, "grant_type": form["grant_type"]})
        if self.refuse:
            return httpx.Response(
                400, json={"error": "invalid_grant", "error_description": "Invalid JWT Signature."}
            )
        return httpx.Response(200, json={"access_token": f"ya29.token-{len(self.asked)}-secret",
                                         "expires_in": 3600, "token_type": "Bearer"})  # fmt: skip


def test_google_service_account_gets_a_token_with_its_key(google_key) -> None:
    google = FakeGoogleSignIn(google_key["public"])
    sign_in = A.GoogleServiceAccount(google_key["path"], transport=httpx.MockTransport(google))
    assert sign_in() == "ya29.token-1-secret"
    asked = google.asked[0]
    assert asked["iss"] == "listener@proj.iam.gserviceaccount.com"
    assert asked["scope"] == A.GOOGLE_SCOPE and asked["grant_type"].endswith("jwt-bearer")
    assert asked["exp"] - asked["iat"] == 3600


def test_google_token_is_kept_until_shortly_before_it_runs_out(google_key) -> None:
    google, clock = FakeGoogleSignIn(google_key["public"]), [1_800_000_000.0]
    sign_in = A.GoogleServiceAccount(google_key["path"], transport=httpx.MockTransport(google),
                                     now=lambda: clock[0])  # fmt: skip
    assert sign_in() == sign_in() and len(google.asked) == 1
    clock[0] += 3600 - 60  # a minute left: renewed quietly
    assert sign_in() == "ya29.token-2-secret" and len(google.asked) == 2


def test_google_drive_is_listed_with_the_service_account(google_key, tmp_path) -> None:
    google, drive = FakeGoogleSignIn(google_key["public"]), FakeDrive()
    drive.put("inbox/rx-001.png", PNG)
    settings = Settings(
        listener_connector="gdrive",
        gdrive_folder_id="root-folder",
        gdrive_access_token="",
        gdrive_service_account_file=str(google_key["path"]),
    )
    token = A.GoogleServiceAccount(google_key["path"], transport=httpx.MockTransport(google))
    connector = C.GoogleDriveConnector.from_settings(settings, token=token,
                                                     transport=_through(drive))  # fmt: skip
    assert C.check(connector)["waiting"] == 1 and len(google.asked) == 1
    assert isinstance(A.gdrive_token_source(settings), A.GoogleServiceAccount)  # by settings


def test_a_refused_google_key_is_an_expired_sign_in_and_shows_no_key(google_key) -> None:
    google = FakeGoogleSignIn(google_key["public"])
    google.refuse = True
    sign_in = A.GoogleServiceAccount(google_key["path"], transport=httpx.MockTransport(google))
    with pytest.raises(C.SignInExpired, match="Invalid JWT Signature") as caught:
        sign_in()
    assert "PRIVATE KEY" not in str(caught.value) and google_key["pem"][40:80] not in str(
        caught.value
    )


def test_a_missing_or_damaged_google_key_file_is_said_in_plain_words(tmp_path) -> None:
    with pytest.raises(C.SignInRequired, match="is not there"):
        A.GoogleServiceAccount(tmp_path / "nope.json")
    bad = tmp_path / "bad.json"
    bad.write_text('{"type": "authorized_user"}', encoding="utf-8")
    with pytest.raises(C.SignInRequired, match="not a service account key"):
        A.GoogleServiceAccount(bad)


def test_google_describe_says_which_address_to_share_the_folder_with(google_key) -> None:
    who = A.GoogleServiceAccount(google_key["path"]).describe()
    assert who["how"] == "service account" and who["as"].endswith("gserviceaccount.com")
    assert "PRIVATE" not in json.dumps(who)


# ------------------------------------------------------------------ the login and check commands


def _cli(monkeypatch, capsys, settings: Settings, sign_in: Any, *argv: str) -> tuple[int, str, str]:
    monkeypatch.setattr("cdi_adapter.config.get_settings", lambda: settings)
    if sign_in is not None:
        monkeypatch.setattr(A, "sign_in_for", lambda s: sign_in)
    code = cli.main(list(argv))
    out, err = capsys.readouterr()
    return code, out, err


def test_the_login_command_does_the_one_time_sign_in(monkeypatch, capsys, tmp_path) -> None:
    settings = _settings(tmp_path)
    sign_in = A.GraphSignIn(settings, http_client=FakeEntra())
    code, out, _ = _cli(monkeypatch, capsys, settings, sign_in, "login")
    assert code == 0 and "ABCD-1234" in out and "https://microsoft.com/devicelogin" in out
    assert "sign-in      device code, as clerk@clinic.example (Files.ReadWrite)" in out
    assert "no code is asked for again" in out and "no password was typed" in out
    assert "very-secret" not in out and "eyJ" not in out


def test_the_login_command_on_a_local_folder_says_none_is_needed(monkeypatch, capsys) -> None:
    get_settings.cache_clear()
    code, out, _ = _cli(monkeypatch, capsys, Settings(listener_connector="local"), None, "login")
    assert code == 0 and "none needed for this drive" in out


def test_the_login_command_refuses_a_too_broad_sign_in(monkeypatch, capsys, tmp_path) -> None:
    settings = _settings(tmp_path)
    sign_in = A.GraphSignIn(settings, http_client=FakeEntra(scope="Files.ReadWrite.All"))
    code, _, err = _cli(monkeypatch, capsys, settings, sign_in, "login")
    assert code == 1 and err.startswith("NOT SIGNED IN:") and "Files.ReadWrite.All" in err


def test_the_check_command_says_who_is_signed_in(monkeypatch, capsys, tmp_path) -> None:
    settings = _settings(tmp_path)
    _signed_in(tmp_path, FakeEntra())
    sign_in, graph = A.GraphSignIn(settings, http_client=FakeEntra()), FakeGraph()
    connector = C.GraphConnector.from_settings(settings, token=sign_in,
                                               transport=_through(graph))  # fmt: skip
    monkeypatch.setattr(cli.connectors, "get_connector", lambda s=None: connector)
    code, out, _ = _cli(monkeypatch, capsys, settings, sign_in, "check")
    assert code == 0 and "nothing was processed" in out
    assert "sign-in      device code, as clerk@clinic.example (Files.ReadWrite)" in out


def test_the_check_command_says_when_nobody_has_signed_in(monkeypatch, capsys, tmp_path) -> None:
    settings = _settings(tmp_path)
    sign_in = A.GraphSignIn(settings, http_client=FakeEntra())
    code, out, err = _cli(monkeypatch, capsys, settings, sign_in, "check")
    assert code == 2 and out == "" and err.startswith("SIGN-IN NEEDED:") and "listener login" in err


def test_a_pasted_test_token_still_works_and_needs_no_sign_in(tmp_path) -> None:
    settings = _settings(tmp_path, graph_access_token="test-token")
    assert A.sign_in_for(settings) is None
    assert A.graph_token_source(settings)() == "test-token"
