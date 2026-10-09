"""Loopback-only HTTP transport for the local model endpoint (M4 T41, docs/08, docs/10).

Destination validation is independent of the API dialect (docs/08: "Do not equate an
OpenAI-compatible API schema with cloud use: validate the destination independently"):

* the URL must be ``http://HOST:PORT`` with an explicit port, no user info, query,
  fragment or path;
* HOST must be an IPv4 address in 127.0.0.0/8, the IPv6 loopback ``::1`` (written
  ``[::1]``), an IPv4-mapped IPv6 loopback, or the word ``localhost``, which is mapped to
  127.0.0.1 *without* name resolution (a hosts file or DNS answer is never trusted);
  any other name is rejected rather than resolved, and so are numeric shorthands
  (``127.1``, ``2130706433``), zone IDs and unspecified addresses;
* after connecting, the socket's peer address is checked again (loopback or the
  connection is closed);
* responses with a 3xx status are rejected (``REDIRECT_REJECTED``); redirects are never
  followed. ``http.client`` is used directly, so ``HTTP(S)_PROXY`` environment variables
  are never consulted;
* only an allowlist of (method, path) pairs can be requested, and none of them downloads,
  creates or deletes models;
* response bodies are size-limited.

There is no fallback: when the loopback endpoint fails, the request fails.
"""

from __future__ import annotations

import http.client
import ipaddress
import json
import socket
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

MAX_RESPONSE_BYTES = 1 << 20
MAX_URL = 200
# The only requests the adapter can make. /api/pull, /api/create, /api/delete, /api/copy and
# /api/push are deliberately absent (no model download or mutation during verification).
ALLOWED_REQUESTS = frozenset({("GET", "/api/version"), ("GET", "/api/tags"), ("POST", "/api/show"), ("POST", "/api/chat")})


class EndpointError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True)
class LoopbackEndpoint:
    url: str
    ip: str  # literal loopback IP actually connected to
    port: int

    @property
    def display(self) -> str:
        host = f"[{self.ip}]" if ":" in self.ip else self.ip
        return f"http://{host}:{self.port}"


def is_loopback_ip(text: str) -> bool:
    if type(text) is not str or "%" in text:  # zone-scoped addresses are never loopback here
        return False
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped.is_loopback
    return ip.is_loopback


def validate_loopback_endpoint(url: Any) -> LoopbackEndpoint:
    """Parse and validate a configured endpoint; raises :class:`EndpointError`."""
    if type(url) is not str or not url:
        raise EndpointError("INVALID_ENDPOINT", "the endpoint must be a non-empty string such as http://127.0.0.1:11434")
    if len(url) > MAX_URL or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in url):
        raise EndpointError("INVALID_ENDPOINT", "the endpoint contains whitespace/control characters or is too long")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise EndpointError("INVALID_ENDPOINT", f"cannot parse endpoint: {exc}") from None
    if parts.scheme != "http":
        raise EndpointError("INVALID_ENDPOINT", f"scheme {parts.scheme!r} is not supported; the local adapter speaks plain http to a loopback address")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise EndpointError("INVALID_ENDPOINT", "user information in the endpoint is not allowed")
    if parts.query or parts.fragment or parts.path not in ("", "/"):
        raise EndpointError("INVALID_ENDPOINT", "the endpoint must not contain a path, query or fragment")
    if port is None:
        raise EndpointError("INVALID_ENDPOINT", "the endpoint needs an explicit port (e.g. http://127.0.0.1:11434)")
    if not 1 <= port <= 65535:
        raise EndpointError("INVALID_ENDPOINT", f"port {port} out of range")
    host = parts.hostname or ""
    if "%" in host:
        raise EndpointError("NON_LOOPBACK", "IPv6 zone identifiers are not loopback destinations")
    if host == "localhost":
        ip = "127.0.0.1"  # fixed mapping; never resolved through DNS or a hosts file
    else:
        try:
            addr = ipaddress.ip_address(host)
        except ValueError:
            raise EndpointError("NON_LOOPBACK", f"host {host!r} is not a literal loopback address; hostnames are not resolved "
                                                "(use 127.0.0.1, [::1] or localhost)") from None
        if not is_loopback_ip(str(addr)):
            raise EndpointError("NON_LOOPBACK", f"{host} is not a loopback address; remote inference endpoints are not supported "
                                                "(no cloud or network fallback exists)")
        ip = str(addr)
    return LoopbackEndpoint(url=url, ip=ip, port=port)


@dataclass
class HttpResult:
    status: int
    body: Any
    raw_bytes: int
    elapsed_s: float


class LoopbackHttp:
    """Minimal JSON-over-HTTP client bound to one validated loopback endpoint."""

    def __init__(self, endpoint: LoopbackEndpoint, timeout_s: float) -> None:
        self.endpoint = endpoint
        self.timeout_s = timeout_s
        self.requests = 0

    def request(self, method: str, path: str, payload: Any | None = None, *, timeout_s: float | None = None) -> HttpResult:
        import time

        if (method, path) not in ALLOWED_REQUESTS:
            raise EndpointError("REQUEST_NOT_ALLOWED", f"{method} {path} is not an allowed model-endpoint request")
        conn = http.client.HTTPConnection(self.endpoint.ip, self.endpoint.port, timeout=timeout_s or self.timeout_s)
        t0 = time.perf_counter()
        try:
            try:
                conn.connect()
            except OSError as exc:
                raise EndpointError("UNREACHABLE", f"cannot connect to {self.endpoint.display}: {exc.strerror or exc}") from None
            peer = conn.sock.getpeername()[0] if conn.sock is not None else ""
            if not is_loopback_ip(peer):
                raise EndpointError("NON_LOOPBACK", f"connected peer {peer!r} is not a loopback address")
            body = None if payload is None else json.dumps(payload).encode("utf-8")
            headers = {"Accept": "application/json", "Connection": "close"}
            if body is not None:
                headers["Content-Type"] = "application/json"
            self.requests += 1
            try:
                conn.request(method, path, body=body, headers=headers)
                resp = conn.getresponse()
                if 300 <= resp.status < 400:
                    raise EndpointError("REDIRECT_REJECTED", f"{method} {path} answered {resp.status} (redirect to {resp.getheader('Location')!r}); "
                                                             "redirects are never followed")
                data = resp.read(MAX_RESPONSE_BYTES + 1)
            except (socket.timeout, TimeoutError):
                raise EndpointError("TIMEOUT", f"{method} {path} timed out after {timeout_s or self.timeout_s:.0f} s") from None
            except (OSError, http.client.HTTPException) as exc:
                raise EndpointError("TRANSPORT", f"{method} {path} failed: {type(exc).__name__}: {exc}") from None
        finally:
            conn.close()
        if len(data) > MAX_RESPONSE_BYTES:
            raise EndpointError("RESPONSE_TOO_LARGE", f"{method} {path} returned more than {MAX_RESPONSE_BYTES} bytes")
        try:
            parsed = json.loads(data.decode("utf-8")) if data else None
        except (UnicodeDecodeError, ValueError, RecursionError):
            raise EndpointError("MALFORMED_RESPONSE", f"{method} {path} did not return JSON (HTTP {resp.status})") from None
        if resp.status != 200:
            err = parsed.get("error") if isinstance(parsed, dict) else None
            raise EndpointError("HTTP_ERROR", f"{method} {path} returned HTTP {resp.status}" + (f": {str(err)[:300]}" if err else ""))
        return HttpResult(resp.status, parsed, len(data), time.perf_counter() - t0)


__all__ = ["EndpointError", "LoopbackEndpoint", "LoopbackHttp", "validate_loopback_endpoint", "is_loopback_ip", "ALLOWED_REQUESTS", "MAX_RESPONSE_BYTES"]
