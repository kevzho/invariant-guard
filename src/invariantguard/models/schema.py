"""JSON Schema loading and validation.

Schemas live in ``invariantguard/schemas/*.schema.json`` (draft 2020-12) and reference
each other by URN. Validation needs the optional ``jsonschema`` package (dev extra);
when it is absent, ``SchemaValidationUnavailable`` is raised and callers disclose that.
"""

from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources
from typing import Any

SCHEMA_NAMES = (
    "common",
    "source_span",
    "finding",
    "obligation_result",
    "evidence",
    "snapshot",
    "contract_revision",
    "candidate_batch",
    "agent_candidate_batch",
    "context_packet",
    "run_profile",
    "analysis_plan",
    "report",
)


class SchemaValidationUnavailable(Exception):
    pass


class SchemaValidationError(Exception):
    def __init__(self, name: str, errors: list[str]) -> None:
        super().__init__(f"{name}: {len(errors)} schema error(s): " + "; ".join(errors[:5]))
        self.name = name
        self.errors = errors


def schema_id(name: str) -> str:
    return f"urn:invariantguard:schema:{name}:1.0"


@lru_cache(maxsize=None)
def load_schema(name: str) -> dict[str, Any]:
    if name not in SCHEMA_NAMES:
        raise KeyError(name)
    text = resources.files("invariantguard.schemas").joinpath(f"{name}.schema.json").read_text(encoding="utf-8")
    return json.loads(text)


@lru_cache(maxsize=None)
def _validator(name: str):
    try:
        import jsonschema
        from referencing import Registry, Resource
    except ImportError as exc:  # pragma: no cover - exercised only without dev extras
        raise SchemaValidationUnavailable(f"jsonschema not installed: {exc}") from None
    registry = Registry()
    for n in SCHEMA_NAMES:
        registry = registry.with_resource(schema_id(n), Resource.from_contents(load_schema(n)))
    cls = jsonschema.Draft202012Validator
    cls.check_schema(load_schema(name))
    return cls(load_schema(name), registry=registry)


def validation_errors(name: str, instance: Any) -> list[str]:
    v = _validator(name)
    errs = sorted(v.iter_errors(instance), key=lambda e: list(e.absolute_path))
    return [f"{'/'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message[:300]}" for e in errs]


def validate(name: str, instance: Any) -> None:
    errors = validation_errors(name, instance)
    if errors:
        raise SchemaValidationError(name, errors)
