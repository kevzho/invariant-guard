"""The LocalReasoner protocol (docs/08) and its result types.

```
capabilities() -> model identity, limits, structured-output support
propose_contracts(ContextPacket) -> raw structured response (parsed into a CandidateBatch
                                    by semantic/candidates.py, never trusted as-is)
propose_probe_parameters(Contract, InputProfile) -> BoundedInputCandidates
explain_finding(EvidencePacket) -> OptionalExplanation
```

M4 implements ``capabilities`` and ``propose_contracts``. Boundary inputs are requested
inside each contract candidate (``boundary_inputs``) and validated as plain values against
the input profile, so a separate ``propose_probe_parameters`` round trip is not used yet;
``explain_finding`` is not implemented. Both report ``UNSUPPORTED`` rather than pretending
(``ReasonerCapabilities.operations`` lists what an adapter really does).

A reasoner has no tools: it receives a rendered prompt and returns text. It cannot run
commands, read files, approve contracts or reach the network beyond its validated
destination (a loopback endpoint, or an explicitly allowed bring-your-own-key provider:
semantic/remote.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


class ReasonerUnavailable(Exception):
    """The model cannot be used (endpoint down, model absent, digest mismatch, ...)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ModelIdentity:
    artifact_id: str  # e.g. "ollama:qwen2.5-coder:7b"
    revision: str  # content digest of the model artifact as reported by the runtime
    license: str  # declared license id + digest of the license text shipped with the artifact
    runtime: str  # e.g. "ollama 0.35.1"
    quantization: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_batch_json(self) -> dict[str, str]:
        """The CandidateBatch ``model_identity`` object (schema fields only)."""
        return {"artifact_id": self.artifact_id, "revision": self.revision, "license": self.license, "runtime": self.runtime,
                "quantization": self.quantization}

    def to_json(self) -> dict[str, Any]:
        return {**self.to_batch_json(), "details": self.details}


@dataclass
class ReasonerCapabilities:
    available: bool
    reason: str
    identity: ModelIdentity | None = None
    structured_output: str = "none"  # "json_schema" when the runtime constrains decoding to a JSON schema
    context_limit_tokens: int | None = None
    operations: tuple[str, ...] = ()
    endpoint: str | None = None
    probe_ms: float | None = None

    def to_json(self) -> dict[str, Any]:
        return {"available": self.available, "reason": self.reason, "identity": self.identity.to_json() if self.identity else None,
                "structured_output": self.structured_output, "context_limit_tokens": self.context_limit_tokens,
                "operations": list(self.operations), "endpoint": self.endpoint, "probe_ms": self.probe_ms}


@dataclass
class ReasonerResponse:
    """One raw model answer. ``text`` is untrusted and is parsed strictly elsewhere."""

    text: str
    elapsed_ms: float
    prompt_tokens: int | None = None
    output_tokens: int | None = None
    done_reason: str | None = None
    runtime_timing_ms: dict[str, float] = field(default_factory=dict)
    request_options: dict[str, Any] = field(default_factory=dict)
    request_bytes: int | None = None  # size of the request body actually sent (remote adapters)
    request_sha256: str | None = None


@dataclass
class Prompt:
    system: str
    user: str
    response_schema: dict[str, Any]
    prompt_version: str
    digest: str


@runtime_checkable
class LocalReasoner(Protocol):
    def capabilities(self) -> ReasonerCapabilities: ...

    def propose_contracts(self, prompt: Prompt) -> ReasonerResponse: ...

    def propose_probe_parameters(self, contract: dict[str, Any], input_profile: str) -> dict[str, Any]: ...

    def explain_finding(self, evidence: dict[str, Any]) -> dict[str, Any] | None: ...


UNSUPPORTED_PROBE_PARAMETERS = {"status": "UNSUPPORTED",
                                "reason": "M4 requests boundary inputs inside each contract candidate (validated as plain values); "
                                          "a separate probe-parameter round trip is not implemented"}
UNSUPPORTED_EXPLANATION = {"status": "UNSUPPORTED", "reason": "model explanations of findings are not implemented; reports use deterministic text"}

__all__ = ["LocalReasoner", "ModelIdentity", "ReasonerCapabilities", "ReasonerResponse", "ReasonerUnavailable", "Prompt",
           "UNSUPPORTED_PROBE_PARAMETERS", "UNSUPPORTED_EXPLANATION"]
