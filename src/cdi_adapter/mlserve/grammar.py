"""Grammar-locked decoding for the vLLM backend (backlog E2-S3, ARCHITECTURE §3.4, §14 #3).

The client sends every extraction's JSON schema (``bundled_schema``: all ``$ref`` inlined). The
gateway turns it into an XGrammar EBNF grammar, and vLLM masks every token the grammar cannot
accept. So the model can only emit JSON that matches the schema.

vLLM 0.11.0 pins XGrammar 0.1.25. Checked token by token with the real Qwen2.5-VL tokenizer
(``tests/test_grammar_unit.py``), XGrammar enforces types, ``required``, ``additionalProperties:
false``, ``enum``, ``const``, ``anyOf``, ``minItems`` and integer ranges exactly, **but not a
``number`` range**: for ``minimum 0, maximum 1`` it emits ``"1." [0-9]+``, so ``1.8`` passes.
Every bounded number in our schemas is a 0-1 ``confidence``. Each one is therefore swapped for a
placeholder before conversion, and the placeholder is replaced by an exact rule afterwards.

Anything else the grammar could not enforce exactly is refused with :class:`GrammarError`
instead of being sent loosely: a schema edit that adds one fails loudly (and in CI), not as
silently unconstrained output on the pod.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

UNIT_INTERVAL = {"type": "number", "minimum": 0, "maximum": 1}
_MARK = "__cdi_unit_interval__"
_MARK_LITERAL = json.dumps(json.dumps(_MARK))  # how XGrammar writes the JSON string in EBNF
_RULE_NAME = "cdi_unit_interval"
# 0, 0.x (up to 6 decimals), 1, 1.0 (up to 6 zeros): every value is inside [0, 1]
UNIT_INTERVAL_RULE = f'{_RULE_NAME} ::= ("0" ("." [0-9]{{1,6}})?) | ("1" ("." "0"{{1,6}})?)'

_ANNOTATIONS = {"description", "title", "$id", "$comment", "examples", "default"}


class GrammarError(ValueError):
    """The schema uses a feature the grammar cannot enforce exactly."""


def _is_unit_interval(node: dict[str, Any]) -> bool:
    core = {k: v for k, v in node.items() if k not in _ANNOTATIONS}
    return core == UNIT_INTERVAL


def unsupported(schema: Any, path: str = "#") -> list[str]:
    """Where ``schema`` goes beyond what the grammar enforces exactly (empty: fully enforced).

    Covers vLLM 0.11.0's own list (``has_xgrammar_unsupported_json_features``: ``multipleOf``,
    ``uniqueItems`` / ``contains`` / ``min|maxContains``, string ``format``, ``min|maxProperties``
    / ``propertyNames`` / ``patternProperties``), plus what we measured: a number range other
    than 0-1, ``maxItems`` / ``pattern`` / ``min|maxLength`` (not used by our schemas; not
    verified token by token, so refused), and a ``$ref`` left un-inlined.
    """
    out: list[str] = []
    if isinstance(schema, list):
        for i, v in enumerate(schema):
            out += unsupported(v, f"{path}/{i}")
        return out
    if not isinstance(schema, dict):
        return out
    if _is_unit_interval(schema):
        return out
    bad = [k for k in ("multipleOf", "uniqueItems", "contains", "minContains", "maxContains",
                       "format", "minProperties", "maxProperties", "propertyNames",
                       "patternProperties", "maxItems", "pattern", "minLength", "maxLength",
                       "$ref", "not", "if", "allOf", "oneOf") if k in schema]  # fmt: skip
    types = schema.get("type")
    number = types == "number" or (isinstance(types, list) and "number" in types)
    if number and any(k in schema for k in ("minimum", "maximum", "exclusiveMinimum",
                                            "exclusiveMaximum")):  # fmt: skip
        bad.append("number range other than 0-1")
    out += [f"{path}: {k}" for k in bad]
    for k, v in schema.items():
        if k == "properties" and isinstance(v, dict):  # a map of names, not a schema itself
            for name, sub in v.items():
                out += unsupported(sub, f"{path}/properties/{name}")
        elif k not in ("enum", "const", "default", "examples") and isinstance(v, dict | list):
            out += unsupported(v, f"{path}/{k}")
    return out


def with_placeholders(schema: Any) -> Any:
    """Every 0-1 number replaced by a string constant the EBNF step can find and swap."""
    if isinstance(schema, dict):
        if _is_unit_interval(schema):
            return {"const": _MARK}
        return {k: with_placeholders(v) for k, v in schema.items()}
    if isinstance(schema, list):
        return [with_placeholders(v) for v in schema]
    return schema


def schema_to_ebnf(schema: dict[str, Any]) -> str:
    """The exact XGrammar EBNF for ``schema`` (cached per schema). Needs ``xgrammar``, which is
    in the gateway venv (``requirements-vllm.lock``)."""
    # key order kept: XGrammar emits properties in schema order, which is the order the
    # prompt asks for (sorting would force ``confidence`` first)
    return _ebnf(json.dumps(schema))


@lru_cache(maxsize=64)
def _ebnf(schema_json: str) -> str:
    import xgrammar as xgr

    schema = json.loads(schema_json)
    problems = unsupported(schema)
    if problems:
        raise GrammarError("schema cannot be grammar-locked exactly: " + "; ".join(problems[:10]))
    grammar = str(xgr.Grammar.from_json_schema(with_placeholders(schema), any_whitespace=False))
    if _MARK_LITERAL in grammar:
        grammar = grammar.replace(_MARK_LITERAL, _RULE_NAME) + "\n" + UNIT_INTERVAL_RULE + "\n"
    xgr.Grammar.from_ebnf(grammar)  # the same parse vLLM runs before accepting the request
    return grammar
