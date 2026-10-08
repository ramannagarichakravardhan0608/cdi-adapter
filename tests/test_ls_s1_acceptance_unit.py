"""LS-S1 acceptance: watch a folder on OneDrive, SharePoint, Google Drive or this computer,
chosen by settings only.

One group of tests per acceptance criterion, written on 2026-10-08 to record what the code does
today. **No application code was changed for this file.**

How they run: the local drive is a real folder (``tmp_path``). OneDrive, SharePoint and Google
Drive are the **real connector classes** talking HTTPS to in-memory stand-ins for Microsoft
Graph and Google Drive (the ones in ``tests/test_listener_connectors_unit.py``): they answer
the same addresses with the same shapes and can be told to say "too many requests". **No real
OneDrive, SharePoint or Google account is called** - that proof is LS-S10's.

A criterion the code does not meet is marked ``xfail(strict=True)`` with what is missing: the
suite stays green while the gap is on record, and the day the feature is built the strict mark
fails the run until it is removed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from cdi_adapter.config import Settings
from cdi_adapter.listener import __main__ as cli
from cdi_adapter.listener import connectors as C
from cdi_adapter.listener import service as S
from tests.test_listener_connectors_unit import Drive, FakeDrive

ROOT = Path(__file__).resolve().parents[1]
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
CLOUD = ["onedrive", "sharepoint", "gdrive"]
EVERY = ["local", *CLOUD]


class _Reader:
    """A reader that only records what it was handed."""

    def __init__(self) -> None:
        self.read_files: list[str] = []

    def reader(self) -> S.Reader:
        def read(name: str, raw: bytes, mime: str) -> S.Outcome:
            self.read_files.append(name)
            return S.Outcome(True, job_id="job-" + name)

        return S.Reader(read=read)


def _start(drive: Drive, reader: _Reader) -> dict[str, Any]:
    """Start the listener on a drive: make the folders, then look at the inbox twice (a file is
    taken on the second look, once it is seen not to have changed)."""
    listener = S.Listener(drive.connector, reader.reader())
    drive.connector.ensure_folders()
    listener.poll()
    return listener.poll()


# ------------------------------------------------------------------ AC1
# GIVEN the setting says gdrive and a Google folder is given WHEN the listener starts
# THEN it lists that Google folder and no code was changed


def test_ac1_the_setting_gdrive_gives_the_google_drive_connector() -> None:
    settings = Settings(listener_connector="gdrive", gdrive_folder_id="folder-123")
    connector = C.get_connector(settings)
    assert isinstance(connector, C.GoogleDriveConnector) and connector.name == "gdrive"
    assert connector.source_id == "gdrive:folder-123"  # the folder that was given


def test_ac1_the_listener_lists_the_google_folder_it_was_given(tmp_path) -> None:
    drive = Drive("gdrive", tmp_path)
    drive.put("inbox/from-google-1.png", PNG)
    drive.put("inbox/from-google-2.png", PNG)
    drive.connector.ensure_folders()
    assert sorted(f.name for f in drive.connector.list("inbox")) == [
        "from-google-1.png",
        "from-google-2.png",
    ]
    listings = [c for c in drive.fake.calls if c == ("GET", "/drive/v3/files")]
    assert listings  # it asked Google Drive's file listing, not anything else


def test_ac1_it_asks_for_that_folder_and_no_other(tmp_path) -> None:
    asked: list[str] = []
    fake = FakeDrive()
    fake.put("inbox/rx.png", PNG)

    def spy(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/drive/v3/files" and request.method == "GET":
            asked.append(request.url.params["q"])
        return fake(request)

    settings = Settings(listener_connector="gdrive", gdrive_folder_id="root-folder",
                        gdrive_access_token="test-token")  # fmt: skip
    connector = C.GoogleDriveConnector.from_settings(settings, transport=httpx.MockTransport(spy))
    connector.ensure_folders()
    connector.list("inbox")
    assert asked[0] == "'root-folder' in parents and trashed = false"  # the folder from settings


def test_ac1_when_the_listener_starts_it_reads_what_is_in_the_google_folder(tmp_path) -> None:
    drive = Drive("gdrive", tmp_path)
    drive.put("inbox/rx-001.png", PNG)
    reader = _Reader()
    report = _start(drive, reader)
    assert reader.read_files == ["rx-001.png"] and report["succeeded"] == ["rx-001.png"]
    assert drive.names("success") == ["rx-001.png"] and drive.names("inbox") == []


def test_ac1_the_same_listener_code_runs_on_every_drive(tmp_path) -> None:
    """ "No code was changed": the loop is handed a connector and never asks which kind it is."""
    import inspect

    for kind in EVERY:
        drive = Drive(kind, tmp_path / kind)
        drive.put("inbox/rx.png", PNG)
        reader = _Reader()
        assert _start(drive, reader)["succeeded"] == ["rx.png"], kind
    loop = inspect.getsource(S)
    for word in ("GoogleDriveConnector", "GraphConnector", "LocalConnector", "gdrive", "onedrive"):
        assert word not in loop, word


def test_ac1_switching_drives_is_a_change_of_settings_and_nothing_else() -> None:
    """The two ready-made settings files differ only in the drive's own settings."""

    def settings_file(name: str) -> dict[str, str]:
        text = (ROOT / name).read_text(encoding="utf-8")
        pairs = [ln.split("=", 1) for ln in text.splitlines() if ln and not ln.startswith("#")]
        return {k.strip(): v.strip() for k, v in pairs}

    one, google = settings_file(".env.onedrive.example"), settings_file(".env.gdrive.example")
    assert google["CDI_LISTENER_CONNECTOR"] == "gdrive" and "CDI_GDRIVE_FOLDER_ID" in google
    shared = set(one) & set(google)
    assert {k for k in shared if one[k] != google[k]} == {"CDI_LISTENER_CONNECTOR"}


def test_ac1_gdrive_without_a_folder_is_refused_in_plain_words() -> None:
    with pytest.raises(C.ListenerError, match="CDI_GDRIVE_FOLDER_ID"):
        C.get_connector(Settings(listener_connector="gdrive", gdrive_folder_id=""))


def test_ac1_without_a_sign_in_the_google_folder_cannot_be_listed() -> None:
    """ISSUE, recorded as a fact: signing in is LS-S2 and is not built. Until it is, a Google
    (or Microsoft) folder can only be listed with a bearer token pasted into settings for a
    test run; without one the listener says so and lists nothing."""
    connector = C.get_connector(Settings(listener_connector="gdrive", gdrive_folder_id="f",
                                         gdrive_access_token=""))  # fmt: skip
    with pytest.raises(C.SignInRequired, match="CDI_GDRIVE_ACCESS_TOKEN"):
        connector.ensure_folders()


# ------------------------------------------------------------------ AC2
# GIVEN the setting changes from onedrive to local WHEN the listener restarts
# THEN only the local folder is read and the retry agent leaves the OneDrive rows alone

_AC2 = ("LS-S1 AC2, 'the retry agent leaves the OneDrive rows alone': there is no retry agent "
        "(LS-S7 is not built). Since LS-S3 every file the listener sees is a row that carries "
        "its drive's source id, which is what the agent will need.")  # fmt: skip


def test_ac2_after_the_switch_only_the_local_folder_is_read(tmp_path) -> None:
    cloud = Drive("onedrive", tmp_path)
    cloud.put("inbox/on-onedrive.png", PNG)
    first = _Reader()
    assert _start(cloud, first)["succeeded"] == ["on-onedrive.png"]
    cloud.put("inbox/still-on-onedrive.png", PNG)  # arrives on OneDrive after the switch
    calls_before = len(cloud.fake.calls)

    local = Drive("local", tmp_path)  # the setting now says local; the listener restarts
    local.put("inbox/on-this-computer.png", PNG)
    second = _Reader()
    report = _start(local, second)

    assert second.read_files == ["on-this-computer.png"]  # the local file, and only it
    assert report["succeeded"] == ["on-this-computer.png"]
    assert len(cloud.fake.calls) == calls_before  # not one more call to OneDrive
    assert cloud.names("inbox") == ["still-on-onedrive.png"]  # left where it is, untouched


def test_ac2_the_setting_decides_which_one_drive_is_built(tmp_path) -> None:
    """Only one drive is active at a time: the one the setting names."""
    base = {"listener_local_root": str(tmp_path)}
    assert isinstance(C.get_connector(Settings(listener_connector="onedrive", **base)),
                      C.GraphConnector)  # fmt: skip
    after = C.get_connector(Settings(listener_connector="local", **base))
    assert isinstance(after, C.LocalConnector) and not isinstance(after, C.GraphConnector)


def test_ac2_each_drive_says_where_its_files_come_from(tmp_path) -> None:
    """What a retry agent will need to tell one drive's files from another's."""
    ids = {}
    for kind in EVERY:
        drive = Drive(kind, tmp_path / kind)
        drive.connector.ensure_folders()
        ids[kind] = drive.connector.source_id
        assert ids[kind].startswith(kind + ":")
    assert len(set(ids.values())) == len(EVERY)
    assert ids["onedrive"] != ids["local"]


def test_ac2_the_run_command_follows_the_setting(monkeypatch, capsys, tmp_path) -> None:
    from cdi_adapter.config import get_settings

    (tmp_path / "inbox").mkdir()
    (tmp_path / "inbox" / "local-file.png").write_bytes(PNG)
    reader = _Reader()
    monkeypatch.setenv("CDI_LISTENER_CONNECTOR", "local")
    monkeypatch.setenv("CDI_LISTENER_LOCAL_ROOT", str(tmp_path))
    monkeypatch.setattr(S, "pipeline_reader", lambda settings=None: reader.reader())
    monkeypatch.setattr(S.time, "sleep", lambda s: None)
    get_settings.cache_clear()
    try:
        assert cli.main(["run", "--once", "--interval", "0"]) == 0
    finally:
        get_settings.cache_clear()
    assert reader.read_files == ["local-file.png"]
    assert "watching     local:" in capsys.readouterr().out


@pytest.mark.xfail(strict=True, reason=_AC2)
def test_ac2_there_is_a_retry_agent() -> None:
    names = {p.stem for p in (ROOT / "src" / "cdi_adapter" / "listener").glob("*.py")}
    assert names & {"recovery", "retry", "retries"}


def test_ac2_the_files_the_listener_has_seen_are_kept_with_their_drive() -> None:
    """Was an expected failure until LS-S3 built the ``listener_file`` table (2026-10-08)."""
    migrations = "\n".join(
        p.read_text(encoding="utf-8") for p in (ROOT / "db" / "alembic" / "versions").glob("*.py")
    )
    assert "listener_file" in migrations


# ------------------------------------------------------------------ AC3
# GIVEN the folders success, error, quarantine and log do not exist WHEN the check command runs
# THEN they are created and no file is processed


@pytest.mark.parametrize("kind", EVERY)
def test_ac3_the_check_creates_the_four_missing_folders(kind: str, tmp_path) -> None:
    drive = Drive(kind, tmp_path)
    drive.put("inbox/rx-001.png", PNG)
    drive.put("processing/")
    result = C.check(drive.connector)
    assert result["folders_created"] == ["success", "error", "quarantine", "log"]
    assert drive.names() == ["error", "inbox", "log", "processing", "quarantine", "success"]


@pytest.mark.parametrize("kind", EVERY)
def test_ac3_no_file_is_processed_by_the_check(kind: str, tmp_path) -> None:
    drive = Drive(kind, tmp_path)
    drive.put("inbox/rx-001.png", b"first")
    drive.put("inbox/rx-002.pdf", b"second")
    result = C.check(drive.connector)
    assert result["waiting"] == 2  # counted ...
    assert drive.names("inbox") == ["rx-001.png", "rx-002.pdf"]  # ... and left where they are
    assert all(count == 0 for count in result["in_folders"].values())
    if drive.fake is not None:  # nothing downloaded, nothing moved
        assert not [c for c in drive.fake.calls if c[1].endswith("/content")]
        assert not [c for c in drive.fake.calls if c[0] == "PATCH"]


def test_ac3_the_check_never_hands_a_file_to_the_reader() -> None:
    import inspect

    source = inspect.getsource(C.check).replace(
        C.check.__doc__ or "", ""
    )  # the code, not its notes
    assert ".download(" not in source and ".move(" not in source and ".write_note(" not in source
    assert "download" not in source and "move" not in source and "write_note" not in source


@pytest.mark.parametrize("kind", EVERY)
def test_ac3_a_second_check_creates_nothing_more(kind: str, tmp_path) -> None:
    drive = Drive(kind, tmp_path)
    assert len(C.check(drive.connector)["folders_created"]) == 6
    assert C.check(drive.connector)["folders_created"] == []


def test_ac3_the_check_command_says_what_it_made_and_that_nothing_was_processed(
    monkeypatch, capsys, tmp_path
) -> None:
    from cdi_adapter.config import get_settings

    (tmp_path / "inbox").mkdir()
    (tmp_path / "processing").mkdir()
    (tmp_path / "inbox" / "rx-001.png").write_bytes(b"scan")
    monkeypatch.setenv("CDI_LISTENER_CONNECTOR", "local")
    monkeypatch.setenv("CDI_LISTENER_LOCAL_ROOT", str(tmp_path))
    get_settings.cache_clear()
    try:
        code = cli.main(["check"])
    finally:
        get_settings.cache_clear()
    said = capsys.readouterr().out
    assert code == 0 and "created: success, error, quarantine, log" in said
    assert "waiting      1 file(s) in the inbox" in said and "nothing was processed" in said
    assert (tmp_path / "inbox" / "rx-001.png").read_bytes() == b"scan"


# ------------------------------------------------------------------ AC4
# GIVEN the drive answers 'too many requests' with a wait time WHEN a call is made
# THEN the listener waits that long and tries again


@pytest.mark.parametrize("kind", CLOUD)
def test_ac4_it_waits_as_long_as_the_drive_asks_and_tries_again(kind: str, tmp_path) -> None:
    drive = Drive(kind, tmp_path)
    drive.put("inbox/rx-001.png", PNG)
    drive.fake.throttle = ["7"]  # the next call is answered 429, Retry-After: 7
    result = C.check(drive.connector)
    assert drive.sleeps == [7.0]  # it waited that long ...
    assert result["waiting"] == 1  # ... tried again, and got its answer


@pytest.mark.parametrize("kind", CLOUD)
def test_ac4_several_refusals_in_a_row_are_each_waited_out(kind: str, tmp_path) -> None:
    drive = Drive(kind, tmp_path)
    drive.fake.throttle = ["3", "5", "2"]
    assert len(drive.connector.ensure_folders()) == 6
    assert drive.sleeps == [3.0, 5.0, 2.0]


def test_ac4_being_told_to_wait_is_not_counted_as_a_failure(tmp_path) -> None:
    """A file read while the drive was throttling still goes through as a success."""
    drive = Drive("onedrive", tmp_path)
    drive.put("inbox/rx-001.png", PNG)
    drive.connector.ensure_folders()
    listener = S.Listener(drive.connector, _Reader().reader())
    listener.poll()
    drive.fake.throttle = ["4", "4"]
    report = listener.poll()
    assert report["succeeded"] == ["rx-001.png"] and report["failed"] == []
    assert drive.sleeps == [4.0, 4.0]


def test_ac4_the_wait_time_can_be_seconds_or_a_date() -> None:
    def answer(value: str | None) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": value} if value else {})

    assert C.retry_after(answer("12")) == 12.0
    at = "Wed, 07 Oct 2026 10:00:30 GMT"
    assert C.retry_after(answer(at), now=lambda: 1791367200.0) == pytest.approx(30.0)
    assert C.retry_after(answer(None)) == 5.0  # the drive did not say: a short default


def test_ac4_googles_own_way_of_saying_slow_down_is_waited_on_too(tmp_path) -> None:
    drive = Drive("gdrive", tmp_path)
    drive.fake.rate_limit_403 = 2  # 403 with reason userRateLimitExceeded, no wait time
    assert len(drive.connector.ensure_folders()) == 6
    assert drive.sleeps == [5.0, 5.0]


def test_ac4_it_does_not_wait_for_ever(tmp_path) -> None:
    drive = Drive("onedrive", tmp_path, listener_max_wait_seconds=100)
    drive.fake.throttle = ["60"] * 10
    with pytest.raises(C.Throttled, match="too many requests"):
        drive.connector.ensure_folders()
    assert drive.sleeps == [60.0]  # one wait; a second would pass the 100 s allowed


def test_ac4_an_ordinary_refusal_is_not_waited_on(tmp_path) -> None:
    def forbidden(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": {"errors": [{"reason": "insufficientRights"}]}})

    sleeps: list[float] = []
    connector = C.GoogleDriveConnector.from_settings(
        Settings(gdrive_access_token="test-token", gdrive_folder_id="f"),
        transport=httpx.MockTransport(forbidden), sleep=sleeps.append)  # fmt: skip
    with pytest.raises(C.ListenerError, match="403"):
        connector.ensure_folders()
    assert sleeps == []
