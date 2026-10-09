"""Closed enumerations from docs/03_EVIDENCE_AND_POLICY.md.

Every axis is independent. Values are serialized by name. The JSON schemas list the
same closed sets; an unknown value is a validation error, never silently interpreted.
"""

from __future__ import annotations

from enum import Enum


class _StrEnum(str, Enum):
    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return self.value


class PropertyResult(_StrEnum):
    PROVED_SAFE = "PROVED_SAFE"
    PROVED_UNSAFE = "PROVED_UNSAFE"
    POSSIBLY_UNSAFE = "POSSIBLY_UNSAFE"
    TESTED = "TESTED"
    OBSERVED_FAILURE = "OBSERVED_FAILURE"
    OBSERVED_REGRESSION = "OBSERVED_REGRESSION"
    UNVERIFIED = "UNVERIFIED"
    UNKNOWN = "UNKNOWN"


class ScopeValidity(_StrEnum):
    EXACT_MODEL = "EXACT_MODEL"
    CONDITIONAL = "CONDITIONAL"
    OVERAPPROXIMATE = "OVERAPPROXIMATE"
    BOUNDED = "BOUNDED"
    EXECUTED = "EXECUTED"


class Authority(_StrEnum):
    DECLARED = "DECLARED"
    APPROVED = "APPROVED"
    INFERRED = "INFERRED"
    REJECTED = "REJECTED"
    RETIRED = "RETIRED"


class PatchRelation(_StrEnum):
    INTRODUCED = "INTRODUCED"
    PRE_EXISTING = "PRE_EXISTING"
    RESOLVED = "RESOLVED"
    CHANGED = "CHANGED"
    UNDETERMINED = "UNDETERMINED"


class Severity(_StrEnum):
    ERROR = "ERROR"
    WARNING = "WARNING"
    INFO = "INFO"


class RunOutcome(_StrEnum):
    COMPLETE = "COMPLETE"
    INCOMPLETE = "INCOMPLETE"
    ERROR = "ERROR"
    CANCELLED = "CANCELLED"


class Verdict(_StrEnum):
    PASS = "PASS"
    WARN = "WARN"
    BLOCK = "BLOCK"
    INCOMPLETE = "INCOMPLETE"
    ERROR = "ERROR"


class DomainSource(_StrEnum):
    """Where the admitted input domain of an obligation came from."""

    CONFIG_DECLARED = "CONFIG_DECLARED"  # .guard/config.toml [[domains]]
    ANNOTATION = "ANNOTATION"  # type annotations only (not enforced at runtime)
    LITERAL_ONLY = "LITERAL_ONLY"  # no parameters influence the obligation
    NONE = "NONE"  # no domain established
    CONTRACT_PRECONDITION = "CONTRACT_PRECONDITION"  # M3: a contract's precondition over its input profile


class AnalysisMethod(_StrEnum):
    INTERVAL = "INTERVAL"
    UNREACHABLE = "UNREACHABLE"
    SMT = "SMT"
    WITNESS_SEARCH = "WITNESS_SEARCH"
    UNSUPPORTED = "UNSUPPORTED"
    BUDGET = "BUDGET"
    PATH_FACT = "PATH_FACT"  # M2: branch-refinement fact (e.g. receiver narrowed to non-None)
    PROBE = "PROBE"  # M3: executed contract probes (dynamic evidence, never a proof)


class SolverStatus(_StrEnum):
    SAT = "SAT"
    UNSAT = "UNSAT"
    UNKNOWN = "UNKNOWN"
    TIMEOUT = "TIMEOUT"
    RESOURCE_LIMIT = "RESOURCE_LIMIT"
    UNAVAILABLE = "UNAVAILABLE"
    DISABLED = "DISABLED"


# Exit codes from docs/09_INTERFACES_AND_STORAGE.md.
EXIT_PASS = 0
EXIT_BLOCK = 1
EXIT_INCOMPLETE = 2
EXIT_ERROR = 3
EXIT_USAGE = 4
