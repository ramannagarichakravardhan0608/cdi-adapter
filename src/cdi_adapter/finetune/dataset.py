"""The training data, model-neutral (SW-S3 FR1; LoRA sheet DS-S1).

A dataset is a folder::

    <dataset>/
      manifest.json     name, data_class, rights
      examples.jsonl    one example per line: {"id", "role", "image", "target"}
      images/...        the pictures the examples name

* ``role`` is ``extraction`` (a page and its answer as JSON) or ``line_reader`` (a line crop
  and its text);
* ``target`` is the correct answer - a JSON object for extraction, a string for a line;
* nothing in it is written for a particular model. Each model's chat or prompt format is
  built from it at training time, by that model's adapter.

Two rules are checked before anything is trained (:func:`load`):

* ``data_class`` is ``synthetic`` or ``deidentified``. Pictures of identified patients are
  refused here: real patient images never leave the controlled environment (FT-S1);
* ``rights.may_train`` is true and ``rights.source`` says where the data came from: data with
  no usage rights is not trained on.

An example that is not in the agreed form (an unknown role, a missing picture, an answer
that is not the right kind or, for extraction, does not fit the answer schema named in the
manifest - ``prescription.v3`` unless it says otherwise) is a ``format`` problem
(:class:`DataFormatError`); the two rules above are ``data`` problems (:class:`DataRefused`).

The **data version** is a checksum of the manifest, every example and every picture's bytes:
the same version means the same data, byte for byte (SW-S3 AC2).
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROLES = ("extraction", "line_reader")
DATA_CLASSES = ("synthetic", "deidentified")
DEFAULT_SCHEMA = "prescription.v3"


class DataRefused(ValueError):
    """The data may not be trained on."""


class DataFormatError(DataRefused):
    """An example is not in the agreed form."""


@dataclass(frozen=True)
class Example:
    id: str
    role: str
    image: Path
    target: Any


@dataclass
class Dataset:
    folder: Path
    name: str
    data_class: str
    rights: dict[str, Any]
    version: str
    schema: str
    examples: list[Example] = field(default_factory=list)


def load(folder: Path) -> Dataset:
    """Read a dataset and check it may be trained on. Raises :class:`DataRefused`."""
    manifest_path = folder / "manifest.json"
    lines_path = folder / "examples.jsonl"
    if not manifest_path.is_file() or not lines_path.is_file():
        raise DataRefused(f"{folder} is not a dataset (manifest.json and examples.jsonl)")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    data_class = str(manifest.get("data_class") or "")
    if data_class not in DATA_CLASSES:
        raise DataRefused(
            f"data_class {data_class!r} is not one of {DATA_CLASSES}: pictures of "
            "identified patients are never trained on outside the controlled "
            "environment"
        )
    rights = dict(manifest.get("rights") or {})
    if rights.get("may_train") is not True or not rights.get("source"):
        raise DataRefused(
            "the dataset does not record a right to train on it "
            "(rights.may_train and rights.source)"
        )
    h = hashlib.sha256()
    h.update(
        json.dumps({k: v for k, v in manifest.items() if k != "version"}, sort_keys=True).encode()
    )
    schema = str(manifest.get("schema") or DEFAULT_SCHEMA)
    examples = []
    for n, raw in enumerate(lines_path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise DataFormatError(f"examples.jsonl line {n} is not JSON: {exc}") from exc
        role = row.get("role")
        if role not in ROLES:
            raise DataFormatError(f"examples.jsonl line {n}: role {role!r} is not one of {ROLES}")
        image = folder / str(row.get("image") or "")
        if not image.is_file():
            raise DataFormatError(f"examples.jsonl line {n}: no picture at {row.get('image')!r}")
        target = row.get("target")
        if (role == "extraction" and not isinstance(target, dict)) or (
            role == "line_reader" and not isinstance(target, str)
        ):
            raise DataFormatError(
                f"examples.jsonl line {n}: the target of a {role} example must be "
                f"{'a JSON object' if role == 'extraction' else 'a text'}"
            )
        if role == "extraction":
            from cdi_adapter.ml.client import validate_schema

            wrong = validate_schema(target, schema)
            if wrong:
                raise DataFormatError(
                    f"examples.jsonl line {n}: the answer does not fit {schema}: {wrong[0]}"
                )
        h.update(json.dumps(row, sort_keys=True).encode())
        h.update(hashlib.sha256(image.read_bytes()).digest())
        examples.append(Example(str(row.get("id") or n), role, image, target))
    if not examples:
        raise DataRefused("the dataset has no examples")
    return Dataset(
        folder,
        str(manifest.get("name") or folder.name),
        data_class,
        rights,
        h.hexdigest()[:16],
        schema,
        examples,
    )


def select(ds: Dataset, role: str, n: int | None, seed: int) -> list[Example]:
    """The examples of a role, in an order fixed by the seed, the first ``n`` of them. The
    same data, role, ``n`` and seed always give the same examples in the same order."""
    chosen = [e for e in ds.examples if e.role == role]
    if not chosen:
        raise DataRefused(f"the dataset has no {role} examples")
    random.Random(seed).shuffle(chosen)
    return chosen[:n] if n else chosen
