"""Test double for the bring-your-own-key adapters: a fake OpenAI-compatible / Anthropic server on 127.0.0.1.

It drives the real adapters, transport, redaction and parsing code with controlled answers and
failure modes. It is not evidence about any hosted provider (none was exercised; see
docs/IMPLEMENTATION_STATUS.md "Remote and agent proposers").
"""

from __future__ import annotations

import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

SECRET = "sk-test-SECRET-6f1d0c2b9a8e7d6c5b4a"
KEY_ENV = "IG_TEST_PROVIDER_KEY"
TASK_RE = re.compile(r"^TASK: propose up to (\d+) contracts for (\S+)\.$", re.M)


class FakeProvider:
    """``mode``: ok | malformed | oversized | redirect | slow | unauthorized | echo_key | reject_json_schema | wrong_model.

    ``answer(symbol, body) -> str`` supplies the assistant text for chat requests."""

    def __init__(self, dialect: str = "openai", *, mode: str = "ok", answer: Callable[[str, dict[str, Any]], str] | None = None,
                 model: str = "fake-remote-1", require_key: bool = True, slow_s: float = 3.0) -> None:
        assert dialect in ("openai", "anthropic")
        self.dialect = dialect
        self.mode = mode
        self.answer = answer or (lambda sym, body: json.dumps({"candidates": []}))
        self.model = model
        self.require_key = require_key
        self.slow_s = slow_s
        self.requests: list[dict[str, Any]] = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a: Any) -> None:
                pass

            def _send(self, code: int, obj: Any, headers: dict[str, str] | None = None) -> None:
                data = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                try:
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def _key(self) -> str | None:
                if outer.dialect == "anthropic":
                    return self.headers.get("x-api-key")
                auth = self.headers.get("Authorization") or ""
                return auth[7:] if auth.startswith("Bearer ") else None

            def _route(self, method: str) -> None:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n)) if n else None
                outer.requests.append({"method": method, "path": self.path, "body": body, "headers": dict(self.headers.items())})
                key = self._key()
                if outer.mode == "redirect":
                    return self._send(302, {}, {"Location": "https://attacker.example/steal"})
                if outer.mode == "unauthorized" or (outer.require_key and key != SECRET):
                    return self._send(401, {"error": {"type": "authentication_error", "message": f"invalid x-api-key {key}"}})
                if outer.mode == "slow" and method == "POST":
                    time.sleep(outer.slow_s)
                if outer.mode == "oversized" and method == "POST":
                    return self._send(200, b'{"pad": "' + b"x" * (2 << 20) + b'"}')
                if method == "GET":
                    if outer.dialect == "openai" and self.path.endswith("/models"):
                        return self._send(200, {"object": "list", "data": [{"id": outer.model, "object": "model"}]})
                    if outer.dialect == "anthropic" and self.path.startswith("/v1/models/"):
                        mid = self.path.rsplit("/", 1)[1]
                        if mid != outer.model:
                            return self._send(404, {"type": "error", "error": {"type": "not_found_error", "message": f"model: {mid}"}})
                        return self._send(200, {"id": outer.model, "type": "model", "display_name": "Fake", "max_input_tokens": 100000,
                                                "max_tokens": 8000})
                    return self._send(404, {"error": "no route"})
                if outer.mode == "malformed":
                    return self._send(200, b"<html>not json</html>")
                if outer.mode == "reject_json_schema" and ((body or {}).get("response_format", {}).get("type") == "json_schema"
                                                           or "output_config" in (body or {})):
                    return self._send(400, {"error": {"message": "response_format json_schema is not supported"}})
                if outer.dialect == "openai" and self.path.endswith("/chat/completions"):
                    user = next(m["content"] for m in body["messages"] if m["role"] == "user")
                    m = TASK_RE.search(user)
                    text = outer.answer(m.group(2) if m else "", body)
                    if outer.mode == "echo_key":
                        text = text.replace("ECHO", key or "")
                    served = "someone-else" if outer.mode == "wrong_model" else outer.model
                    return self._send(200, {"id": "chatcmpl-1", "object": "chat.completion", "model": served,
                                            "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
                                            "usage": {"prompt_tokens": 100, "completion_tokens": 50}})
                if outer.dialect == "anthropic" and self.path == "/v1/messages":
                    m = TASK_RE.search(body["messages"][0]["content"])
                    text = outer.answer(m.group(2) if m else "", body)
                    if outer.mode == "echo_key":
                        text = text.replace("ECHO", key or "")
                    served = "someone-else" if outer.mode == "wrong_model" else outer.model
                    return self._send(200, {"id": "msg_1", "type": "message", "role": "assistant", "model": served,
                                            "content": [{"type": "thinking", "thinking": ""}, {"type": "text", "text": text}],
                                            "stop_reason": "end_turn", "usage": {"input_tokens": 100, "output_tokens": 50}})
                return self._send(404, {"error": f"no route {method} {self.path}"})

            def do_GET(self) -> None:
                self._route("GET")

            def do_POST(self) -> None:
                self._route("POST")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}" + ("/v1" if self.dialect == "openai" else "")

    def __enter__(self) -> "FakeProvider":
        self.thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.server.shutdown()
        self.server.server_close()

    def posts(self) -> list[dict[str, Any]]:
        return [r for r in self.requests if r["method"] == "POST"]


def provider_config(base_url: str, provider: str = "openai_compatible", *, model: str = "fake-remote-1", key_env: str | None = KEY_ENV,
                    extra: str = "", timeout: int = 20) -> str:
    lines = ["[semantic]", "enabled = true", f'provider = "{provider}"', f'base_url = "{base_url}"', f'model_id = "{model}"',
             f"request_timeout_s = {timeout}"]
    if key_env:
        lines.append(f'api_key_env = "{key_env}"')
    if extra:
        lines.append(extra)
    return "\n".join(lines) + "\n"
