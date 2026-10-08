"""LS-S3 on real PostgreSQL: the ``listener_file`` table behaves as the in-memory ledger does.

The loop's rules are unit-tested against the in-memory ledger
(``tests/test_ls_s3_acceptance_unit.py``). This proves the same rules hold in the SQL: counting
unchanged looks, the lease (one worker), recognising the same bytes, and the ignored list.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from cdi_adapter.db import session_scope
from cdi_adapter.listener import ledger as L
from cdi_adapter.listener.connectors import RemoteFile

pytestmark = pytest.mark.integration


def _source() -> str:
    return f"local:/test/{uuid.uuid4().hex}"


def _file(name: str = "rx.png", size: int = 100, tag: str | None = "v1") -> RemoteFile:
    return RemoteFile(f"inbox/{name}", name, "inbox", size=size, fingerprint=tag)


def test_the_table_is_there(db_env):
    assert L.DbLedger().ready() is None


def test_unchanged_looks_are_counted_and_a_change_starts_again(db_env):
    ledger, source = L.DbLedger(), _source()
    assert ledger.observe(source, _file()).stable_polls == 1
    assert ledger.observe(source, _file()).stable_polls == 2
    assert ledger.observe(source, _file(size=250)).stable_polls == 1  # it grew
    assert ledger.observe(source, _file(size=250, tag="v2")).stable_polls == 1  # new version tag
    assert ledger.observe(source, _file(size=250, tag="v2")).stable_polls == 2


def test_the_lease_lets_one_worker_through(db_env):
    ledger, source = L.DbLedger(), _source()
    entry = ledger.observe(source, _file())
    assert ledger.claim(entry, "worker-1", 600) is True
    assert ledger.claim(entry, "worker-2", 600) is False
    assert ledger.claim(entry, "worker-1", 600) is True  # the holder may renew
    looked = ledger.observe(source, _file())  # another worker looks at it meanwhile
    assert looked.state == "processing" and looked.lease_owner == "worker-1"  # lease intact
    with session_scope() as c:  # the lease runs out: worker-1 died
        c.execute(text("UPDATE listener_file SET lease_until = now() - interval '1 second' "
                       "WHERE id = :i"), {"i": entry.id})  # fmt: skip
    assert ledger.claim(entry, "worker-2", 600) is True


def test_the_same_bytes_are_recognised_only_once_they_went_through(db_env):
    ledger, source, sha = L.DbLedger(), _source(), uuid.uuid4().hex * 2
    first = ledger.observe(source, _file("first.png"))
    ledger.claim(first, "w", 600)
    assert ledger.processed(sha) is None
    ledger.finish(first, "failed", sha256=sha, kind="quality", reason="too blurry")
    assert ledger.processed(sha) is None  # a failed read is not "already processed"
    again = ledger.observe(source, _file("first.png", tag="v2"))
    ledger.claim(again, "w", 600)
    ledger.finish(again, "succeeded", sha256=sha, job_id="job-1",
                  file=RemoteFile("success/first.png", "first.png", "success"))  # fmt: skip
    found = ledger.processed(sha)
    assert found is not None and found.name == "first.png" and found.job_id == "job-1"
    second = ledger.observe(source, _file("second.png"))
    ledger.claim(second, "w", 600)
    ledger.finish(second, "duplicate", sha256=sha, duplicate_of=found.id, job_id=found.job_id,
                  reason="the same bytes as first.png")  # fmt: skip
    row = ledger.find(source, "inbox/second.png")
    assert row is not None and row.state == "duplicate" and row.duplicate_of == found.id
    assert ledger.claim(row, "w", 600) is False  # finished: not claimable


def test_an_ignored_file_is_recorded_once_with_its_reason(db_env):
    ledger, source = L.DbLedger(), _source()
    docx = _file("letter.docx")
    assert ledger.ignore(source, docx, "file type not allowed") is True
    assert ledger.ignore(source, docx, "file type not allowed") is False  # not again
    (row,) = ledger.ignored(source)
    assert row.name == "letter.docx" and row.reason == "file type not allowed"
    assert ledger.ignored(_source()) == []


def test_a_file_put_back_where_one_was_already_dealt_with_starts_fresh(db_env):
    ledger, source = L.DbLedger(), _source()
    entry = ledger.observe(source, _file())
    ledger.claim(entry, "w", 600)
    ledger.finish(entry, "failed", kind="quality", reason="too dark")  # stays at inbox/rx.png
    back = ledger.observe(source, _file())
    assert back.state == "seen" and back.stable_polls == 1 and back.reason is None
