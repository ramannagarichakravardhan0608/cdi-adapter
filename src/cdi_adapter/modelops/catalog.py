"""The model registry (backlog SW-S2): every model, recorded, pinned, and chosen by a setting.

``models/registry.json`` lists every model the product can run. For each: the roles it can
fill, the model id, the EXACT revision, a checksum of its files, its licence with a saved copy
of the licence text, what it was trained on, how it is served, the GPU memory it needs, and the
prompt and schema versions it goes with.

A model becomes CHAMPION for a role by a setting - no code changes:

=============  ============================  =====================================
Role           Setting                        What it changes
=============  ============================  =====================================
extraction     ``CDI_CHAMPION_EXTRACTION``    ``vlm_model_id`` + ``vlm_model_revision``
handwriting    ``CDI_CHAMPION_HANDWRITING``   ``trocr_model_id`` + ``trocr_model_revision``
printed        ``CDI_CHAMPION_PRINTED``       (the reader ships its models inside it)
=============  ============================  =====================================

The service REFUSES to start (:func:`check`) when a champion setting names a model that is not
registered (AC1), is registered for another role, is retired, has no licence text saved (AC3),
has no exact revision, or whose prompt has changed since it was registered. With no champion
setting the service runs as it always has, on the model settings themselves - and says so.

The files of a model are fetched at the pinned revision only, never "latest", in the safe
format, with remote code off (:func:`load_kwargs`); once loaded, their checksum is compared
with the registry's (:func:`verify`, AC4). A change on the hub under another revision cannot
reach a pinned load; a file changed under the SAME revision is refused.

    python -m cdi_adapter.modelops.catalog list
    python -m cdi_adapter.modelops.catalog checksum <folder of a model snapshot>
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from cdi_adapter.config import Settings, get_settings

ROOT = Path(__file__).resolve().parents[3]
REGISTRY = ROOT / "models" / "registry.json"
ROLES = ("extraction", "handwriting", "printed")
SETTING = {"extraction": "CDI_CHAMPION_EXTRACTION", "handwriting": "CDI_CHAMPION_HANDWRITING",
           "printed": "CDI_CHAMPION_PRINTED"}  # fmt: skip
STATUSES = ("candidate", "champion", "retired")
_REVISION = re.compile(r"^[0-9a-f]{40}$|^\d+(\.\d+)+$")  # a commit hash, or a package version
# the document type whose prompt goes with a role's model
PROMPT_FOR = {"extraction": "prescription"}


class ModelMismatch(RuntimeError):
    """The files loaded are not the files the registry pinned."""


@dataclass(frozen=True)
class Entry:
    id: str
    roles: tuple[str, ...]
    model_id: str
    revision: str | None
    checksum: str | None
    licence: dict[str, Any]
    trained_on: str | None
    served_by: str | None
    gpu_memory_gb: float | None
    prompt_version: str | None
    schema_version: str | None
    status: str

    @property
    def licence_text(self) -> Path | None:
        name = (self.licence or {}).get("text_file")
        return (ROOT / name) if name else None


def registry_path(settings: Settings | None = None) -> Path:
    s = settings or get_settings()
    return Path(s.model_registry_path) if s.model_registry_path else REGISTRY


@lru_cache(maxsize=8)
def _read(path: str) -> tuple[Entry, ...]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    out = []
    for m in data.get("models", []):
        out.append(Entry(
            id=str(m["id"]), roles=tuple(m.get("roles") or ()), model_id=str(m["model_id"]),
            revision=m.get("revision"), checksum=m.get("checksum"),
            licence=dict(m.get("licence") or {}), trained_on=m.get("trained_on"),
            served_by=m.get("served_by"), gpu_memory_gb=m.get("gpu_memory_gb"),
            prompt_version=m.get("prompt_version"), schema_version=m.get("schema_version"),
            status=str(m.get("status") or "candidate")))  # fmt: skip
    return tuple(out)


def entries(settings: Settings | None = None) -> tuple[Entry, ...]:
    return _read(str(registry_path(settings)))


def forget() -> None:
    _read.cache_clear()


def find(value: str, settings: Settings | None = None) -> Entry | None:
    """An entry by its registry id, or by model id (``Org/Name``), optionally ``@revision``."""
    wanted, _, revision = value.strip().partition("@")
    for e in entries(settings):
        if wanted in (e.id, e.model_id) and (not revision or revision == e.revision):
            return e
    return None


def champion_setting(role: str, settings: Settings) -> str:
    return str(getattr(settings, f"champion_{role}", "") or "").strip()


def champion(role: str, settings: Settings | None = None) -> Entry | None:
    """The champion the setting names for this role (None: no champion is set)."""
    s = settings or get_settings()
    named = champion_setting(role, s)
    return find(named, s) if named else None


def _licence_saved(e: Entry) -> bool:
    path = e.licence_text
    return bool(path and path.is_file() and path.read_text(encoding="utf-8").strip())


def licence_problem(e: Entry) -> str | None:
    """SW-S7: why this model's licence keeps it out of use, or None - the one check in front of
    every swap (a champion, a contest, a switch, shadow mode, fine-tuning). No licence text
    saved, or a licence the policy blocks (research-only, non-commercial, strong copyleft,
    proprietary, custom, unknown) without a recorded decision to use it."""
    from cdi_adapter import licences

    if not _licence_saved(e):
        return "has no licence text saved"
    return licences.model_problem(e.licence)


def refusal(role: str, settings: Settings | None = None) -> str | None:
    """Why this role's champion setting cannot be used, in words that name the setting."""
    s = settings or get_settings()
    named = champion_setting(role, s)
    if not named:
        return None
    said = f"{SETTING[role]}={named!r}"
    e = find(named, s)
    if e is None:
        listed = sorted(x.id for x in entries(s) if role in x.roles)
        return (f"{said}: the model is not registered in {registry_path(s).name} "
                f"(registered for {role}: {', '.join(listed) or 'none'})")  # fmt: skip
    if role not in e.roles:
        return f"{said}: {e.id} is registered for {', '.join(e.roles)}, not for {role}"
    if e.status == "retired":
        return f"{said}: {e.id} is retired"
    if not e.revision or not _REVISION.match(e.revision):
        return f"{said}: {e.id} has no exact revision pinned (never 'latest')"
    if not _licence_saved(e):
        return (f"{said}: {e.id} has no licence text saved; a model without one cannot be "
                f"champion (save it and name it in licence.text_file)")  # fmt: skip
    why = licence_problem(e)  # SW-S7 AC1: a research-only licence is refused, text or not
    if why:
        return f"{said}: {e.id} cannot be champion: {why}"
    doc_type = PROMPT_FOR.get(role)
    if doc_type and e.prompt_version:
        from cdi_adapter.extract.prompt import prompt_version

        now = prompt_version(doc_type)
        if now != e.prompt_version:
            return (f"{said}: the prompt has changed since {e.id} was registered "
                    f"(registered {e.prompt_version}, now {now}); register it with this "
                    "prompt")  # fmt: skip
    return None


def problems(settings: Settings | None = None) -> list[str]:
    s = settings or get_settings()
    return [r for r in (refusal(role, s) for role in ROLES) if r]


def check(settings: Settings | None = None) -> None:
    """At start: refuse a champion that cannot be used (AC1, AC3)."""
    found = problems(settings)
    if found:
        raise SystemExit("refusing to start: " + "; ".join(found))


def apply(settings: Settings | None = None) -> Settings:
    """Make the champions the models the service loads: the champion's model id and pinned
    revision replace the model settings of its role. Called once at start, after :func:`check`.
    No champion set: the settings are left exactly as they are."""
    s = settings or get_settings()
    e = champion("extraction", s)
    if e is not None:
        s.vlm_model_id, s.vlm_model_revision = e.model_id, e.revision
    e = champion("handwriting", s)
    if e is not None:
        s.trocr_model_id = e.model_id
        s.trocr_model_revision = e.revision or s.trocr_model_revision
    return s


def identity(role: str, settings: Settings | None = None) -> dict[str, Any]:
    """What a result records about the model of a role: id, revision, and where it was chosen."""
    s = settings or get_settings()
    e = champion(role, s)
    model_id: str | None
    revision: str | None
    if role == "extraction":
        model_id, revision = s.vlm_model_id, s.vlm_model_revision
    elif role == "handwriting":
        model_id, revision = s.trocr_model_id, s.trocr_model_revision
    else:
        model_id, revision = (e.model_id, e.revision) if e else (None, None)
    listed = e or next(
        (x for x in entries(s) if x.model_id == model_id and role in x.roles), None)
    return {"model_id": model_id, "revision": revision or (listed.revision if listed else None),
            "registered_as": listed.id if listed else None,
            "chosen_by": SETTING[role] if e else "model settings (no champion set)"}  # fmt: skip


# ----------------------------------------------------------- loading safely (NFR1, NFR2, AC4)


def load_kwargs(revision: str | None) -> dict[str, Any]:
    """What every ``from_pretrained`` is given: the exact revision, never 'latest'; no code
    that comes with a model; the safe file format."""
    if not revision or revision in ("main", "latest", "master"):
        raise ModelMismatch("a model is loaded only at an exact pinned revision, never 'latest'")
    return {"revision": revision, "trust_remote_code": False, "use_safetensors": True}


def checksum(folder: Path) -> str:
    """The checksum of a model's files: SHA-256 over every file's relative path and its own
    SHA-256, in path order - so a renamed, added, removed or changed file changes it. Links (a
    Hugging Face cache snapshot is made of them) are followed to the file they point at."""
    h = hashlib.sha256()
    files = sorted(p for p in folder.rglob("*") if p.is_file())
    if not files:
        raise ModelMismatch(f"no model files in {folder}")
    for path in files:
        inner = hashlib.sha256()
        with path.open("rb") as fh:
            for block in iter(lambda: fh.read(1 << 20), b""):
                inner.update(block)
        h.update(path.relative_to(folder).as_posix().encode() + b"\0" + inner.digest())
    return h.hexdigest()


def snapshot(cache: Path, model_id: str, revision: str) -> Path:
    """Where a pinned revision's files are, in a Hugging Face cache."""
    return cache / f"models--{model_id.replace('/', '--')}" / "snapshots" / revision


def verify(entry: Entry, folder: Path) -> str:
    """Compare the loaded files with the registry. A mismatch is refused; with no checksum
    registered yet, the one found is returned to be written into the registry."""
    found = checksum(folder)
    if entry.checksum and found != entry.checksum:
        raise ModelMismatch(f"{entry.id} at {entry.revision}: the files do not match the "
                            f"registry (registered {entry.checksum[:12]}, "
                            f"found {found[:12]})")  # fmt: skip
    return found


def verify_loaded(model_id: str, revision: str | None,
                  settings: Settings | None = None) -> str | None:  # fmt: skip
    """After a model was loaded: find its snapshot in the cache and verify it. Returns the
    checksum, or None when the model is not registered (it is then only logged)."""
    s = settings or get_settings()
    e = next((x for x in entries(s) if x.model_id == model_id and x.revision == revision), None)
    if e is None or not revision:
        return None
    from huggingface_hub import snapshot_download

    folder = Path(snapshot_download(model_id, revision=revision, local_files_only=True))
    return verify(e, folder)


def describe(settings: Settings | None = None) -> list[dict[str, Any]]:
    s = settings or get_settings()
    out = []
    for e in entries(s):
        out.append({"id": e.id, "roles": list(e.roles), "model_id": e.model_id,
                    "revision": e.revision, "checksum": e.checksum, "status": e.status,
                    "licence": e.licence.get("name"), "licence_text_saved": _licence_saved(e),
                    "prompt_version": e.prompt_version, "schema_version": e.schema_version,
                    "champion_for": [r for r in ROLES if champion(r, s) == e]})  # fmt: skip
    return out


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["checksum"] and len(args) == 2:
        print(checksum(Path(args[1])))
        return 0
    if args[:1] == ["list"]:
        for d in describe():
            print(f"{d['id']:28} {', '.join(d['roles']):12} {d['revision'] or '-':42} "
                  f"licence text {'saved' if d['licence_text_saved'] else 'MISSING'}  "
                  f"checksum {(d['checksum'] or 'MISSING')[:12]}  {d['status']}")  # fmt: skip
        found = problems()
        for p in found:
            print(f"  REFUSED  {p}")
        return 1 if found else 0
    print(__doc__.split("\n\n")[0])
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
