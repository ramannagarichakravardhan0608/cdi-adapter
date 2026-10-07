"""LS-S1: watch a folder on OneDrive, SharePoint, Google Drive or this computer, by settings only.

One set of checks, run against every drive. The local drive is a real folder (``tmp_path``). The
two cloud drives are the **real connector classes** talking HTTPS to in-memory stand-ins for
Microsoft Graph and Google Drive (``httpx.MockTransport``): the stand-ins answer the same
addresses with the same shapes, keep files in a dictionary, and can be told to answer "too many
requests". No real OneDrive or Google account is touched here - that proof is LS-S10's - so what
these tests show is that the connectors ask each service the right things and behave the same
way as each other, not that either service agrees.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import httpx
import pytest

from cdi_adapter.config import Settings
from cdi_adapter.listener import __main__ as cli
from cdi_adapter.listener import connectors as C

FOLDER_MIME = "application/vnd.google-apps.folder"


# ------------------------------------------------------------------ a stand-in for Microsoft Graph


class FakeGraph:
    """Enough of Microsoft Graph's drive API to hold a folder of files."""

    def __init__(self, drive: str = "/me/drive") -> None:
        self.drive, self.n = drive, 0
        self.items: dict[str, dict[str, Any]] = {}
        self.root = self._add("root", None, folder=True)
        self.throttle: list[str] = []  # Retry-After values for the next calls
        self.calls: list[tuple[str, str]] = []
        self.unauthorised = False

    def _add(self, name: str, parent: str | None, *, folder: bool = False,
             content: bytes = b"") -> str:  # fmt: skip
        self.n += 1
        item_id = f"item{self.n}"
        self.items[item_id] = {
            "id": item_id,
            "name": name,
            "parent": parent,
            "folder": folder,
            "content": content,
            "stamp": f"2026-10-0{1 + self.n % 8}T10:00:00Z",
        }
        return item_id

    def put(self, path: str, content: bytes = b"x") -> str:
        """Place a file (or, with a trailing slash, a folder) at a path under the root."""
        parent = self.root
        parts = [p for p in path.split("/") if p]
        for i, part in enumerate(parts):
            last = i == len(parts) - 1
            found = self._child(parent, part)
            if found is None:
                found = self._add(part, parent, folder=not last or path.endswith("/"),
                                  content=content if last else b"")  # fmt: skip
            parent = found
        return parent

    def _child(self, parent: str, name: str) -> str | None:
        for i in self.items.values():
            if i["parent"] == parent and i["name"].lower() == name.lower():
                return str(i["id"])
        return None

    def names(self, path: str = "") -> list[str]:
        parent = self.root
        for part in [p for p in path.split("/") if p]:
            found = self._child(parent, part)
            assert found is not None, f"no folder {path}"
            parent = found
        return sorted(i["name"] for i in self.items.values() if i["parent"] == parent)

    def _json(self, item: dict[str, Any]) -> dict[str, Any]:
        out = {"id": item["id"], "name": item["name"], "size": len(item["content"]),
               "lastModifiedDateTime": item["stamp"], "eTag": f"tag-{item['id']}",
               "parentReference": {"driveId": "drive-1", "id": item["parent"]}}  # fmt: skip
        out["folder" if item["folder"] else "file"] = {}
        return out

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append((request.method, path))
        assert request.headers["Authorization"] == "Bearer test-token"
        if self.unauthorised:
            return httpx.Response(401, json={"error": {"code": "InvalidAuthenticationToken"}})
        if self.throttle:
            return httpx.Response(429, headers={"Retry-After": self.throttle.pop(0)}, json={})
        assert path.startswith("/v1.0" + self.drive), path
        rest = path[len("/v1.0" + self.drive):]  # fmt: skip
        body = json.loads(request.content) if request.content[:1] == b"{" else {}

        if rest == "/root":
            return httpx.Response(200, json=self._json(self.items[self.root]))
        if m := re.fullmatch(r"/root:/(.+)", rest):
            item_id: str | None = self.root
            for part in m.group(1).split("/"):
                item_id = self._child(item_id, part) if item_id else None
            if item_id is None:
                return httpx.Response(404, json={"error": {"code": "itemNotFound"}})
            return httpx.Response(200, json=self._json(self.items[item_id]))
        if m := re.fullmatch(r"/items/(\w+)/children", rest):
            parent = m.group(1)
            if request.method == "GET":
                kids = [self._json(i) for i in self.items.values() if i["parent"] == parent]
                skip = int(request.url.params.get("$skip", "0"))
                page = kids[skip : skip + 2]  # two to a page, so paging is always exercised
                out: dict[str, Any] = {"value": page}
                if skip + 2 < len(kids):
                    out["@odata.nextLink"] = (f"https://graph.test/v1.0{self.drive}/items/{parent}"
                                              f"/children?$skip={skip + 2}")  # fmt: skip
                return httpx.Response(200, json=out)
            if self._child(parent, body["name"]):
                return httpx.Response(409, json={"error": {"code": "nameAlreadyExists"}})
            made = self._add(body["name"], parent, folder=True)
            return httpx.Response(201, json=self._json(self.items[made]))
        if m := re.fullmatch(r"/items/(\w+)/content", rest):
            item = self.items.get(m.group(1))
            if item is None:
                return httpx.Response(404, json={})
            return httpx.Response(200, content=item["content"])
        if m := re.fullmatch(r"/items/(\w+):/(.+):/content", rest):
            parent, name = m.group(1), m.group(2)
            if self._child(parent, name):
                return httpx.Response(409, json={"error": {"code": "nameAlreadyExists"}})
            made = self._add(name, parent, content=request.content)
            return httpx.Response(201, json=self._json(self.items[made]))
        if (m := re.fullmatch(r"/items/(\w+)", rest)) and request.method == "PATCH":
            item = self.items.get(m.group(1))
            if item is None:
                return httpx.Response(404, json={})
            parent = body["parentReference"]["id"]
            clash = self._child(parent, body["name"])
            if clash and clash != item["id"]:
                return httpx.Response(409, json={"error": {"code": "nameAlreadyExists"}})
            item["parent"], item["name"] = parent, body["name"]
            return httpx.Response(200, json=self._json(item))
        raise AssertionError(f"the connector asked Graph something unexpected: {request.method} "
                             f"{path}")  # fmt: skip


# ------------------------------------------------------------------ a stand-in for Google Drive


class FakeDrive:
    """Enough of Google Drive v3 to hold a folder of files. Like the real one, it lets two
    files share a name."""

    def __init__(self) -> None:
        self.n = 0
        self.files: dict[str, dict[str, Any]] = {}
        self.root = "root-folder"
        self.files[self.root] = {"id": self.root, "name": "Incoming", "parent": None,
                                 "mimeType": FOLDER_MIME, "content": b""}  # fmt: skip
        self.throttle: list[str] = []
        self.rate_limit_403 = 0
        self.calls: list[tuple[str, str]] = []

    def _add(self, name: str, parent: str, *, mime: str = "image/png",
             content: bytes = b"") -> str:  # fmt: skip
        self.n += 1
        file_id = f"file{self.n}"
        self.files[file_id] = {
            "id": file_id,
            "name": name,
            "parent": parent,
            "mimeType": mime,
            "content": content,
            "modifiedTime": f"2026-10-0{1 + self.n % 8}T10:00:00.000Z",
        }
        return file_id

    def put(self, path: str, content: bytes = b"x") -> str:
        parent = self.root
        parts = [p for p in path.split("/") if p]
        for i, part in enumerate(parts):
            last = i == len(parts) - 1
            found = next((f["id"] for f in self.files.values()
                          if f["parent"] == parent and f["name"] == part), None)  # fmt: skip
            if found is None or (last and not path.endswith("/")):
                is_folder = not last or path.endswith("/")
                found = self._add(part, parent, mime=FOLDER_MIME if is_folder else "image/png",
                                  content=b"" if is_folder else content)  # fmt: skip
            parent = found
        return parent

    def names(self, path: str = "") -> list[str]:
        parent = self.root
        for part in [p for p in path.split("/") if p]:
            parent = next(f["id"] for f in self.files.values()
                          if f["parent"] == parent and f["name"] == part)  # fmt: skip
        return sorted(f["name"] for f in self.files.values() if f["parent"] == parent)

    def _json(self, f: dict[str, Any]) -> dict[str, Any]:
        out = {"id": f["id"], "name": f["name"], "mimeType": f["mimeType"],
               "modifiedTime": f.get("modifiedTime")}  # fmt: skip
        if f["mimeType"] != FOLDER_MIME:
            out["size"] = str(len(f["content"]))
            out["md5Checksum"] = f"md5-{f['id']}"
        return out

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path, params = request.url.path, request.url.params
        self.calls.append((request.method, path))
        assert request.headers["Authorization"] == "Bearer test-token"
        if self.throttle:
            return httpx.Response(429, headers={"Retry-After": self.throttle.pop(0)}, json={})
        if self.rate_limit_403:
            self.rate_limit_403 -= 1
            return httpx.Response(403, json={"error": {"errors": [
                {"domain": "usageLimits", "reason": "userRateLimitExceeded"}]}})  # fmt: skip
        body = json.loads(request.content) if request.content[:1] == b"{" else {}

        if path == "/drive/v3/files" and request.method == "GET":
            m = re.fullmatch(r"'([\w-]+)' in parents and trashed = false", params["q"])
            assert m, params["q"]
            if m.group(1) not in self.files:
                return httpx.Response(404, json={})
            kids = [self._json(f) for f in self.files.values() if f["parent"] == m.group(1)]
            start = int(params.get("pageToken", "0"))
            out: dict[str, Any] = {"files": kids[start : start + 2]}
            if start + 2 < len(kids):
                out["nextPageToken"] = str(start + 2)
            return httpx.Response(200, json=out)
        if path == "/drive/v3/files" and request.method == "POST":
            made = self._add(body["name"], body["parents"][0], mime=body["mimeType"])
            return httpx.Response(200, json=self._json(self.files[made]))
        if m := re.fullmatch(r"/drive/v3/files/([\w-]+)", path):
            f = self.files.get(m.group(1))
            if f is None:
                return httpx.Response(404, json={})
            if request.method == "GET":
                assert params["alt"] == "media"
                return httpx.Response(200, content=f["content"])
            assert f["parent"] == params["removeParents"], "moved from the wrong folder"
            f["parent"], f["name"] = params["addParents"], body["name"]
            return httpx.Response(200, json=self._json(f))
        if m := re.fullmatch(r"/upload/drive/v3/files/([\w-]+)", path):
            assert params["uploadType"] == "media"
            self.files[m.group(1)]["content"] = request.content
            return httpx.Response(200, json=self._json(self.files[m.group(1)]))
        raise AssertionError(f"the connector asked Drive something unexpected: {request.method} "
                             f"{path}")  # fmt: skip


# ------------------------------------------------------------------ each drive, behind one face


class Drive:
    """One drive under test: the connector, and a way to put files on it and look at it that
    does not go through the connector."""

    def __init__(self, kind: str, tmp: Path, **overrides: Any) -> None:
        self.kind, self.sleeps = kind, []  # type: list[float]
        self.settings = Settings(listener_connector=kind, listener_local_root=str(tmp / "watch"),
                                 graph_access_token="test-token", graph_site_id="site-9",
                                 gdrive_access_token="test-token", gdrive_folder_id="root-folder",
                                 **overrides)  # fmt: skip
        self.fake: Any = None
        if kind in ("onedrive", "sharepoint"):
            self.fake = FakeGraph("/me/drive" if kind == "onedrive" else "/sites/site-9/drive")
            self.connector: Any = C.GraphConnector.from_settings(
                self.settings, name=kind, transport=httpx.MockTransport(self.fake),
                sleep=self.sleeps.append)  # fmt: skip
        elif kind == "gdrive":
            self.fake = FakeDrive()
            self.connector = C.GoogleDriveConnector.from_settings(
                self.settings, transport=httpx.MockTransport(self.fake),
                sleep=self.sleeps.append)  # fmt: skip
        else:
            self.connector = C.get_connector(self.settings)
            self.root = tmp / "watch"

    def put(self, path: str, content: bytes = b"x") -> None:
        if self.fake is not None:
            self.fake.put(path, content)
            return
        target = self.root / path
        if path.endswith("/"):
            target.mkdir(parents=True, exist_ok=True)
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

    def names(self, path: str = "") -> list[str]:
        if self.fake is not None:
            return list(self.fake.names(path))
        return sorted(p.name for p in (self.root / path).iterdir())


KINDS = ["local", "onedrive", "sharepoint", "gdrive"]


@pytest.fixture(params=KINDS)
def drive(request, tmp_path) -> Drive:
    return Drive(request.param, tmp_path)


# ------------------------------------------------------------------ the same checks on every drive


def test_every_drive_is_a_connector(drive):
    assert isinstance(drive.connector, C.Connector)
    assert drive.connector.name == drive.kind


def test_the_check_creates_the_missing_folders_and_processes_nothing(drive):
    """AC3: success, error, quarantine and log do not exist; the check makes them and touches
    no file."""
    drive.put("inbox/rx-001.png", b"first")
    drive.put("inbox/rx-002.pdf", b"second")
    drive.put("processing/")
    result = C.check(drive.connector)
    assert result["folders_created"] == ["success", "error", "quarantine", "log"]
    assert result["waiting"] == 2 and result["connector"] == drive.kind
    assert result["in_folders"] == {"processing": 0, "success": 0, "error": 0, "quarantine": 0,
                                    "log": 0}  # fmt: skip
    assert drive.names("inbox") == ["rx-001.png", "rx-002.pdf"]  # still there, untouched
    assert drive.names() == ["error", "inbox", "log", "processing", "quarantine", "success"]


def test_a_second_check_creates_nothing_more(drive):
    assert len(C.check(drive.connector)["folders_created"]) == 6
    assert C.check(drive.connector)["folders_created"] == []
    assert len(drive.names()) == 6


def test_the_check_never_downloads_or_moves_a_file(drive):
    drive.put("inbox/rx-001.png")
    C.check(drive.connector)
    if drive.fake is not None:
        assert not [c for c in drive.fake.calls if c[1].endswith("/content")]
        assert not [c for c in drive.fake.calls if c[0] == "PATCH"]


def test_only_files_are_listed_never_folders_and_every_one_of_them(drive):
    for n in range(5):  # more than one page on the cloud stand-ins
        drive.put(f"inbox/rx-{n}.png", b"x" * (n + 1))
    drive.put("inbox/a-subfolder/")
    drive.connector.ensure_folders()
    files = drive.connector.list("inbox")
    assert sorted(f.name for f in files) == [f"rx-{n}.png" for n in range(5)]
    assert all(f.folder == "inbox" and f.size == int(f.name[3]) + 1 for f in files)
    stamps = [f.modified for f in files]
    assert stamps == sorted(stamps)  # oldest first


def test_a_file_is_downloaded_as_it_was_put(drive):
    drive.put("inbox/rx-001.png", b"\x89PNG the very bytes")
    drive.connector.ensure_folders()
    (file,) = drive.connector.list("inbox")
    assert drive.connector.download(file) == b"\x89PNG the very bytes"


def test_a_file_moves_through_the_folders_and_is_never_copied_or_lost(drive):
    drive.put("inbox/rx-001.png", b"the scan")
    drive.connector.ensure_folders()
    (file,) = drive.connector.list("inbox")
    for folder in ("processing", "error", "processing", "success"):
        file = drive.connector.move(file, folder)
        assert file.folder == folder and file.name == "rx-001.png"
        assert [f.name for f in drive.connector.list(folder)] == ["rx-001.png"]
    assert drive.connector.list("inbox") == [] and drive.connector.list("processing") == []
    assert drive.connector.download(file) == b"the scan"


def test_a_move_onto_a_file_of_the_same_name_keeps_both(drive):
    """Never delete, never overwrite - on Google Drive too, which would otherwise hold two
    files under one name."""
    drive.put("success/rx-001.png", b"an earlier one")
    drive.put("inbox/rx-001.png", b"today's")
    drive.connector.ensure_folders()
    (file,) = drive.connector.list("inbox")
    moved = drive.connector.move(file, "success")
    assert moved.name == "rx-001.1.png"
    assert drive.names("success") == ["rx-001.1.png", "rx-001.png"]
    kept = {f.name: drive.connector.download(f) for f in drive.connector.list("success")}
    assert kept == {"rx-001.png": b"an earlier one", "rx-001.1.png": b"today's"}


def test_a_third_file_of_the_same_name_gets_the_next_number(drive):
    drive.put("error/rx.pdf")
    drive.put("error/rx.1.pdf")
    drive.put("inbox/rx.pdf")
    drive.connector.ensure_folders()
    (file,) = drive.connector.list("inbox")
    assert drive.connector.move(file, "error").name == "rx.2.pdf"


def test_a_file_can_be_renamed_as_it_moves(drive):
    drive.put("inbox/IMG_0001.png")
    drive.connector.ensure_folders()
    (file,) = drive.connector.list("inbox")
    assert drive.connector.move(file, "quarantine", name="rx-held.png").name == "rx-held.png"
    assert drive.names("quarantine") == ["rx-held.png"]


def test_a_note_is_written_into_log_and_never_over_another(drive):
    drive.connector.ensure_folders()
    first = drive.connector.write_note("rx-001.png.run1.log", "blurred: retake")
    second = drive.connector.write_note("rx-001.png.run1.log", "again")
    assert (first.folder, first.name) == ("log", "rx-001.png.run1.log")
    assert second.name == "rx-001.png.run1.1.log"
    notes = {f.name: drive.connector.download(f) for f in drive.connector.list("log")}
    assert notes == {"rx-001.png.run1.log": b"blurred: retake", "rx-001.png.run1.1.log": b"again"}


def test_the_root_folder_itself_can_be_the_inbox(tmp_path):
    """An empty inbox name: files are dropped straight into the watched folder, beside the
    other five."""
    for kind in KINDS:
        drive = Drive(kind, tmp_path / kind, listener_inbox="")
        drive.put("rx-001.png", b"dropped at the top")
        result = C.check(drive.connector)
        assert result["folders_created"] == ["processing", "success", "error", "quarantine", "log"]
        assert result["waiting"] == 1, kind
        (file,) = drive.connector.list("inbox")
        assert drive.connector.move(file, "success").folder == "success"
        assert "rx-001.png" not in drive.names() and drive.names("success") == ["rx-001.png"]


def test_the_folders_can_be_called_something_else(tmp_path):
    for kind in KINDS:
        drive = Drive(kind, tmp_path / kind, listener_success="completed", listener_log="notes")
        C.check(drive.connector)
        assert {"completed", "notes"} <= set(drive.names()), kind
        assert "success" not in drive.names()


def test_an_unknown_folder_is_refused_by_name(drive):
    drive.connector.ensure_folders()
    with pytest.raises(ValueError, match="unknown folder 'done'"):
        drive.connector.list("done")


def test_a_file_that_has_gone_is_said_to_be_gone(drive):
    drive.put("inbox/rx-001.png")
    drive.connector.ensure_folders()
    (file,) = drive.connector.list("inbox")
    ghost = C.RemoteFile("inbox/never-was.png" if drive.kind == "local" else "nosuchid",
                         "never-was.png", "inbox")  # fmt: skip
    with pytest.raises(C.FileGone):
        drive.connector.download(ghost)
    with pytest.raises(C.FileGone):
        drive.connector.move(ghost, "error")
    assert drive.connector.download(file) == b"x"  # the real one is unharmed


def test_each_drive_says_where_its_files_come_from(tmp_path):
    """What later stories store with every file, so a retry agent can leave another drive's
    rows alone after the setting changes (AC2's second half is theirs to prove)."""
    ids = {}
    for kind in KINDS:
        drive = Drive(kind, tmp_path / kind)
        drive.connector.ensure_folders()
        ids[kind] = drive.connector.source_id
        assert ids[kind].startswith(kind + ":")
    assert len(set(ids.values())) == len(KINDS)


# ------------------------------------------------------------------ AC1 / AC2: chosen by a setting


def test_the_setting_alone_chooses_the_drive(tmp_path):
    base = {"listener_local_root": str(tmp_path), "gdrive_folder_id": "f1", "graph_site_id": "s1"}
    for kind, cls in (
        ("local", C.LocalConnector),
        ("onedrive", C.GraphConnector),
        ("sharepoint", C.GraphConnector),
        ("gdrive", C.GoogleDriveConnector),
    ):
        made = C.get_connector(Settings(listener_connector=kind, **base))
        assert isinstance(made, cls) and made.name == kind
    assert C.get_connector(Settings(listener_connector=" GDrive ", **base)).name == "gdrive"


def test_local_is_the_default_and_no_cloud_drive_is_touched_by_default():
    assert Settings().listener_connector == "local"
    assert Settings().graph_access_token == "" and Settings().gdrive_access_token == ""


def test_changing_from_onedrive_to_local_reads_only_the_local_folder(tmp_path):
    """AC2, the part this story owns: after the switch the OneDrive stand-in is never called."""
    cloud = Drive("onedrive", tmp_path)
    cloud.put("inbox/on-onedrive.png")
    C.check(cloud.connector)
    before = len(cloud.fake.calls)

    local = Drive("local", tmp_path)
    local.put("inbox/on-this-computer.png")
    result = C.check(C.get_connector(local.settings))
    assert result["connector"] == "local" and result["waiting"] == 1
    assert [f.name for f in local.connector.list("inbox")] == ["on-this-computer.png"]
    assert len(cloud.fake.calls) == before  # not one more call to OneDrive
    assert result["source_id"] != cloud.connector.source_id


def test_gdrive_lists_the_google_folder_it_is_given(tmp_path):
    """AC1: the setting says gdrive, a folder id is given, and that folder is what is listed."""
    drive = Drive("gdrive", tmp_path)
    drive.put("inbox/from-google.png")
    connector = drive.connector
    assert [f.name for f in (connector.ensure_folders(), connector.list("inbox"))[1]] == [
        "from-google.png"
    ]
    asked = [c for c in drive.fake.calls if c == ("GET", "/drive/v3/files")]
    assert asked and connector.source_id == "gdrive:root-folder"


def test_onedrive_and_sharepoint_differ_only_in_the_drives_address(tmp_path):
    one, share = Drive("onedrive", tmp_path / "a"), Drive("sharepoint", tmp_path / "b")
    C.check(one.connector)
    C.check(share.connector)
    assert all(p.startswith("/v1.0/me/drive") for _, p in one.fake.calls)
    assert all(p.startswith("/v1.0/sites/site-9/drive") for _, p in share.fake.calls)
    strip = lambda calls, prefix: [(m, p[len(prefix) :]) for m, p in calls]  # noqa: E731
    assert [m for m, _ in strip(one.fake.calls, "/v1.0/me/drive")] == [
        m for m, _ in strip(share.fake.calls, "/v1.0/sites/site-9/drive")
    ]


@pytest.mark.parametrize(("extra", "prefix"), [
    ({"graph_drive_id": "b!abc"}, "/v1.0/drives/b!abc"),
    ({"graph_user": "desk@clinic.example"}, "/v1.0/users/desk@clinic.example/drive"),
])  # fmt: skip
def test_a_drive_id_or_a_user_names_the_drive_instead(extra, prefix):
    fake = FakeGraph(prefix[len("/v1.0"):])  # fmt: skip
    settings = Settings(listener_connector="onedrive", graph_access_token="test-token", **extra)
    connector = C.GraphConnector.from_settings(settings, transport=httpx.MockTransport(fake))
    assert len(connector.ensure_folders()) == 6


def test_a_folder_path_on_the_drive_is_the_root(tmp_path):
    fake = FakeGraph()
    fake.put("Clinic/Incoming/inbox/rx-001.png")
    settings = Settings(listener_connector="onedrive", graph_access_token="test-token",
                        listener_root="/Clinic/Incoming/")  # fmt: skip
    connector = C.GraphConnector.from_settings(settings, transport=httpx.MockTransport(fake))
    assert C.check(connector)["waiting"] == 1
    assert fake.names("Clinic/Incoming") == ["error", "inbox", "log", "processing", "quarantine",
                                             "success"]  # fmt: skip
    assert fake.names() == ["Clinic"]  # nothing was made at the drive's root


def test_a_root_folder_that_is_not_there_is_said_plainly():
    settings = Settings(listener_connector="onedrive", graph_access_token="test-token",
                        listener_root="No/Such/Folder")  # fmt: skip
    connector = C.GraphConnector.from_settings(settings, transport=httpx.MockTransport(FakeGraph()))
    with pytest.raises(C.ListenerError, match="check CDI_LISTENER_ROOT"):
        connector.ensure_folders()


def test_sharepoint_needs_to_be_told_which_site():
    with pytest.raises(C.ListenerError, match="CDI_GRAPH_SITE_ID"):
        C.get_connector(Settings(listener_connector="sharepoint"))


def test_gdrive_needs_to_be_told_which_folder():
    with pytest.raises(C.ListenerError, match="CDI_GDRIVE_FOLDER_ID"):
        C.get_connector(Settings(listener_connector="gdrive"))


def test_an_unknown_drive_names_the_ones_there_are():
    with pytest.raises(C.ListenerError, match="gdrive, local, onedrive, sharepoint"):
        C.get_connector(Settings(listener_connector="dropbox"))


# ------------------------------------------------------------------ a drive of your own


class ShelfConnector:
    """A custom drive: the five calls, nothing else. Not registered, not imported by the
    listener - named in the setting."""

    name = "shelf"

    def __init__(self, settings: Settings) -> None:
        self.files: list[C.RemoteFile] = [C.RemoteFile("1", "rx.png", "inbox")]

    @property
    def source_id(self) -> str:
        return "shelf:one"

    def ensure_folders(self) -> list[str]:
        return []

    def list(self, folder: str = "inbox") -> list[C.RemoteFile]:
        return [f for f in self.files if f.folder == folder]

    def download(self, file: C.RemoteFile) -> bytes:
        return b"shelf"

    def move(self, file: C.RemoteFile, to: str, *, name: str | None = None) -> C.RemoteFile:
        return C.RemoteFile(file.id, name or file.name, to)

    def write_note(self, name: str, text: str) -> C.RemoteFile:
        return C.RemoteFile("n", name, "log")


class NotADrive:
    def __init__(self, settings: Settings) -> None:
        pass

    def list(self, folder: str = "inbox") -> list[C.RemoteFile]:
        return []


def test_a_class_of_your_own_is_named_in_the_setting_and_no_code_changes():
    for spelling in (f"{__name__}:ShelfConnector", f"{__name__}.ShelfConnector"):
        made = C.get_connector(Settings(listener_connector=spelling))
        assert isinstance(made, ShelfConnector) and isinstance(made, C.Connector)
        assert C.check(made) == {"connector": "shelf", "source_id": "shelf:one",
                                 "folders_created": [], "waiting": 1,
                                 "in_folders": dict.fromkeys(C.FOLDERS[1:], 0)}  # fmt: skip


def test_a_class_that_cannot_do_the_five_things_is_refused_with_what_is_missing():
    with pytest.raises(C.ListenerError, match="no ensure_folders, download, move, write_note"):
        C.get_connector(Settings(listener_connector=f"{__name__}:NotADrive"))
    with pytest.raises(C.ListenerError, match="cannot load that class"):
        C.get_connector(Settings(listener_connector="no.such.module:Thing"))


def test_a_drive_can_be_registered_under_a_short_name():
    C.register("shelf", ShelfConnector)
    try:
        assert "shelf" in C.registered()
        assert isinstance(C.get_connector(Settings(listener_connector="shelf")), ShelfConnector)
    finally:
        C._REGISTRY.pop("shelf")


# ------------------------------------------------------------------ AC4: too many requests


@pytest.mark.parametrize("kind", ["onedrive", "sharepoint", "gdrive"])
def test_the_drive_says_wait_and_the_listener_waits_that_long_and_tries_again(kind, tmp_path):
    drive = Drive(kind, tmp_path)
    drive.put("inbox/rx-001.png")
    drive.fake.throttle = ["7", "2"]
    result = C.check(drive.connector)  # not a failure: the answer still comes
    assert result["waiting"] == 1
    assert drive.sleeps == [7.0, 2.0]


def test_googles_403_rate_limit_is_waited_on_like_a_429(tmp_path):
    drive = Drive("gdrive", tmp_path)
    drive.fake.rate_limit_403 = 2
    assert len(drive.connector.ensure_folders()) == 6
    assert drive.sleeps == [5.0, 5.0]  # it gave no wait time: the default


def test_a_drive_that_never_stops_saying_wait_is_reported_not_waited_on_for_ever(tmp_path):
    drive = Drive("onedrive", tmp_path, listener_max_wait_seconds=100)
    drive.fake.throttle = ["60"] * 10
    with pytest.raises(C.Throttled, match="too many requests"):
        drive.connector.ensure_folders()
    assert drive.sleeps == [60.0]  # one wait; a second would pass the 100 s allowed


def test_retry_after_reads_seconds_a_date_or_nothing():
    def answer(value: str | None) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": value} if value else {})

    assert C.retry_after(answer("12")) == 12.0
    assert C.retry_after(answer(None)) == 5.0
    assert C.retry_after(answer("soon")) == 5.0
    at = "Wed, 07 Oct 2026 10:00:30 GMT"
    assert C.retry_after(answer(at), now=lambda: 1791367200.0) == pytest.approx(30.0)
    assert C.retry_after(answer(at), now=lambda: 1791369999.0) == 0.0  # already past


def test_a_forbidden_answer_that_is_not_a_rate_limit_is_not_waited_on(tmp_path):
    def forbidden(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": {"errors": [{"reason": "insufficientRights"}]}})

    settings = Settings(gdrive_access_token="test-token", gdrive_folder_id="f")
    sleeps: list[float] = []
    connector = C.GoogleDriveConnector.from_settings(
        settings, transport=httpx.MockTransport(forbidden), sleep=sleeps.append)  # fmt: skip
    with pytest.raises(C.ListenerError, match="403"):
        connector.ensure_folders()
    assert sleeps == []


# ------------------------------------------------------------------ signing in is LS-S2


@pytest.mark.parametrize(("kind", "setting"), [("onedrive", "CDI_GRAPH_ACCESS_TOKEN"),
                                               ("gdrive", "CDI_GDRIVE_ACCESS_TOKEN")])  # fmt: skip
def test_no_sign_in_is_said_in_plain_words(kind, setting):
    settings = Settings(listener_connector=kind, gdrive_folder_id="f")
    with pytest.raises(C.SignInRequired, match=setting):
        C.get_connector(settings).ensure_folders()


def test_an_expired_sign_in_is_said_in_plain_words(tmp_path):
    drive = Drive("onedrive", tmp_path)
    drive.fake.unauthorised = True
    with pytest.raises(C.SignInRequired, match="missing or has expired"):
        drive.connector.ensure_folders()


def test_the_token_is_asked_for_on_every_call_so_a_renewed_one_is_used(tmp_path):
    """LS-S2 will plug its renewing sign-in in here: any callable that returns a token."""
    fake, asked = FakeGraph(), []

    def token() -> str:
        asked.append(1)
        return "test-token"

    connector = C.GraphConnector.from_settings(Settings(), token=token,
                                               transport=httpx.MockTransport(fake))  # fmt: skip
    connector.ensure_folders()
    assert len(asked) == len(fake.calls) > 1


def test_no_token_is_shown_when_settings_are_printed():
    shown = repr(Settings(graph_access_token="s3cret-graph", gdrive_access_token="s3cret-drive"))
    assert "s3cret" not in shown


# ------------------------------------------------------------------ the check command


def _run(monkeypatch, capsys, root: Path, *args: str, **env: str) -> tuple[int, str, str]:
    from cdi_adapter.config import get_settings

    monkeypatch.setenv("CDI_LISTENER_LOCAL_ROOT", str(root))
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    try:
        code = cli.main(["check", *args])
    finally:
        get_settings.cache_clear()
    out = capsys.readouterr()
    return code, out.out, out.err


def test_the_check_command_reports_and_processes_nothing(monkeypatch, capsys, tmp_path):
    (tmp_path / "inbox").mkdir()
    (tmp_path / "inbox" / "rx-001.png").write_bytes(b"scan")
    code, out, _ = _run(monkeypatch, capsys, tmp_path, CDI_LISTENER_CONNECTOR="local")
    assert code == 0
    assert "drive        local" in out and "waiting      1 file(s) in the inbox" in out
    assert "created: processing, success, error, quarantine, log" in out
    assert "nothing was processed" in out
    assert (tmp_path / "inbox" / "rx-001.png").read_bytes() == b"scan"


def test_the_check_command_can_answer_in_json(monkeypatch, capsys, tmp_path):
    code, out, _ = _run(monkeypatch, capsys, tmp_path, "--json", CDI_LISTENER_CONNECTOR="local")
    result = json.loads(out)
    assert code == 0 and result["waiting"] == 0 and len(result["folders_created"]) == 6


def test_the_check_command_says_when_sign_in_is_missing(monkeypatch, capsys, tmp_path):
    code, out, err = _run(monkeypatch, capsys, tmp_path, CDI_LISTENER_CONNECTOR="onedrive",
                          CDI_GRAPH_ACCESS_TOKEN="")  # fmt: skip
    assert code == 2 and out == "" and err.startswith("SIGN-IN NEEDED:")


def test_the_check_command_says_when_the_setting_is_wrong(monkeypatch, capsys, tmp_path):
    code, _, err = _run(monkeypatch, capsys, tmp_path, CDI_LISTENER_CONNECTOR="dropbox")
    assert code == 1 and err.startswith("NOT READY:") and "dropbox" in err


# ------------------------------------------------------------------ the two example settings files


def _settings_file(name: str) -> dict[str, str]:
    text = (Path(__file__).resolve().parents[1] / name).read_text(encoding="utf-8")
    pairs = [ln.split("=", 1) for ln in text.splitlines() if ln and not ln.startswith("#")]
    return {k.strip(): v.strip() for k, v in pairs}


def test_the_two_example_files_differ_only_in_the_drives_own_settings():
    one, google = _settings_file(".env.onedrive.example"), _settings_file(".env.gdrive.example")
    assert one["CDI_LISTENER_CONNECTOR"] == "onedrive"
    assert google["CDI_LISTENER_CONNECTOR"] == "gdrive"
    shared = set(one) & set(google)
    assert {k for k in shared if one[k] != google[k]} == {"CDI_LISTENER_CONNECTOR"}
    only = (set(one) | set(google)) - shared
    assert only == {"CDI_LISTENER_ROOT", "CDI_GRAPH_DRIVE_ID", "CDI_GRAPH_USER",
                    "CDI_GRAPH_SITE_ID", "CDI_GDRIVE_FOLDER_ID"}  # fmt: skip


def test_the_example_files_hold_no_secret_and_only_settings_that_exist():
    known = {"CDI_" + name.upper() for name in Settings.model_fields}
    for name in (".env.onedrive.example", ".env.gdrive.example"):
        values = _settings_file(name)
        assert set(values) <= known, set(values) - known
        assert not [k for k in values if "TOKEN" in k or "SECRET" in k or "PASSWORD" in k]


def test_the_old_folder_watcher_is_left_as_it_was():
    """LS-S1 adds a listener beside ``cdi-ingest-watch``; it does not replace or change it."""
    from cdi_adapter.ingest import watcher

    source = Path(watcher.__file__).read_text(encoding="utf-8")
    assert "cdi_adapter.listener" not in source and "CDI_INBOX_DIR" in source
    scripts = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    assert 'cdi-ingest-watch = "cdi_adapter.ingest.watcher:main"' in scripts
    assert 'cdi-listener = "cdi_adapter.listener.__main__:main"' in scripts
