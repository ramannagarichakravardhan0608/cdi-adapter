"""A document whose review was cleared away can be sent again (owner, 2026-10-10).

A prescription was tested, its item taken off the Reviewer's lists, and then the same file was
uploaded again: nothing happened. The website answered the upload with the first job (E9-S3)
and the listener filed the file as a duplicate (LS-S3), so no review item was made. Both rules
stay - for a document that is still in front of a reviewer, or approved. One that is in front
of nobody is read again.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from cdi_adapter.config import Settings
from cdi_adapter.listener import connectors as C
from cdi_adapter.listener import service as S
from cdi_adapter.webapp import jobstore

# ------------------------------------------------------------- the website


class Store:
    """A job store in memory. ``live`` holds the jobs whose document is still under review."""

    def __init__(self) -> None:
        self.jobs: dict[str, Any] = {}
        self.live: set[str] = set()
        self.objects = SimpleNamespace(put_bytes=lambda *a, **k: None, delete=lambda *a: None)

    def by_key(self, key: str) -> Any:
        return self.jobs.get(key)

    def insert(self, job: Any, meta: Any, *, actor: str, accepted_by: str, key: str,
               priority: int) -> None:  # fmt: skip
        if key in self.jobs:
            raise jobstore.Duplicate(key)
        self.jobs[key] = job

    def cleared(self, job: Any) -> bool:
        return job.state == "review" and job.id not in self.live


@pytest.fixture
def store(monkeypatch) -> Store:
    monkeypatch.setattr(jobstore, "dispatch", lambda job_id: None)
    return Store()


def _upload(store: Store, raw: bytes = b"picture", name: str = "rx.jpg") -> Any:
    return jobstore.submit([(name, raw, "image/jpeg")], actor="admin", st=store)  # type: ignore[arg-type]


def test_a_repeat_of_an_upload_still_being_read_is_the_same_job(store) -> None:
    first = _upload(store)
    assert _upload(store).id == first.id and len(store.jobs) == 1  # E9-S3, unchanged


def test_a_repeat_of_an_upload_under_review_is_the_same_job(store) -> None:
    first = _upload(store)
    first.state = "review"
    store.live.add(first.id)
    assert _upload(store).id == first.id and len(store.jobs) == 1


def test_an_upload_whose_review_was_cleared_is_read_again(store) -> None:
    first = _upload(store)
    first.state = "review"  # finished, and its item rejected / cleared: in front of nobody
    second = _upload(store)
    assert second.id != first.id and len(store.jobs) == 2


def test_the_new_run_is_itself_accepted_once(store) -> None:
    first = _upload(store)
    first.state = "review"
    second = _upload(store)
    assert _upload(store).id == second.id and len(store.jobs) == 2  # a retried POST: one job
    second.state = "review"
    store.live.add(second.id)
    assert _upload(store).id == second.id  # under review again: a repeat again


def test_it_can_be_cleared_and_sent_again_more_than_once(store) -> None:
    seen = set()
    for _ in range(4):
        job = _upload(store)
        assert job.id not in seen
        seen.add(job.id)
        job.state = "review"
    assert len(store.jobs) == 4


def test_a_failed_run_is_not_cleared(store) -> None:
    """A job that failed keeps its key here: the listener's own retry rule deals with it."""
    first = _upload(store)
    first.state = "error"
    assert _upload(store).id == first.id


def test_another_file_is_never_mixed_up_with_a_cleared_one(store) -> None:
    first = _upload(store)
    first.state = "review"
    other = _upload(store, raw=b"another picture")
    assert other.id != first.id and len(store.jobs) == 2


def test_the_real_store_asks_the_review_tables() -> None:
    source = Path(jobstore.__file__).read_text(encoding="utf-8")
    assert "def cleared(self, job: Job) -> bool:" in source and "has_live_item" in source
    from cdi_adapter.canon import repo

    assert repo.LIVE == ("workbasket", "claimed", "approved")


# ------------------------------------------------------------- the watched folder


class Recorder:
    def __init__(self) -> None:
        self.read: list[str] = []

    def reader(self) -> S.Reader:
        def read(name: str, raw: bytes, mime: str) -> S.Outcome:
            self.read.append(name)
            return S.Outcome(True, job_id=f"job-{len(self.read)}")

        return S.Reader(read=read)


def _png(shade: int) -> bytes:
    import io

    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (64, 64), (shade, shade, shade)).save(out, format="PNG")
    return out.getvalue()


def _listener(folder: Path, rec: Recorder, **more: Any) -> S.Listener:
    settings = Settings(listener_connector="local", listener_local_root=str(folder),
                        listener_inbox="")  # fmt: skip
    connector = C.get_connector(settings)
    connector.ensure_folders()
    return S.Listener(connector, rec.reader(), **more)


def _drop_and_read(listener: S.Listener, folder: Path, name: str, raw: bytes) -> dict[str, Any]:
    (folder / name).write_bytes(raw)
    listener.poll()
    return listener.poll()


def test_the_same_file_is_a_duplicate_while_its_document_is_under_review(tmp_path) -> None:
    rec, picture = Recorder(), _png(120)
    listener = _listener(tmp_path, rec, filed=lambda first: True)
    _drop_and_read(listener, tmp_path, "rx.png", picture)
    report = _drop_and_read(listener, tmp_path, "rx-again.png", picture)
    assert rec.read == ["rx.png"] and len(report["duplicates"]) == 1  # LS-S3, unchanged


def test_the_same_file_is_read_again_once_its_review_was_cleared(tmp_path) -> None:
    rec, picture = Recorder(), _png(140)
    listener = _listener(tmp_path, rec, filed=lambda first: False)
    _drop_and_read(listener, tmp_path, "rx.png", picture)
    report = _drop_and_read(listener, tmp_path, "rx-again.png", picture)
    assert rec.read == ["rx.png", "rx-again.png"]
    assert report["duplicates"] == [] and len(report["succeeded"]) == 1


def test_a_listener_built_by_hand_keeps_the_duplicate_rule(tmp_path) -> None:
    rec, picture = Recorder(), _png(160)
    listener = _listener(tmp_path, rec)
    _drop_and_read(listener, tmp_path, "a.png", picture)
    assert len(_drop_and_read(listener, tmp_path, "b.png", picture)["duplicates"]) == 1


def test_when_the_database_cannot_say_the_file_stays_a_duplicate(monkeypatch) -> None:
    """Reading twice is the mistake to avoid: no answer means "still filed"."""
    import cdi_adapter.db as db

    def down() -> Any:
        raise RuntimeError("database down")

    monkeypatch.setattr(db, "session_scope", down)
    assert S.review_is_live(SimpleNamespace(document_id="d1")) is True  # type: ignore[arg-type]
    assert S.review_is_live(SimpleNamespace(document_id=None)) is True  # type: ignore[arg-type]
