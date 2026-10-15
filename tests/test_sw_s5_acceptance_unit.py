"""SW-S5 "Deploy the winner safely: shadow run, one-setting switch, one-step rollback" - the
parts that need no database. The rest (AC1-AC3 end to end, with real gateways over HTTP and
PostgreSQL) is ``test_sw_s5_acceptance_integration.py``.

* AC1  shadow mode: the candidate is asked AFTER the real result is saved, its answer is saved
       apart, and nothing it does - an answer, a failure, a crash - reaches the document;
* AC4  a model pack with a wrong checksum (or signature, or registry entry) is refused, and
       nothing is installed.

Every model, key and file here is made up for the test.
"""

from __future__ import annotations

import json
import re
import shutil
import tarfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from cdi_adapter.config import get_settings
from cdi_adapter.modelops import catalog, ops, pack, shadow
from cdi_adapter.modelops.__main__ import build_parser

ROOT = Path(__file__).resolve().parents[1]
REV = "e" * 40


@pytest.fixture
def registry(tmp_path, monkeypatch):
    """A registry with a licensed candidate and an unlicensed one; settings point at it."""
    licence = ROOT / "licences" / "models" / "test-sw-s5-apache.txt"
    licence.parent.mkdir(parents=True, exist_ok=True)
    licence.write_text("Apache License, Version 2.0 (test copy)\n", encoding="utf-8")

    def write(checksum: str | None = None) -> Path:
        models = [
            {
                "id": "cand",
                "roles": ["extraction"],
                "model_id": "Org/Candidate-7B",
                "revision": REV,
                "checksum": checksum,
                "status": "candidate",
                "licence": {
                    "name": "Apache-2.0",
                    "text_file": "licences/models/test-sw-s5-apache.txt",
                },
            },
            {
                "id": "bare",
                "roles": ["extraction"],
                "model_id": "Org/No-Licence",
                "revision": REV,
                "status": "candidate",
                "licence": {"name": "?", "text_file": None},
            },
        ]
        path = tmp_path / "registry.json"
        path.write_text(json.dumps({"models": models}), encoding="utf-8")
        monkeypatch.setenv("CDI_MODEL_REGISTRY_PATH", str(path))
        get_settings.cache_clear()
        catalog.forget()
        return path

    write()
    yield SimpleNamespace(write=write)
    get_settings.cache_clear()
    catalog.forget()
    licence.unlink()
    if not any(licence.parent.iterdir()):
        licence.parent.rmdir()


def _shadow_on(monkeypatch, name: str = "cand", percent: int = 100, inline: bool = True) -> None:
    monkeypatch.setenv("CDI_SHADOW_EXTRACTION", name)
    monkeypatch.setenv("CDI_SHADOW_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("CDI_SHADOW_PERCENT", str(percent))
    monkeypatch.setenv("CDI_SHADOW_INLINE", "true" if inline else "false")
    get_settings.cache_clear()
    shadow.forget()


REQ = SimpleNamespace(image_png=b"png", prompt="read it", schema="prescription.v3", max_tokens=64)
CHAMPION = {
    "patient": {"name": "Asha Testwala", "age": "30 Y"},
    "medications": [{"drug_name": "Paracetamol", "confidence": 0.9}],
    "confidence": 0.9,
    "_untrusted_text": [],
}


class Fake:
    def __init__(self, answer: Any = None, error: Exception | None = None) -> None:
        self.answer, self.error, self.asked = answer, error, 0

    def vlm_json(self, image, prompt, schema, *, max_tokens=1024):
        self.asked += 1
        assert (image, prompt, schema) == (b"png", "read it", "prescription.v3")
        if self.error:
            raise self.error
        return self.answer


# ------------------------------------------------------------------ AC1: shadow mode


def test_ac1_shadow_mode_is_off_unless_a_candidate_is_named(registry, monkeypatch):
    monkeypatch.delenv("CDI_SHADOW_EXTRACTION", raising=False)
    get_settings.cache_clear()
    assert shadow.configured() is None
    assert shadow.after_extraction("d", REQ, CHAMPION, "champ", "v1") is False


def test_ac1_a_candidate_without_its_licence_text_never_runs_in_shadow(registry, monkeypatch):
    for name in ("bare", "Org/Never-Registered"):
        _shadow_on(monkeypatch, name)
        assert shadow.configured() is None


def test_ac1_the_candidate_is_named_from_the_registry_and_has_its_own_gateway(
    registry, monkeypatch
):
    _shadow_on(monkeypatch)
    s = shadow.configured()
    assert s == shadow.Shadow("Org/Candidate-7B", REV, "cand", "http://127.0.0.1:9", 100)
    client = shadow._client(s)
    assert client.fallbacks == []  # never the production fallback: a wrong name on an answer
    assert client.model == "Org/Candidate-7B"
    monkeypatch.setenv("CDI_SHADOW_URL", "")
    get_settings.cache_clear()
    assert shadow.configured() is None


def test_ac1_the_candidates_answer_is_saved_apart_with_how_far_it_agreed(registry, monkeypatch):
    _shadow_on(monkeypatch)
    saved: list[dict[str, Any]] = []
    monkeypatch.setattr(shadow, "save", saved.append)
    candidate = {
        "patient": {"name": "Asha  testwala", "age": "31 Y"},
        "medications": [{"drug_name": "Paracetamol", "confidence": 0.4}],
        "confidence": 0.5,
        "allergies": ["none"],
        "_raw_answer": "{...}",
    }
    fake = Fake(candidate)
    row = shadow.answer(
        shadow.configured(), "doc-1", REQ, CHAMPION, "Org/Champion", "v1", client=fake
    )
    assert fake.asked == 1 and saved == [row]
    assert row["model_id"] == "Org/Candidate-7B" and row["registered_as"] == "cand"
    assert (row["champion_model"], row["champion_version"]) == ("Org/Champion", "v1")
    assert "_raw_answer" not in row["answer"] and row["error"] is None
    agree = row["agreement"]
    # the name agrees (spacing and case aside); the age does not; confidence is not compared
    assert agree["compared"] == 3 and agree["same"] == 2 and agree["rate"] == 0.6667
    assert agree["differ"] == ["patient.age"]
    assert agree["only_candidate"] == ["allergies[0]"]


def test_ac1_a_candidate_that_fails_is_a_row_with_its_error_never_an_error(registry, monkeypatch):
    _shadow_on(monkeypatch)
    saved: list[dict[str, Any]] = []
    monkeypatch.setattr(shadow, "save", saved.append)
    row = shadow.answer(
        shadow.configured(),
        "doc-2",
        REQ,
        CHAMPION,
        "c",
        "v1",
        client=Fake(error=TimeoutError("took too long")),
    )
    assert row["error"] == "TimeoutError: took too long" and row["answer"] is None
    assert saved == [row]

    def broken_save(_: Any) -> None:
        raise RuntimeError("database down")

    monkeypatch.setattr(shadow, "save", broken_save)
    shadow.answer(shadow.configured(), "doc-3", REQ, CHAMPION, "c", "v1", client=Fake({}))


def test_ac1_a_gateway_serving_another_model_is_not_recorded_as_the_candidate(
    registry, monkeypatch
):
    _shadow_on(monkeypatch)
    monkeypatch.setattr(ops, "gateway_health", lambda url: {"status": "ok", "model": "Org/Other"})
    saved: list[dict[str, Any]] = []
    monkeypatch.setattr(shadow, "save", saved.append)
    row = shadow.answer(shadow.configured(), "doc-4", REQ, CHAMPION, "c", "v1")
    assert row["error"] == (
        "RuntimeError: http://127.0.0.1:9 serves Org/Other, not Org/Candidate-7B"
    )


def test_ac1_shadow_mode_never_reaches_the_document_whatever_happens(registry, monkeypatch):
    _shadow_on(monkeypatch)

    def explode(*_: Any, **__: Any) -> None:
        raise RuntimeError("shadow bug")

    monkeypatch.setattr(shadow, "answer", explode)
    assert shadow.after_extraction("doc-5", REQ, CHAMPION, "c", "v1") is False
    monkeypatch.setattr(shadow, "configured", explode)
    assert shadow.after_extraction("doc-5", REQ, CHAMPION, "c", "v1") is False


def test_ac1_by_default_the_candidate_answers_on_its_own_thread(registry, monkeypatch):
    _shadow_on(monkeypatch, inline=False)
    asked: list[Any] = []
    monkeypatch.setattr(shadow, "answer", lambda *a: asked.append(a))
    submitted: list[Any] = []
    monkeypatch.setattr(
        shadow, "_executor", lambda: SimpleNamespace(submit=lambda fn, *a: submitted.append(a))
    )
    assert shadow.after_extraction("doc-6", REQ, CHAMPION, "c", "v1") is True
    assert asked == [] and len(submitted) == 1  # handed over: the document did not wait


def test_ac1_a_share_of_documents_by_document_id(registry, monkeypatch):
    _shadow_on(monkeypatch, percent=30)
    s = shadow.configured()
    picks = [shadow.chosen(f"doc-{i}", s) for i in range(400)]
    assert 0.2 < sum(picks) / 400 < 0.4
    assert picks == [shadow.chosen(f"doc-{i}", s) for i in range(400)]  # the same every time


def test_ac1_extraction_asks_the_shadow_only_after_the_real_result_is_saved(monkeypatch):
    from cdi_adapter.extract import service

    order: list[str] = []
    ctx = SimpleNamespace(document_id="doc-7")
    vlm = SimpleNamespace(
        model="Org/Champion", version="v1", vlm_json=lambda *a, **k: {"medications": []}
    )
    monkeypatch.setattr(service, "_start", lambda d, v: ctx)
    monkeypatch.setattr(service, "_request", lambda c, s: REQ)
    monkeypatch.setattr(
        service, "_complete", lambda *a, **k: order.append("real result saved") or {"facts": 1}
    )
    monkeypatch.setattr(
        shadow, "after_extraction", lambda *a: order.append(f"shadow asked {a[3]}") or True
    )
    assert service.extract_document("doc-7", client=vlm) == {"facts": 1}
    assert order == ["real result saved", "shadow asked Org/Champion"]

    def fails(*a: Any, **k: Any) -> None:
        raise RuntimeError("the champion failed")

    order.clear()
    monkeypatch.setattr(service, "_complete", fails)
    monkeypatch.setattr(service, "_record_failure", lambda *a: None)
    with pytest.raises(RuntimeError):
        service.extract_document("doc-7", client=vlm)
    assert order == []  # no real result: nothing to shadow


def test_ac1_shadow_answers_are_kept_where_no_result_or_screen_reads_them():
    migration = (ROOT / "db/alembic/versions/0041_shadow_results_and_switch.py").read_text("utf-8")
    assert "CREATE TABLE shadow_result" in migration
    assert "REFERENCES source_document (id) ON DELETE CASCADE" in migration  # erased with it
    readers = [
        p.relative_to(ROOT).as_posix()
        for p in (ROOT / "src").rglob("*.py")
        if re.search(r"(FROM|INTO|JOIN|UPDATE) shadow_result", p.read_text("utf-8"))
    ]
    # the shadow module, and the drill that checks its rows; no result, review or export
    assert sorted(readers) == [
        "src/cdi_adapter/modelops/drill.py",
        "src/cdi_adapter/modelops/shadow.py",
    ]


# ------------------------------------------------------------------ AC2 / AC3: switch and drain


def test_drain_waits_for_the_documents_still_running_on_the_old_model(monkeypatch):
    seen = iter([[{"document_id": "a"}, {"document_id": "b"}], [{"document_id": "b"}], []])
    monkeypatch.setattr(ops, "in_flight", lambda conn, v, stale: next(seen))
    monkeypatch.setattr(ops, "session_scope", _no_db)
    t = [0.0]
    got = ops.drain(
        "v1", poll_s=2.0, clock=lambda: t[0], sleep=lambda s: t.__setitem__(0, t[0] + s)
    )
    assert got == {"version": "v1", "running_at_switch": 2, "waited_s": 4.0, "still_running": []}


def test_drain_gives_up_after_its_limit_and_names_what_still_runs(monkeypatch):
    monkeypatch.setattr(ops, "in_flight", lambda conn, v, stale: [{"document_id": "stuck"}])
    monkeypatch.setattr(ops, "session_scope", _no_db)
    t = [0.0]
    got = ops.drain(
        "v1", wait_s=5, poll_s=2.0, clock=lambda: t[0], sleep=lambda s: t.__setitem__(0, t[0] + s)
    )
    assert got["still_running"] == ["stuck"] and got["waited_s"] == 6.0


class _no_db:  # noqa: N801
    def __enter__(self) -> None:
        return None

    def __exit__(self, *a: Any) -> None:
        return None


def test_a_switch_needs_a_binding_contest_won(registry, tmp_path):
    index = tmp_path / "index.jsonl"
    assert ops.contest_won("Org/Candidate-7B", REV, index) is None  # no contest at all
    rows = [
        {"contest_id": "c1", "candidate": "cand", "decision": "promote", "binding": False},
        {"contest_id": "c2", "candidate": "cand", "decision": "keep champion", "binding": True},
        {"contest_id": "c3", "candidate": "other", "decision": "promote", "binding": True},
    ]
    index.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    assert ops.contest_won("Org/Candidate-7B", REV, index) is None
    with index.open("a", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {"contest_id": "c4", "candidate": "cand", "decision": "promote", "binding": True}
            )
            + "\n"
        )
    assert ops.contest_won("Org/Candidate-7B", REV, index) == "c4"


def test_the_commands_are_there():
    p = build_parser()
    a = p.parse_args(["switch", "v2", "--url", "http://g", "--force", "--reason", "drill"])
    assert (a.cmd, a.version, a.force, a.wait_s) == ("switch", "v2", True, 600.0)
    assert p.parse_args(["drain", "v1"]).cmd == "drain"
    assert p.parse_args(["rollback"]).cmd == "rollback"
    assert p.parse_args(["shadow-report"]).cmd == "shadow-report"


# ------------------------------------------------------------------ AC4: model packs


@pytest.fixture(scope="module")
def keys(tmp_path_factory) -> SimpleNamespace:
    folder = tmp_path_factory.mktemp("keys")
    pack.keygen(folder / "maker.pem", folder / "maker.pub.pem")
    pack.keygen(folder / "other.pem", folder / "other.pub.pem")
    return SimpleNamespace(
        private=folder / "maker.pem",
        public=folder / "maker.pub.pem",
        other_private=folder / "other.pem",
        other_public=folder / "other.pub.pem",
    )


def _model_files(folder: Path) -> Path:
    (folder / "sub").mkdir(parents=True)
    (folder / "config.json").write_text('{"made": "up"}', "utf-8")
    (folder / "model.safetensors").write_bytes(b"\x00made-up weights\x01" * 64)
    (folder / "sub" / "tokenizer.json").write_text("{}", "utf-8")
    return folder


@pytest.fixture
def made(registry, keys, tmp_path) -> Path:
    return pack.make(
        _model_files(tmp_path / "weights"),
        "cand",
        keys.private,
        tmp_path / "pack",
        by="made-up maker",
    )


def test_ac4_a_good_pack_is_verified_and_installed_where_the_loader_looks(made, keys, tmp_path):
    manifest = pack.verify(made, keys.public)
    assert (manifest["id"], manifest["model_id"], manifest["revision"]) == (
        "cand",
        "Org/Candidate-7B",
        REV,
    )
    assert manifest["checksum"] == catalog.checksum(made / "files")
    cache = tmp_path / "cache"
    target = pack.install(made, cache, keys.public)
    assert target == catalog.snapshot(cache, "Org/Candidate-7B", REV)
    assert catalog.checksum(target) == manifest["checksum"]
    assert pack.install(made, cache, keys.public) == target  # again: nothing to do


def test_ac4_a_pack_with_a_changed_file_is_refused_and_nothing_installed(made, keys, tmp_path):
    weights = made / "files" / "model.safetensors"
    weights.write_bytes(weights.read_bytes()[:-1] + b"\x02")
    cache = tmp_path / "cache"
    with pytest.raises(pack.PackRefused) as refused:
        pack.install(made, cache, keys.public)
    assert refused.value.reason == "checksum"
    assert "changed: model.safetensors" in refused.value.detail
    assert not cache.exists()


@pytest.mark.parametrize(
    ("change", "said"),
    [
        (
            lambda f: (f / "files" / "sub" / "tokenizer.json").unlink(),
            "missing: sub/tokenizer.json",
        ),
        (lambda f: (f / "files" / "extra.bin").write_bytes(b"x"), "added: extra.bin"),
        (lambda f: (f / "LICENCE.txt").write_text("another licence", "utf-8"), "LICENCE.txt"),
    ],
)
def test_ac4_a_missing_added_or_changed_file_is_refused(made, keys, tmp_path, change, said):
    change(made)
    with pytest.raises(pack.PackRefused) as refused:
        pack.install(made, tmp_path / "cache", keys.public)
    assert refused.value.reason == "checksum" and said in refused.value.detail


def test_ac4_a_pack_json_edited_to_match_changed_files_fails_its_signature(made, keys, tmp_path):
    weights = made / "files" / "model.safetensors"
    weights.write_bytes(b"swapped weights")
    manifest = json.loads((made / "pack.json").read_text("utf-8"))
    manifest["files"]["model.safetensors"] = pack._sha(weights)
    manifest["checksum"] = catalog.checksum(made / "files")
    (made / "pack.json").write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n", "utf-8")
    with pytest.raises(pack.PackRefused) as refused:
        pack.install(made, tmp_path / "cache", keys.public)
    assert refused.value.reason == "signature"


def test_ac4_a_pack_signed_by_someone_else_or_with_no_key_is_refused(registry, keys, tmp_path):
    other = pack.make(_model_files(tmp_path / "w"), "cand", keys.other_private, tmp_path / "p")
    with pytest.raises(pack.PackRefused, match="signature does not match"):
        pack.verify(other, keys.public)
    with pytest.raises(pack.PackRefused, match="no public key is configured"):
        pack.verify(other)


def test_ac4_the_registry_checksum_must_be_the_packs(made, keys, registry, tmp_path):
    registry.write(checksum="0" * 64)
    with pytest.raises(pack.PackRefused) as refused:
        pack.install(made, tmp_path / "cache", keys.public)
    assert refused.value.reason == "checksum"
    assert "the registry expects 000000000000 for cand" in refused.value.detail
    registry.write(checksum=catalog.checksum(made / "files"))
    assert pack.install(made, tmp_path / "cache", keys.public).is_dir()


def test_ac4_a_model_without_a_licence_text_is_neither_packed_nor_installed(
    registry, keys, tmp_path
):
    with pytest.raises(pack.PackRefused) as refused:
        pack.make(_model_files(tmp_path / "w"), "bare", keys.private, tmp_path / "p")
    assert refused.value.reason == "licence"


def test_ac4_an_installed_copy_is_never_overwritten_and_other_revisions_stay(made, keys, tmp_path):
    cache = tmp_path / "cache"
    old = catalog.snapshot(cache, "Org/Candidate-7B", "f" * 40)  # the previous model's files
    old.mkdir(parents=True)
    (old / "model.safetensors").write_bytes(b"previous")
    here = catalog.snapshot(cache, "Org/Candidate-7B", REV)
    here.mkdir(parents=True)
    (here / "model.safetensors").write_bytes(b"someone else's copy")
    with pytest.raises(pack.PackRefused) as refused:
        pack.install(made, cache, keys.public)
    assert refused.value.reason == "installed"
    assert (here / "model.safetensors").read_bytes() == b"someone else's copy"
    assert (old / "model.safetensors").read_bytes() == b"previous"


def test_ac4_a_tar_pack_works_and_cannot_write_outside_its_folder(registry, keys, tmp_path):
    tar = pack.make(_model_files(tmp_path / "w"), "cand", keys.private, tmp_path / "cand.tar")
    assert pack.install(tar, tmp_path / "cache", keys.public).is_dir()
    evil = tmp_path / "evil.tar"
    with tarfile.open(evil, "w") as t:
        info = tarfile.TarInfo("../../escaped.txt")
        info.size = 1
        import io

        t.addfile(info, io.BytesIO(b"x"))
    with pytest.raises((tarfile.TarError, pack.PackRefused)):
        pack.install(evil, tmp_path / "cache", keys.public)
    assert not (tmp_path.parent / "escaped.txt").exists()


def test_ac4_the_install_command_refuses_with_exit_code_2(made, keys, tmp_path, capsys):
    shutil.copy(made / "LICENCE.txt", made / "files" / "added.txt")
    code = pack.main(
        ["install", str(made), "--into", str(tmp_path / "c"), "--public-key", str(keys.public)]
    )
    assert code == 2
    assert "refused (checksum)" in capsys.readouterr().err


# ------------------------------------------------------------------ not done yet


@pytest.mark.xfail(
    strict=True,
    reason="not done yet: the swap drill has not run on the GPU host with two real models (it "
    "needs a second registered model with its licence text, and the pod) - docs/drill.md",
)
def test_the_quarterly_swap_drill_has_been_recorded_on_the_gpu_host():
    found = sorted((ROOT / "docs" / "evidence" / "SW-S5").glob("drill-gpu-*.json"))
    assert found and json.loads(found[-1].read_text("utf-8"))["passed"]
