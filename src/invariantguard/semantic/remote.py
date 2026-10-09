"""Bring-your-own-key contract proposers: OpenAI-compatible chat completions and Anthropic Messages.

These adapters are opt-in (DECISIONS I-105). They implement the same ``LocalReasoner``
protocol as the Ollama adapter, so their answers enter exactly the same strict parsing,
validation, non-vacuity, falsification and authority pipeline. Nothing they return can
raise authority above INFERRED, approve anything, change a baseline or count as proof.

Destination policy (``validate_provider_url``), independent of the API dialect:

* ``base_url`` is ``scheme://host[:port][/path]`` without user info, query or fragment;
* a literal loopback host (127.0.0.0/8, ``[::1]``, or ``localhost`` mapped to 127.0.0.1
  without name resolution) may use plain http; the connected peer must be loopback;
* any other host needs **https with certificate and hostname verification** and an
  explicit ``remote_allowed = true``; plain http to a non-loopback host is refused;
* redirects (3xx) are rejected and never followed; ``http.client`` is used directly, so
  ``HTTP(S)_PROXY`` variables are never consulted; response and request bodies are size
  capped; every request has a timeout; only an allowlist of (method, path) is requested.

Secrets: ``api_key_env`` names an environment variable. The key is read from the
environment at request time, placed in one request header, and never stored on an
object, logged, put into reports, evidence, cache keys, artifacts or exception text.
Error text received from a provider is redacted for the key before it is reported.

There is no fallback in either direction: if the configured provider fails, discovery is
UNAVAILABLE/INCOMPLETE, exactly like the loopback adapter.
"""

from __future__ import annotations

import hashlib
import http.client
import ipaddress
import json
import os
import socket
import ssl
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlsplit

from .endpoint import is_loopback_ip
from .reasoner import (UNSUPPORTED_EXPLANATION, UNSUPPORTED_PROBE_PARAMETERS, ModelIdentity, Prompt, ReasonerCapabilities, ReasonerResponse,
                       ReasonerUnavailable)

MAX_URL = 300
MAX_RESPONSE_BYTES = 1 << 20
DEFAULT_MAX_REQUEST_BYTES = 256 * 1024
ANTHROPIC_VERSION = "2023-06-01"
HOST_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789.-")
REMOTE_WARNING = ("a bounded context packet (the target function, selected callers/callees/tests and static facts) is sent to this host; "
                  "providers, especially free tiers, may retain or train on submitted data")


class DestinationError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Destination:
    scheme: str  # http | https
    host: str  # hostname as configured (lowercase) or literal IP
    connect_host: str  # what is connected to (literal IP for loopback)
    port: int
    base_path: str  # "" or "/v1" (no trailing slash)
    loopback: bool

    @property
    def display(self) -> str:
        h = f"[{self.host}]" if ":" in self.host else self.host
        default = 443 if self.scheme == "https" else 80
        return f"{self.scheme}://{h}" + ("" if self.port == default else f":{self.port}") + self.base_path

    @property
    def host_label(self) -> str:
        h = f"[{self.host}]" if ":" in self.host else self.host
        return f"{h}:{self.port}"


def validate_provider_url(url: Any, *, remote_allowed: bool) -> Destination:
    """Parse and validate a provider base URL; raises :class:`DestinationError`."""
    if type(url) is not str or not url:
        raise DestinationError("INVALID_ENDPOINT", "base_url must be a non-empty string such as https://api.example.com/v1")
    if len(url) > MAX_URL or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in url):
        raise DestinationError("INVALID_ENDPOINT", "base_url contains whitespace/control characters or is too long")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise DestinationError("INVALID_ENDPOINT", f"cannot parse base_url: {exc}") from None
    if parts.scheme not in ("http", "https"):
        raise DestinationError("INVALID_ENDPOINT", f"scheme {parts.scheme!r} is not supported (http for loopback, https otherwise)")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise DestinationError("INVALID_ENDPOINT", "user information in base_url is not allowed (credentials come from api_key_env only)")
    if parts.query or parts.fragment:
        raise DestinationError("INVALID_ENDPOINT", "base_url must not contain a query or fragment")
    path = parts.path.rstrip("/")
    if path and (not path.startswith("/") or "//" in path or any(seg in (".", "..") for seg in path.split("/"))
                 or any(not (c.isalnum() or c in "/-_.~") for c in path)):
        raise DestinationError("INVALID_ENDPOINT", "base_url path may contain only /, letters, digits, '-', '_', '.' and '~'")
    if port is not None and not 1 <= port <= 65535:
        raise DestinationError("INVALID_ENDPOINT", f"port {port} out of range")
    host = (parts.hostname or "").lower()
    if not host or "%" in host:
        raise DestinationError("INVALID_ENDPOINT", "base_url needs a host (IPv6 zone identifiers are not accepted)")
    literal: ipaddress.IPv4Address | ipaddress.IPv6Address | None
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if host == "localhost":
        loopback, connect = True, "127.0.0.1"  # fixed mapping; never resolved
    elif literal is not None:
        loopback, connect = is_loopback_ip(str(literal)), str(literal)
        if literal.is_unspecified or literal.is_multicast:
            raise DestinationError("INVALID_ENDPOINT", f"{host} is not a usable destination")
    else:
        if (not set(host) <= HOST_CHARS or host.startswith(("-", ".")) or ".." in host or host.rstrip(".").rsplit(".", 1)[-1].isdigit()
                or _inet_aton_accepts(host)):
            raise DestinationError("INVALID_ENDPOINT", "the host must be an ASCII DNS name or a literal IP address (numeric shorthands are refused)")
        if host.endswith(".localhost") or host == "localhost.":
            raise DestinationError("NON_LOOPBACK", f"{host} is not mapped to loopback without resolution; use 127.0.0.1, [::1] or localhost")
        loopback, connect = False, host
    if not loopback:
        if parts.scheme != "https":
            raise DestinationError("INSECURE_REMOTE", f"plain http to the non-loopback host {host} is refused; use https (TLS verification is always on)")
        if not remote_allowed:
            raise DestinationError("REMOTE_NOT_ALLOWED", f"{host} is not a loopback address; sending context packets to it needs an explicit "
                                                        "semantic.remote_allowed = true (data leaves this machine)")
    elif parts.scheme == "http" and port is None:
        raise DestinationError("INVALID_ENDPOINT", "a loopback base_url needs an explicit port (e.g. http://127.0.0.1:11434/v1)")
    return Destination(parts.scheme, host if literal is None else str(literal), connect, port or (443 if parts.scheme == "https" else 80), path, loopback)


def _inet_aton_accepts(host: str) -> bool:
    """True for legacy numeric forms (``0x7f000001``, ``017700000001``) that resolvers map to an address."""
    try:
        socket.inet_aton(host)
    except OSError:
        return False
    return True


@dataclass
class HttpResult:
    status: int
    body: Any
    raw_bytes: int
    elapsed_s: float
    request_bytes: int
    request_sha256: str


class ProviderHttp:
    """JSON over HTTP(S) to one validated destination, with an allowlist of requests."""

    def __init__(self, dest: Destination, *, timeout_s: float, allowed: frozenset[tuple[str, str]], api_key_env: str | None,
                 max_request_bytes: int = DEFAULT_MAX_REQUEST_BYTES, max_response_bytes: int = MAX_RESPONSE_BYTES) -> None:
        self.dest = dest
        self.timeout_s = timeout_s
        self.allowed = allowed
        self.api_key_env = api_key_env
        self.max_request_bytes = max_request_bytes
        self.max_response_bytes = max_response_bytes
        self.requests = 0

    # The key is fetched per request and lives only in local variables and the header dict.
    def _key(self) -> str | None:
        if not self.api_key_env:
            return None
        v = os.environ.get(self.api_key_env, "")
        if not v.strip():
            raise ReasonerUnavailable("API_KEY_MISSING", f"environment variable {self.api_key_env} is not set or empty (the key is never read from "
                                                         "the configuration)")
        if any(ord(c) < 33 or ord(c) > 126 for c in v.strip()):
            raise ReasonerUnavailable("API_KEY_INVALID", f"environment variable {self.api_key_env} contains characters that cannot be sent in a header")
        return v.strip()

    def _redact(self, text: str) -> str:
        if self.api_key_env:
            v = os.environ.get(self.api_key_env, "").strip()
            if len(v) >= 4:
                text = text.replace(v, "[REDACTED]")
        return text

    def _redact_bytes(self, data: bytes) -> bytes:
        if self.api_key_env:
            v = os.environ.get(self.api_key_env, "").strip()
            if len(v) >= 4:
                data = data.replace(v.encode("utf-8"), b"[REDACTED]")
                data = data.replace(json.dumps(v)[1:-1].encode("utf-8"), b"[REDACTED]")
        return data

    def _fail(self, code: str, msg: str) -> ReasonerUnavailable:
        return ReasonerUnavailable(code, self._redact(msg))

    def request(self, method: str, path: str, payload: Any | None, headers: dict[str, str] | None = None, *, auth: str = "bearer",
                timeout_s: float | None = None) -> HttpResult:
        if (method, path) not in self.allowed:
            raise ReasonerUnavailable("REQUEST_NOT_ALLOWED", f"{method} {path} is not an allowed request for this adapter")
        full = self.dest.base_path + path
        body = None if payload is None else json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if body is not None and len(body) > self.max_request_bytes:
            raise ReasonerUnavailable("REQUEST_TOO_LARGE", f"request body is {len(body)} bytes; the cap is {self.max_request_bytes} "
                                                           "(semantic.max_request_bytes); nothing was sent")
        key = self._key()
        hdrs = {"Accept": "application/json", "Connection": "close", "User-Agent": "invariantguard"}
        if body is not None:
            hdrs["Content-Type"] = "application/json"
        if key is not None:
            if auth == "x-api-key":
                hdrs["x-api-key"] = key
            else:
                hdrs["Authorization"] = "Bearer " + key
        hdrs.update(headers or {})
        to = timeout_s or self.timeout_s
        if self.dest.scheme == "https":
            ctx = ssl.create_default_context()  # CERT_REQUIRED + check_hostname; never disabled
            conn: http.client.HTTPConnection = http.client.HTTPSConnection(self.dest.connect_host, self.dest.port, timeout=to, context=ctx)
        else:
            conn = http.client.HTTPConnection(self.dest.connect_host, self.dest.port, timeout=to)
        t0 = time.perf_counter()
        try:
            try:
                conn.connect()
            except ssl.SSLError as exc:
                raise self._fail("TLS_ERROR", f"TLS verification with {self.dest.host_label} failed: {exc.reason or exc}") from None
            except (socket.timeout, TimeoutError):
                raise self._fail("TIMEOUT", f"connecting to {self.dest.host_label} timed out after {to:.0f} s") from None
            except OSError as exc:
                raise self._fail("UNREACHABLE", f"cannot connect to {self.dest.host_label}: {exc.strerror or type(exc).__name__}") from None
            peer = conn.sock.getpeername()[0] if conn.sock is not None else ""
            if self.dest.loopback and not is_loopback_ip(peer):
                raise self._fail("NON_LOOPBACK", f"connected peer {peer!r} is not a loopback address")
            self.requests += 1
            try:
                conn.request(method, full, body=body, headers=hdrs)
                resp = conn.getresponse()
                if 300 <= resp.status < 400:
                    raise self._fail("REDIRECT_REJECTED", f"{method} {full} answered {resp.status}; redirects are never followed")
                data = resp.read(self.max_response_bytes + 1)
            except (socket.timeout, TimeoutError):
                raise self._fail("TIMEOUT", f"{method} {full} timed out after {to:.0f} s") from None
            except ssl.SSLError as exc:
                raise self._fail("TLS_ERROR", f"{method} {full}: {exc.reason or exc}") from None
            except (OSError, http.client.HTTPException) as exc:
                raise self._fail("TRANSPORT", f"{method} {full} failed: {type(exc).__name__}") from None
        finally:
            del key, hdrs
            conn.close()
        if len(data) > self.max_response_bytes:
            raise self._fail("RESPONSE_TOO_LARGE", f"{method} {full} returned more than {self.max_response_bytes} bytes")
        data = self._redact_bytes(data)  # a provider echoing the key never gets it into parsed text, reports or storage
        try:
            parsed = json.loads(data.decode("utf-8")) if data else None
        except (UnicodeDecodeError, ValueError, RecursionError):
            parsed = None
            if resp.status == 200:
                raise self._fail("MALFORMED_RESPONSE", f"{method} {full} did not return JSON (HTTP {resp.status})") from None
        if resp.status in (401, 403):
            raise self._fail("AUTH_FAILED", f"{method} {full} returned HTTP {resp.status}: the provider rejected the credentials from "
                                            f"{self.api_key_env or '(no api_key_env configured)'}")
        if resp.status != 200:
            raise self._fail("HTTP_ERROR" if resp.status not in (400, 422) else "BAD_REQUEST",
                             f"{method} {full} returned HTTP {resp.status}" + (f": {_error_text(parsed)[:300]}" if parsed is not None else ""))
        return HttpResult(resp.status, parsed, len(data), time.perf_counter() - t0, len(body or b""),
                          "sha256:" + hashlib.sha256(body or b"").hexdigest())


def _error_text(parsed: Any) -> str:
    if isinstance(parsed, dict):
        e = parsed.get("error")
        if isinstance(e, dict):
            return str(e.get("message") or e.get("type") or "")
        if e is not None:
            return str(e)
    return ""


# -- structured-output schema transforms -------------------------------------------------------------
_ANTHROPIC_DROP = frozenset({"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf", "minLength", "maxLength", "minItems", "maxItems"})
LEAF_KINDS = ("arg", "literal", "result", "before_arg", "after_arg")


def unroll_schema(schema: dict[str, Any], depth: int = 4, drop: frozenset[str] = frozenset()) -> dict[str, Any]:
    """A non-recursive copy of the per-packet response schema.

    The DSL schema is recursive (``$defs/pre`` and ``$defs/post`` refer to themselves).
    Providers that reject recursive schemas get leveled definitions instead: ``pre_0`` may
    contain ``pre_1`` and so on, and the last level holds only leaf nodes. The size is linear
    in ``depth``. This only narrows what the decoder may emit: deeper but valid DSL is still
    accepted by the parser, and every answer is validated strictly anyway. Keywords in
    ``drop`` (unsupported constraints) are removed.
    """
    defs = schema.get("$defs", {})

    def is_leaf(alt: dict[str, Any]) -> bool:
        return "$ref" not in json.dumps(alt)

    def strip(node: Any, level: int | None) -> Any:
        if isinstance(node, list):
            return [strip(x, level) for x in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node and node["$ref"].startswith("#/$defs/"):
            name = node["$ref"].rsplit("/", 1)[1]
            return {"$ref": f"#/$defs/{name}_{0 if level is None else level + 1}"}
        return {k: strip(v, level) for k, v in node.items() if k != "$defs" and k not in drop}

    out_defs: dict[str, Any] = {}
    for name, d in defs.items():
        alts = d.get("anyOf", [])
        for level in range(depth + 1):
            use = alts if level < depth else [a for a in alts if is_leaf(a)]
            out_defs[f"{name}_{level}"] = {"anyOf": [strip(a, level) for a in use]}
    root = strip({k: v for k, v in schema.items() if k != "$defs"}, None)
    root["$defs"] = out_defs
    return root


def _served_ok(requested: str, served: Any) -> bool:
    return served is None or served == requested or (isinstance(served, str) and served.startswith(requested + "-"))


def _identity(provider: str, dest: Destination, model_id: str, served: str | None, extra: dict[str, Any]) -> ModelIdentity:
    return ModelIdentity(
        artifact_id=f"{provider}:{dest.host}/{model_id}",
        revision=f"unpinned: provider-served model {served or model_id} (a remote/served model has no verifiable artifact digest)",
        license="provider terms of service (not an open-weight artifact; not verified by guard)",
        runtime=f"{provider} at {dest.display}",
        quantization="unknown (not reported by the provider)",
        details={"provider": provider, "host": dest.host_label, "base_url": dest.display, "loopback": dest.loopback, "served_model": served, **extra})


class _RemoteBase:
    provider = ""
    operations = ("propose_contracts",)

    def __init__(self, dest: Destination, model_id: str, *, api_key_env: str | None, structured_output: str, timeout_s: float,
                 max_output_tokens: int, max_request_bytes: int, allowed: frozenset[tuple[str, str]]) -> None:
        if not model_id:
            raise ValueError("model_id is required")
        self.dest = dest
        self.model_id = model_id
        self.api_key_env = api_key_env
        self.structured_output = structured_output
        self.max_output_tokens = max_output_tokens
        self.http = ProviderHttp(dest, timeout_s=timeout_s, allowed=allowed, api_key_env=api_key_env, max_request_bytes=max_request_bytes)
        self._caps: ReasonerCapabilities | None = None
        self._mode: str | None = None  # structured-output mode actually used (after an "auto" downgrade)

    def egress_profile(self) -> dict[str, Any]:
        return {"provider": self.provider, "host": self.dest.host_label, "model_id": self.model_id, "context_left_machine": not self.dest.loopback,
                "base_url": self.dest.display, "api_key_env": self.api_key_env, "warning": None if self.dest.loopback else REMOTE_WARNING}

    def _unavailable(self, code: str, msg: str, t0: float) -> ReasonerCapabilities:
        return ReasonerCapabilities(False, f"{code}: {msg}", endpoint=self.dest.display, probe_ms=round((time.perf_counter() - t0) * 1000, 3))

    def propose_probe_parameters(self, contract: dict[str, Any], input_profile: str) -> dict[str, Any]:
        return dict(UNSUPPORTED_PROBE_PARAMETERS)

    def explain_finding(self, evidence: dict[str, Any]) -> dict[str, Any] | None:
        return dict(UNSUPPORTED_EXPLANATION)


class OpenAICompatibleReasoner(_RemoteBase):
    """``POST {base_url}/chat/completions`` (OpenAI-compatible; e.g. Ollama's /v1, vLLM, hosted services)."""

    provider = "openai_compatible"

    def __init__(self, dest: Destination, model_id: str, *, api_key_env: str | None = None, structured_output: str = "auto", timeout_s: float = 600.0,
                 max_output_tokens: int = 16000, max_request_bytes: int = DEFAULT_MAX_REQUEST_BYTES, temperature: float = 0.0, seed: int | None = 0) -> None:
        super().__init__(dest, model_id, api_key_env=api_key_env, structured_output=structured_output, timeout_s=timeout_s,
                         max_output_tokens=max_output_tokens, max_request_bytes=max_request_bytes,
                         allowed=frozenset({("GET", "/models"), ("POST", "/chat/completions")}))
        self.temperature = temperature
        self.seed = seed

    def capabilities(self) -> ReasonerCapabilities:
        if self._caps is not None:
            return self._caps
        t0 = time.perf_counter()
        listed: bool | None = None
        try:
            res = self.http.request("GET", "/models", None, timeout_s=min(30.0, self.http.timeout_s))
            data = res.body.get("data") if isinstance(res.body, dict) else None
            if isinstance(data, list):
                listed = any(isinstance(m, dict) and m.get("id") == self.model_id for m in data)
        except ReasonerUnavailable as exc:
            if exc.code in ("AUTH_FAILED", "API_KEY_MISSING", "API_KEY_INVALID", "UNREACHABLE", "TLS_ERROR", "REDIRECT_REJECTED", "NON_LOOPBACK",
                            "TIMEOUT", "RESPONSE_TOO_LARGE"):
                return self._unavailable(exc.code, f"{self.dest.display}: {exc.message}", t0)
            listed = None  # some compatible servers have no model list; identity then comes from responses
        if listed is False:
            return self._unavailable("MODEL_ABSENT", f"model {self.model_id!r} is not listed by {self.dest.display}/models; guard never installs or "
                                                     "selects another model", t0)
        ident = _identity(self.provider, self.dest, self.model_id, None, {"model_listed": listed, "structured_output": self.structured_output})
        self._caps = ReasonerCapabilities(True, f"{self.model_id} at {self.dest.display} (OpenAI-compatible chat completions"
                                                + ("" if self.dest.loopback else "; REMOTE: context packets leave this machine") + ")",
                                          ident, self.structured_output, None, self.operations, self.dest.display,
                                          round((time.perf_counter() - t0) * 1000, 3))
        return self._caps

    def _body(self, prompt: Prompt, mode: str) -> dict[str, Any]:
        body: dict[str, Any] = {"model": self.model_id, "stream": False, "temperature": self.temperature, "max_tokens": self.max_output_tokens,
                                "messages": [{"role": "system", "content": prompt.system}, {"role": "user", "content": prompt.user}]}
        if self.seed is not None:
            body["seed"] = self.seed
        if mode == "json_schema":
            body["response_format"] = {"type": "json_schema", "json_schema": {"name": "contract_candidates", "schema": prompt.response_schema,
                                                                               "strict": False}}
        else:
            body["response_format"] = {"type": "json_object"}
        return body

    def propose_contracts(self, prompt: Prompt) -> ReasonerResponse:
        caps = self.capabilities()
        if not caps.available:
            raise ReasonerUnavailable("UNAVAILABLE", caps.reason)
        mode = self._mode or ("json_schema" if self.structured_output in ("auto", "json_schema") else "json_object")
        t0 = time.perf_counter()
        downgraded = None
        try:
            res = self.http.request("POST", "/chat/completions", self._body(prompt, mode))
        except ReasonerUnavailable as exc:
            if not (exc.code == "BAD_REQUEST" and mode == "json_schema" and self.structured_output == "auto"):
                raise
            # The server rejected json_schema structured output: use JSON mode (same provider, same
            # model; the answer is validated identically). Recorded in request_options.
            downgraded = exc.message[:200]
            mode = self._mode = "json_object"
            res = self.http.request("POST", "/chat/completions", self._body(prompt, mode))
        elapsed = round((time.perf_counter() - t0) * 1000, 3)
        d = res.body if isinstance(res.body, dict) else {}
        served = d.get("model")
        if not _served_ok(self.model_id, served):
            raise ReasonerUnavailable("MODEL_MISMATCH", f"the provider answered with model {str(served)[:100]!r}, not {self.model_id!r}")
        choices = d.get("choices") if isinstance(d.get("choices"), list) else []
        ch = choices[0] if choices and isinstance(choices[0], dict) else {}
        msg = ch.get("message") if isinstance(ch.get("message"), dict) else {}
        text = msg.get("content") if isinstance(msg.get("content"), str) else ""
        usage = d.get("usage") if isinstance(d.get("usage"), dict) else {}
        return ReasonerResponse(text=text, elapsed_ms=elapsed, prompt_tokens=usage.get("prompt_tokens"), output_tokens=usage.get("completion_tokens"),
                                done_reason=ch.get("finish_reason"), runtime_timing_ms={},
                                request_options={"structured_output": mode, "structured_output_downgraded_from_json_schema": downgraded,
                                                 "temperature": self.temperature, "seed": self.seed, "max_tokens": self.max_output_tokens,
                                                 "served_model": served},
                                request_bytes=res.request_bytes, request_sha256=res.request_sha256)


class AnthropicReasoner(_RemoteBase):
    """``POST {base_url}/v1/messages`` (Anthropic Messages API, raw HTTP; see DECISIONS I-105 for why not the SDK)."""

    provider = "anthropic"

    def __init__(self, dest: Destination, model_id: str, *, api_key_env: str | None, structured_output: str = "auto", timeout_s: float = 600.0,
                 max_output_tokens: int = 16000, max_request_bytes: int = DEFAULT_MAX_REQUEST_BYTES, effort: str | None = None) -> None:
        if not api_key_env:
            raise ValueError("the anthropic provider needs api_key_env")
        super().__init__(dest, model_id, api_key_env=api_key_env, structured_output=structured_output, timeout_s=timeout_s,
                         max_output_tokens=max_output_tokens, max_request_bytes=max_request_bytes,
                         allowed=frozenset({("GET", "/v1/models/" + quote(model_id, safe="")), ("POST", "/v1/messages")}))
        self.effort = effort

    def _headers(self) -> dict[str, str]:
        return {"anthropic-version": ANTHROPIC_VERSION}

    def capabilities(self) -> ReasonerCapabilities:
        if self._caps is not None:
            return self._caps
        t0 = time.perf_counter()
        try:
            res = self.http.request("GET", "/v1/models/" + quote(self.model_id, safe=""), None, self._headers(), auth="x-api-key",
                                    timeout_s=min(30.0, self.http.timeout_s))
        except ReasonerUnavailable as exc:
            if exc.code == "HTTP_ERROR" and "HTTP 404" in exc.message:
                return self._unavailable("MODEL_ABSENT", f"model {self.model_id!r} is not available at {self.dest.display}", t0)
            return self._unavailable(exc.code, f"{self.dest.display}: {exc.message}", t0)
        m = res.body if isinstance(res.body, dict) else {}
        if m.get("id") not in (None, self.model_id):
            return self._unavailable("MODEL_MISMATCH", f"the provider describes model {str(m.get('id'))[:100]!r}, not {self.model_id!r}", t0)
        ident = _identity(self.provider, self.dest, self.model_id, m.get("id"),
                          {"display_name": m.get("display_name"), "max_input_tokens": m.get("max_input_tokens"), "max_tokens": m.get("max_tokens"),
                           "structured_output": self.structured_output, "anthropic_version": ANTHROPIC_VERSION})
        ctx = m.get("max_input_tokens") if isinstance(m.get("max_input_tokens"), int) else None
        self._caps = ReasonerCapabilities(True, f"{self.model_id} at {self.dest.display} (Anthropic Messages"
                                                + ("" if self.dest.loopback else "; REMOTE: context packets leave this machine") + ")",
                                          ident, self.structured_output, ctx, self.operations, self.dest.display,
                                          round((time.perf_counter() - t0) * 1000, 3))
        return self._caps

    def _body(self, prompt: Prompt, mode: str) -> dict[str, Any]:
        body: dict[str, Any] = {"model": self.model_id, "max_tokens": self.max_output_tokens, "system": prompt.system,
                                "messages": [{"role": "user", "content": prompt.user}]}
        oc: dict[str, Any] = {}
        if mode == "json_schema":
            # Structured outputs reject recursive schemas and numeric/length constraints: send an
            # unrolled, constraint-free copy (the answer is still validated strictly).
            oc["format"] = {"type": "json_schema", "schema": unroll_schema(prompt.response_schema, 4, _ANTHROPIC_DROP)}
        if self.effort:
            oc["effort"] = self.effort
        if oc:
            body["output_config"] = oc
        return body

    def propose_contracts(self, prompt: Prompt) -> ReasonerResponse:
        caps = self.capabilities()
        if not caps.available:
            raise ReasonerUnavailable("UNAVAILABLE", caps.reason)
        mode = self._mode or ("json_schema" if self.structured_output in ("auto", "json_schema") else "none")
        t0 = time.perf_counter()
        downgraded = None
        try:
            res = self.http.request("POST", "/v1/messages", self._body(prompt, mode), self._headers(), auth="x-api-key")
        except ReasonerUnavailable as exc:
            if not (exc.code == "BAD_REQUEST" and mode == "json_schema" and self.structured_output == "auto"):
                raise
            downgraded = exc.message[:200]
            mode = self._mode = "none"
            res = self.http.request("POST", "/v1/messages", self._body(prompt, mode), self._headers(), auth="x-api-key")
        elapsed = round((time.perf_counter() - t0) * 1000, 3)
        d = res.body if isinstance(res.body, dict) else {}
        served = d.get("model")
        if not _served_ok(self.model_id, served):
            raise ReasonerUnavailable("MODEL_MISMATCH", f"the provider answered with model {str(served)[:100]!r}, not {self.model_id!r}")
        blocks = d.get("content") if isinstance(d.get("content"), list) else []
        text = "".join(b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str))
        usage = d.get("usage") if isinstance(d.get("usage"), dict) else {}
        stop = d.get("stop_reason")
        details = d.get("stop_details") if stop == "refusal" and isinstance(d.get("stop_details"), dict) else None
        return ReasonerResponse(text=text, elapsed_ms=elapsed, prompt_tokens=usage.get("input_tokens"), output_tokens=usage.get("output_tokens"),
                                done_reason=stop, runtime_timing_ms={},
                                request_options={"structured_output": mode, "structured_output_downgraded_from_json_schema": downgraded,
                                                 "max_tokens": self.max_output_tokens, "effort": self.effort, "served_model": served,
                                                 "refusal_category": (details or {}).get("category")},
                                request_bytes=res.request_bytes, request_sha256=res.request_sha256)


def make_remote_reasoner(sem: dict[str, Any]) -> _RemoteBase:
    """The configured bring-your-own-key adapter. Destination policy is re-checked here."""
    try:
        dest = validate_provider_url(sem.get("base_url"), remote_allowed=bool(sem.get("remote_allowed")))
    except DestinationError as exc:
        raise ReasonerUnavailable(exc.code, exc.message) from None
    common = dict(api_key_env=sem.get("api_key_env") or None, structured_output=str(sem.get("structured_output", "auto")),
                  timeout_s=float(sem.get("request_timeout_s", 600)), max_output_tokens=int(sem.get("max_output_tokens", 16000)),
                  max_request_bytes=int(sem.get("max_request_bytes", DEFAULT_MAX_REQUEST_BYTES)))
    if sem.get("provider") == "openai_compatible":
        return OpenAICompatibleReasoner(dest, sem["model_id"], temperature=float(sem.get("temperature", 0.0)), seed=sem.get("seed", 0), **common)
    if sem.get("provider") == "anthropic":
        return AnthropicReasoner(dest, sem["model_id"], effort=sem.get("effort") or None, **common)
    raise ReasonerUnavailable("UNKNOWN_PROVIDER", f"provider {sem.get('provider')!r} is not a remote adapter")


__all__ = ["Destination", "DestinationError", "validate_provider_url", "ProviderHttp", "OpenAICompatibleReasoner", "AnthropicReasoner",
           "make_remote_reasoner", "unroll_schema", "REMOTE_WARNING"]
