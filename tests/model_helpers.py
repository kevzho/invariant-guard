"""Test doubles for M4: a fake Ollama HTTP server on 127.0.0.1 and a loopback-only socket audit.

The fake server lets unit/integration tests drive the real adapter, transport and parsing
code with controlled (including hostile) model answers. It is NOT evidence for the M4 gate:
the gate run uses the real local model (benchmarks/model_eval.py, docs/IMPLEMENTATION_STATUS.md).
"""

from __future__ import annotations

import json
import re
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

import pytest

from invariantguard.semantic.endpoint import is_loopback_ip

_REAL_CONNECT = socket.socket.connect
_REAL_CONNECT_EX = socket.socket.connect_ex
_REAL_CREATE_CONNECTION = socket.create_connection

FAKE_DIGEST = "ab" * 32
LICENSE_TEXT = "Fake License 1.0\n\nThis text stands in for a model license in tests.\n"
TASK_RE = re.compile(r"^TASK: propose up to (\d+) contracts for (\S+)\.$", re.M)


@pytest.fixture
def loopback_only(monkeypatch):
    """Allow socket connections to loopback addresses only, and record every destination."""
    seen: list[Any] = []

    def connect(self, address, *a, **k):
        seen.append(address)
        host = address[0] if isinstance(address, tuple) else str(address)
        if not is_loopback_ip(host):
            raise AssertionError(f"non-loopback connection attempted: {address!r}")
        return _REAL_CONNECT(self, address, *a, **k)

    def connect_ex(self, address, *a, **k):
        seen.append(address)
        host = address[0] if isinstance(address, tuple) else str(address)
        if not is_loopback_ip(host):
            raise AssertionError(f"non-loopback connection attempted: {address!r}")
        return _REAL_CONNECT_EX(self, address, *a, **k)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.setattr(socket, "create_connection", _REAL_CREATE_CONNECTION)
    return seen


def candidate(pre: Any, post: Any, profile: str = "bounded_builtin_numeric_list_v1", *, snippet: int = 0, lines: tuple[int, int] | None = None,
              **extra: Any) -> dict[str, Any]:
    c = {"precondition": pre, "predicate": post, "input_profile": profile, "source_ref": {"snippet": snippet, "start_line": (lines or (1, 1))[0],
                                                                                           "end_line": (lines or (1, 1))[1]},
         "rationale": "test", "uncertainty": "test", "unsupported_dependencies": [], "boundary_inputs": []}
    c.update(extra)
    return c


TRUE = {"kind": "literal", "value": True}


def arg(n: str) -> dict[str, Any]:
    return {"kind": "arg", "name": n}


RESULT = {"kind": "result"}


class FakeOllama:
    """A minimal Ollama look-alike. ``chat(symbol, body)`` returns the assistant message text."""

    def __init__(self, chat: Callable[[str, dict[str, Any]], str] | None = None, *, model: str = "fake-model:1b", digest: str = FAKE_DIGEST,
                 version: str = "0.0.0-fake", redirect_to: str | None = None, models_present: bool = True, oversized: bool = False) -> None:
        self.chat = chat or (lambda sym, body: json.dumps({"candidates": []}))
        self.model = model
        self.digest = digest
        self.version = version
        self.redirect_to = redirect_to
        self.models_present = models_present
        self.oversized = oversized
        self.requests: list[tuple[str, str, Any]] = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a: Any) -> None:  # silence
                pass

            def _send(self, code: int, obj: Any, headers: dict[str, str] | None = None) -> None:
                data = json.dumps(obj).encode() if not isinstance(obj, bytes) else obj
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(data)

            def _route(self, method: str) -> None:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n)) if n else None
                outer.requests.append((method, self.path, body))
                if outer.redirect_to:
                    return self._send(307, {}, {"Location": outer.redirect_to})
                if outer.oversized:
                    return self._send(200, b"{" + b" " * (2 << 20) + b"}")
                if (method, self.path) == ("GET", "/api/version"):
                    return self._send(200, {"version": outer.version})
                if (method, self.path) == ("GET", "/api/tags"):
                    models = [{"name": outer.model, "model": outer.model, "digest": outer.digest, "size": 123,
                               "details": {"quantization_level": "Q4_K_M", "family": "fake", "parameter_size": "1B", "format": "gguf"}}]
                    return self._send(200, {"models": models if outer.models_present else []})
                if (method, self.path) == ("POST", "/api/show"):
                    return self._send(200, {"license": LICENSE_TEXT, "details": {"quantization_level": "Q4_K_M", "family": "fake", "parameter_size": "1B",
                                                                                "format": "gguf"},
                                            "model_info": {"general.architecture": "fake", "fake.context_length": 4096, "general.license": "fake-1.0"},
                                            "capabilities": ["completion"]})
                if (method, self.path) == ("POST", "/api/chat"):
                    user = next(m["content"] for m in body["messages"] if m["role"] == "user")
                    m = TASK_RE.search(user)
                    text = outer.chat(m.group(2) if m else "", body)
                    return self._send(200, {"model": outer.model, "message": {"role": "assistant", "content": text}, "done": True, "done_reason": "stop",
                                            "prompt_eval_count": 100, "eval_count": 50, "total_duration": 1_000_000, "eval_duration": 500_000})
                return self._send(404, {"error": f"no route {method} {self.path}"})

            def do_GET(self) -> None:
                self._route("GET")

            def do_POST(self) -> None:
                self._route("POST")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def __enter__(self) -> "FakeOllama":
        self.thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.server.shutdown()
        self.server.server_close()

    def chats(self) -> list[dict[str, Any]]:
        return [b for m, p, b in self.requests if p == "/api/chat"]


def semantic_config(url: str, model: str = "fake-model:1b", *, digest: str | None = None, extra: str = "", timeout: int = 20) -> str:
    lines = ["[semantic]", "enabled = true", f'endpoint = "{url}"', f'model_id = "{model}"', f"request_timeout_s = {timeout}"]
    if digest:
        lines.append(f'model_digest = "{digest}"')
    if extra:
        lines.append(extra)
    return "\n".join(lines) + "\n"


def unused_loopback_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port
