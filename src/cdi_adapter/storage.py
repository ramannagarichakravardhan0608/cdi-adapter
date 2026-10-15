"""Object storage for the irreplaceable bytes: original scans + rendered pages (ARCHITECTURE §3.3).

MinIO (S3-compatible) through boto3, pointed at ``CDI_S3_ENDPOINT_URL``. With a MinIO cluster
(backlog E10-S6) that setting lists every node, comma-separated: a node that stops answering is
skipped and the next one takes the call, so losing one node costs a retry, never a request.
Key layout (§5 S1):

    documents/<sha[:2]>/<sha>/original.<ext>
    documents/<sha[:2]>/<sha>/pages/NNNN.png
    documents/<sha[:2]>/<sha>/pages/NNNN.thumb.png
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable, Iterator
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol, cast

import boto3
from botocore.client import Config
from botocore.exceptions import (
    ClientError,
    ConnectionClosedError,
    ConnectTimeoutError,
    EndpointConnectionError,
    ReadTimeoutError,
)

from cdi_adapter.config import Settings, get_settings


def _prefix(sha256: str) -> str:
    if len(sha256) != 64 or any(c not in "0123456789abcdef" for c in sha256):
        raise ValueError("sha256 must be 64 lowercase hex characters")
    return f"documents/{sha256[:2]}/{sha256}"


def original_key(sha256: str, ext: str) -> str:
    return f"{_prefix(sha256)}/original.{ext.lstrip('.').lower()}"


def page_key(sha256: str, page_no: int) -> str:
    return f"{_prefix(sha256)}/pages/{page_no:04d}.png"


def thumb_key(sha256: str, page_no: int) -> str:
    return f"{_prefix(sha256)}/pages/{page_no:04d}.thumb.png"


# a node that is gone or cannot serve (lost quorum answers 503): try the next node
_NODE_DOWN = (EndpointConnectionError, ConnectTimeoutError, ReadTimeoutError, ConnectionClosedError)


def _node_down(exc: Exception) -> bool:
    if isinstance(exc, _NODE_DOWN):
        return True
    return isinstance(exc, ClientError) and exc.response.get("ResponseMetadata", {}).get(
        "HTTPStatusCode"
    ) in (502, 503, 504)


class Store(Protocol):
    """The object-store interface (SW-S1): everything the rest of the product asks of the
    place the scans are kept. An adapter implements exactly this; nothing outside its adapter
    names its library. ``docs/interfaces.md`` has the contract; ``tests/contracts.py`` the test
    every adapter must pass.

    * a key is a ``/``-separated path; bytes go in and come out unchanged;
    * ``put_bytes`` replaces; ``get_bytes`` of a key that is not there raises;
    * ``delete`` of a key that is not there is not an error; ``exists`` never raises for it;
    * ``healthy`` answers True / False and never raises;
    * ``listing`` gives every ``(key, size)``; ``content_type`` what ``put_bytes`` was given."""

    name: str

    @property
    def version(self) -> str | None: ...
    def ensure_bucket(self) -> None: ...
    def put_bytes(self, key: str, data: bytes, content_type: str) -> None: ...
    def get_bytes(self, key: str) -> bytes: ...
    def delete(self, key: str) -> None: ...
    def exists(self, key: str) -> bool: ...
    def healthy(self) -> bool: ...
    def listing(self) -> Iterator[tuple[str, int]]: ...
    def content_type(self, key: str) -> str | None: ...


class ObjectStore:
    """The S3 adapter (``CDI_OBJECT_STORE=s3``): a thin wrapper over an S3 client for one
    bucket, over one or several MinIO nodes - or any service that speaks the S3 API."""

    name = "s3"

    @property
    def version(self) -> str | None:
        return str(boto3.__version__)

    def __init__(self, settings: Settings | None = None, *, client=None) -> None:
        s = settings or get_settings()
        self.bucket = s.s3_bucket
        self.endpoints = [e.strip() for e in s.s3_endpoint_url.split(",") if e.strip()]
        several = len(self.endpoints) > 1
        cfg = Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},  # MinIO
            # one node of several: give up on it quickly, the next node takes the call
            retries=(
                {"total_max_attempts": 1, "mode": "standard"}
                if several
                else {"max_attempts": 3, "mode": "standard"}
            ),
            connect_timeout=2 if several else 60,
        )
        self._clients = [client] if client is not None else [
            boto3.client("s3", endpoint_url=e, aws_access_key_id=s.s3_access_key,
                         aws_secret_access_key=s.s3_secret_key, region_name="us-east-1",
                         config=cfg)
            for e in self.endpoints
        ]  # fmt: skip
        self._current = 0

    @property
    def client(self):  # noqa: ANN201 - a boto3 S3 client
        """The node answering now (the first that did last time)."""
        return self._clients[self._current]

    def _call(self, fn):  # noqa: ANN001, ANN202
        last: Exception | None = None
        for step in range(len(self._clients)):
            i = (self._current + step) % len(self._clients)
            try:
                result = fn(self._clients[i])
            except Exception as exc:
                if not _node_down(exc):
                    raise
                last = exc
                continue
            self._current = i
            return result
        assert last is not None
        raise last

    def ensure_bucket(self) -> None:
        try:
            self._call(lambda c: c.head_bucket(Bucket=self.bucket))
        except ClientError:
            self._call(lambda c: c.create_bucket(Bucket=self.bucket))

    def put_bytes(self, key: str, data: bytes, content_type: str) -> None:
        self._call(lambda c: c.put_object(Bucket=self.bucket, Key=key, Body=data,
                                          ContentType=content_type))  # fmt: skip

    def get_bytes(self, key: str) -> bytes:
        return self._call(lambda c: c.get_object(Bucket=self.bucket, Key=key)["Body"].read())

    def delete(self, key: str) -> None:
        """Remove one object (a no-op when it is already gone)."""
        self._call(lambda c: c.delete_object(Bucket=self.bucket, Key=key))

    def exists(self, key: str) -> bool:
        try:
            self._call(lambda c: c.head_object(Bucket=self.bucket, Key=key))
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return False
            raise
        return True

    def healthy(self) -> bool:
        try:
            self._call(lambda c: c.head_bucket(Bucket=self.bucket))
        except Exception:  # noqa: BLE001 - any failure is "not healthy" for /healthz
            return False
        return True

    def listing(self) -> Iterator[tuple[str, int]]:
        """Every object of the bucket: ``(key, size in bytes)``."""
        pages = self.client.get_paginator("list_objects_v2").paginate(Bucket=self.bucket)
        for page in pages:
            for o in page.get("Contents", []):
                yield o["Key"], int(o["Size"])

    def content_type(self, key: str) -> str | None:
        head = self._call(lambda c: c.head_object(Bucket=self.bucket, Key=key))
        return head.get("ContentType")


class FileStore:
    """The filesystem adapter (``CDI_OBJECT_STORE=fs``, SW-S6): the scans as files under
    ``CDI_FILE_STORE_ROOT``, so a site needs no object-store server. The folder must be on an
    encrypted volume - the files are patient data; this adapter does not encrypt them itself.

    * a key is a relative ``/`` path inside the root: ``..``, an absolute path or a backslash
      is refused, so no key reaches outside it;
    * a write goes to a temporary file in the same folder and is then moved into place: a
      reader sees the old bytes or the new ones, never half a file;
    * the content type is kept beside the files, in ``.content-types/`` (not part of a listing).
    """

    name = "fs"
    META = ".content-types"

    def __init__(self, settings: Settings | None = None, *, root: str | Path | None = None) -> None:
        s = settings or get_settings()
        where = root if root is not None else s.file_store_root
        if not str(where or "").strip():
            raise ValueError("CDI_OBJECT_STORE=fs needs CDI_FILE_STORE_ROOT: the folder (on an "
                             "encrypted volume) the scans are kept in")  # fmt: skip
        self.root = Path(where).resolve()
        self.bucket = str(self.root)  # what a report names as "where the objects are"

    @property
    def version(self) -> str | None:
        return None  # part of this product: no library of its own

    def _path(self, key: str, *, meta: bool = False) -> Path:
        parts = key.split("/")
        if (not key or key.startswith("/") or "\\" in key or ":" in key
                or any(p in ("", ".", "..") for p in parts) or parts[0] == self.META):  # fmt: skip
            raise ValueError(f"not a key this store accepts: {key!r}")
        return self.root.joinpath(self.META, *parts) if meta else self.root.joinpath(*parts)

    def ensure_bucket(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _write(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def put_bytes(self, key: str, data: bytes, content_type: str) -> None:
        self._write(self._path(key), bytes(data))
        self._write(self._path(key, meta=True), json.dumps({"content_type": content_type}).encode())

    def get_bytes(self, key: str) -> bytes:
        return self._path(key).read_bytes()  # FileNotFoundError when it is not there

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)
        self._path(key, meta=True).unlink(missing_ok=True)

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def healthy(self) -> bool:
        try:
            return self.root.is_dir() and os.access(self.root, os.R_OK | os.W_OK)
        except OSError:
            return False

    def listing(self) -> Iterator[tuple[str, int]]:
        if not self.root.is_dir():
            return
        for path in sorted(self.root.rglob("*")):
            rel = path.relative_to(self.root).as_posix()
            if (path.is_file() and not rel.startswith(self.META + "/")
                    and not path.name.startswith(".tmp-")):  # fmt: skip
                yield rel, path.stat().st_size

    def content_type(self, key: str) -> str | None:
        meta = self._path(key, meta=True)
        if not meta.is_file():
            if not self.exists(key):
                raise FileNotFoundError(key)
            return None
        return cast("str | None", json.loads(meta.read_text("utf-8")).get("content_type"))


# ------------------------------------------------------------------ the registry (SW-S1)

StoreFactory = Callable[[Settings], Any]
_REGISTRY: dict[str, tuple[StoreFactory, tuple[str, ...], str | None]] = {}


def register(name: str, factory: StoreFactory, *, libraries: tuple[str, ...] = (),
             package: str | None = None) -> None:  # fmt: skip
    """Make an object store selectable by ``CDI_OBJECT_STORE=<name>``. ``factory(settings)``
    returns a :class:`Store`. The store in use is made again at the next ``get_store()``."""
    _REGISTRY[name.strip().lower()] = (factory, tuple(libraries), package)
    get_store.cache_clear()


def unregister(name: str) -> None:
    _REGISTRY.pop(name.strip().lower(), None)
    get_store.cache_clear()


def registered() -> list[str]:
    return sorted(_REGISTRY)


def needs(name: str) -> tuple[tuple[str, ...], str | None]:
    """``(libraries, package)`` of a registered store."""
    _, libraries, package = _REGISTRY[name.strip().lower()]
    return libraries, package


def missing(exc: Exception) -> bool:
    """Is this a store saying "that key is not here"? Each adapter has its own error for it
    (S3: a ClientError with NoSuchKey / 404; a folder: FileNotFoundError; a dict: KeyError);
    a caller that must tell "gone" from "the store is down" asks here, and so never has to
    name an adapter's library."""
    if isinstance(exc, ClientError):
        return exc.response.get("Error", {}).get("Code") in ("NoSuchKey", "404", "NotFound")
    return isinstance(exc, KeyError | FileNotFoundError)


def listing(store: Any) -> Iterator[tuple[str, int]]:
    """Every ``(key, size)`` of a store. For a stand-in written before the interface had
    ``listing`` (it offers an S3 client instead), the client is asked - here, in the one
    module that knows the S3 API."""
    if hasattr(store, "listing"):
        yield from store.listing()
        return
    for page in store.client.get_paginator("list_objects_v2").paginate(Bucket=store.bucket):
        for o in page.get("Contents", []):
            yield o["Key"], int(o["Size"])


def content_type_of(store: Any, key: str) -> str | None:
    if hasattr(store, "content_type"):
        return cast("str | None", store.content_type(key))
    return cast("str | None", store.client.head_object(Bucket=store.bucket, Key=key).get(
        "ContentType"))  # fmt: skip


@lru_cache
def get_store() -> ObjectStore:
    """The object store the settings name (``CDI_OBJECT_STORE``). Typed as the S3 adapter for
    the callers written against it; what they may rely on is :class:`Store`."""
    settings = get_settings()
    name = settings.object_store.strip().lower()
    entry = _REGISTRY.get(name)
    if entry is None:
        raise ValueError(f"CDI_OBJECT_STORE={settings.object_store!r} is not a registered "
                         f"object store (available: {', '.join(registered())})")  # fmt: skip
    return cast(ObjectStore, entry[0](settings))


register("s3", lambda settings: ObjectStore(settings), libraries=("boto3",), package="boto3")
register("fs", lambda settings: FileStore(settings))  # SW-S6: no object-store server needed
