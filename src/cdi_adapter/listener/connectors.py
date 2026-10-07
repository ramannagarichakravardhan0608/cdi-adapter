"""Watch a folder on OneDrive, SharePoint, Google Drive or this computer (backlog LS-S1).

The listener watches one folder where prescription pictures and PDFs are dropped. **Which drive
that folder lives on is a setting** - ``CDI_LISTENER_CONNECTOR`` = ``onedrive``, ``sharepoint``,
``gdrive``, ``local``, or the dotted name of a class of your own - and nothing else in the
listener knows the difference. Only one drive is active at a time: :func:`get_connector` builds
the one the setting names.

Every drive holds the same six folders, by the same logical names (:data:`FOLDERS`):

==============  =============================================================================
``inbox``       where files are dropped - or the root folder itself, when ``CDI_LISTENER_INBOX``
                is empty
``processing``  a file being read
``success``     read and saved
``error``       failed, will be tried again
``quarantine``  failed for good: a person must look
``log``         one note per failed run
==============  =============================================================================

What a drive has to be able to do is :class:`Connector`: ``ensure_folders``, ``list``,
``download``, ``move`` and ``write_note``. A new kind of drive is a class with those five and a
line in settings; the three here are :class:`LocalConnector`, :class:`GraphConnector` (OneDrive
and SharePoint are the same Microsoft Graph API, reached through a different drive address) and
:class:`GoogleDriveConnector`.

**Nothing is ever deleted and nothing is ever overwritten.** A move that meets a file of the
same name in the target folder keeps both (``scan.pdf`` then ``scan.1.pdf``), on every drive -
including Google Drive, which would otherwise happily hold two files with one name.

**"Too many requests" is not a failure.** When a drive answers 429 (or 503, or Google's 403
"rate limit exceeded") the call waits as long as the drive asks (``Retry-After``) and is made
again; only when the total wait would pass ``CDI_LISTENER_MAX_WAIT_SECONDS`` does
:class:`Throttled` reach the caller.

**Which drive a file came from** is :attr:`Connector.source_id` (``local:/data/inbox``,
``onedrive:<drive id>:<folder id>``, ``gdrive:<folder id>``). Later stories store it with every
file they track, which is what lets a retry agent leave another drive's rows alone after the
setting is changed.

**Signing in is LS-S2, not this story.** The two cloud connectors ask a *token source* - any
callable returning a bearer token - for each call. Until LS-S2 builds the real sign-in, the token
source reads ``CDI_GRAPH_ACCESS_TOKEN`` / ``CDI_GDRIVE_ACCESS_TOKEN`` (a token pasted in for a
test run) and raises :class:`SignInRequired`, in plain words, when there is none.

Both cloud connectors speak plain HTTPS through ``httpx``, which the project already depends on:
no Microsoft or Google SDK is installed, so there is no extra to install for either.
"""

from __future__ import annotations

import importlib
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path, PurePosixPath
from typing import Any, Protocol, runtime_checkable

import httpx

from cdi_adapter.config import Settings, get_settings
from cdi_adapter.logging import get_logger

log = get_logger(__name__)

FOLDERS = ("inbox", "processing", "success", "error", "quarantine", "log")
TokenSource = Callable[[], str]
_GOOGLE_FOLDER = "application/vnd.google-apps.folder"
_DEFAULT_WAIT = 5.0  # a drive that throttles without saying for how long
_TIMEOUT = 60.0


class ListenerError(RuntimeError):
    """Something the operator has to put right before the listener can run."""


class SignInRequired(ListenerError):
    """The drive needs a sign-in that has not been done, or has expired (LS-S2)."""


class Throttled(ListenerError):
    """The drive kept answering "too many requests" for longer than we are allowed to wait."""


class FileGone(ListenerError):
    """The file is no longer where it was listed: someone moved or removed it."""


@dataclass(frozen=True)
class RemoteFile:
    """One file on a drive, as the listener sees it."""

    id: str  # the drive's own handle for it (a path, on this computer)
    name: str
    folder: str  # the logical folder it was listed in
    size: int | None = None
    modified: datetime | None = None
    fingerprint: str | None = None  # the drive's own hash or version tag, when it gives one


@runtime_checkable
class Connector(Protocol):
    """What the listener needs from a drive. Five calls and two names."""

    name: str  # what the setting called it

    @property
    def source_id(self) -> str: ...

    def ensure_folders(self) -> list[str]:
        """Find the six folders, creating any that are missing; returns the logical names of
        the ones it had to create. Reads no file."""
        ...

    def list(self, folder: str = "inbox") -> list[RemoteFile]:
        """The files (never the sub-folders) in one logical folder, oldest first."""
        ...

    def download(self, file: RemoteFile) -> bytes: ...

    def move(self, file: RemoteFile, to: str, *, name: str | None = None) -> RemoteFile:
        """Move a file to another logical folder, keeping any file already there by that name."""
        ...

    def write_note(self, name: str, text: str) -> RemoteFile:
        """Write a small text note into ``log``."""
        ...


# ------------------------------------------------------------------ shared pieces


def folder_names(settings: Settings) -> dict[str, str]:
    """Logical folder -> what it is called on the drive. An empty inbox name means the root
    folder itself is the inbox."""
    return {
        "inbox": settings.listener_inbox.strip().strip("/"),
        "processing": settings.listener_processing,
        "success": settings.listener_success,
        "error": settings.listener_error,
        "quarantine": settings.listener_quarantine,
        "log": settings.listener_log,
    }


def _check_folder(folder: str) -> str:
    if folder not in FOLDERS:
        raise ValueError(f"unknown folder {folder!r}: one of {', '.join(FOLDERS)}")
    return folder


def unique_name(name: str, taken: set[str]) -> str:
    """``name``, or ``stem.1.ext``, ``stem.2.ext`` ... - the first not already taken. Compared
    without regard to case, as OneDrive and Windows compare names."""
    lowered = {t.lower() for t in taken}
    if name.lower() not in lowered:
        return name
    stem, suffix = PurePosixPath(name).stem, PurePosixPath(name).suffix
    n = 1
    while f"{stem}.{n}{suffix}".lower() in lowered:
        n += 1
    return f"{stem}.{n}{suffix}"


def _oldest_first(files: list[RemoteFile]) -> list[RemoteFile]:
    return sorted(files, key=lambda f: (f.modified or datetime.min.replace(tzinfo=UTC), f.name))


def _when(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def retry_after(response: httpx.Response, now: Callable[[], float] = time.time) -> float:
    """How long the drive asked us to wait: ``Retry-After`` in seconds or as a date; a default
    when it throttles without saying."""
    raw = (response.headers.get("Retry-After") or "").strip()
    if not raw:
        return _DEFAULT_WAIT
    try:
        return max(0.0, float(raw))
    except ValueError:
        pass
    try:
        return max(0.0, parsedate_to_datetime(raw).timestamp() - now())
    except (TypeError, ValueError):
        return _DEFAULT_WAIT


def _throttled(response: httpx.Response) -> bool:
    if response.status_code in (429, 503):
        return True
    if response.status_code == 403:  # Google says "slow down" with a 403 and a reason
        try:
            errors = response.json().get("error", {}).get("errors", [])
        except ValueError:
            return False
        return any(e.get("reason") in ("rateLimitExceeded", "userRateLimitExceeded")
                   for e in errors if isinstance(e, dict))  # fmt: skip
    return False


class _Http:
    """One drive's HTTPS calls: the bearer token on every request, and the waiting when the
    drive says "too many requests"."""

    def __init__(self, base_url: str, token: TokenSource, *, max_wait: float,
                 transport: httpx.BaseTransport | None = None,
                 sleep: Callable[[float], None] = time.sleep) -> None:  # fmt: skip
        self._client = httpx.Client(base_url=base_url.rstrip("/"), timeout=_TIMEOUT,
                                    transport=transport, follow_redirects=True)  # fmt: skip
        self._token, self._max_wait, self._sleep = token, max_wait, sleep

    def send(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        waited = 0.0
        while True:
            headers = {"Authorization": f"Bearer {self._token()}", **kwargs.pop("headers", {})}
            response = self._client.request(method, url, headers=headers, **kwargs)
            if not _throttled(response):
                break
            wait = retry_after(response)
            if waited + wait > self._max_wait:
                raise Throttled(f"the drive is still saying 'too many requests' after waiting "
                                f"{waited:.0f} s; it asked for {wait:.0f} s more")  # fmt: skip
            log.warning("drive_throttled", wait_seconds=wait, url=str(response.request.url.path))
            self._sleep(wait)
            waited += wait
        if response.status_code == 401:
            raise SignInRequired("the drive refused the sign-in: it is missing or has expired. "
                                 "Sign in again (LS-S2) and re-run the check.")  # fmt: skip
        return response

    def json(self, method: str, url: str, *, ok: tuple[int, ...] = (200, 201),
             **kwargs: Any) -> dict[str, Any]:  # fmt: skip
        response = self.send(method, url, **kwargs)
        if response.status_code not in ok:
            raise ListenerError(f"{method} {response.request.url.path} answered "
                                f"{response.status_code}: {response.text[:200]}")  # fmt: skip
        return response.json() if response.content else {}


def _static_token(value: str, what: str, setting: str) -> TokenSource:
    """The stand-in token source until LS-S2: a token from settings, or a plain refusal."""

    def token() -> str:
        if not value:
            raise SignInRequired(f"no sign-in for {what}: sign in (LS-S2), or for a test run "
                                 f"put a bearer token in {setting}")  # fmt: skip
        return value

    return token


# ------------------------------------------------------------------ this computer


class LocalConnector:
    """A folder on this computer (or a share mounted on it)."""

    name = "local"

    def __init__(self, root: str | Path, names: dict[str, str]) -> None:
        self.root, self._names = Path(root), names

    @classmethod
    def from_settings(cls, settings: Settings) -> LocalConnector:
        return cls(settings.listener_local_root, folder_names(settings))

    @property
    def source_id(self) -> str:
        return f"local:{self.root.resolve().as_posix()}"

    def _dir(self, folder: str) -> Path:
        name = self._names[_check_folder(folder)]
        return self.root / name if name else self.root

    def ensure_folders(self) -> list[str]:
        created = []
        self.root.mkdir(parents=True, exist_ok=True)
        for folder in FOLDERS:
            path = self._dir(folder)
            if not path.is_dir():
                path.mkdir(parents=True)
                created.append(folder)
        return created

    def list(self, folder: str = "inbox") -> list[RemoteFile]:
        path = self._dir(folder)
        if not path.is_dir():
            return []
        out = []
        for entry in path.iterdir():
            if entry.is_file():
                stat = entry.stat()
                out.append(RemoteFile(entry.relative_to(self.root).as_posix(), entry.name, folder,
                                      stat.st_size,
                                      datetime.fromtimestamp(stat.st_mtime, tz=UTC)))  # fmt: skip
        return _oldest_first(out)

    def _path(self, file: RemoteFile) -> Path:
        path = self.root / file.id
        if not path.is_file():
            raise FileGone(f"{file.name} is no longer in {file.folder}")
        return path

    def download(self, file: RemoteFile) -> bytes:
        return self._path(file).read_bytes()

    def move(self, file: RemoteFile, to: str, *, name: str | None = None) -> RemoteFile:
        src, target = self._path(file), self._dir(to)
        target.mkdir(parents=True, exist_ok=True)
        dest = target / unique_name(name or file.name, {p.name for p in target.iterdir()})
        src.rename(dest)  # a rename: the bytes are never copied and never lost half-way
        return RemoteFile(dest.relative_to(self.root).as_posix(), dest.name, to, file.size,
                          file.modified, file.fingerprint)  # fmt: skip

    def write_note(self, name: str, text: str) -> RemoteFile:
        target = self._dir("log")
        target.mkdir(parents=True, exist_ok=True)
        dest = target / unique_name(name, {p.name for p in target.iterdir()})
        dest.write_text(text, encoding="utf-8")
        return RemoteFile(dest.relative_to(self.root).as_posix(), dest.name, "log",
                          len(text.encode("utf-8")), datetime.now(tz=UTC))  # fmt: skip


# ------------------------------------------------------------------ OneDrive and SharePoint


class GraphConnector:
    """OneDrive and SharePoint: one Microsoft Graph API, two ways of naming the drive.

    ``drive`` is the drive's address under Graph: ``/me/drive`` (the signed-in person's
    OneDrive), ``/users/<name>/drive`` (someone's OneDrive, for an app-only sign-in),
    ``/sites/<site id>/drive`` (a SharePoint site's document library) or ``/drives/<id>``.
    ``root_path`` is the folder inside it that holds the six folders ("" = the drive's root).
    """

    def __init__(self, name: str, drive: str, root_path: str, names: dict[str, str],
                 token: TokenSource, *, base_url: str, max_wait: float,
                 transport: httpx.BaseTransport | None = None,
                 sleep: Callable[[float], None] = time.sleep) -> None:  # fmt: skip
        self.name, self._drive, self._names = name, "/" + drive.strip("/"), names
        self._root_path = root_path.strip().strip("/")
        self._http = _Http(base_url, token, max_wait=max_wait, transport=transport, sleep=sleep)
        self._ids: dict[str, str] = {}  # logical folder -> item id
        self._drive_id = ""

    @classmethod
    def from_settings(cls, settings: Settings, *, name: str = "onedrive",
                      token: TokenSource | None = None,
                      transport: httpx.BaseTransport | None = None,
                      sleep: Callable[[float], None] = time.sleep) -> GraphConnector:  # fmt: skip
        if settings.graph_drive_id:
            drive = f"/drives/{settings.graph_drive_id}"
        elif name == "sharepoint":
            if not settings.graph_site_id:
                raise ListenerError("sharepoint needs CDI_GRAPH_SITE_ID (or CDI_GRAPH_DRIVE_ID)")
            drive = f"/sites/{settings.graph_site_id}/drive"
        elif settings.graph_user:
            drive = f"/users/{settings.graph_user}/drive"
        else:
            drive = "/me/drive"
        token = token or _static_token(settings.graph_access_token, "Microsoft Graph",
                                       "CDI_GRAPH_ACCESS_TOKEN")  # fmt: skip
        return cls(name, drive, settings.listener_root, folder_names(settings), token,
                   base_url=settings.graph_base_url, max_wait=settings.listener_max_wait_seconds,
                   transport=transport, sleep=sleep)  # fmt: skip

    @property
    def source_id(self) -> str:
        self._resolve()
        return f"{self.name}:{self._drive_id}:{self._ids['root']}"

    def _resolve(self, *, create: bool = False) -> list[str]:
        """Find the root folder and the six folders in it; with ``create`` make the missing."""
        if "root" not in self._ids:
            where = f"{self._drive}/root:/{self._root_path}" if self._root_path else (
                f"{self._drive}/root")  # fmt: skip
            response = self._http.send("GET", where)
            if response.status_code == 404:
                raise ListenerError(f"the folder '{self._root_path}' was not found on the drive: "
                                    "check CDI_LISTENER_ROOT")  # fmt: skip
            if response.status_code != 200:
                raise ListenerError(f"the drive answered {response.status_code}: "
                                    f"{response.text[:200]}")  # fmt: skip
            item = response.json()
            self._ids["root"] = item["id"]
            self._drive_id = (item.get("parentReference") or {}).get("driveId") or self._drive
        have = {i["name"].lower(): i["id"] for i in self._children(self._ids["root"])
                if "folder" in i}  # fmt: skip
        created = []
        for folder in FOLDERS:
            name = self._names[folder]
            if not name:
                self._ids[folder] = self._ids["root"]  # the root itself is the inbox
            elif name.lower() in have:
                self._ids[folder] = have[name.lower()]
            elif create:
                made = self._http.json(
                    "POST", f"{self._drive}/items/{self._ids['root']}/children",
                    json={"name": name, "folder": {},
                          "@microsoft.graph.conflictBehavior": "fail"})  # fmt: skip
                self._ids[folder] = made["id"]
                created.append(folder)
        return created

    def _children(self, item_id: str) -> Iterator[dict[str, Any]]:
        url: str | None = (
            f"{self._drive}/items/{item_id}/children"
            "?$select=id,name,size,file,folder,lastModifiedDateTime,eTag&$top=200"
        )
        while url:
            page = self._http.json("GET", url)
            yield from page.get("value", [])
            url = page.get("@odata.nextLink")

    def _folder_id(self, folder: str) -> str:
        _check_folder(folder)
        if folder not in self._ids:
            self._resolve()
        if folder not in self._ids:
            raise ListenerError(f"the '{self._names[folder]}' folder does not exist on the "
                                "drive yet: run the check command to create it")  # fmt: skip
        return self._ids[folder]

    def ensure_folders(self) -> list[str]:
        return self._resolve(create=True)

    def list(self, folder: str = "inbox") -> list[RemoteFile]:
        out = [RemoteFile(i["id"], i["name"], folder, i.get("size"),
                          _when(i.get("lastModifiedDateTime")), i.get("eTag"))
               for i in self._children(self._folder_id(folder)) if "folder" not in i]  # fmt: skip
        return _oldest_first(out)

    def download(self, file: RemoteFile) -> bytes:
        response = self._http.send("GET", f"{self._drive}/items/{file.id}/content")
        if response.status_code == 404:
            raise FileGone(f"{file.name} is no longer in {file.folder}")
        if response.status_code != 200:
            raise ListenerError(f"downloading {file.name} answered {response.status_code}")
        return response.content

    def move(self, file: RemoteFile, to: str, *, name: str | None = None) -> RemoteFile:
        target = self._folder_id(to)
        taken = {i["name"] for i in self._children(target)}
        for _ in range(20):  # another writer may take the name between our look and our move
            wanted = unique_name(name or file.name, taken)
            response = self._http.send(
                "PATCH", f"{self._drive}/items/{file.id}",
                json={"parentReference": {"id": target}, "name": wanted,
                      "@microsoft.graph.conflictBehavior": "fail"})  # fmt: skip
            if response.status_code == 409:
                taken.add(wanted)
                continue
            if response.status_code == 404:
                raise FileGone(f"{file.name} is no longer in {file.folder}")
            if response.status_code != 200:
                raise ListenerError(f"moving {file.name} answered {response.status_code}: "
                                    f"{response.text[:200]}")  # fmt: skip
            item = response.json()
            return RemoteFile(item["id"], item["name"], to, item.get("size", file.size),
                              _when(item.get("lastModifiedDateTime")) or file.modified,
                              item.get("eTag"))  # fmt: skip
        raise ListenerError(f"could not find a free name for {file.name} in {to}")

    def write_note(self, name: str, text: str) -> RemoteFile:
        target = self._folder_id("log")
        taken = {i["name"] for i in self._children(target)}
        for _ in range(20):
            wanted = unique_name(name, taken)
            response = self._http.send(
                "PUT", f"{self._drive}/items/{target}:/{wanted}:/content"
                       "?@microsoft.graph.conflictBehavior=fail",
                content=text.encode("utf-8"), headers={"Content-Type": "text/plain"})  # fmt: skip
            if response.status_code == 409:
                taken.add(wanted)
                continue
            if response.status_code not in (200, 201):
                raise ListenerError(f"writing the note answered {response.status_code}")
            item = response.json()
            return RemoteFile(
                item["id"],
                item["name"],
                "log",
                item.get("size"),
                _when(item.get("lastModifiedDateTime")),
                item.get("eTag"),
            )
        raise ListenerError(f"could not find a free name for the note {name}")


# ------------------------------------------------------------------ Google Drive


class GoogleDriveConnector:
    """A Google Drive folder, named by its id (the last part of the folder's web address).

    Drive has no paths and lets two files share a name, so the six folders are found by name
    among the root folder's children and a move looks at the target folder first.
    """

    name = "gdrive"

    def __init__(self, folder_id: str, names: dict[str, str], token: TokenSource, *,
                 base_url: str, max_wait: float, transport: httpx.BaseTransport | None = None,
                 sleep: Callable[[float], None] = time.sleep) -> None:  # fmt: skip
        if not folder_id:
            raise ListenerError("gdrive needs CDI_GDRIVE_FOLDER_ID: the id of the Drive folder")
        self._root, self._names = folder_id, names
        self._http = _Http(base_url, token, max_wait=max_wait, transport=transport, sleep=sleep)
        self._ids: dict[str, str] = {}

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        token: TokenSource | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> GoogleDriveConnector:
        token = token or _static_token(settings.gdrive_access_token, "Google Drive",
                                       "CDI_GDRIVE_ACCESS_TOKEN")  # fmt: skip
        return cls(settings.gdrive_folder_id, folder_names(settings), token,
                   base_url=settings.gdrive_base_url,
                   max_wait=settings.listener_max_wait_seconds, transport=transport,
                   sleep=sleep)  # fmt: skip

    @property
    def source_id(self) -> str:
        return f"gdrive:{self._root}"

    def _children(self, folder_id: str) -> Iterator[dict[str, Any]]:
        params = {"q": f"'{folder_id}' in parents and trashed = false", "pageSize": "200",
                  "fields": "nextPageToken,files(id,name,mimeType,size,modifiedTime,md5Checksum)",
                  "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"}  # fmt: skip
        while True:
            response = self._http.send("GET", "/drive/v3/files", params=params)
            if response.status_code == 404:
                raise ListenerError("the Google Drive folder was not found, or is not shared "
                                    "with this sign-in: check CDI_GDRIVE_FOLDER_ID")  # fmt: skip
            if response.status_code != 200:
                raise ListenerError(f"Google Drive answered {response.status_code}: "
                                    f"{response.text[:200]}")  # fmt: skip
            page = response.json()
            yield from page.get("files", [])
            if not page.get("nextPageToken"):
                return
            params = {**params, "pageToken": page["nextPageToken"]}

    def _resolve(self, *, create: bool = False) -> list[str]:
        have = {f["name"].lower(): f["id"] for f in self._children(self._root)
                if f.get("mimeType") == _GOOGLE_FOLDER}  # fmt: skip
        created = []
        for folder in FOLDERS:
            name = self._names[folder]
            if not name:
                self._ids[folder] = self._root
            elif name.lower() in have:
                self._ids[folder] = have[name.lower()]
            elif create:
                made = self._http.json(
                    "POST",
                    "/drive/v3/files",
                    params={"supportsAllDrives": "true"},
                    json={"name": name, "mimeType": _GOOGLE_FOLDER, "parents": [self._root]},
                )
                self._ids[folder] = made["id"]
                created.append(folder)
        return created

    def _folder_id(self, folder: str) -> str:
        _check_folder(folder)
        if folder not in self._ids:
            self._resolve()
        if folder not in self._ids:
            raise ListenerError(f"the '{self._names[folder]}' folder does not exist on the "
                                "drive yet: run the check command to create it")  # fmt: skip
        return self._ids[folder]

    def ensure_folders(self) -> list[str]:
        return self._resolve(create=True)

    def list(self, folder: str = "inbox") -> list[RemoteFile]:
        out = [RemoteFile(f["id"], f["name"], folder, int(f["size"]) if f.get("size") else None,
                          _when(f.get("modifiedTime")), f.get("md5Checksum"))
               for f in self._children(self._folder_id(folder))
               if f.get("mimeType") != _GOOGLE_FOLDER]  # fmt: skip
        return _oldest_first(out)

    def download(self, file: RemoteFile) -> bytes:
        response = self._http.send(
            "GET",
            f"/drive/v3/files/{file.id}",
            params={"alt": "media", "supportsAllDrives": "true"},
        )
        if response.status_code == 404:
            raise FileGone(f"{file.name} is no longer in {file.folder}")
        if response.status_code != 200:
            raise ListenerError(f"downloading {file.name} answered {response.status_code}")
        return response.content

    def move(self, file: RemoteFile, to: str, *, name: str | None = None) -> RemoteFile:
        source, target = self._folder_id(file.folder), self._folder_id(to)
        # Drive would keep two files of one name without a word: look first, so both are kept
        # under names a person can tell apart
        wanted = unique_name(name or file.name, {f["name"] for f in self._children(target)})
        response = self._http.send(
            "PATCH", f"/drive/v3/files/{file.id}",
            params={"addParents": target, "removeParents": source, "supportsAllDrives": "true",
                    "fields": "id,name,size,modifiedTime,md5Checksum"},
            json={"name": wanted})  # fmt: skip
        if response.status_code == 404:
            raise FileGone(f"{file.name} is no longer in {file.folder}")
        if response.status_code != 200:
            raise ListenerError(f"moving {file.name} answered {response.status_code}: "
                                f"{response.text[:200]}")  # fmt: skip
        f = response.json()
        return RemoteFile(f["id"], f["name"], to, int(f["size"]) if f.get("size") else file.size,
                          _when(f.get("modifiedTime")) or file.modified,
                          f.get("md5Checksum") or file.fingerprint)  # fmt: skip

    def write_note(self, name: str, text: str) -> RemoteFile:
        target = self._folder_id("log")
        wanted = unique_name(name, {f["name"] for f in self._children(target)})
        made = self._http.json("POST", "/drive/v3/files", params={"supportsAllDrives": "true"},
                               json={"name": wanted, "mimeType": "text/plain",
                                     "parents": [target]})  # fmt: skip
        body = text.encode("utf-8")
        self._http.json("PATCH", f"/upload/drive/v3/files/{made['id']}",
                        params={"uploadType": "media", "supportsAllDrives": "true"},
                        content=body, headers={"Content-Type": "text/plain"})  # fmt: skip
        return RemoteFile(made["id"], wanted, "log", len(body), datetime.now(tz=UTC))


# ------------------------------------------------------------------ chosen by a setting


Factory = Callable[[Settings], Connector]
_REGISTRY: dict[str, Factory] = {}


def register(name: str, factory: Factory) -> None:
    """Make a drive available under ``name`` for ``CDI_LISTENER_CONNECTOR``."""
    _REGISTRY[name.strip().lower()] = factory


def registered() -> list[str]:
    return sorted(_REGISTRY)


register("local", LocalConnector.from_settings)
register("onedrive", lambda s: GraphConnector.from_settings(s, name="onedrive"))
register("sharepoint", lambda s: GraphConnector.from_settings(s, name="sharepoint"))
register("gdrive", GoogleDriveConnector.from_settings)


def get_connector(settings: Settings | None = None) -> Connector:
    """The one drive the settings name. A built-in name, a registered one, or a class of your
    own given as ``package.module:Class`` (built with ``from_settings(settings)`` if it has
    one, else with ``Class(settings)``)."""
    s = settings or get_settings()
    wanted = s.listener_connector.strip()
    factory = _REGISTRY.get(wanted.lower())
    if factory is not None:
        return factory(s)
    if ":" in wanted or "." in wanted:
        module, _, attr = wanted.replace(":", ".").rpartition(".")
        try:
            cls = getattr(importlib.import_module(module), attr)
        except (ImportError, AttributeError) as exc:
            raise ListenerError(f"CDI_LISTENER_CONNECTOR={wanted!r}: cannot load that class "
                                f"({exc})") from exc  # fmt: skip
        made = cls.from_settings(s) if hasattr(cls, "from_settings") else cls(s)
        missing = [m for m in ("ensure_folders", "list", "download", "move", "write_note")
                   if not callable(getattr(made, m, None))]  # fmt: skip
        if missing:
            raise ListenerError(f"{wanted} is not a drive the listener can use: it has no "
                                f"{', '.join(missing)}")  # fmt: skip
        return made  # type: ignore[no-any-return]
    raise ListenerError(f"CDI_LISTENER_CONNECTOR={wanted!r} is not a known drive: use one of "
                        f"{', '.join(registered())}, or package.module:Class")  # fmt: skip


def check(connector: Connector) -> dict[str, Any]:
    """The check command's work: sign in, find the folders (creating the missing), and count
    what is waiting. **Reads no file and moves none.**"""
    created = connector.ensure_folders()
    return {
        "connector": connector.name,
        "source_id": connector.source_id,
        "folders_created": created,
        "waiting": len(connector.list("inbox")),
        "in_folders": {f: len(connector.list(f)) for f in FOLDERS if f != "inbox"},
    }
