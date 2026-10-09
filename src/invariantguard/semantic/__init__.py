"""Local open-weight reasoning (M4, docs/08).

The model is a *proposer* only. Its candidates enter exactly the same mechanical pipeline
as deterministic template candidates (``contracts/templates.validate_proposals``, the
non-vacuity check and optional dynamic falsification); it never sets authority above
INFERRED, approves anything, or produces proof. Nothing here is imported by ``guard
check``/``guard verify``; the package loads only when model discovery is requested.

Proposer sources (all through the same pipeline): the Ollama loopback adapter
(``ollama.py``, the offline default), opt-in bring-your-own-key adapters (``remote.py``:
OpenAI-compatible and Anthropic; https + ``remote_allowed``; disclosed data egress; never a
fallback) and the calling coding agent (``agent.py``: context packets out, candidate batches
in). DECISIONS I-105/I-106.
"""

from .context import ContextBudget, ContextPacketBuilder, build_context_packet
from .endpoint import EndpointError, LoopbackEndpoint, validate_loopback_endpoint
from .reasoner import LocalReasoner, ModelIdentity, ReasonerCapabilities, ReasonerUnavailable

__all__ = [
    "ContextBudget", "ContextPacketBuilder", "build_context_packet", "EndpointError", "LoopbackEndpoint", "validate_loopback_endpoint",
    "LocalReasoner", "ModelIdentity", "ReasonerCapabilities", "ReasonerUnavailable",
]
