"""Model-gateway client + structured-output safety (ARCHITECTURE §3.4 "Client side").

``vlm_json(image, prompt, schema)`` = generate -> ``extract_json`` (tolerates fences / prose) ->
``repair_payload`` (rehomes values put in the wrong key, fills required confidence, drops stray
keys, resolves ``cdi:common.defs`` ``$ref``) -> validate against a ``referencing.Registry`` of all
local schemas -> on repeated failure returns the repaired best-effort object with
``_partial: true`` **instead of raising**. A document is never lost to a formatting nit, and a
``_partial`` payload is never auto-trusted downstream (S6 holds it for review).
"""

from __future__ import annotations

import base64
import copy
import difflib
import json
import re
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from cdi_adapter import tracing
from cdi_adapter.config import get_settings
from cdi_adapter.logging import get_logger
from cdi_adapter.mlserve.backends import GenItem

if TYPE_CHECKING:
    from cdi_adapter.modelops.router import Route

log = get_logger(__name__)

SCHEMA_DIR = Path(__file__).resolve().parents[3] / "schemas"
FILLED_CONFIDENCE = 0.5  # a confidence the model did not give is filled low, never high

# key aliases the 7B commonly emits instead of the schema's own names
_ALIASES = {
    "medications": ("medicines", "drugs", "rx", "prescriptions", "medication", "meds"),
    "tests": ("results", "lab_results", "labs", "investigations", "test_results", "analytes"),
    "vitals": ("vital_signs", "vital", "observations"),
    "diagnoses": ("diagnosis", "dx", "impression_diagnoses", "conditions", "provisional_diagnosis"),
    "allergies": ("allergy", "allergic_to", "drug_allergies"),
    "advice": ("instructions", "advise", "recommendations", "notes"),
    "chief_complaints": ("complaints", "chief_complaint", "c_o", "presenting_complaints"),
    "findings_list": ("findings", "observations_list"),
    "impression_concepts": ("impression", "impressions", "conclusion"),
    "procedures": ("procedure_list", "operations", "surgeries"),
    "patient": ("patient_details", "patient_info", "demographics"),
}
_ITEM_NAME_ALIASES = (
    "name", "drug", "drug_name", "medicine", "test", "test_name", "analyte",
    "vital", "parameter", "substance", "allergen", "text", "procedure",
)  # fmt: skip


# ------------------------------------------------------------------ schema registry


@lru_cache
def registry() -> Registry:
    resources = []
    for path in sorted(SCHEMA_DIR.glob("*.json")):
        contents = json.loads(path.read_text(encoding="utf-8"))
        resources.append((contents["$id"], Resource.from_contents(contents)))
    return Registry().with_resources(resources)


def schema_names() -> list[str]:
    return [p.name[: -len(".json")] for p in sorted(SCHEMA_DIR.glob("*.json"))]


def load_schema(name: str) -> dict[str, Any]:
    uri = name if name.startswith("cdi:") else f"cdi:{name}"
    return registry().contents(uri)


def _deref(node: Any, base: str) -> tuple[Any, str]:
    """Follow ``$ref`` (absolute ``cdi:`` or document-relative ``#/...``) to its target."""
    while isinstance(node, dict) and "$ref" in node:
        ref = node["$ref"]
        uri = base + ref if ref.startswith("#") else ref
        doc, _, pointer = uri.partition("#")
        base = doc
        node = registry().contents(doc)
        for part in filter(None, pointer.split("/")):
            node = node[part]
    return node, base


def bundled_schema(name: str) -> dict[str, Any]:
    """The schema with every ``$ref`` inlined (for a server-side grammar that cannot resolve
    ``cdi:`` URIs)."""
    root = load_schema(name)

    def inline(node: Any, base: str) -> Any:
        node, base = _deref(node, base)
        if isinstance(node, dict):
            return {k: inline(v, base) for k, v in node.items() if k not in ("$defs", "$schema")}
        if isinstance(node, list):
            return [inline(v, base) for v in node]
        return node

    out = inline(root, root["$id"])
    out["$id"] = root["$id"]
    return out


def validate_schema(payload: Any, name: str) -> list[str]:
    """Validation errors as readable strings; empty list means valid."""
    schema = load_schema(name)
    validator = Draft202012Validator(schema, registry=registry())
    errors = sorted(validator.iter_errors(payload), key=lambda e: list(e.absolute_path))
    return [f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}" for e in errors]


# ------------------------------------------------------------------ JSON extraction


_FENCE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.S)


def extract_json(text: str) -> dict[str, Any]:
    """First JSON object in the model's text, tolerating ``` fences and surrounding prose."""
    candidates = [m.group(1) for m in _FENCE.finditer(text)] + [text]
    decoder = json.JSONDecoder()
    for cand in candidates:
        for i, ch in enumerate(cand):
            if ch != "{":
                continue
            try:
                obj, _ = decoder.raw_decode(cand, i)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                return obj
    raise ValueError("no JSON object found in model output")


# ------------------------------------------------------------------ repair


def _types(node: dict[str, Any]) -> set[str]:
    t = node.get("type")
    if isinstance(t, list):
        return set(t)
    if isinstance(t, str):
        return {t}
    types: set[str] = set()
    for alt in node.get("anyOf", []):
        types |= _types(alt)
    return types


def _fix_confidence(v: Any) -> Any:
    if isinstance(v, str):
        try:
            v = float(v.strip().rstrip("%")) / (100 if v.strip().endswith("%") else 1)
        except ValueError:
            return FILLED_CONFIDENCE
    if isinstance(v, bool) or not isinstance(v, int | float):
        return FILLED_CONFIDENCE
    if 1 < v <= 100:  # a percentage
        v = v / 100
    return min(max(float(v), 0.0), 1.0)


def _object_branch(node: dict[str, Any], base: str) -> tuple[dict[str, Any] | None, str]:
    node, base = _deref(node, base)
    if node.get("type") == "object":
        return node, base
    for alt in node.get("anyOf", []):
        alt, b = _deref(alt, base)
        if alt.get("type") == "object":
            return alt, b
    return None, base


def _repair_item(item: Any, item_schema: dict[str, Any], base: str) -> Any:
    obj, obase = _object_branch(item_schema, base)
    if obj is None:
        return item
    required = obj.get("required", [])
    name_key = next((k for k in required if k != "confidence"), None)
    only_object = "string" not in _types(_deref(item_schema, base)[0])
    if isinstance(item, str) and only_object and name_key:
        item = {name_key: item}
    if not isinstance(item, dict):
        return item
    item = dict(item)
    if name_key and name_key not in item:
        for alias in _ITEM_NAME_ALIASES:
            if alias in item and isinstance(item[alias], str):
                item[name_key] = item.pop(alias)
                break
    if "confidence" in obj.get("properties", {}):
        if "confidence" in item:
            item["confidence"] = _fix_confidence(item["confidence"])
        elif "confidence" in required:
            item["confidence"] = FILLED_CONFIDENCE
    return item


def repair_payload(payload: dict[str, Any], name: str) -> dict[str, Any]:
    """Best-effort structural repair against schema ``name``. Never invents clinical values."""
    root = load_schema(name)
    base = root["$id"]
    props: dict[str, Any] = root.get("properties", {})
    out: dict[str, Any] = {}
    strays: dict[str, Any] = {}
    for k, v in payload.items():
        (out if k in props else strays)[k] = v

    # rehome values put under the wrong key
    for k, v in strays.items():
        if k.startswith("_"):
            continue
        key = k.lower().strip().replace(" ", "_").replace("/", "_")
        target = next((p for p, al in _ALIASES.items() if p in props and key in al), None)
        if target is None:
            close = difflib.get_close_matches(key, list(props), n=1, cutoff=0.8)
            target = close[0] if close else None
        if target and out.get(target) in (None, [], {}, ""):
            out[target] = v

    for key, node in props.items():
        if key not in out:
            continue
        resolved, rbase = _deref(node, base)
        types = _types(resolved)
        value = out[key]
        if key == "confidence":
            out[key] = _fix_confidence(value)
        elif "array" in types:
            if value is None:
                out[key] = []
            elif not isinstance(value, list):
                out[key] = [value]
            items = resolved.get("items")
            if items is not None:
                out[key] = [_repair_item(i, items, rbase) for i in out[key]]
        elif "object" in types and resolved.get("type") == "object" and isinstance(value, dict):
            out[key] = _repair_item(value, resolved, rbase)

    if "confidence" in root.get("required", []) and "confidence" not in out:
        out["confidence"] = FILLED_CONFIDENCE
    # stray keys that could not be rehomed are dropped (additionalProperties: false)
    return out


def is_partial(payload: dict[str, Any]) -> bool:
    return bool(payload.get("_partial"))


@dataclass(frozen=True)
class JsonRequest:
    """One document's structured-output request (``vlm_json`` arguments), for batching (E2-S8)."""

    image_png: bytes | None
    prompt: str
    schema: str
    max_tokens: int = 1024


# ------------------------------------------------------------------ clients


class MLClient:
    """Base client: subclasses implement :meth:`generate`."""

    backend = "base"
    model = "unknown"
    version: str | None = None  # the registered model version serving it (backlog E10-S5)
    role = "primary"  # "primary" or "canary" (E10-S5)

    def generate(
        self,
        prompt: str,
        image_png: bytes | None = None,
        *,
        max_tokens: int = 1024,
        json_schema: dict[str, Any] | None = None,
    ) -> str:
        raise NotImplementedError

    def generate_batch(self, items: list[GenItem]) -> list[str]:
        """One text per item, in order (backlog E2-S8). The base calls ``generate`` in turn."""
        return [self.generate(i.prompt, i.image_png, max_tokens=i.max_tokens,
                              json_schema=i.json_schema) for i in items]  # fmt: skip

    def vlm_json_batch(self, requests: list[JsonRequest]) -> list[dict[str, Any]]:
        """``vlm_json`` for several documents with the first attempt in one batched call (E2-S8).

        Every document is its own sequence with its own prompt, image and schema. A document whose
        batched answer does not validate continues on exactly the single-call path (the same
        re-ask with its errors, the same retries, the same ``_partial`` fallback).
        """
        items = [GenItem(r.prompt, r.image_png, r.max_tokens, bundled_schema(r.schema))
                 for r in requests]  # fmt: skip
        texts: list[str | None] = [None] * len(items)
        if len(items) > 1:
            texts = list(self.generate_batch(items))
        return [
            self.vlm_json(r.image_png, r.prompt, r.schema, max_tokens=r.max_tokens, first_text=t)
            for r, t in zip(requests, texts, strict=True)
        ]

    def vlm_json(
        self,
        image_png: bytes | None,
        prompt: str,
        schema: str,
        *,
        max_tokens: int = 1024,
        retries: int | None = None,
        first_text: str | None = None,
    ) -> dict[str, Any]:
        """``first_text``: the answer to the first attempt, already generated (by a batch)."""
        retries = get_settings().extract_retries if retries is None else retries
        grammar = bundled_schema(schema)
        best: dict[str, Any] | None = None
        errors: list[str] = []
        ask = prompt
        for attempt in range(retries + 1):
            if attempt == 0 and first_text is not None:
                text = first_text
            else:
                text = self.generate(ask, image_png, max_tokens=max_tokens, json_schema=grammar)
            try:
                repaired = repair_payload(extract_json(text), schema)
            except ValueError as exc:
                errors = [str(exc)]
            else:
                errors = validate_schema(repaired, schema)
                if not errors:
                    return repaired
                best = repaired
            ask = (
                f"{prompt}\n\nYour previous answer was invalid ({'; '.join(errors[:5])}). "
                "Return ONLY one JSON object that matches the schema."
            )
            log.info("vlm_json_retry", schema=schema, attempt=attempt + 1, errors=errors[:5])
        partial = copy.deepcopy(best) if best is not None else {}
        partial["_partial"] = True
        partial["_errors"] = errors[:20]
        log.warning("vlm_json_partial", schema=schema, errors=errors[:5])
        return partial


class HttpMLClient(MLClient):
    """Calls the model gateway (``POST {CDI_MLSERVE_URL}/vlm/generate``)."""

    backend = "http"

    def __init__(self, base_url: str | None = None, *, timeout: float = 300.0,
                 transport: httpx.BaseTransport | None = None) -> None:  # fmt: skip
        self.base_url = (base_url or get_settings().mlserve_url).rstrip("/")
        self._timeout, self._transport = timeout, transport
        self._http = httpx.Client(base_url=self.base_url, timeout=timeout, transport=transport)
        self.model = get_settings().vlm_model_id

    def generate(self, prompt, image_png=None, *, max_tokens=1024, json_schema=None) -> str:
        body = {
            "prompt": prompt,
            "image_b64": base64.b64encode(image_png).decode() if image_png else None,
            "max_tokens": max_tokens,
            "json_schema": json_schema,
        }
        t0 = time.perf_counter()
        # the gateway continues this trace (E10-S3)
        r = self._http.post("/vlm/generate", json=body, headers=tracing.inject_headers({}))
        r.raise_for_status()
        data = r.json()
        self.model = data.get("model", self.model)
        log.info("vlm_generate", model=self.model, seconds=round(time.perf_counter() - t0, 2))
        return data["text"]

    def generate_batch(self, items: list[GenItem]) -> list[str]:
        body = {"items": [{"prompt": i.prompt,
                           "image_b64": base64.b64encode(i.image_png).decode()
                           if i.image_png else None,
                           "max_tokens": i.max_tokens, "json_schema": i.json_schema}
                          for i in items]}  # fmt: skip
        t0 = time.perf_counter()
        r = self._http.post("/vlm/generate_batch", json=body, headers=tracing.inject_headers({}))
        r.raise_for_status()
        data = r.json()
        self.model = data.get("model", self.model)
        texts: list[str] = data["texts"]
        if len(texts) != len(items):
            raise ValueError(f"gateway returned {len(texts)} texts for {len(items)} items")
        log.info("vlm_generate_batch", model=self.model, items=len(items),
                 seconds=round(time.perf_counter() - t0, 2))  # fmt: skip
        return texts

    def healthz(self) -> dict[str, Any]:
        r = self._http.get("/healthz")
        r.raise_for_status()
        return r.json()


class StubMLClient(MLClient):
    """In-process deterministic keyword responder (CI / no GPU); same contract as the gateway."""

    backend = "stub"

    def __init__(self) -> None:
        from cdi_adapter.mlserve.backends import StubBackend

        self._backend = StubBackend()
        self.model = self._backend.model

    def generate(self, prompt, image_png=None, *, max_tokens=1024, json_schema=None) -> str:
        return self._backend.generate(prompt, image_png, max_tokens=max_tokens,
                                      json_schema=json_schema)  # fmt: skip

    def generate_batch(self, items: list[GenItem]) -> list[str]:
        return self._backend.generate_batch(items)


class RoutedClient(HttpMLClient):
    """The gateway client for one route of the rollout (backlog E10-S5).

    A canary that cannot answer (unreachable, or a 5xx) hands the document to the primary, so a
    broken canary costs a retry, never a document. A 4xx (a refused grammar, E2-S3) is an answer
    and is not retried elsewhere. ``version`` / ``role`` always name the side that answered last.
    """

    def __init__(self, route: Route, **kw: Any) -> None:
        super().__init__(route.url, **kw)
        if route.model_id:
            self.model = route.model_id
        self.version, self.role, self._fallback = route.version, route.role, route.fallback

    def _falls_back(self, exc: Exception) -> bool:
        if self._fallback is None:
            return False
        if isinstance(exc, httpx.HTTPStatusError):
            return exc.response.status_code >= 500
        return isinstance(exc, httpx.TransportError)

    def _to_fallback(self, exc: Exception) -> None:
        from cdi_adapter import metrics

        fb = self._fallback
        assert fb is not None
        log.warning("canary_fallback", canary=self.version, primary=fb.version, error=str(exc))
        metrics.CANARY_FALLBACKS.inc()
        self._http.close()
        self.base_url = fb.url
        self._http = httpx.Client(base_url=fb.url, timeout=self._timeout,
                                  transport=self._transport)  # fmt: skip
        self.model = fb.model_id or self.model
        self.version, self.role, self._fallback = fb.version, fb.role, None

    def generate(self, prompt, image_png=None, *, max_tokens=1024, json_schema=None) -> str:
        try:
            return super().generate(prompt, image_png, max_tokens=max_tokens,
                                    json_schema=json_schema)  # fmt: skip
        except (httpx.HTTPStatusError, httpx.TransportError) as exc:
            if not self._falls_back(exc):
                raise
            self._to_fallback(exc)
            return super().generate(prompt, image_png, max_tokens=max_tokens,
                                    json_schema=json_schema)  # fmt: skip

    def generate_batch(self, items: list[GenItem]) -> list[str]:
        try:
            return super().generate_batch(items)
        except (httpx.HTTPStatusError, httpx.TransportError) as exc:
            if not self._falls_back(exc):
                raise
            self._to_fallback(exc)
            return super().generate_batch(items)


def client_for(route: Route) -> MLClient:
    """``stub`` backend -> in-process stub; anything else -> the gateway of ``route``."""
    if get_settings().mlserve_backend == "stub":
        return StubMLClient()
    return RoutedClient(route)


def get_client(document_id: Any = None) -> MLClient:
    """The client for ``document_id``'s side of the rollout: primary or canary (E10-S5)."""
    if get_settings().mlserve_backend == "stub":
        return StubMLClient()
    from cdi_adapter.modelops import router

    return client_for(router.route(document_id))
