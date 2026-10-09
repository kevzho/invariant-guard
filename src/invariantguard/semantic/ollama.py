"""Ollama adapter for a configured loopback endpoint (M4 T41/T42).

Requests: ``GET /api/version``, ``GET /api/tags``, ``POST /api/show`` (identity) and
``POST /api/chat`` with ``stream: false`` and ``format`` set to the per-packet JSON
schema (constrained decoding). Nothing else is ever requested: the adapter cannot pull,
create, copy or delete models (``endpoint.ALLOWED_REQUESTS``). A model that is not present
locally is UNAVAILABLE with a note that installation is a separate explicit step.

Identity is read from the runtime, never assumed: the artifact digest from ``/api/tags``,
quantization/family/parameter size and context length from ``/api/show``, the declared
license id (GGUF ``general.license``) plus the sha256 of the license text the artifact
ships, and the runtime version from ``/api/version``. When ``model_digest`` is pinned in
the configuration, any other digest makes the model unavailable.
"""

from __future__ import annotations

import hashlib
import time
from typing import Any

from .endpoint import EndpointError, LoopbackEndpoint, LoopbackHttp
from .reasoner import (UNSUPPORTED_EXPLANATION, UNSUPPORTED_PROBE_PARAMETERS, ModelIdentity, Prompt, ReasonerCapabilities, ReasonerResponse,
                       ReasonerUnavailable)

DEFAULT_OPTIONS = {"temperature": 0.0, "seed": 0, "num_ctx": 8192, "num_predict": 1024}


def _norm_digest(d: str) -> str:
    d = d.strip().lower()
    return d.split(":", 1)[1] if d.startswith("sha256:") else d


class OllamaReasoner:
    """LocalReasoner over Ollama's HTTP API on a validated loopback endpoint."""

    def __init__(self, endpoint: LoopbackEndpoint, model_id: str, *, pinned_digest: str | None = None, timeout_s: float = 600.0,
                 options: dict[str, Any] | None = None, keep_alive: str = "10m") -> None:
        if not model_id:
            raise ValueError("model_id is required")
        self.endpoint = endpoint
        self.model_id = model_id
        self.pinned = _norm_digest(pinned_digest) if pinned_digest else None
        self.http = LoopbackHttp(endpoint, timeout_s)
        self.options = {**DEFAULT_OPTIONS, **(options or {})}
        self.keep_alive = keep_alive
        self._caps: ReasonerCapabilities | None = None

    # -- identity ---------------------------------------------------------------------------
    def capabilities(self) -> ReasonerCapabilities:
        if self._caps is not None:
            return self._caps
        t0 = time.perf_counter()
        try:
            version = self.http.request("GET", "/api/version", timeout_s=10).body
            tags = self.http.request("GET", "/api/tags", timeout_s=10).body
        except EndpointError as exc:
            return self._unavailable(exc.code, f"local model endpoint {self.endpoint.display}: {exc.message}", t0)
        models = (tags or {}).get("models") if isinstance(tags, dict) else None
        if not isinstance(models, list):
            return self._unavailable("MALFORMED_RESPONSE", "/api/tags did not list models", t0)
        entry = next((m for m in models if isinstance(m, dict) and m.get("name") == self.model_id), None)
        if entry is None:
            entry = next((m for m in models if isinstance(m, dict) and m.get("model") == self.model_id), None)
        if entry is None:
            return self._unavailable("MODEL_ABSENT", f"model {self.model_id!r} is not present at {self.endpoint.display}; installing a model is a "
                                                     "separate explicit step (e.g. `ollama pull`); guard never downloads models", t0)
        digest = str(entry.get("digest") or "")
        if not digest:
            return self._unavailable("MALFORMED_RESPONSE", "the runtime reported no digest for the model", t0)
        if self.pinned and _norm_digest(digest) != self.pinned:
            return self._unavailable("DIGEST_MISMATCH", f"model {self.model_id!r} has digest {digest}, but the configuration pins "
                                                        f"{self.pinned}; refusing to use a different artifact", t0)
        try:
            show = self.http.request("POST", "/api/show", {"model": self.model_id}, timeout_s=30).body
        except EndpointError as exc:
            return self._unavailable(exc.code, f"/api/show failed: {exc.message}", t0)
        show = show if isinstance(show, dict) else {}
        details = show.get("details") if isinstance(show.get("details"), dict) else {}
        info = show.get("model_info") if isinstance(show.get("model_info"), dict) else {}
        lic_text = show.get("license") if isinstance(show.get("license"), str) else ""
        lic_id = info.get("general.license") if isinstance(info.get("general.license"), str) else None
        first = next((ln.strip() for ln in lic_text.splitlines() if ln.strip()), "")
        license_str = (lic_id or "undeclared") + (f" ({first}; license text sha256:{hashlib.sha256(lic_text.encode()).hexdigest()})" if lic_text
                                                    else " (no license text shipped with the artifact)")
        arch = info.get("general.architecture")
        ctx_len = info.get(f"{arch}.context_length") if isinstance(arch, str) else None
        rt = version.get("version") if isinstance(version, dict) else None
        ident = ModelIdentity(
            artifact_id=f"ollama:{self.model_id}", revision="sha256:" + _norm_digest(digest), license=license_str,
            runtime=f"ollama {rt or 'unknown'}", quantization=str(details.get("quantization_level") or "unknown"),
            details={"family": details.get("family"), "parameter_size": details.get("parameter_size"), "format": details.get("format"),
                     "size_bytes": entry.get("size"), "context_length": ctx_len, "base_model": info.get("general.base_model.0.repo_url"),
                     "license_link": info.get("general.license.link"), "license_id": lic_id,
                     "license_text_sha256": hashlib.sha256(lic_text.encode()).hexdigest() if lic_text else None,
                     "capabilities": show.get("capabilities"), "endpoint": self.endpoint.display})
        self._caps = ReasonerCapabilities(True, f"{self.model_id} at {self.endpoint.display} ({ident.runtime})", ident, "json_schema",
                                          int(ctx_len) if isinstance(ctx_len, int) else None, ("propose_contracts",), self.endpoint.display,
                                          round((time.perf_counter() - t0) * 1000, 3))
        return self._caps

    def _unavailable(self, code: str, msg: str, t0: float) -> ReasonerCapabilities:
        return ReasonerCapabilities(False, f"{code}: {msg}", endpoint=self.endpoint.display, probe_ms=round((time.perf_counter() - t0) * 1000, 3))

    # -- inference ----------------------------------------------------------------------------
    def propose_contracts(self, prompt: Prompt) -> ReasonerResponse:
        caps = self.capabilities()
        if not caps.available:
            raise ReasonerUnavailable("UNAVAILABLE", caps.reason)
        body = {"model": self.model_id, "stream": False, "format": prompt.response_schema, "options": dict(self.options), "keep_alive": self.keep_alive,
                "messages": [{"role": "system", "content": prompt.system}, {"role": "user", "content": prompt.user}]}
        t0 = time.perf_counter()
        try:
            res = self.http.request("POST", "/api/chat", body)
        except EndpointError as exc:
            raise ReasonerUnavailable(exc.code, f"inference request failed: {exc.message}") from None
        elapsed = round((time.perf_counter() - t0) * 1000, 3)
        d = res.body if isinstance(res.body, dict) else {}
        if d.get("model") not in (None, self.model_id):
            raise ReasonerUnavailable("MODEL_MISMATCH", f"the runtime answered with model {d.get('model')!r}, not {self.model_id!r}")
        msg = d.get("message") if isinstance(d.get("message"), dict) else {}
        text = msg.get("content") if isinstance(msg.get("content"), str) else ""
        timing = {k.replace("_duration", "_ms"): round(v / 1e6, 3) for k, v in d.items() if k.endswith("_duration") and isinstance(v, (int, float))}
        return ReasonerResponse(text=text, elapsed_ms=elapsed, prompt_tokens=d.get("prompt_eval_count"), output_tokens=d.get("eval_count"),
                                done_reason=d.get("done_reason"), runtime_timing_ms=timing,
                                request_options={**self.options, "keep_alive": self.keep_alive, "format": "json_schema", "stream": False,
                                                 "prompt_cached_tokens": d.get("prompt_eval_cached_count")})

    def propose_probe_parameters(self, contract: dict[str, Any], input_profile: str) -> dict[str, Any]:
        return dict(UNSUPPORTED_PROBE_PARAMETERS)

    def explain_finding(self, evidence: dict[str, Any]) -> dict[str, Any] | None:
        return dict(UNSUPPORTED_EXPLANATION)


__all__ = ["OllamaReasoner", "DEFAULT_OPTIONS"]
