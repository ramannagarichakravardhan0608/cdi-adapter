"""Model packs: a model shipped to an installed customer, checked before it is installed
(backlog SW-S5 FR4, AC4).

    python -m cdi_adapter.modelops.pack keygen --private pack-key.pem --public pack-key.pub.pem
    python -m cdi_adapter.modelops.pack make <model files> --model <registry id> \\
        --key pack-key.pem --out <pack folder, or a .tar>
    python -m cdi_adapter.modelops.pack verify <pack> [--public-key pack-key.pub.pem]
    python -m cdi_adapter.modelops.pack install <pack> --into <model cache> [--public-key ...]

A pack is a folder (or a ``.tar`` of one)::

    pack.json    the model's id, model id, exact revision, roles, licence name; the SHA-256 of
                 every file; the checksum of them all (the same one the model registry keeps,
                 SW-S2); who made it, when
    pack.sig     the Ed25519 signature of pack.json, by the maker's private key
    LICENCE.txt  the licence text the model comes under
    files/...    the model files

``install`` refuses - and installs nothing - when the signature does not check out against the
public key the customer holds, when any file is missing, added or changed, when the checksum is
not the pack's or not the registry's, or when the registry does not list the model with its
licence text. It puts the files where the model loader looks (a Hugging Face cache snapshot at
the exact revision), never over a different copy, and never removes another revision: the
previous model stays ready for a rollback. Switching to it is then the one setting (SW-S2).
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import json
import shutil
import sys
import tarfile
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from cdi_adapter.config import get_settings
from cdi_adapter.modelops import catalog

PACK_VERSION = "model-pack.v1"


class PackRefused(Exception):
    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason, self.detail = reason, detail


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _files(folder: Path) -> dict[str, str]:
    return {
        p.relative_to(folder).as_posix(): _sha(p) for p in sorted(folder.rglob("*")) if p.is_file()
    }


def keygen(private: Path, public: Path) -> None:  # pragma: no cover - thin wrapper
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    key = Ed25519PrivateKey.generate()
    private.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    public.write_bytes(
        key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    )


def _sign(data: bytes, private_pem: bytes) -> str:
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    key = load_pem_private_key(private_pem, password=None)
    return base64.b64encode(key.sign(data)).decode()  # type: ignore[call-arg, union-attr, arg-type]


def _signed_by(data: bytes, signature: str, public_pem: bytes) -> bool:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from cryptography.hazmat.primitives.serialization import load_pem_public_key

    key = load_pem_public_key(public_pem)
    if not isinstance(key, Ed25519PublicKey):
        raise PackRefused("signature", "the public key is not an Ed25519 key")
    try:
        key.verify(base64.b64decode(signature), data)
    except (InvalidSignature, ValueError):
        return False
    return True


def make(model_files: Path, model: str, private_key: Path, out: Path, *, by: str = "") -> Path:
    """Build a signed pack of a registered model's files."""
    entry = catalog.find(model)
    if entry is None:
        raise PackRefused("registry", f"{model} is not in the model registry")
    licence = entry.licence_text
    if not (licence and licence.is_file() and licence.read_text(encoding="utf-8").strip()):
        raise PackRefused("licence", f"{model} has no licence text saved: it is not shipped")
    folder = out if out.suffix != ".tar" else Path(tempfile.mkdtemp()) / "pack"
    if folder.exists() and any(folder.iterdir()):
        raise PackRefused("out", f"{folder} is not empty")
    shutil.copytree(model_files, folder / "files")
    shutil.copy(licence, folder / "LICENCE.txt")
    manifest = {
        "schema_version": PACK_VERSION,
        "id": entry.id,
        "model_id": entry.model_id,
        "revision": entry.revision,
        "roles": list(entry.roles),
        "licence": entry.licence.get("name"),
        "licence_sha256": _sha(folder / "LICENCE.txt"),
        "checksum": catalog.checksum(folder / "files"),
        "files": _files(folder / "files"),
        "made_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "made_by": by or None,
    }
    data = (json.dumps(manifest, indent=1, sort_keys=True) + "\n").encode()
    (folder / "pack.json").write_bytes(data)
    (folder / "pack.sig").write_text(_sign(data, private_key.read_bytes()) + "\n", "utf-8")
    if out.suffix == ".tar":
        with tarfile.open(out, "w") as tar:
            tar.add(folder, arcname="pack")
        shutil.rmtree(folder.parent)
    return out


def _opened(pack: Path, work: Path) -> Path:
    """The pack's folder: as it is, or a ``.tar`` unpacked safely (no path leaves ``work``)."""
    if pack.is_dir():
        return pack
    if not tarfile.is_tarfile(pack):
        raise PackRefused("format", f"{pack} is neither a pack folder nor a .tar")
    with tarfile.open(pack) as tar:
        tar.extractall(work, filter="data")
    found = [p.parent for p in work.rglob("pack.json")]
    if len(found) != 1:
        raise PackRefused("format", "the .tar does not hold exactly one pack.json")
    return found[0]


def verify(
    pack: Path, public_key: Path | None = None, *, work: Path | None = None
) -> dict[str, Any]:
    """Every check ``install`` makes. Returns the manifest; raises :class:`PackRefused`."""
    work = work or Path(tempfile.mkdtemp())
    folder = _opened(pack, work)
    for name in ("pack.json", "pack.sig", "LICENCE.txt", "files"):
        if not (folder / name).exists():
            raise PackRefused("format", f"the pack has no {name}")
    data = (folder / "pack.json").read_bytes()
    manifest = json.loads(data)
    if manifest.get("schema_version") != PACK_VERSION:
        raise PackRefused("format", f"not a {PACK_VERSION} pack")
    key = public_key or (
        Path(get_settings().model_pack_public_key) if get_settings().model_pack_public_key else None
    )
    if key is None:
        raise PackRefused(
            "signature",
            "no public key is configured (CDI_MODEL_PACK_PUBLIC_KEY): "
            "a pack whose maker cannot be checked is not installed",
        )
    if not _signed_by(data, (folder / "pack.sig").read_text("utf-8").strip(), key.read_bytes()):
        raise PackRefused(
            "signature",
            "the signature does not match: pack.json was changed, or "
            "the pack was not made by the holder of this key",
        )
    listed, found = manifest.get("files") or {}, _files(folder / "files")
    missing = sorted(set(listed) - set(found))
    added = sorted(set(found) - set(listed))
    changed = sorted(k for k in set(listed) & set(found) if listed[k] != found[k])
    if missing or added or changed:
        said = "; ".join(
            f"{w}: {', '.join(v[:5])}"
            for w, v in (("missing", missing), ("added", added), ("changed", changed))
            if v
        )
        raise PackRefused("checksum", f"the files are not the ones the pack was made of ({said})")
    if catalog.checksum(folder / "files") != manifest.get("checksum"):
        raise PackRefused("checksum", "the files' checksum is not the pack's")
    if _sha(folder / "LICENCE.txt") != manifest.get("licence_sha256"):
        raise PackRefused("checksum", "LICENCE.txt is not the one the pack was made with")
    entry = catalog.find(f"{manifest['model_id']}@{manifest['revision']}")
    if entry is None:
        raise PackRefused(
            "registry",
            f"{manifest['model_id']}@{manifest['revision']} is not in "
            "this installation's model registry",
        )
    if entry.checksum and entry.checksum != manifest["checksum"]:
        raise PackRefused(
            "checksum",
            f"the registry expects {entry.checksum[:12]} for "
            f"{entry.id}, the pack holds {manifest['checksum'][:12]}",
        )
    text = entry.licence_text
    if not (text and text.is_file() and text.read_text(encoding="utf-8").strip()):
        raise PackRefused("licence", f"{entry.id} has no licence text saved in the registry")
    manifest["_folder"] = str(folder)
    return manifest


def install(pack: Path, into: Path, public_key: Path | None = None) -> Path:
    """Verify, then copy into the model cache at the exact revision. Never over a different copy;
    never removes anything."""
    with tempfile.TemporaryDirectory() as work:
        manifest = verify(pack, public_key, work=Path(work))
        target = catalog.snapshot(into, manifest["model_id"], manifest["revision"])
        if target.exists():
            if catalog.checksum(target) == manifest["checksum"]:
                return target  # already installed, the same files
            raise PackRefused(
                "installed",
                f"{target} holds other files for the same revision; nothing was changed",
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = target.parent / f".{target.name}.installing"
        if staging.exists():
            shutil.rmtree(staging)
        shutil.copytree(Path(manifest["_folder"]) / "files", staging)
        if catalog.checksum(staging) != manifest["checksum"]:  # the copy itself
            shutil.rmtree(staging)
            raise PackRefused("checksum", "the copy does not match the pack; nothing installed")
        staging.rename(target)
    return target


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m cdi_adapter.modelops.pack")
    sub = ap.add_subparsers(dest="cmd", required=True)
    k = sub.add_parser("keygen")
    k.add_argument("--private", required=True)
    k.add_argument("--public", required=True)
    m = sub.add_parser("make")
    m.add_argument("files")
    m.add_argument("--model", required=True)
    m.add_argument("--key", required=True)
    m.add_argument("--out", required=True)
    m.add_argument("--by", default="")
    for name in ("verify", "install"):
        p = sub.add_parser(name)
        p.add_argument("pack")
        p.add_argument("--public-key", default="")
        if name == "install":
            p.add_argument("--into", required=True)
    args = ap.parse_args(argv)
    key = Path(args.public_key) if getattr(args, "public_key", "") else None
    try:
        if args.cmd == "keygen":
            keygen(Path(args.private), Path(args.public))
            print(f"private key {args.private} (keep it secret); public key {args.public}")
        elif args.cmd == "make":
            print(make(Path(args.files), args.model, Path(args.key), Path(args.out), by=args.by))
        elif args.cmd == "verify":
            got = verify(Path(args.pack), key)
            print(
                f"pack OK: {got['id']} {got['model_id']}@{got['revision']} "
                f"checksum {got['checksum'][:12]}"
            )
        else:
            print(f"installed: {install(Path(args.pack), Path(args.into), key)}")
    except PackRefused as refused:
        print(f"refused ({refused.reason}): {refused.detail}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
